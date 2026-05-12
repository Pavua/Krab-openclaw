# -*- coding: utf-8 -*-
"""Wave 121: Telegram FloodWait observability (Histogram + active Gauge).

Histogram krab_telegram_flood_wait_duration_seconds{caller} — buckets
1, 5, 10, 30, 60, 300, 600, 1800.
Gauge krab_telegram_rate_limited_active{caller} — 1 пока deadline в будущем.

Wired в src/core/error_handler.py::safe_handler и src/reserve_bot.py::_record_flood_wait.

State хранит deadlines в _RATE_LIMIT_DEADLINES{caller: deadline_ts}; на каждом
collect_metrics call вызывается refresh_telegram_rate_limited_active() для
очистки истёкших.
"""

from __future__ import annotations

import time
from threading import Lock

# Legacy counter — для backwards compat с inc_telegram_flood_wait.
try:
    from . import process as _process_metrics  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001
    _process_metrics = None  # type: ignore[assignment]

try:
    from prometheus_client import Gauge as _GaugeRL  # type: ignore[import-not-found]
    from prometheus_client import Histogram as _HistogramRL  # type: ignore[import-not-found]

    _flood_wait_duration_seconds = _HistogramRL(
        "krab_telegram_flood_wait_duration_seconds",
        "Wave 121: FloodWait продолжительность в секундах per caller",
        ["caller"],
        buckets=(1, 5, 10, 30, 60, 300, 600, 1800),
    )
    _rate_limited_active = _GaugeRL(
        "krab_telegram_rate_limited_active",
        "Wave 121: 1 пока FloodWait deadline в будущем",
        ["caller"],
    )
except Exception:  # noqa: BLE001 - prometheus_client optional
    _flood_wait_duration_seconds = None  # type: ignore[assignment]
    _rate_limited_active = None  # type: ignore[assignment]


_RATE_LIMIT_DEADLINES: dict[str, float] = {}
_RATE_LIMIT_LOCK = Lock()


def observe_telegram_flood_wait(caller: str, wait_seconds: float) -> None:
    """Wave 121: фиксирует FloodWait duration + активирует Gauge до истечения.

    Best-effort: try/except, не бросает. Legacy `inc_telegram_flood_wait`
    также вызывается для backwards compat.
    """
    try:
        clean_caller = (str(caller) or "unknown")[:80]
        clean_wait = max(0.0, float(wait_seconds))
        if _flood_wait_duration_seconds is not None:
            _flood_wait_duration_seconds.labels(caller=clean_caller).observe(clean_wait)
        deadline = time.time() + clean_wait
        with _RATE_LIMIT_LOCK:
            _RATE_LIMIT_DEADLINES[clean_caller] = deadline
        if _rate_limited_active is not None:
            _rate_limited_active.labels(caller=clean_caller).set(1)
        if _process_metrics is not None:
            try:
                _process_metrics.inc_telegram_flood_wait(clean_caller)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass


def refresh_telegram_rate_limited_active(now: float | None = None) -> dict[str, int]:
    """Wave 121: обнуляет Gauge для callers с истёкшим deadline.

    Вызывается перед каждым text-mode render /metrics.
    Возвращает snapshot {caller: 1|0} — 1 = active, 0 = expired (для тестов и
    rendering). Best-effort, не бросает.
    """
    snapshot: dict[str, int] = {}
    try:
        current = float(now) if now is not None else time.time()
        with _RATE_LIMIT_LOCK:
            expired = [k for k, dl in _RATE_LIMIT_DEADLINES.items() if dl <= current]
            for k in expired:
                _RATE_LIMIT_DEADLINES.pop(k, None)
                snapshot[k] = 0
                if _rate_limited_active is not None:
                    try:
                        _rate_limited_active.labels(caller=k).set(0)
                    except Exception:  # noqa: BLE001
                        pass
            for k in _RATE_LIMIT_DEADLINES:
                snapshot[k] = 1
    except Exception:  # noqa: BLE001
        pass
    return snapshot


__all__ = [
    "_flood_wait_duration_seconds",
    "_rate_limited_active",
    "observe_telegram_flood_wait",
    "refresh_telegram_rate_limited_active",
]
