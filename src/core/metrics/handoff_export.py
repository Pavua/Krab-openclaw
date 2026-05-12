# -*- coding: utf-8 -*-
"""Wave 143: метрики periodic handoff export.

Счётчики и гистограммы для отслеживания регулярного и shutdown-export
runtime handoff snapshot (см. ``src/core/handoff_auto_export.py``).

Метрики:
  - ``krab_handoff_export_total{outcome, reason}`` — счётчик попыток
    с outcome ∈ {ok, timeout, error} и reason ∈ {periodic_maintenance,
    userbot_stop, manual, unknown}.
  - ``krab_handoff_export_duration_seconds{reason}`` — гистограмма latency
    одной попытки (включая retry, если был).

Все вызовы fail-safe: если ``prometheus_client`` отсутствует или внутренние
объекты не созданы — helper молча игнорирует. Тесты могут patch'ить
атрибуты модуля через monkeypatch.
"""

from __future__ import annotations

try:
    from prometheus_client import Counter as _CounterHE  # type: ignore[import-not-found]
    from prometheus_client import Histogram as _HistogramHE  # type: ignore[import-not-found]

    krab_handoff_export_total = _CounterHE(
        "krab_handoff_export_total",
        "Auto handoff export attempts per outcome/reason (Wave 143)",
        ["outcome", "reason"],
    )
    krab_handoff_export_duration_seconds = _HistogramHE(
        "krab_handoff_export_duration_seconds",
        "Auto handoff export latency seconds per reason (Wave 143)",
        ["reason"],
        # Распределение от быстрого 1s до медленного 60s timeout — handoff
        # endpoint обычно отвечает 2-10s, но cloud probe может тянуть до 30+.
        buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 45.0, 60.0),
    )
except Exception:  # noqa: BLE001 — slim env без prometheus_client
    krab_handoff_export_total = None  # type: ignore[assignment]
    krab_handoff_export_duration_seconds = None  # type: ignore[assignment]


_ALLOWED_OUTCOMES = {"ok", "timeout", "error"}
_ALLOWED_REASONS = {
    "periodic_maintenance",
    "userbot_stop",
    "manual",
    "unknown",
}


def _normalize_outcome(outcome: str | None) -> str:
    o = (outcome or "error").strip().lower()
    if o not in _ALLOWED_OUTCOMES:
        return "error"
    return o


def _normalize_reason(reason: str | None) -> str:
    r = (reason or "unknown").strip().lower()
    if r not in _ALLOWED_REASONS:
        return "unknown"
    return r


def record_handoff_export(
    *,
    outcome: str,
    reason: str,
    duration_seconds: float | None = None,
) -> None:
    """Записать метрики одной попытки handoff export.

    Fail-safe: любой сбой prometheus_client/labels не пробрасывается наружу.

    outcome:          ok | timeout | error
    reason:           periodic_maintenance | userbot_stop | manual | unknown
    duration_seconds: latency одной попытки (включая retry если был). None — пропустить.
    """
    try:
        o = _normalize_outcome(outcome)
        r = _normalize_reason(reason)

        if krab_handoff_export_total is not None:
            krab_handoff_export_total.labels(outcome=o, reason=r).inc()

        if duration_seconds is not None and krab_handoff_export_duration_seconds is not None:
            d = float(duration_seconds)
            if d < 0.0:
                d = 0.0
            krab_handoff_export_duration_seconds.labels(reason=r).observe(d)
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "krab_handoff_export_total",
    "krab_handoff_export_duration_seconds",
    "record_handoff_export",
]
