# -*- coding: utf-8 -*-
"""Wave 91: Browser CDP session pool metrics.

Считает recycle событий BrowserSessionPool с причиной (age/errors/health_fail) и
текущий размер активного пула. Fail-safe: prometheus_client может быть не установлен.
"""

from __future__ import annotations

try:
    from prometheus_client import Counter as _Counter  # type: ignore[import-not-found]
    from prometheus_client import Gauge as _Gauge  # type: ignore[import-not-found]

    krab_browser_session_recycled_total = _Counter(
        "krab_browser_session_recycled_total",
        "Wave 91: количество recycle событий browser pool (по причинам)",
        ["reason"],
    )
    krab_browser_pool_active = _Gauge(
        "krab_browser_pool_active",
        "Wave 91: текущее количество активных browser сессий в пуле",
    )
except Exception:  # noqa: BLE001
    krab_browser_session_recycled_total = None  # type: ignore[assignment]
    krab_browser_pool_active = None  # type: ignore[assignment]


# Допустимые причины recycle — используется тестами и pool-классом
RECYCLE_REASONS: tuple[str, ...] = ("age", "errors", "health_fail", "manual")


def inc_browser_session_recycled(reason: str) -> None:
    """Инкрементит счётчик recycle. Неизвестный reason → нормализуется к 'manual'."""
    counter = krab_browser_session_recycled_total
    if counter is None:
        return
    normalized = reason if reason in RECYCLE_REASONS else "manual"
    try:
        counter.labels(reason=normalized).inc()
    except Exception:  # noqa: BLE001
        pass


def set_browser_pool_active(value: int) -> None:
    """Обновляет gauge активного пула. Fail-safe."""
    gauge = krab_browser_pool_active
    if gauge is None:
        return
    try:
        gauge.set(max(0, int(value)))
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "RECYCLE_REASONS",
    "inc_browser_session_recycled",
    "krab_browser_pool_active",
    "krab_browser_session_recycled_total",
    "set_browser_pool_active",
]
