# -*- coding: utf-8 -*-
"""
src/core/metrics/idle_wake.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Wave 119: Prometheus метрики для idle/wake watcher (macOS long sleep detect).

- Counter `krab_idle_wake_events_total` — общее число обнаруженных wake-событий.
- Histogram `krab_idle_wake_gap_seconds` — фактический gap между tick'ами
  (буджеты от 60s до 8h позволяют ловить как короткие лаги, так и overnight).
- Gauge `krab_last_idle_wake_ts` — unix-ts последнего wake-события.

Fail-safe: prometheus_client опционален — при отсутствии helpers no-op.
"""

from __future__ import annotations

from ..logger import get_logger

logger = get_logger(__name__)


try:
    from prometheus_client import Counter as _Counter  # type: ignore[import-not-found]
    from prometheus_client import Gauge as _Gauge  # type: ignore[import-not-found]
    from prometheus_client import Histogram as _Histogram  # type: ignore[import-not-found]

    krab_idle_wake_events_total = _Counter(
        "krab_idle_wake_events_total",
        "Кол-во обнаруженных idle/wake событий (event loop suspended > threshold)",
    )
    krab_idle_wake_gap_seconds = _Histogram(
        "krab_idle_wake_gap_seconds",
        "Длительность gap (sec) между ожидаемым tick'ом и фактическим",
        # Buckets подобраны под типичные macOS sleep сценарии:
        # 60s = lag, 5m/10m = short nap, 30m/1h = lunch, 2h/4h/8h = overnight.
        buckets=(60.0, 300.0, 600.0, 1800.0, 3600.0, 7200.0, 14400.0, 28800.0),
    )
    krab_last_idle_wake_ts = _Gauge(
        "krab_last_idle_wake_ts",
        "Unix timestamp последнего обнаруженного wake-события",
    )
except Exception:  # noqa: BLE001 - prometheus_client optional
    krab_idle_wake_events_total = None  # type: ignore[assignment]
    krab_idle_wake_gap_seconds = None  # type: ignore[assignment]
    krab_last_idle_wake_ts = None  # type: ignore[assignment]


def record_idle_wake(gap_seconds: float, ts: float) -> None:
    """Инкремент counter + observation histogram + set gauge."""
    try:
        if krab_idle_wake_events_total is not None:
            krab_idle_wake_events_total.inc()
        if krab_idle_wake_gap_seconds is not None:
            krab_idle_wake_gap_seconds.observe(max(0.0, float(gap_seconds)))
        if krab_last_idle_wake_ts is not None:
            krab_last_idle_wake_ts.set(float(ts))
    except Exception:  # noqa: BLE001
        pass
