# -*- coding: utf-8 -*-
"""Hermes ACP Bridge — subprocess + JSON-RPC.

Wave 16-B (Phase B). Connects to Hermes как ACP клиент через stdio.
Реализует AgentEngineClient Protocol.

Hermes binary может быть не установлен — bridge тихо degradates:
- health() возвращает is_healthy=False
- stream() выдаёт один error chunk с finish_reason="engine_unavailable"
- НЕ raise при отсутствии Hermes
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
import warnings
from pathlib import Path
from typing import Any, AsyncIterator

from ..core.agent_engine import EngineHealth, EngineKind, StreamChunk
from ..core.logger import get_logger
from ..core.subprocess_env import clean_subprocess_env

logger = get_logger(__name__)

# Таймаут ожидания graceful shutdown subprocess (сек)
_TERMINATE_TIMEOUT = 5


def _resolve_hermes_binary() -> str | None:
    """Поиск hermes executable: env → PATH → ~/.hermes/bin/.

    Порядок приоритета (Wave 19-C):
      1. KRAB_HERMES_BINARY — явный override из .env
      2. shutil.which("hermes") — системный PATH
      3. ~/.hermes/bin/hermes — стандартное место Phase A install

    Возвращает абсолютный путь к executable или None если не найден.
    """
    # 1. Явный override через env
    env_path = os.environ.get("KRAB_HERMES_BINARY")
    if env_path:
        p = Path(env_path)
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
        # env выставлен, но путь невалиден — логируем, не падаем
        logger.warning(
            "hermes_binary_env_invalid",
            krab_hermes_binary=env_path,
            hint="KRAB_HERMES_BINARY указывает на несуществующий/не-executable файл",
        )

    # 2. Системный PATH
    found = shutil.which("hermes")
    if found:
        return found

    # 3. Стандартное место Phase A install
    user_path = Path.home() / ".hermes" / "bin" / "hermes"
    if user_path.is_file() and os.access(user_path, os.X_OK):
        return str(user_path)

    return None


# TTL кэша health probe (сек)
_HEALTH_CACHE_TTL = 60.0


class HermesACPBridge:
    """Subprocess wrapper над hermes acp. Лениво стартует Hermes process.

    Sessions кэшируются in-memory (chat_id/room -> session_id). На Krab restart
    ResponseDB Hermes сохраняет, но мы делаем new session при первом prompt.
    """

    def __init__(
        self,
        *,
        binary: str | None = None,
        mcp_servers: list[dict] | None = None,
    ) -> None:
        # Приоритет: явный аргумент → _resolve_hermes_binary() (env > PATH > ~/.hermes/bin)
        # Wave 19-C: используем _resolve_hermes_binary() вместо _auto_detect_binary()
        self._binary = binary or _resolve_hermes_binary() or self._auto_detect_binary()
        self._mcp_servers = list(mcp_servers or [])
        self._proc: asyncio.subprocess.Process | None = None
        self._client: Any = None  # acp.Client instance — Phase C
        self._sessions: dict[str, str] = {}  # logical_id -> acp session_id
        self._lock = asyncio.Lock()
        # (timestamp, EngineHealth) — кэш probe
        self._healthy_cache: tuple[float, EngineHealth] | None = None

    @staticmethod
    def _auto_detect_binary() -> str:
        """Ищем hermes binary: сначала ~/.hermes/bin/hermes, потом PATH."""
        # 1. Стандартное место установки Phase A
        candidate = Path.home() / ".hermes" / "bin" / "hermes"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        # 2. Системный PATH
        return shutil.which("hermes") or "hermes"

    @property
    def kind(self) -> EngineKind:
        """Идентификатор движка."""
        return "hermes"

    def _binary_available(self) -> bool:
        """Проверяет, доступен ли бинарь hermes."""
        path = Path(self._binary)
        if path.is_absolute():
            return path.is_file() and os.access(path, os.X_OK)
        # Относительное имя — ищем через shutil.which
        return shutil.which(self._binary) is not None

    async def _ensure_started(self) -> bool:
        """Lazy spawn hermes acp subprocess. Returns True если поднялся."""
        async with self._lock:
            # Если уже работает — ничего не делаем
            if self._proc is not None and self._proc.returncode is None:
                return True
            if not self._binary_available():
                return False
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    self._binary,
                    "acp",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=clean_subprocess_env(),
                )
                # Phase C: wire acp.Client.from_streams(stdin, stdout) здесь
                logger.info("hermes_acp_started", pid=self._proc.pid, binary=self._binary)
                return True
            except (FileNotFoundError, OSError) as exc:
                logger.warning(
                    "hermes_acp_start_failed",
                    binary=self._binary,
                    error=str(exc),
                )
                self._proc = None
                return False

    async def health(self) -> EngineHealth:
        """Health probe с 60s кэшированием.

        НЕ поднимает subprocess, если binary недоступен — сразу unhealthy.
        """
        now = time.monotonic()
        # Возвращаем кэш если свежий
        if self._healthy_cache is not None:
            cache_ts, cached = self._healthy_cache
            if (now - cache_ts) < _HEALTH_CACHE_TTL:
                return cached

        if not self._binary_available():
            # Wave 19-C: подсказываем про install script при отсутствии binary
            logger.info(
                "hermes_binary_missing",
                binary=self._binary,
                hint="Запусти scripts/install_hermes.sh для установки Hermes",
            )
            result = EngineHealth(
                engine="hermes",
                is_healthy=False,
                error=(
                    f"hermes binary not found: {self._binary}. "
                    "Run scripts/install_hermes.sh to install."
                ),
                last_check_at=_now_iso(),
            )
        else:
            t0 = time.monotonic()
            started = await self._ensure_started()
            latency_ms = (time.monotonic() - t0) * 1000
            result = EngineHealth(
                engine="hermes",
                is_healthy=started,
                latency_ms=round(latency_ms, 1) if started else None,
                error=None if started else "subprocess failed to start",
                last_check_at=_now_iso(),
            )

        self._healthy_cache = (now, result)
        return result

    def _invalidate_health_cache(self) -> None:
        """Сбрасывает кэш health — вызывается при изменении состояния."""
        self._healthy_cache = None

    async def stream(
        self,
        prompt: str,
        *,
        ctx: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream Hermes response.

        Phase B: stub — возвращает один error/info chunk.
        Phase C: реальный acp.Client.prompt() loop здесь.
        """
        health = await self.health()
        if not health.is_healthy:
            yield StreamChunk(
                text=f"[Hermes unavailable: {health.error}]",
                chunk_type="finish",
                finish_reason="engine_unavailable",
            )
            return

        # Phase C: реальный acp streaming
        yield StreamChunk(
            text="[Hermes Phase C — streaming not yet implemented]",
            chunk_type="finish",
            finish_reason="not_implemented",
        )

    async def cancel(self, session_id: str) -> bool:
        """Отменяет сессию. Phase C: acp.Client.cancel(session_id)."""
        return False

    async def close(self) -> None:
        """Graceful shutdown subprocess."""
        async with self._lock:
            if self._proc is not None and self._proc.returncode is None:
                try:
                    self._proc.terminate()
                    await asyncio.wait_for(self._proc.wait(), timeout=_TERMINATE_TIMEOUT)
                    logger.info("hermes_acp_stopped", pid=self._proc.pid)
                except asyncio.TimeoutError:
                    logger.warning("hermes_acp_kill_forced", pid=self._proc.pid)
                    self._proc.kill()
                except ProcessLookupError:
                    pass  # процесс уже завершился
            self._proc = None
            self._client = None
            self._sessions.clear()
            self._invalidate_health_cache()


