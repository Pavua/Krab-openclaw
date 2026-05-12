# -*- coding: utf-8 -*-
"""Wave 135: NLU command intent telemetry — per-command dispatch
outcomes + confidence histogram.

Counters/histograms — fail-safe (prometheus_client опционален). Helper
`record_nlu_intent` читает метрики через facade чтобы тесты могли
patch'ить `src.core.prometheus_metrics` атрибуты."""

from __future__ import annotations

try:
    from prometheus_client import Counter as _CounterNLU  # type: ignore[import-not-found]
    from prometheus_client import Histogram as _HistogramNLU  # type: ignore[import-not-found]

    krab_nlu_commands_dispatched_total = _CounterNLU(
        "krab_nlu_commands_dispatched_total",
        "NLU command gate dispatch outcomes per !cmd",
        ["cmd", "outcome"],
    )
    krab_nlu_confidence_score = _HistogramNLU(
        "krab_nlu_confidence_score",
        "Distribution of NLU command-intent confidence scores",
        buckets=(0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99),
    )
except Exception:  # noqa: BLE001
    krab_nlu_commands_dispatched_total = None  # type: ignore[assignment]
    krab_nlu_confidence_score = None  # type: ignore[assignment]


_ALLOWED_OUTCOMES = {"dispatched", "skipped", "error"}


def _facade():
    """Lazy import фасада — позволяет тестам patch'ить facade-атрибуты."""
    import src.core.prometheus_metrics as _pm  # noqa: PLC0415

    return _pm


def record_nlu_intent(
    *,
    cmd: str,
    outcome: str,
    confidence: float | None = None,
) -> None:
    """Записать metrics для одного NLU intent attempt. Fail-safe.

    cmd:        имя команды без префикса `!` (нормализуется).
    outcome:    dispatched | skipped | error.
    confidence: если задано — observe в histogram (clamped 0..1).
    """
    try:
        c = (cmd or "unknown").lstrip("!").lower()[:40] or "unknown"
        o = outcome or "unknown"
        if o not in _ALLOWED_OUTCOMES:
            o = "skipped"
        pm = _facade()
        if pm.krab_nlu_commands_dispatched_total is not None:
            pm.krab_nlu_commands_dispatched_total.labels(cmd=c, outcome=o).inc()
        if confidence is not None and pm.krab_nlu_confidence_score is not None:
            val = float(confidence)
            if val < 0.0:
                val = 0.0
            elif val > 1.0:
                val = 1.0
            pm.krab_nlu_confidence_score.observe(val)
    except Exception:  # noqa: BLE001
        pass
