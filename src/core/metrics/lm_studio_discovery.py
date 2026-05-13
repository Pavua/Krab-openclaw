# -*- coding: utf-8 -*-
"""Wave 239: метрики LM Studio models discovery.

`krab_lm_studio_discovery_total{result}` — counter попыток вытащить
список моделей из LM Studio.

Result labels:
- "success"   — HTTP 200, payload распарсен, кэш обновлён
- "cache_hit" — отдан кэш (TTL не истёк), сетевого вызова не было
- "error"     — timeout / refused / 401 / parse fail

Best-effort: prometheus_client опционален, fallback in-memory dict.
"""

from __future__ import annotations

try:
    from prometheus_client import Counter as _Counter  # type: ignore[import-not-found]

    _lm_studio_discovery_total = _Counter(
        "krab_lm_studio_discovery_total",
        "Wave 239: LM Studio /v1/models discovery outcomes",
        ["result"],
    )
except Exception:  # noqa: BLE001 - prometheus_client опционален
    _lm_studio_discovery_total = None  # type: ignore[assignment]

# Сырой in-memory счётчик (fallback + удобно проверять в unit-тестах).
_LM_STUDIO_DISCOVERY_COUNTER: dict[str, int] = {}


def inc_lm_studio_discovery(*, result: str) -> None:
    """Wave 239: фиксирует исход discovery-вызова. Best-effort."""
    try:
        key = str(result)
        _LM_STUDIO_DISCOVERY_COUNTER[key] = _LM_STUDIO_DISCOVERY_COUNTER.get(key, 0) + 1
        if _lm_studio_discovery_total is not None:
            _lm_studio_discovery_total.labels(result=key).inc()
    except Exception:  # noqa: BLE001 - инструментация best-effort
        pass


__all__ = [
    "_LM_STUDIO_DISCOVERY_COUNTER",
    "_lm_studio_discovery_total",
    "inc_lm_studio_discovery",
]
