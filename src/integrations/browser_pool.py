# -*- coding: utf-8 -*-
"""Wave 91: BrowserSessionPool — auto-recycle stale CDP подключений.

Долгие сессии Chrome иногда зависают: CDP connection становится stale, требует
manual reconnect. Pool:
  * хранит несколько BrowserBridge-подобных сессий;
  * на каждый borrow делает health check `execute_js("1+1")` и пересоздаёт сессию
    при провале;
  * recycles сессию при возрасте > MAX_AGE_SEC или error_count > MAX_ERRORS;
  * фоновый health_audit() (каждые 60s) очищает stale сессии.

Pool — wrapper над существующим src.integrations.browser_bridge.BrowserBridge,
который мы не трогаем (singleton оставляем для уже подключённых cron-задач).
Активируется через KRAB_BROWSER_POOL_ENABLED=1.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import structlog

from src.core.metrics.browser import (
    inc_browser_session_recycled,
    set_browser_pool_active,
)

logger = structlog.get_logger(__name__)


def _env_int(name: str, default: int) -> int:
    """Безопасно читает env-int. Невалидное значение → default."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    """Читает bool-флаг из env (1/true/yes/on)."""
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Конфигурация (читается каждый раз через property — позволяет тестам подменять
# env-переменную после import без reload модуля).
# ---------------------------------------------------------------------------
def pool_enabled() -> bool:
    return _env_flag("KRAB_BROWSER_POOL_ENABLED", default=False)


def pool_max_age_sec() -> int:
    return _env_int("KRAB_BROWSER_POOL_MAX_AGE_SEC", 1800)


def pool_max_errors() -> int:
    return _env_int("KRAB_BROWSER_POOL_MAX_ERRORS", 3)


def pool_max_size() -> int:
    return _env_int("KRAB_BROWSER_POOL_MAX_SIZE", 3)


def pool_audit_interval_sec() -> int:
    return _env_int("KRAB_BROWSER_POOL_AUDIT_INTERVAL_SEC", 60)


SessionFactory = Callable[[], Awaitable[Any]]


@dataclass
class PooledSession:
    """Обёртка над BrowserBridge-подобным объектом с трекингом метаданных."""

    session: Any
    created_ts: float
    last_used_ts: float
    error_count: int = 0
    in_use: bool = False
    # Маркер для тестов / закрытых сессий — больше не возвращается из borrow.
    closed: bool = False
    id: int = field(default_factory=lambda: int(time.time_ns()))

    def age_sec(self, now: float | None = None) -> float:
        return (now if now is not None else time.time()) - self.created_ts


class BrowserSessionPool:
    """Pool браузерных CDP сессий с auto-recycle.

    Использование:
        pool = BrowserSessionPool(factory=lambda: make_browser_bridge())
        async with pool.borrow() as session:
            await session.execute_js("...")
    """

    def __init__(
        self,
        factory: SessionFactory,
        *,
        max_age_sec: int | None = None,
        max_errors: int | None = None,
        max_size: int | None = None,
        health_check: Callable[[Any], Awaitable[bool]] | None = None,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        self._factory = factory
        self._max_age_sec = max_age_sec if max_age_sec is not None else pool_max_age_sec()
        self._max_errors = max_errors if max_errors is not None else pool_max_errors()
        self._max_size = max_size if max_size is not None else pool_max_size()
        self._health_check = health_check or self._default_health_check
        self._now = now_fn or time.time
        self._sessions: list[PooledSession] = []
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Default health probe — `1+1` через execute_js (или ping-метод сессии)
    # ------------------------------------------------------------------
    @staticmethod
    async def _default_health_check(session: Any) -> bool:
        """Базовый health check: пробуем execute_js("1+1") или ping()."""
        try:
            exec_js = getattr(session, "execute_js", None)
            if callable(exec_js):
                result = await exec_js("1+1")
                return result == 2 or str(result).strip() == "2"
            ping = getattr(session, "ping", None)
            if callable(ping):
                return bool(await ping())
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "browser_pool_health_check_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return False
        return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def _close_session(self, pooled: PooledSession, reason: str) -> None:
        """Закрывает одну сессию и инкрементит метрику recycle."""
        if pooled.closed:
            return
        pooled.closed = True
        closer = getattr(pooled.session, "close", None)
        if callable(closer):
            try:
                result = closer()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "browser_pool_session_close_failed",
                    reason=reason,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
        inc_browser_session_recycled(reason)
        logger.info(
            "browser_pool_session_recycled",
            reason=reason,
            session_id=pooled.id,
            age_sec=round(pooled.age_sec(self._now()), 1),
            error_count=pooled.error_count,
        )

    def _should_recycle(self, pooled: PooledSession) -> str | None:
        """Возвращает причину recycle или None если сессия здоровая."""
        if pooled.closed:
            return "manual"
        if pooled.age_sec(self._now()) > self._max_age_sec:
            return "age"
        if pooled.error_count > self._max_errors:
            return "errors"
        return None

    async def _create_session(self) -> PooledSession:
        """Создаёт новую сессию через factory и регистрирует её в пуле."""
        session = await self._factory()
        now = self._now()
        pooled = PooledSession(session=session, created_ts=now, last_used_ts=now)
        self._sessions.append(pooled)
        set_browser_pool_active(self._active_count())
        return pooled

    def _active_count(self) -> int:
        return sum(1 for s in self._sessions if not s.closed)

    # ------------------------------------------------------------------
    # Borrow / return
    # ------------------------------------------------------------------
    async def acquire(self) -> PooledSession:
        """Возвращает здоровую сессию (после health check). Создаёт новую при необходимости."""
        async with self._lock:
            # Сначала чистим closed/просроченные.
            for pooled in list(self._sessions):
                reason = self._should_recycle(pooled)
                if reason and not pooled.in_use:
                    await self._close_session(pooled, reason)

            # Реальное состояние пула после очистки.
            self._sessions = [s for s in self._sessions if not s.closed]

            # Ищем свободную живую сессию.
            for pooled in self._sessions:
                if pooled.in_use:
                    continue
                # Health probe.
                ok = await self._health_check(pooled.session)
                if not ok:
                    await self._close_session(pooled, "health_fail")
                    continue
                pooled.in_use = True
                pooled.last_used_ts = self._now()
                self._sessions = [s for s in self._sessions if not s.closed]
                set_browser_pool_active(self._active_count())
                return pooled

            # Чистка closed после health_fail.
            self._sessions = [s for s in self._sessions if not s.closed]

            # Лимит — если все заняты, ждать; иначе создаём новую.
            if self._active_count() >= self._max_size:
                raise RuntimeError(
                    f"browser_pool_exhausted: max_size={self._max_size} все сессии заняты"
                )

            pooled = await self._create_session()
            pooled.in_use = True
            pooled.last_used_ts = self._now()
            return pooled

    async def release(self, pooled: PooledSession, *, errored: bool = False) -> None:
        """Возвращает сессию в пул. errored=True увеличивает error_count."""
        async with self._lock:
            pooled.in_use = False
            pooled.last_used_ts = self._now()
            if errored:
                pooled.error_count += 1
            reason = self._should_recycle(pooled)
            if reason:
                await self._close_session(pooled, reason)
                self._sessions = [s for s in self._sessions if not s.closed]
                set_browser_pool_active(self._active_count())

    # ------------------------------------------------------------------
    # Public API — context manager
    # ------------------------------------------------------------------
    def borrow(self) -> "_PoolBorrow":
        """Возвращает async context manager: `async with pool.borrow() as sess: ...`."""
        return _PoolBorrow(self)

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------
    async def health_audit(self) -> dict[str, int]:
        """Проходит по всем свободным сессиям, дропает stale. Возвращает счётчики."""
        dropped: dict[str, int] = {"age": 0, "errors": 0, "health_fail": 0}
        async with self._lock:
            for pooled in list(self._sessions):
                if pooled.in_use or pooled.closed:
                    continue
                reason = self._should_recycle(pooled)
                if reason in ("age", "errors"):
                    await self._close_session(pooled, reason)
                    dropped[reason] = dropped.get(reason, 0) + 1
                    continue
                ok = await self._health_check(pooled.session)
                if not ok:
                    await self._close_session(pooled, "health_fail")
                    dropped["health_fail"] = dropped.get("health_fail", 0) + 1
            self._sessions = [s for s in self._sessions if not s.closed]
            set_browser_pool_active(self._active_count())
        return dropped

    async def close_all(self) -> None:
        """Закрывает все сессии (graceful shutdown)."""
        async with self._lock:
            for pooled in list(self._sessions):
                await self._close_session(pooled, "manual")
            self._sessions = []
            set_browser_pool_active(0)

    @property
    def active_count(self) -> int:
        return self._active_count()


class _PoolBorrow:
    """Async context manager обёртка для borrow/release.

    Tracks ошибки внутри блока — при любом исключении пометит сессию как
    errored, чтобы error_count рос и срабатывал recycle.
    """

    def __init__(self, pool: BrowserSessionPool) -> None:
        self._pool = pool
        self._pooled: PooledSession | None = None

    async def __aenter__(self) -> Any:
        self._pooled = await self._pool.acquire()
        return self._pooled.session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._pooled is None:
            return
        await self._pool.release(self._pooled, errored=exc_type is not None)
        self._pooled = None


# ---------------------------------------------------------------------------
# Background task для регистрации в KraabUserbot.start().
# ---------------------------------------------------------------------------
async def run_pool_audit_loop(pool: BrowserSessionPool, interval_sec: int | None = None) -> None:
    """Фоновый цикл health_audit. Стартует в KraabUserbot через asyncio.create_task."""
    if interval_sec is None:
        interval_sec = pool_audit_interval_sec()
    interval_sec = max(5, int(interval_sec))
    logger.info("browser_pool_audit_loop_started", interval_sec=interval_sec)
    while True:
        try:
            dropped = await pool.health_audit()
            if any(dropped.values()):
                logger.info("browser_pool_audit_dropped", **dropped)
        except asyncio.CancelledError:
            logger.info("browser_pool_audit_loop_cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "browser_pool_audit_loop_error",
                error=str(exc),
                error_type=type(exc).__name__,
            )
        await asyncio.sleep(interval_sec)


__all__ = [
    "BrowserSessionPool",
    "PooledSession",
    "pool_audit_interval_sec",
    "pool_enabled",
    "pool_max_age_sec",
    "pool_max_errors",
    "pool_max_size",
    "run_pool_audit_loop",
]
