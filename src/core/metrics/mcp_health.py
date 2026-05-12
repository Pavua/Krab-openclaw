# -*- coding: utf-8 -*-
"""Prometheus-метрики Wave 109 MCP servers health probe.

Gauge ``krab_mcp_server_alive{server}`` — последний результат probe (1=alive, 0=down).
Counter ``krab_mcp_server_probe_failures_total{server, reason}`` — суммарные провалы.

Если ``prometheus_client`` недоступен — объекты None, helper-ы no-op.
Hot-path никогда не ломаем.
"""

from __future__ import annotations

from typing import Any

try:
    from prometheus_client import Counter as _Counter  # type: ignore[import-not-found]
    from prometheus_client import Gauge as _Gauge  # type: ignore[import-not-found]

    krab_mcp_server_alive: Any = _Gauge(
        "krab_mcp_server_alive",
        "MCP server last probe result (Wave 109): 1=alive, 0=down",
        ["server"],
    )
    krab_mcp_server_probe_failures_total: Any = _Counter(
        "krab_mcp_server_probe_failures_total",
        "MCP server probe failures (Wave 109) by server and reason",
        ["server", "reason"],
    )
except Exception:  # noqa: BLE001 - prometheus_client optional
    krab_mcp_server_alive = None
    krab_mcp_server_probe_failures_total = None


def record_probe_result(*, server: str, alive: bool, reason: str | None = None) -> None:
    """Записывает результат одного probe-цикла.

    alive=True → gauge.set(1); иначе gauge.set(0) + counter.inc(reason).
    reason ∈ {timeout, exception, no_tools, transport, unknown}.
    """
    s = (server or "unknown")[:80]
    try:
        if krab_mcp_server_alive is not None:
            krab_mcp_server_alive.labels(server=s).set(1 if alive else 0)
    except Exception:  # noqa: BLE001
        pass
    if not alive:
        r = (reason or "unknown")[:40]
        try:
            if krab_mcp_server_probe_failures_total is not None:
                krab_mcp_server_probe_failures_total.labels(server=s, reason=r).inc()
        except Exception:  # noqa: BLE001
            pass
