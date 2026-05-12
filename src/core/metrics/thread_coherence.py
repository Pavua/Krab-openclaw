# -*- coding: utf-8 -*-
"""Feature K: Thread coherence metrics (observability-only).

Histogram score (-1..1) + counter drift events.
"""

from __future__ import annotations

try:
    from prometheus_client import Counter as _Counter2  # type: ignore[import-not-found]
    from prometheus_client import Histogram as _Histogram2  # type: ignore[import-not-found]

    _thread_coherence_score = _Histogram2(
        "krab_thread_coherence_score",
        "Thread coherence score (-1..1) — semantic similarity текущего сообщения к предыдущим",
        buckets=(-1.0, -0.5, -0.2, 0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
    )
    _thread_coherence_drift_total = _Counter2(
        "krab_thread_coherence_drift_total",
        "Количество детектированных drift'ов в thread coherence",
        ["explicit"],
    )
except Exception:  # noqa: BLE001
    _thread_coherence_score = None  # type: ignore[assignment]
    _thread_coherence_drift_total = None  # type: ignore[assignment]


def _facade():
    """Lazy import фасада."""
    import src.core.prometheus_metrics as _pm  # noqa: PLC0415

    return _pm


def observe_thread_coherence(score: float | None, *, drift: bool, explicit: bool) -> None:
    """Записывает thread coherence в Prometheus. Fail-safe, no-op без prom_client."""
    try:
        pm = _facade()
        if pm._thread_coherence_score is not None and score is not None:
            pm._thread_coherence_score.observe(float(score))
        if drift and pm._thread_coherence_drift_total is not None:
            pm._thread_coherence_drift_total.labels(explicit=str(bool(explicit)).lower()).inc()
    except Exception:  # noqa: BLE001
        pass
