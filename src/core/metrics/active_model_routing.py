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

    _active_model_switches_total = _CounterActiveModelSwitches(
        "krab_active_model_switches_total",
        "Wave 230: primary model switches via /api/admin/model/switch",
        ["from_model", "to_model"],
    )
except Exception:  # noqa: BLE001 - prometheus_client опционален
    _active_model_switches_total = None  # type: ignore[assignment]

# In-memory счётчик (fallback + удобный pull для тестов).
_ACTIVE_MODEL_SWITCH_COUNTER: dict[tuple[str, str], int] = {}


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


__all__ = [
    "_ACTIVE_MODEL_SWITCH_COUNTER",
    "_active_model_switches_total",
    "inc_active_model_switch",
]
