# -*- coding: utf-8 -*-
"""Wave 124: Prometheus метрики health-watchdog для OpenClaw Gateway.

Существующий `scripts/openclaw_gateway_watchdog.sh` (Session 13) проверяет
только presence в `launchctl list`, не детектит frozen state (PID жив, port
:18789 не отвечает). Wave 124 добавляет HTTP probe + auto kickstart + метрики.

Метрики:
    krab_openclaw_gateway_healthy           — gauge 1/0 (последний probe)
    krab_openclaw_gateway_restarts_total    — counter (auto-kickstart events)
    krab_openclaw_gateway_probe_failures_total{reason} — counter probe-провалов

Hot-path safety: prometheus_client опциональный, всё fail-safe.
"""

from __future__ import annotations

from typing import Any

try:
    from prometheus_client import Counter as _Counter  # type: ignore[import-not-found]
    from prometheus_client import Gauge as _Gauge  # type: ignore[import-not-found]

    krab_openclaw_gateway_healthy: Any = _Gauge(
        "krab_openclaw_gateway_healthy",
        "OpenClaw gateway last health probe (Wave 124): 1=healthy, 0=down/frozen",
    )
    krab_openclaw_gateway_restarts_total: Any = _Counter(
        "krab_openclaw_gateway_restarts_total",
        "OpenClaw gateway auto-restarts via watchdog (Wave 124)",
    )
    krab_openclaw_gateway_probe_failures_total: Any = _Counter(
        "krab_openclaw_gateway_probe_failures_total",
        "OpenClaw gateway probe failures (Wave 124) by reason",
        ["reason"],
    )
except Exception:  # noqa: BLE001 — prometheus_client optional
    krab_openclaw_gateway_healthy = None
    krab_openclaw_gateway_restarts_total = None
    krab_openclaw_gateway_probe_failures_total = None


def record_probe_result(*, healthy: bool, reason: str | None = None) -> None:
    """Записывает результат одного probe цикла.

    healthy=True → gauge=1; False → gauge=0 + counter[reason].inc().
    reason ∈ {timeout, connection_refused, http_error, exception, unknown}.
    """
    try:
        if krab_openclaw_gateway_healthy is not None:
            krab_openclaw_gateway_healthy.set(1 if healthy else 0)
    except Exception:  # noqa: BLE001
        pass
    if not healthy:
        r = (reason or "unknown")[:40]
        try:
            if krab_openclaw_gateway_probe_failures_total is not None:
                krab_openclaw_gateway_probe_failures_total.labels(reason=r).inc()
        except Exception:  # noqa: BLE001
            pass


def record_restart() -> None:
    """Инкремент counter после успешного launchctl kickstart."""
    try:
        if krab_openclaw_gateway_restarts_total is not None:
            krab_openclaw_gateway_restarts_total.inc()
    except Exception:  # noqa: BLE001
        pass
