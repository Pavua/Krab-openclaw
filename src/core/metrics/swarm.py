# -*- coding: utf-8 -*-
"""
src/core/metrics/swarm.py
~~~~~~~~~~~~~~~~~~~~~~~~~

Wave 89: Prometheus метрики для swarm activity.

- Counter `krab_swarm_runs_total{team, status}` (status: started/done/failed)
- Histogram `krab_swarm_run_duration_seconds{team}`

Fail-safe: если prometheus_client отсутствует — объекты None, helpers no-op.
"""

from __future__ import annotations

from ..logger import get_logger

logger = get_logger(__name__)


try:
    from prometheus_client import Counter as _Counter  # type: ignore[import-not-found]
    from prometheus_client import Histogram as _Histogram  # type: ignore[import-not-found]

    krab_swarm_runs_total = _Counter(
        "krab_swarm_runs_total",
        "Количество swarm-запусков по команде и статусу",
        ["team", "status"],
    )
    krab_swarm_run_duration_seconds = _Histogram(
        "krab_swarm_run_duration_seconds",
        "Длительность одного swarm round (секунды) по команде",
        ["team"],
        # Buckets подобраны под типичную latency: 5s быстрый round, 60-300s
        # обычный multi-role с tool calls, 600s+ — выбросы.
        buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1200.0),
    )
except Exception:  # noqa: BLE001 - prometheus_client optional
    krab_swarm_runs_total = None  # type: ignore[assignment]
    krab_swarm_run_duration_seconds = None  # type: ignore[assignment]


def record_swarm_run_started(team: str) -> None:
    """Counter инкремент при старте swarm-запуска."""
    try:
        if krab_swarm_runs_total is not None:
            krab_swarm_runs_total.labels(team=(team or "unknown")[:64], status="started").inc()
    except Exception:  # noqa: BLE001
        pass


def record_swarm_run_finished(*, team: str, status: str, duration_seconds: float | None) -> None:
    """Counter + Histogram при завершении swarm-запуска.

    status ∈ {done, failed}. duration None → histogram не записывается.
    """
    try:
        t = (team or "unknown")[:64]
        s = (status or "done")[:32]
        if krab_swarm_runs_total is not None:
            krab_swarm_runs_total.labels(team=t, status=s).inc()
        if duration_seconds is not None and krab_swarm_run_duration_seconds is not None:
            krab_swarm_run_duration_seconds.labels(team=t).observe(
                max(0.0, float(duration_seconds))
            )
    except Exception:  # noqa: BLE001
        pass
