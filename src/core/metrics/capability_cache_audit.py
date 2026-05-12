# -*- coding: utf-8 -*-
"""Wave 129: capability cache mismatch counter.

Counter krab_capability_cache_mismatches_total{source} — инкрементируется
при detection mismatch между cached permissions и actual Telegram state.
"""

from __future__ import annotations

try:
    from prometheus_client import Counter as _CounterCC  # type: ignore[import-not-found]

    _capability_cache_mismatch_total = _CounterCC(
        "krab_capability_cache_mismatches_total",
        "Wave 129: mismatches detected by chat capability cache audit",
        ["source"],
    )
except Exception:  # noqa: BLE001
    _capability_cache_mismatch_total = None  # type: ignore[assignment]


_CAPABILITY_CACHE_MISMATCH_COUNTER: dict[str, int] = {}


def inc_capability_cache_mismatch(source: str = "audit") -> None:
    """Wave 129: фиксирует mismatch. Best-effort, не бросает."""
    try:
        key = (str(source) or "unknown")[:40]
        _CAPABILITY_CACHE_MISMATCH_COUNTER[key] = _CAPABILITY_CACHE_MISMATCH_COUNTER.get(key, 0) + 1
        if _capability_cache_mismatch_total is not None:
            _capability_cache_mismatch_total.labels(source=key).inc()
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "_CAPABILITY_CACHE_MISMATCH_COUNTER",
    "_capability_cache_mismatch_total",
    "inc_capability_cache_mismatch",
]
