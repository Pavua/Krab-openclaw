# -*- coding: utf-8 -*-
"""
Wave 113: Telegram MCP session connection pool.

Цель — переиспользовать одну Pyrogram session между несколькими MCP tool-
вызовами и автоматически перезапускать её после периода простоя, чтобы:
  * избавиться от cold-start на каждый запрос (login/handshake ~1.5–3 s);
  * освобождать SQLite session lock когда сервер реально idle;
  * иметь наблюдаемость (Prometheus counters) сколько раз session reused,
    recycled, и сколько активных сейчас (0 или 1 на pool).

Реализация — тонкий instrumentation-слой поверх существующего
``TelegramBridge`` из ``telegram_bridge.py``. Bridge уже singleton и держит
одного Pyrogram-клиента, поэтому pool здесь не заменяет его, а добавляет:

  1. ``acquire()`` — возвращает живого клиента; если идл-таймаут истёк,
     перезапускает session и инкрементит ``recycled_total``.
  2. ``release()`` — отмечает момент возврата (для idle-таймера).
  3. ``stats()`` — read-only snapshot для Prometheus exporter.

Pool НЕ управляет concurrency между tool-вызовами — это по-прежнему делает
``TelegramBridge._operation_lock``. Pyrogram session.db sqlite не выдерживает
параллельных записей с одного процесса, поэтому сериализация остаётся.

Metrics (имена согласованы с brief):
  * ``krab_mcp_telegram_session_active``           — Gauge 0/1
  * ``krab_mcp_telegram_session_reused_total``     — Counter
  * ``krab_mcp_telegram_session_recycled_total``   — Counter
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

# Idle window: после этого времени без acquire — session будет recycled при
# следующем обращении. Дефолт 5 минут согласно brief.
DEFAULT_IDLE_TIMEOUT_SEC = 300.0


class _BridgeLike(Protocol):
    """Минимальный контракт bridge для pool — позволяет подменить в тестах."""

    async def start(self) -> None: ...
    async def stop(self) -> None: ...


@dataclass
class SessionStats:
    """Read-only снимок состояния пула."""

    active: int = 0  # 0 / 1 — pool держит максимум одну session
    reused_total: int = 0
    recycled_total: int = 0
    last_acquired_at: float | None = None
    last_released_at: float | None = None

    def as_prometheus(self) -> dict[str, float]:
        """Формат под /metrics endpoint."""
        return {
            "krab_mcp_telegram_session_active": float(self.active),
            "krab_mcp_telegram_session_reused_total": float(self.reused_total),
            "krab_mcp_telegram_session_recycled_total": float(self.recycled_total),
        }


@dataclass
class TelegramSessionPool:
    """Idle-aware re-use pool поверх ``TelegramBridge``-singleton.

    Параметры:
      bridge:       TelegramBridge-совместимый объект (start/stop async).
      idle_timeout: сколько секунд без acquire — после чего рестарт.
      now_fn:       инжектится в тестах для контроля времени.
    """

    bridge: _BridgeLike
    idle_timeout: float = DEFAULT_IDLE_TIMEOUT_SEC
    now_fn: Callable[[], float] = field(default=time.monotonic)

    _started: bool = field(default=False, init=False)
    _last_release_at: float | None = field(default=None, init=False)
    _stats: SessionStats = field(default_factory=SessionStats, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    async def acquire(self) -> _BridgeLike:
        """Возвращает живой bridge; перезапускает после idle-таймаута.

        Поведение:
          * первый вызов — ``bridge.start()``, ``reused_total`` не растёт.
          * следующий вызов в пределах idle window — reuse, ++reused_total.
          * вызов после idle window — stop+start, ++recycled_total.
        """
        async with self._lock:
            now = self.now_fn()
            if not self._started:
                await self.bridge.start()
                self._started = True
                self._stats.active = 1
            elif (
                self._last_release_at is not None
                and (now - self._last_release_at) >= self.idle_timeout
            ):
                # Idle expire → recycle session.
                try:
                    await self.bridge.stop()
                finally:
                    self._stats.active = 0
                await self.bridge.start()
                self._started = True
                self._stats.active = 1
                self._stats.recycled_total += 1
            else:
                # Reuse существующей session.
                self._stats.reused_total += 1

            self._stats.last_acquired_at = now
            return self.bridge

    async def release(self) -> None:
        """Маркирует конец операции — стартует idle-таймер."""
        async with self._lock:
            self._last_release_at = self.now_fn()
            self._stats.last_released_at = self._last_release_at

    async def shutdown(self) -> None:
        """Финальная остановка (вызывается из FastMCP lifespan teardown)."""
        async with self._lock:
            if self._started:
                try:
                    await self.bridge.stop()
                finally:
                    self._started = False
                    self._stats.active = 0

    def stats(self) -> SessionStats:
        """Снимок текущей статистики — copy-safe (dataclass без mutable полей)."""
        return SessionStats(
            active=self._stats.active,
            reused_total=self._stats.reused_total,
            recycled_total=self._stats.recycled_total,
            last_acquired_at=self._stats.last_acquired_at,
            last_released_at=self._stats.last_released_at,
        )

    def prometheus_lines(self) -> list[str]:
        """Готовые строки для экспонирования через ``/metrics`` endpoint."""
        snap = self.stats()
        lines: list[str] = []
        for name, value in snap.as_prometheus().items():
            lines.append(f"{name} {value}")
        return lines


__all__ = [
    "DEFAULT_IDLE_TIMEOUT_SEC",
    "SessionStats",
    "TelegramSessionPool",
]