def _now_iso() -> str:
    """Текущее UTC время в ISO 8601."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Singleton fabric (lazy)
# ---------------------------------------------------------------------------

_bridge: HermesACPBridge | None = None
# Async lock для double-checked locking при конкурентном создании синглтона
_bridge_lock = asyncio.Lock()


async def get_hermes_bridge() -> HermesACPBridge:
    """Async singleton accessor с asyncio.Lock — защита от concurrent создания.

    Использует double-checked locking: быстрый путь без lock если уже создан,
    иначе захватывает lock и проверяет ещё раз внутри.
    """
    global _bridge  # noqa: PLW0603
    # Быстрый путь — без lock если синглтон уже инициализирован
    if _bridge is not None:
        return _bridge
    async with _bridge_lock:
        # Double-checked locking: повторная проверка внутри lock
        if _bridge is None:
            _bridge = HermesACPBridge()
        return _bridge


def get_hermes_bridge_sync() -> HermesACPBridge:
    """Sync version — НЕ thread-safe для первого создания.

    .. deprecated::
        Используй async версию ``await get_hermes_bridge()``.
        Оставлен для backward compat — удалить в Session 38+
        (callsites только в тестах, production src мигрирован в Wave 16-P).
    """
    warnings.warn(
        "get_hermes_bridge_sync() устарел — используй async get_hermes_bridge(). "
        "Запланировано удаление в Session 38+.",
        DeprecationWarning,
        stacklevel=2,
    )
    global _bridge  # noqa: PLW0603
    if _bridge is None:
        _bridge = HermesACPBridge()
    return _bridge


def reset_hermes_bridge() -> None:
    """Сбрасывает синглтон. Используется в тестах."""
    global _bridge  # noqa: PLW0603
    _bridge = None
