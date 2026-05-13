# -*- coding: utf-8 -*-
"""Wave 225: метрики применения alias-резолвера локального MLX backend.

`krab_mlx_local_alias_resolved_total{result}` — счётчик попыток подменить
короткий идентификатор модели (`mlx-local-kv4/*`) на полный путь, который
ожидает `mlx_lm.server`.

Result labels:
- "hit"         — short_id найден в alias-map, payload переписан
- "miss"        — target — MLX local, но alias не нашёлся (логируем warning)
- "passthrough" — backend не MLX local, resolver сразу вернул исходное имя

Best-effort: prometheus_client опционален, in-memory dict для text-render.
"""

from __future__ import annotations

try:
    from prometheus_client import Counter as _CounterMLXAlias  # type: ignore[import-not-found]

    _mlx_local_alias_resolved_total = _CounterMLXAlias(
        "krab_mlx_local_alias_resolved_total",
        "Wave 225: MLX local alias resolver outcomes (short_id → full path)",
        ["result"],
    )
except Exception:  # noqa: BLE001 - prometheus_client optional
    _mlx_local_alias_resolved_total = None  # type: ignore[assignment]

# Сырой in-memory счётчик (text-render + fallback при отсутствии prom_client).
_MLX_LOCAL_ALIAS_COUNTER: dict[str, int] = {}


def inc_mlx_local_alias_resolved(*, result: str) -> None:
    """Wave 225: фиксирует исход resolver-а. Best-effort, не бросает."""
    try:
        key = str(result)
        _MLX_LOCAL_ALIAS_COUNTER[key] = _MLX_LOCAL_ALIAS_COUNTER.get(key, 0) + 1
        if _mlx_local_alias_resolved_total is not None:
            _mlx_local_alias_resolved_total.labels(result=key).inc()
    except Exception:  # noqa: BLE001 - инструментация best-effort
        pass


__all__ = [
    "_MLX_LOCAL_ALIAS_COUNTER",
    "_mlx_local_alias_resolved_total",
    "inc_mlx_local_alias_resolved",
]
