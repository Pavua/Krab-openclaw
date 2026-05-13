# -*- coding: utf-8 -*-
"""Wave 230: метрика переключений primary модели через `/admin/models`.

`krab_active_model_switches_total{from_model,to_model}` — каждый успешный
``POST /api/admin/model/switch`` инкрементирует counter. Pattern идентичен
Wave 70 / Wave 225: prometheus_client опционален, fallback — in-memory dict.
"""

from __future__ import annotations

try:
    from prometheus_client import (
        Counter as _CounterActiveModelSwitches,  # type: ignore[import-not-found]
    )
    from prometheus_client import (
        Histogram as _HistogramActiveModelResolve,  # type: ignore[import-not-found]
    )

    _active_model_switches_total = _CounterActiveModelSwitches(
        "krab_active_model_switches_total",
        "Wave 230: primary model switches via /api/admin/model/switch",
        ["from_model", "to_model"],
    )
    _active_model_resolve_duration = _HistogramActiveModelResolve(
        "krab_active_model_resolve_duration_seconds",
        (
            "Wave 235: latency reading active_model.json (cache miss path). "
            "Спайки > 0.1s = индикатор перегруженного default executor или "
            "медленного диска → потенциальный :8080 freeze."
        ),
        ["source"],
        # Buckets: 100us = cache hit OS page-cache, 1ms = warm SSD,
        # 10ms = cold/iCloud, 100ms = degraded, 1s = критический freeze.
        buckets=(0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
    )
except Exception:  # noqa: BLE001 - prometheus_client опционален
    _active_model_switches_total = None  # type: ignore[assignment]
    _active_model_resolve_duration = None  # type: ignore[assignment]

# In-memory счётчик (fallback + удобный pull для тестов).
_ACTIVE_MODEL_SWITCH_COUNTER: dict[tuple[str, str], int] = {}

# In-memory pull последних observation'ов (fallback + тесты).
_ACTIVE_MODEL_RESOLVE_OBSERVATIONS: list[tuple[str, float]] = []


def inc_active_model_switch(*, from_model: str, to_model: str) -> None:
    """Фиксирует переключение. Best-effort, не бросает."""
    try:
        key = (str(from_model or "-"), str(to_model or "-"))
        _ACTIVE_MODEL_SWITCH_COUNTER[key] = _ACTIVE_MODEL_SWITCH_COUNTER.get(key, 0) + 1
        if _active_model_switches_total is not None:
            _active_model_switches_total.labels(
                from_model=key[0],
                to_model=key[1],
            ).inc()
    except Exception:  # noqa: BLE001 - инструментация best-effort
        pass


def observe_resolve_duration(seconds: float, *, source: str = "file") -> None:
    """Wave 235: фиксирует длительность чтения active_model.json.

    ``source`` ∈ {"file", "async"} — sync read vs async (через asyncio.to_thread).
    Best-effort, не бросает.
    """
    try:
        v = max(0.0, float(seconds))
        src = (str(source or "file") or "file")[:16]
        _ACTIVE_MODEL_RESOLVE_OBSERVATIONS.append((src, v))
        # Cap in-memory buffer чтобы не съесть RAM в long-running процессе.
        if len(_ACTIVE_MODEL_RESOLVE_OBSERVATIONS) > 1024:
            del _ACTIVE_MODEL_RESOLVE_OBSERVATIONS[:512]
        if _active_model_resolve_duration is not None:
            _active_model_resolve_duration.labels(source=src).observe(v)
    except Exception:  # noqa: BLE001 - метрика best-effort
        pass


__all__ = [
    "_ACTIVE_MODEL_RESOLVE_OBSERVATIONS",
    "_ACTIVE_MODEL_SWITCH_COUNTER",
    "_active_model_resolve_duration",
    "_active_model_switches_total",
    "inc_active_model_switch",
    "observe_resolve_duration",
]
