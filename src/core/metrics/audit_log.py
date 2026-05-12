# -*- coding: utf-8 -*-
"""Wave 122: Prometheus-метрики owner-panel audit log.

Если ``prometheus_client`` недоступен — экспортируемые объекты None,
helper-функции тихо no-op. Hot-path никогда не ломаем.
"""

from __future__ import annotations

from typing import Any

# Импорт «мягкий»: prometheus_client может быть не установлен в тестовом окружении.
try:
    from prometheus_client import Counter as _Counter  # type: ignore[import-not-found]

    krab_owner_panel_requests_total: Any = _Counter(
        "krab_owner_panel_requests_total",
        "Owner-panel API requests (Wave 122 audit log)",
        ["method", "path", "status_class"],
    )
except Exception:  # noqa: BLE001
    krab_owner_panel_requests_total = None

# Wave 139: dedicated counter для 5xx-ошибок с разбивкой по error_class.
try:
    from prometheus_client import Counter as _Counter5xx  # type: ignore[import-not-found]

    krab_owner_panel_5xx_total: Any = _Counter5xx(
        "krab_owner_panel_5xx_total",
        "Owner-panel 5xx responses (Wave 139 error tracking)",
        ["path", "error_class"],
    )
except Exception:  # noqa: BLE001
    krab_owner_panel_5xx_total = None


def classify_status(status: int) -> str:
    """Возвращает класс HTTP-статуса: 2xx / 3xx / 4xx / 5xx / other."""
    if 200 <= status < 300:
        return "2xx"
    if 300 <= status < 400:
        return "3xx"
    if 400 <= status < 500:
        return "4xx"
    if 500 <= status < 600:
        return "5xx"
    return "other"


def record_request(method: str, path: str, status: int) -> None:
    """Инкрементирует counter request'ов (no-op если prometheus недоступен)."""
    if krab_owner_panel_requests_total is None:
        return
    try:
        krab_owner_panel_requests_total.labels(
            method=method,
            path=path,
            status_class=classify_status(status),
        ).inc()
    except Exception:  # noqa: BLE001
        pass


def record_5xx(path: str, error_class: str) -> None:
    """Wave 139: инкремент 5xx counter (no-op без prometheus)."""
    if krab_owner_panel_5xx_total is None:
        return
    try:
        krab_owner_panel_5xx_total.labels(
            path=path,
            error_class=error_class or "Unknown",
        ).inc()
    except Exception:  # noqa: BLE001
        pass
