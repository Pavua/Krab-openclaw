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
import contextlib
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

# Wave 16-Q (Phase C): sentinel для finish событий в acp event queue
_STREAM_FINISH_SENTINEL = object()


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


class _HermesEventClient:
    """Wave 16-Q (Phase C): минимальный acp.Client impl для приёма session_update'ов.

    acp protocol: agent (Hermes) шлёт `session_update` notifications с
    SessionNotification.update — это chunked content для streaming. Мы
    конвертируем их в StreamChunk и кладём в asyncio.Queue, откуда
    HermesACPBridge.stream() их выбирает.

    Не наследуемся напрямую от acp.Client — duck typing достаточен (acp
    проверяет наличие методов через protocol). Это упрощает тесты.
    """

    def __init__(self) -> None:
        # Per-session queues: session_id → Queue[StreamChunk | sentinel]
        self._queues: dict[str, asyncio.Queue] = {}

    def get_queue(self, session_id: str) -> asyncio.Queue:
        """Возвращает queue для session_id, создавая её при первом обращении."""
        q = self._queues.get(session_id)
        if q is None:
            q = asyncio.Queue()
            self._queues[session_id] = q
        return q

    def drop_queue(self, session_id: str) -> None:
        """Очищает queue после finish (память не утечёт)."""
        self._queues.pop(session_id, None)

    async def session_update(self, params: Any) -> None:
        """ACP method: agent → client streaming chunk.

        params — SessionNotification с полями: session_id, update.
        update это discriminated union (text content, tool call, finish, etc.).
        Phase C minimal: extract text → StreamChunk("text") → queue.
        """
        session_id = str(getattr(params, "session_id", "") or "")
        update = getattr(params, "update", None)
        if not session_id or update is None:
            return

        q = self.get_queue(session_id)

        # Минимально: вытаскиваем text из любого update content
        text = ""
        # ACP SessionUpdate имеет sessionUpdate discriminator: agent_message_chunk,
        # tool_call, end_of_turn и т.д. Phase C: только agent_message_chunk → text.
        update_type = (
            getattr(update, "sessionUpdate", None)
            or getattr(update, "session_update", None)
            or getattr(update, "type", "")
        )
        if str(update_type) in ("agent_message_chunk", "agentMessageChunk"):
            content = getattr(update, "content", None)
            text = getattr(content, "text", "") if content is not None else ""
            if text:
                await q.put(StreamChunk(text=text, chunk_type="text"))
        elif str(update_type) in ("end_of_turn", "endOfTurn", "stop"):
            await q.put(_STREAM_FINISH_SENTINEL)

    # ──── Stub methods для других acp.Client callbacks ────
    # Hermes может позвать их, но Phase C их не использует — возвращаем None/error.

    async def request_permission(self, params: Any) -> Any:
        """Permission request — Phase C: всегда deny (no tool execution)."""
        return None

    async def write_text_file(self, params: Any) -> None:
        return None

    async def read_text_file(self, params: Any) -> Any:
        return None

    async def create_terminal(self, params: Any) -> Any:
        return None

    async def kill_terminal(self, params: Any) -> None:
        return None

    async def terminal_output(self, params: Any) -> Any:
        return None

    async def release_terminal(self, params: Any) -> None:
        return None

    async def wait_for_terminal_exit(self, params: Any) -> Any:
        return None


