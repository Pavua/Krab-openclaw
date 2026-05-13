# -*- coding: utf-8 -*-
"""Wave 223: метрики routing'а long-context задач на локальный MLX.

krab_mlx_local_routing_total{reason} — счётчик решений роутера направить
запрос в локальный MLX-провайдер (:8088) вместо облака.

Reason labels:
- "long_context" — promt_tokens > threshold
- "task_type"    — task_type в списке KRAB_MLX_LOCAL_TASK_TYPES
- "fallback"     — env-gate OFF / нет совпадений (остался cloud)

Best-effort: prometheus_client опциональный, in-memory dict для text render.
"""

from __future__ import annotations

try:
    from prometheus_client import Counter as _CounterLC  # type: ignore[import-not-found]

    _mlx_local_routing_total = _CounterLC(
        "krab_mlx_local_routing_total",
        "Wave 223: long-context routing decisions to local MLX provider",
        ["reason"],
    )
except Exception:  # noqa: BLE001 - prometheus_client optional
    _mlx_local_routing_total = None  # type: ignore[assignment]

# Сырой in-memory счётчик (текст-render + fallback при отсутствии prom_client).
_MLX_LOCAL_ROUTING_COUNTER: dict[str, int] = {}


def inc_mlx_local_routing(*, reason: str) -> None:
    """Wave 223: фиксирует решение роутера. Best-effort, не бросает."""
    try:
        key = str(reason)
        _MLX_LOCAL_ROUTING_COUNTER[key] = _MLX_LOCAL_ROUTING_COUNTER.get(key, 0) + 1
        if _mlx_local_routing_total is not None:
            _mlx_local_routing_total.labels(reason=key).inc()
    except Exception:  # noqa: BLE001 - инструментация best-effort
        pass


__all__ = [
    "_MLX_LOCAL_ROUTING_COUNTER",
    "_mlx_local_routing_total",
    "inc_mlx_local_routing",
]
