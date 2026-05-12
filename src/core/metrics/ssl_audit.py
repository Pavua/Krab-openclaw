# -*- coding: utf-8 -*-
"""Prometheus-метрики Wave 130 SSL cert expiry audit.

Gauge ``krab_ssl_cert_days_remaining{host}`` — количество дней до истечения
сертификата для каждого проверяемого хоста.

Если ``prometheus_client`` недоступен — объекты None, helper-ы no-op.
Hot-path никогда не ломаем.
"""

from __future__ import annotations

from typing import Any

try:
    from prometheus_client import Gauge as _Gauge  # type: ignore[import-not-found]

    krab_ssl_cert_days_remaining: Any = _Gauge(
        "krab_ssl_cert_days_remaining",
        "Days remaining until SSL cert expiry (Wave 130) by host",
        ["host"],
    )
except Exception:  # noqa: BLE001 - prometheus_client optional
    krab_ssl_cert_days_remaining = None


def record_cert_days(*, host: str, days_until_expiry: float) -> None:
    """Обновляет gauge с количеством дней до истечения cert'а для хоста."""
    h = (host or "unknown")[:255]
    try:
        if krab_ssl_cert_days_remaining is not None:
            krab_ssl_cert_days_remaining.labels(host=h).set(float(days_until_expiry))
    except Exception:  # noqa: BLE001
        pass