class HermesACPBridge:
    """Subprocess wrapper над hermes acp. Лениво стартует Hermes process.

    Sessions кэшируются in-memory (chat_id/room -> session_id). На Krab restart
    ResponseDB Hermes сохраняет, но мы делаем new session при первом prompt.

    Wave 16-Q (Phase C): full ACP wiring — connect_to_agent + Client callback
    queue → stream() consumes chunks. Без binary всё равно gracefully degraded.
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
        # Wave 16-Q: connect_to_agent connection — управляет JSON-RPC через stdio
        self._connection: Any = None
        # Wave 16-Q: callback receiver для session_update notifications
        self._event_client: _HermesEventClient | None = None
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
        """Lazy spawn hermes acp subprocess + acp connect_to_agent handshake (Phase C).

        Returns True если subprocess запустился И acp protocol handshake
        прошёл. Без connection возвращаем False — fallback на openclaw в
        agent_engine_resolver.
        """
        async with self._lock:
            if (
                self._proc is not None
                and self._proc.returncode is None
                and self._connection is not None
            ):
                return True
            if not self._binary_available():
                return False
            try:
                # subprocess_exec — execFile-style (без shell), безопасно от injection
                self._proc = await asyncio.create_subprocess_exec(
                    self._binary,
                    "acp",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=clean_subprocess_env(),
                )
                # Wave 16-Q (Phase C): wire acp.connect_to_agent на stdio subprocess'a.
                # connect_to_agent под капотом кидает task для приёма session_update
                # notifications в self._event_client.
                from acp import (  # noqa: PLC0415
                    PROTOCOL_VERSION,
                    InitializeRequest,
                    connect_to_agent,
                )

                self._event_client = _HermesEventClient()
                self._connection = connect_to_agent(
                    self._event_client,
                    self._proc.stdin,
                    self._proc.stdout,
                )
                # ACP handshake: initialize() согласует версии protocol + capabilities
                init_req = InitializeRequest(
                    protocolVersion=PROTOCOL_VERSION,
                    clientCapabilities={},
                )
                await asyncio.wait_for(self._connection.initialize(init_req), timeout=10.0)
                logger.info(
                    "hermes_acp_started",
                    pid=self._proc.pid,
                    binary=self._binary,
                    protocol_version=PROTOCOL_VERSION,
                )
                return True
            except (FileNotFoundError, OSError) as exc:
                logger.warning(
                    "hermes_acp_start_failed",
                    binary=self._binary,
                    error=str(exc),
                )
                self._proc = None
                self._connection = None
                self._event_client = None
                return False
            except Exception as exc:  # noqa: BLE001
                # ACP handshake failure / import error — degraded, fallback на openclaw
                logger.warning(
                    "hermes_acp_handshake_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                if self._proc is not None:
                    with contextlib.suppress(ProcessLookupError, OSError):
                        self._proc.terminate()
                self._proc = None
                self._connection = None
                self._event_client = None
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
        """Stream Hermes response через ACP session_update notifications.

        Wave 16-Q (Phase C):
        1. health check → если unhealthy, finish chunk (engine_unavailable).
        2. new_session() через ACP, либо resume по logical_id из ctx.
        3. background prompt() task; параллельно consumer вытаскивает
           StreamChunk из event_client.queue до finish sentinel.
        """
        health = await self.health()
        if not health.is_healthy:
            yield StreamChunk(
                text=f"[Hermes unavailable: {health.error}]",
                chunk_type="finish",
                finish_reason="engine_unavailable",
            )
            return

        if self._connection is None or self._event_client is None:
            yield StreamChunk(
                text="[Hermes connection not ready]",
                chunk_type="finish",
                finish_reason="engine_unavailable",
            )
            return

        # Resolve session: ctx.logical_id → existing session_id или new
        logical_id = str((ctx or {}).get("logical_id") or "default")
        session_id = self._sessions.get(logical_id)
        try:
            if session_id is None:
                from acp import NewSessionRequest  # noqa: PLC0415

                # cwd + mcpServers — required по ACP protocol
                resp = await asyncio.wait_for(
                    self._connection.new_session(
                        NewSessionRequest(
                            cwd=str(Path.cwd()),
                            mcpServers=self._mcp_servers,
                        )
                    ),
                    timeout=10.0,
                )
                session_id = str(getattr(resp, "session_id", "") or getattr(resp, "sessionId", ""))
                if session_id:
                    self._sessions[logical_id] = session_id
                else:
                    yield StreamChunk(
                        text="[Hermes: empty session_id from new_session]",
                        chunk_type="finish",
                        finish_reason="protocol_error",
                    )
                    return
        except Exception as exc:  # noqa: BLE001
            logger.warning("hermes_new_session_failed", error=str(exc))
            yield StreamChunk(
                text=f"[Hermes new_session failed: {exc}]",
                chunk_type="finish",
                finish_reason="protocol_error",
            )
            return

        queue = self._event_client.get_queue(session_id)

        # Background prompt task — он шлёт промпт, а мы консумим chunks
        # из queue до finish sentinel или раннего exception.
        async def _do_prompt() -> None:
            from acp import PromptRequest  # noqa: PLC0415

            try:
                await self._connection.prompt(
                    PromptRequest(
                        sessionId=session_id,
                        prompt=[{"type": "text", "text": prompt}],
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("hermes_prompt_failed", session_id=session_id, error=str(exc))
            finally:
                # Гарантируем finish sentinel чтобы consumer не висел
                with contextlib.suppress(Exception):
                    await queue.put(_STREAM_FINISH_SENTINEL)

        prompt_task = asyncio.create_task(_do_prompt())
        try:
            while True:
                chunk = await queue.get()
                if chunk is _STREAM_FINISH_SENTINEL:
                    break
                yield chunk
        finally:
            with contextlib.suppress(asyncio.CancelledError):
                if not prompt_task.done():
                    prompt_task.cancel()
                    await asyncio.gather(prompt_task, return_exceptions=True)
            self._event_client.drop_queue(session_id)

        yield StreamChunk(
            text="",
            chunk_type="finish",
            finish_reason="end_of_turn",
        )

    async def cancel(self, session_id: str) -> bool:
        """Отменяет сессию через acp connection.cancel().

        session_id — ACP session_id (не logical_id chat'а). Возвращает True
        если cancel notification отправлено (без ack — fire-and-forget).
        """
        if self._connection is None or not session_id:
            return False
        try:
            from acp import CancelNotification  # noqa: PLC0415

            await self._connection.cancel(CancelNotification(sessionId=session_id))
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("hermes_cancel_failed", session_id=session_id, error=str(exc))
            return False

    async def close(self) -> None:
        """Graceful shutdown subprocess + acp connection."""
        async with self._lock:
            # 1. Закрываем acp connection (это закрывает stdio pipes для agent)
            if self._connection is not None:
                with contextlib.suppress(Exception):
                    await self._connection.close()
                self._connection = None
            # 2. Terminate subprocess если ещё жив
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
            self._event_client = None
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
