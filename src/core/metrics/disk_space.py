# -*- coding: utf-8 -*-
"""Prometheus-метрики Wave 111 disk space monitor.

Gauge ``krab_disk_free_bytes{mount}`` — свободное место в байтах на точке монтирования.
Gauge ``krab_disk_used_pct{mount}`` — % использования диска (0..100).

Если ``prometheus_client`` недоступен — объекты None, helper-ы no-op.
Hot-path никогда не ломаем.
"""

from __future__ import annotations

from typing import Any

try:
    from prometheus_client import Gauge as _Gauge  # type: ignore[import-not-found]

    krab_disk_free_bytes: Any = _Gauge(
        "krab_disk_free_bytes",
        "Free disk space in bytes (Wave 111) by mount point",
        ["mount"],
    )
    krab_disk_used_pct: Any = _Gauge(
        "krab_disk_used_pct",
        "Disk usage percent 0..100 (Wave 111) by mount point",
        ["mount"],
    )
except Exception:  # noqa: BLE001 - prometheus_client optional
    krab_disk_free_bytes = None
    krab_disk_used_pct = None


def record_disk_usage(*, mount: str, free_bytes: int, used_pct: float) -> None:
    """Обновляет gauge'ы disk usage для указанной точки монтирования."""
    m = (mount or "unknown")[:120]
    try:
        if krab_disk_free_bytes is not None:
            krab_disk_free_bytes.labels(mount=m).set(float(free_bytes))
    except Exception:  # noqa: BLE001
        pass
    try:
        if krab_disk_used_pct is not None:
            krab_disk_used_pct.labels(mount=m).set(float(used_pct))
    except Exception:  # noqa: BLE001
        pass
