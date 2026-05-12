# -*- coding: utf-8 -*-
"""Wave 86: pressure-aware model selection metrics.

krab_free_memory_gb — on-scrape Gauge (rendered в collect.py).
krab_pressure_aware_fallback_total{from_model, to_model, reason} — counter.

Best-effort: prometheus_client опциональный, in-memory dict для text render.
"""

from __future__ import annotations

try:
    from prometheus_client import Counter as _CounterPA  # type: ignore[import-not-found]

    _pressure_aware_fallback_total = _CounterPA(
        "krab_pressure_aware_fallback_total",
        "Wave 86: model selection fallbacks driven by memory pressure",
        ["from_model", "to_model", "reason"],
    )
except Exception:  # noqa: BLE001 - prometheus_client optional
    _pressure_aware_fallback_total = None  # type: ignore[assignment]

# Сырой in-memory счётчик (text render + fallback если prom_client отсутствует).
_PRESSURE_AWARE_FALLBACK_COUNTER: dict[tuple[str, str, str], int] = {}


def inc_pressure_aware_fallback(*, from_model: str, to_model: str, reason: str) -> None:
    """Wave 86: фиксирует pressure-driven fallback. Best-effort, не бросает."""
    try:
        key = (str(from_model), str(to_model), str(reason))
        _PRESSURE_AWARE_FALLBACK_COUNTER[key] = _PRESSURE_AWARE_FALLBACK_COUNTER.get(key, 0) + 1
        if _pressure_aware_fallback_total is not None:
            _pressure_aware_fallback_total.labels(
                from_model=str(from_model),
                to_model=str(to_model),
                reason=str(reason),
            ).inc()
    except Exception:  # noqa: BLE001 - инструментация best-effort
        pass


__all__ = [
    "_pressure_aware_fallback_total",
    "_PRESSURE_AWARE_FALLBACK_COUNTER",
    "inc_pressure_aware_fallback",
]
