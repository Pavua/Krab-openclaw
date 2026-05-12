# -*- coding: utf-8 -*-
"""Wave 73: Smart Message Routing 5-stage pipeline observability."""

from __future__ import annotations

try:
    from prometheus_client import Counter as _CounterSR  # type: ignore[import-not-found]
    from prometheus_client import Histogram as _HistogramSR  # type: ignore[import-not-found]

    krab_smart_routing_decisions_total = _CounterSR(
        "krab_smart_routing_decisions_total",
        "Smart Routing 5-stage pipeline decisions by stage and outcome (Wave 73)",
        ["stage", "outcome"],
    )
    krab_smart_routing_stage_duration_seconds = _HistogramSR(
        "krab_smart_routing_stage_duration_seconds",
        "Smart Routing per-stage duration (seconds)",
        ["stage"],
        buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    )
except Exception:  # noqa: BLE001
    krab_smart_routing_decisions_total = None  # type: ignore[assignment]
    krab_smart_routing_stage_duration_seconds = None  # type: ignore[assignment]


_SMART_ROUTING_STAGES: frozenset[str] = frozenset(
    {"hard_gate", "chat_policy", "regex", "llm_classifier", "feedback"}
)
_SMART_ROUTING_OUTCOMES: frozenset[str] = frozenset({"allow", "deny"})

_DECISION_PATH_TO_STAGE: dict[str, str] = {
    "hard_gate": "hard_gate",
    "policy_silent": "chat_policy",
    "regex_high": "regex",
    "regex_low": "regex",
    "media_present": "regex",
    "regex_threshold_fallback": "regex",
    "llm_yes": "llm_classifier",
    "llm_no": "llm_classifier",
    "llm_error_fallback": "feedback",
}


def _facade():
    """Lazy import фасада."""
    import src.core.prometheus_metrics as _pm  # noqa: PLC0415

    return _pm


def record_smart_routing_decision(
    stage: str,
    outcome: str,
    *,
    duration_sec: float | None = None,
) -> None:
    """Wave 73: инкрементирует krab_smart_routing_decisions_total. Fail-safe."""
    try:
        s = stage if stage in _SMART_ROUTING_STAGES else "unknown"
        o = outcome if outcome in _SMART_ROUTING_OUTCOMES else "unknown"
        pm = _facade()
        if pm.krab_smart_routing_decisions_total is not None:
            pm.krab_smart_routing_decisions_total.labels(stage=s, outcome=o).inc()
        if (
            duration_sec is not None
            and duration_sec >= 0
            and pm.krab_smart_routing_stage_duration_seconds is not None
        ):
            pm.krab_smart_routing_stage_duration_seconds.labels(stage=s).observe(
                float(duration_sec)
            )
    except Exception:  # noqa: BLE001
        pass


def map_smart_routing_path(decision_path: str, should_respond: bool) -> tuple[str, str]:
    """Wave 73: decision_path + should_respond → (stage, outcome) для Prometheus."""
    stage = _DECISION_PATH_TO_STAGE.get(decision_path, "unknown")
    outcome = "allow" if should_respond else "deny"
    return stage, outcome
