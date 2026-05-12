# -*- coding: utf-8 -*-
"""Wave 109: периодический health probe для MCP-серверов.

### Зачем

В Krab подключено 11+ MCP-серверов (context7, firecrawl, github, sentry,
krab-hammerspoon, krab-telegram, krab-telegram-owner, tor-full, osint-tools,
hexstrike-ai-manual, brave-search, ...). Если процесс падает или транспорт
рвётся, мы узнаём об этом только при следующем tool call — то есть в момент,
когда уже произошла deadline пользователя.

Health probe раз в `KRAB_MCP_PROBE_INTERVAL_SEC=300` секунд делает легковесный
`list_tools()` через активную сессию `MCPClientManager`. Если сервер не
зарегистрирован в `MCPClientManager.sessions` или вернул tools=0 — считаем
сервер недоступным.

### Что НЕ делает

- Не запускает серверы сам. Стартует их `MCPClientManager.ensure_server`/
  `start_server`. Probe только наблюдает.
- Не лечит. Записывает snapshot + Prometheus, alert поднимает Prometheus
  при `krab_mcp_server_alive == 0 for 15m`.

### Snapshot

Per-server dict:
- ``last_probe_ts`` — Unix timestamp (UTC).
- ``last_ok`` — bool, успех последнего probe.
- ``consecutive_fails`` — текущая серия провалов (0 если последний — успех).
- ``total_fails`` — суммарно за время жизни probe.
- ``last_reason`` — короткая строка ('ok' | 'timeout' | 'exception' | ...).
- ``last_error`` — repr(exc) для последнего fail, иначе пустая строка.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .logger import get_logger
from .metrics.mcp_health import record_probe_result

logger = get_logger(__name__)


# Default probe interval (Wave 109): 5 мин. Tests подменяют через конструктор.
_DEFAULT_INTERVAL_SEC: float = 300.0
# Per-probe timeout: 5 сек — list_tools обычно < 100мс, более 5 сек = сервер залип.
_DEFAULT_PROBE_TIMEOUT_SEC: float = 5.0


def _env_interval() -> float:
    """Читает `KRAB_MCP_PROBE_INTERVAL_SEC` с graceful fallback на дефолт."""
    raw = os.environ.get("KRAB_MCP_PROBE_INTERVAL_SEC", "").strip()
    if not raw:
        return _DEFAULT_INTERVAL_SEC
    try:
        value = float(raw)
        # Защита от 0/негатив: иначе loop спалит CPU.
        if value < 1.0:
            return _DEFAULT_INTERVAL_SEC
        return value
    except (TypeError, ValueError):
        return _DEFAULT_INTERVAL_SEC


def _now_ts() -> float:
    return time.time()


class MCPHealthProbe:
    """Periodic probe для всех зарегистрированных MCP-серверов.

    Используется как module-level singleton (`mcp_health_probe` ниже).
    Под капотом — `MCPClientManager.sessions.list_tools()` с 5s timeout.

    Тестируемость:
    - `manager_fn` инжектируется (callable возвращающий MCPClientManager-like).
    - `probe_fn` инжектируется (per-server probe: name → awaitable bool/raise).
    - `now_fn` инжектируется для детерминированных snapshot timestamps.
    """

    def __init__(
        self,
        *,
        manager_fn: Callable[[], Any] | None = None,
        probe_fn: Callable[[str, Any], Awaitable[None]] | None = None,
        timeout_sec: float = _DEFAULT_PROBE_TIMEOUT_SEC,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._snapshot: dict[str, dict[str, Any]] = {}
        self._manager_fn = manager_fn or self._default_manager_fn
        self._probe_fn = probe_fn or self._default_probe_fn
        self._timeout_sec = float(timeout_sec)
        self._now_fn: Callable[[], float] = now_fn or _now_ts
        self._task: asyncio.Task[None] | None = None

    # ---- Defaults (production wiring) ----------------------------------

    @staticmethod
    def _default_manager_fn() -> Any:
        """Lazy import избегает циклической зависимости при импорте модуля.

        В тестах подменяется на стаб через конструктор.
        """
        from .. import mcp_client  # local import to break cycle

        return mcp_client.mcp_manager

    @staticmethod
    async def _default_probe_fn(server: str, session: Any) -> None:
        """Базовый probe: list_tools(). Бросает если возвращает пустой tools.

        Любая ошибка propagates наверх — `probe_once` классифицирует reason.
        """
        result = await session.list_tools()
        tools = getattr(result, "tools", None) or []
        if not tools:
            raise RuntimeError("no_tools")

    # ---- Public API -----------------------------------------------------

    async def probe_once(self) -> dict[str, dict[str, Any]]:
        """Один цикл probe по всем активным сессиям + неактивным registered.

        Возвращает snapshot копию.
        """
        manager = self._manager_fn()
        sessions: dict[str, Any] = dict(getattr(manager, "sessions", {}) or {})

        # Probes выполняем последовательно: их немного (~10-11),
        # параллельность даст копеечный выигрыш и усложнит логи.
        for server, session in sessions.items():
            await self._probe_server(server, session)

        # Сервера, которые зарегистрированы в managed реестре, но не имеют
        # активной сессии (например, ensure_server ещё не вызвали или failed):
        # фиксируем как «down, no_session», чтобы alert смог сработать.
        try:
            from .mcp_registry import get_managed_mcp_servers

            for managed_name in get_managed_mcp_servers().keys():
                if managed_name not in sessions:
                    self._record(managed_name, ok=False, reason="no_session", error="")
        except Exception as exc:  # noqa: BLE001
            # Не валим probe если registry дёрнулся — это не критично.
            logger.warning(
                "mcp_probe_registry_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

        return self.get_snapshot()

    async def _probe_server(self, server: str, session: Any) -> None:
        try:
            await asyncio.wait_for(
                self._probe_fn(server, session),
                timeout=self._timeout_sec,
            )
        except asyncio.TimeoutError:
            self._record(server, ok=False, reason="timeout", error="")
            logger.warning(
                "mcp_probe_timeout",
                server=server,
                timeout_sec=self._timeout_sec,
            )
            return
        except Exception as exc:  # noqa: BLE001
            reason = "no_tools" if str(exc) == "no_tools" else "exception"
            self._record(server, ok=False, reason=reason, error=repr(exc))
            logger.warning(
                "mcp_probe_failed",
                server=server,
                reason=reason,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        self._record(server, ok=True, reason="ok", error="")

    def _record(self, server: str, *, ok: bool, reason: str, error: str) -> None:
        ts = self._now_fn()
        with self._lock:
            entry = self._snapshot.setdefault(
                server,
                {
                    "last_probe_ts": 0.0,
                    "last_ok": False,
                    "consecutive_fails": 0,
                    "total_fails": 0,
                    "last_reason": "never",
                    "last_error": "",
                },
            )
            entry["last_probe_ts"] = float(ts)
            entry["last_ok"] = bool(ok)
            entry["last_reason"] = reason
            entry["last_error"] = error
            if ok:
                entry["consecutive_fails"] = 0
            else:
                entry["consecutive_fails"] = int(entry.get("consecutive_fails") or 0) + 1
                entry["total_fails"] = int(entry.get("total_fails") or 0) + 1
        record_probe_result(server=server, alive=ok, reason=None if ok else reason)

    def get_snapshot(self) -> dict[str, dict[str, Any]]:
        """Глубокая копия snapshot для API/тестов (caller не мутирует state)."""
        with self._lock:
            return {server: dict(entry) for server, entry in self._snapshot.items()}

    def reset(self) -> None:
        """Сброс снимка — нужен тестам и при reload MCP реестра."""
        with self._lock:
            self._snapshot.clear()

    # ---- Background loop ------------------------------------------------

    async def periodic_loop(self, interval_seconds: float | None = None) -> None:
        """Фоновый async loop: probe_once каждые `interval_seconds`.

        Завершается при `CancelledError` (graceful shutdown). Любая иная
        ошибка внутри `probe_once` — поглощается с warning, loop живёт.
        """
        interval = float(interval_seconds) if interval_seconds is not None else _env_interval()
        logger.info("mcp_health_probe_loop_started", interval_sec=interval)
        try:
            while True:
                try:
                    await self.probe_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "mcp_health_probe_cycle_failed",
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("mcp_health_probe_loop_stopped")
            raise

    def start_background(self, interval_seconds: float | None = None) -> asyncio.Task[None]:
        """Создаёт asyncio.Task, держит ссылку чтобы GC не съел.

        Идемпотентно: если task уже запущен и жив — возвращает существующий.
        """
        if self._task is not None and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self.periodic_loop(interval_seconds))
        return self._task

    def stop_background(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None


# Module-level singleton — pattern совпадает с chat_ban_cache, silence_manager,
# inbox_service. Bootstrap вызывает `mcp_health_probe.start_background()` из
# userbot_bridge после старта MCPClientManager.
mcp_health_probe = MCPHealthProbe()
