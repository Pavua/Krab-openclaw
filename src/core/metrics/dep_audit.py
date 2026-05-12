# -*- coding: utf-8 -*-
"""
src/core/metrics/dep_audit.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Wave 110: Prometheus метрики для pip-audit dependency vulnerability scan.

- Gauge `krab_dependency_vulns_total{severity}` — количество CVE по severity
  (critical/high/medium/low/unknown).

Fail-safe: если prometheus_client отсутствует — объекты None, helpers no-op.
"""

from __future__ import annotations

from ..logger import get_logger

logger = get_logger(__name__)


try:
    from prometheus_client import Gauge as _Gauge  # type: ignore[import-not-found]

    krab_dependency_vulns_total = _Gauge(
        "krab_dependency_vulns_total",
        "Количество известных CVE в pip-зависимостях по severity",
        ["severity"],
    )
except Exception:  # noqa: BLE001 - prometheus_client optional
    krab_dependency_vulns_total = None  # type: ignore[assignment]


# Ожидаемые уровни severity. Используется чтобы выставить 0 для отсутствующих
# уровней (иначе старые ненулевые значения остаются после resolve).
_KNOWN_SEVERITIES: tuple[str, ...] = (
    "critical",
    "high",
    "medium",
    "low",
    "unknown",
)


def record_dependency_vulns(by_severity: dict[str, int]) -> None:
    """Обновить Gauge по агрегации severity→count.

    Все известные severity выставляются явно (включая 0), чтобы resolve
    после фикса корректно убирал alert.
    """
    if krab_dependency_vulns_total is None:
        return
    try:
        for sev in _KNOWN_SEVERITIES:
            value = int(by_severity.get(sev, 0))
            krab_dependency_vulns_total.labels(severity=sev).set(value)
        # Прочие severity (если pip-audit отдаст что-то новое) — тоже
        # записываем, но не очищаем.
        for sev, value in by_severity.items():
            if sev not in _KNOWN_SEVERITIES:
                try:
                    krab_dependency_vulns_total.labels(severity=sev).set(int(value))
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        logger.warning("dep_audit_metric_update_failed", exc_info=True)
