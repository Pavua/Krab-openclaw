# -*- coding: utf-8 -*-
"""Wave 131: per-endpoint latency histogram для owner-panel.

Counter из Wave 122 фиксировал только факт request'а; для SLA/perf-budget
нужна distribution: какие endpoints медленные, где P99 уходит за 1s,
не утекает ли latency после релиза.

Метрика:
    krab_owner_panel_request_duration_seconds{method, path_pattern}
    buckets: 0.001, 0.01, 0.05, 0.1, 0.5, 1, 5, 10

Cardinality concern:
    Используем `request.scope["route"].path` (FastAPI path-pattern,
    e.g. ``/api/inbox/{item_id}``), а не raw URL — иначе каждый
    {item_id} стал бы отдельным label-set. Fallback на ``"unmatched"``
    для 404 / WebSocket, чтобы не плодить динамические path'ы.
    Exempt paths (`/metrics`, `/health*`) не наблюдаются вовсе.

prometheus_client soft-import — модуль безопасен при отсутствии зависимости.
"""

from __future__ import annotations

from typing import Any

# Buckets — explicit (no `+Inf`, prometheus_client добавит его сам).
LATENCY_BUCKETS: tuple[float, ...] = (
    0.001,
    0.01,
    0.05,
    0.1,
    0.5,
    1.0,
    5.0,
    10.0,
)

try:
    from prometheus_client import Histogram as _Histogram  # type: ignore[import-not-found]

    krab_owner_panel_request_duration_seconds: Any = _Histogram(
        "krab_owner_panel_request_duration_seconds",
        "Owner-panel API request duration (Wave 131 per-endpoint latency)",
        ["method", "path_pattern"],
        buckets=LATENCY_BUCKETS,
    )
except Exception:  # noqa: BLE001
    krab_owner_panel_request_duration_seconds = None


def observe_request_duration(
    method: str,
    path_pattern: str,
    duration_seconds: float,
) -> None:
    """Observe duration в histogram (no-op если prometheus недоступен).

    ``path_pattern`` — FastAPI route template (e.g. ``/api/inbox/{item_id}``);
    raw URL paths сюда передавать НЕ нужно — cardinality взорвётся.
    """
    if krab_owner_panel_request_duration_seconds is None:
        return
    try:
        krab_owner_panel_request_duration_seconds.labels(
            method=method,
            path_pattern=path_pattern,
        ).observe(max(0.0, float(duration_seconds)))
    except Exception:  # noqa: BLE001
        pass
