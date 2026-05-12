# -*- coding: utf-8 -*-
"""Prometheus-метрики Wave 94 provider quarantine.

Counter ``krab_provider_quarantine_total{provider, reason}`` — события
quarantine (триггер, expiry, cleared). Gauge ``krab_provider_quarantined
{provider}`` — текущее состояние (1/0).

Если ``prometheus_client`` недоступен — объекты None, helper-ы no-op.
Hot-path никогда не ломаем.
"""

from __future__ import annotations

from typing import Any

try:
    from prometheus_client import Counter as _Counter  # type: ignore[import-not-found]
    from prometheus_client import Gauge as _Gauge  # type: ignore[import-not-found]

    krab_provider_quarantine_total: Any = _Counter(
        "krab_provider_quarantine_total",
        "Provider quarantine events (Wave 94) by provider and reason",
        ["provider", "reason"],
    )
    krab_provider_quarantined: Any = _Gauge(
        "krab_provider_quarantined",
        "Provider currently quarantined (1) or active (0)",
        ["provider"],
    )
except Exception:  # noqa: BLE001 - prometheus_client optional
    krab_provider_quarantine_total = None
    krab_provider_quarantined = None


def record_quarantine_event(
    *,
    provider: str,
    reason: str,
    quarantined: bool,
) -> None:
    """Записывает quarantine event: counter инкрементируется, gauge переключается.

    reason ∈ {auth, quota, network, timeout, unknown, cleared, expired, ...}.
    quarantined=True → gauge.set(1); False → gauge.set(0).
    """
    p = (provider or "unknown")[:80]
    r = (reason or "unknown")[:40]
    try:
        if krab_provider_quarantine_total is not None:
            krab_provider_quarantine_total.labels(provider=p, reason=r).inc()
    except Exception:  # noqa: BLE001
        pass
    try:
        if krab_provider_quarantined is not None:
            krab_provider_quarantined.labels(provider=p).set(1 if quarantined else 0)
    except Exception:  # noqa: BLE001
        pass
