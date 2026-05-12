# -*- coding: utf-8 -*-
"""Prometheus-метрики Wave 96 owner-panel rate-limiter.

Если ``prometheus_client`` недоступен — экспортируемые объекты None,
helper-функции тихо no-op. Hot-path никогда не ломаем.
"""

from __future__ import annotations

import ipaddress
from typing import Any

# Импорт «мягкий»: prometheus_client может быть не установлен в окружении тестов.
try:
    from prometheus_client import Counter as _Counter  # type: ignore[import-not-found]
    from prometheus_client import Gauge as _Gauge  # type: ignore[import-not-found]

    krab_rate_limit_blocks_total: Any = _Counter(
        "krab_rate_limit_blocks_total",
        "Owner-panel rate-limiter блокировки (429)",
        ["path", "ip_class"],
    )
    krab_rate_limit_active_keys: Any = _Gauge(
        "krab_rate_limit_active_keys",
        "Активные ключи (IP/token) в token-bucket стейте",
    )
except Exception:  # noqa: BLE001
    krab_rate_limit_blocks_total = None
    krab_rate_limit_active_keys = None


def classify_ip(ip: str | None) -> str:
    """Возвращает грубый класс IP: localhost / lan / wan / unknown."""
    if not ip:
        return "unknown"
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "unknown"
    if addr.is_loopback:
        return "localhost"
    if addr.is_private or addr.is_link_local:
        return "lan"
    return "wan"


def record_block(path: str, ip: str | None) -> None:
    """Инкрементирует counter блокировок (no-op если prometheus недоступен)."""
    if krab_rate_limit_blocks_total is None:
        return
    try:
        krab_rate_limit_blocks_total.labels(path=path, ip_class=classify_ip(ip)).inc()
    except Exception:  # noqa: BLE001
        pass


def set_active_keys(value: int) -> None:
    """Обновляет gauge числа активных ключей."""
    if krab_rate_limit_active_keys is None:
        return
    try:
        krab_rate_limit_active_keys.set(value)
    except Exception:  # noqa: BLE001
        pass
