# -*- coding: utf-8 -*-
"""Wave 73: тесты observability для 5-stage Smart Message Routing pipeline.

Проверяем:
  - record_smart_routing_decision() инкрементирует Counter с правильными labels;
  - map_smart_routing_path() корректно маппит decision_path → (stage, outcome);
  - detect_smart_trigger() emits metric на каждый return path;
  - alert SmartRoutingHighDenyRate присутствует в YAML rules с правильными
    expr/severity/for.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from src.core import prometheus_metrics as pm
from src.core.trigger_detector import detect_smart_trigger

# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

RULES_PATH = (
    Path(__file__).resolve().parents[2] / "deploy" / "monitoring" / "rules" / "krab_alerts.yml"
)


def _counter_value(stage: str, outcome: str) -> float:
    """Прочитать текущее значение Counter{stage, outcome}."""
    counter = pm.krab_smart_routing_decisions_total
    if counter is None:
        return 0.0
    return counter.labels(stage=stage, outcome=outcome)._value.get()


def _make_policy_store(mode: str = "normal", threshold: float = 0.5):
    """Минимальный фейк ChatResponsePolicyStore."""
    from src.core.chat_response_policy import ChatMode

    policy = MagicMock()
    policy.mode = ChatMode.SILENT if mode == "silent" else ChatMode.NORMAL
    policy.effective_threshold.return_value = threshold

    store = MagicMock()
    store.get_policy.return_value = policy
    return store


# --------------------------------------------------------------------------- #
# Unit tests: helpers                                                          #
# --------------------------------------------------------------------------- #


def test_map_smart_routing_path_covers_all_paths() -> None:
    """Все decision_path значения мапятся на корректные stages."""
    cases = [
        ("hard_gate", True, ("hard_gate", "allow")),
        ("policy_silent", False, ("chat_policy", "deny")),
        ("regex_high", True, ("regex", "allow")),
        ("regex_low", False, ("regex", "deny")),
        ("media_present", True, ("regex", "allow")),
        ("media_present", False, ("regex", "deny")),
        ("regex_threshold_fallback", True, ("regex", "allow")),
        ("llm_yes", True, ("llm_classifier", "allow")),
        ("llm_no", False, ("llm_classifier", "deny")),
        ("llm_error_fallback", True, ("feedback", "allow")),
        ("llm_error_fallback", False, ("feedback", "deny")),
    ]
    for path, should_respond, expected in cases:
        assert pm.map_smart_routing_path(path, should_respond) == expected, path

    # Невалидный path → unknown stage.
    assert pm.map_smart_routing_path("garbage", True) == ("unknown", "allow")


def test_record_smart_routing_decision_increments_counter() -> None:
    """record_smart_routing_decision() инкрементирует Counter."""
    if pm.krab_smart_routing_decisions_total is None:
        pytest.skip("prometheus_client not installed")

    before = _counter_value("hard_gate", "allow")
    pm.record_smart_routing_decision("hard_gate", "allow")
    pm.record_smart_routing_decision("hard_gate", "allow")
    after = _counter_value("hard_gate", "allow")
    assert after - before == pytest.approx(2.0)


def test_record_smart_routing_decision_rejects_invalid_labels() -> None:
    """Невалидные stage/outcome нормализуются в "unknown" (cardinality guard)."""
    if pm.krab_smart_routing_decisions_total is None:
        pytest.skip("prometheus_client not installed")

    before = _counter_value("unknown", "unknown")
    pm.record_smart_routing_decision("bogus_stage", "bogus_outcome")
    after = _counter_value("unknown", "unknown")
    assert after - before == pytest.approx(1.0)


def test_record_smart_routing_decision_observes_duration() -> None:
    """Если duration_sec задан — Histogram observe вызывается."""
    if pm.krab_smart_routing_stage_duration_seconds is None:
        pytest.skip("prometheus_client not installed")

    # Berry: достаём sum через _sum.get() — Histogram внутреннее.
    hist = pm.krab_smart_routing_stage_duration_seconds.labels(stage="llm_classifier")
    before = hist._sum.get()
    pm.record_smart_routing_decision("llm_classifier", "allow", duration_sec=0.25)
    after = hist._sum.get()
    assert after - before == pytest.approx(0.25)


# --------------------------------------------------------------------------- #
# Integration: detect_smart_trigger emits metric per stage                     #
# --------------------------------------------------------------------------- #


def test_detect_smart_trigger_hard_gate_emits_metric() -> None:
    """Stage 1 hard_gate → counter{stage=hard_gate, outcome=allow} +1."""
    if pm.krab_smart_routing_decisions_total is None:
        pytest.skip("prometheus_client not installed")

    before = _counter_value("hard_gate", "allow")
    result = asyncio.run(
        detect_smart_trigger(
            text="hi",
            chat_id="123",
            is_reply_to_me=True,
            has_explicit_mention=False,
            has_command=False,
            chat_context=[],
            policy_store=_make_policy_store(),
        )
    )
    after = _counter_value("hard_gate", "allow")
    assert result.decision_path == "hard_gate"
    assert result.should_respond is True
    assert after - before == pytest.approx(1.0)


def test_detect_smart_trigger_policy_silent_emits_deny() -> None:
    """Stage 2 policy_silent → counter{stage=chat_policy, outcome=deny} +1."""
    if pm.krab_smart_routing_decisions_total is None:
        pytest.skip("prometheus_client not installed")

    before = _counter_value("chat_policy", "deny")
    result = asyncio.run(
        detect_smart_trigger(
            text="random message",
            chat_id="456",
            is_reply_to_me=False,
            has_explicit_mention=False,
            has_command=False,
            chat_context=[],
            policy_store=_make_policy_store(mode="silent"),
        )
    )
    after = _counter_value("chat_policy", "deny")
    assert result.decision_path == "policy_silent"
    assert after - before == pytest.approx(1.0)


def test_detect_smart_trigger_regex_low_emits_deny() -> None:
    """Stage 3 regex_low → counter{stage=regex, outcome=deny} +1."""
    if pm.krab_smart_routing_decisions_total is None:
        pytest.skip("prometheus_client not installed")

    before = _counter_value("regex", "deny")
    result = asyncio.run(
        detect_smart_trigger(
            text="abc def ghi",  # ничего что регекс ловит → score ~ 0
            chat_id="789",
            is_reply_to_me=False,
            has_explicit_mention=False,
            has_command=False,
            chat_context=[],
            policy_store=_make_policy_store(),
        )
    )
    after = _counter_value("regex", "deny")
    assert result.decision_path == "regex_low"
    assert after - before == pytest.approx(1.0)


def test_detect_smart_trigger_metric_registered() -> None:
    """Metric krab_smart_routing_decisions_total зарегистрирован с правильным name."""
    if pm.krab_smart_routing_decisions_total is None:
        pytest.skip("prometheus_client not installed")

    counter = pm.krab_smart_routing_decisions_total
    # prometheus_client v0.x: _name атрибут содержит base name (без _total).
    assert counter._name == "krab_smart_routing_decisions"
    assert set(counter._labelnames) == {"stage", "outcome"}


# --------------------------------------------------------------------------- #
# Alert rule: SmartRoutingHighDenyRate                                         #
# --------------------------------------------------------------------------- #


def test_smart_routing_high_deny_rate_alert_present() -> None:
    """Alert SmartRoutingHighDenyRate существует в krab_wave_47_49 group."""
    assert RULES_PATH.exists(), f"Rules file missing: {RULES_PATH}"
    rules = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
    all_alerts: dict[str, dict] = {}
    for group in rules.get("groups", []):
        for rule in group.get("rules", []):
            name = rule.get("alert")
            if name:
                all_alerts[name] = rule

    assert "SmartRoutingHighDenyRate" in all_alerts, "alert не найден"
    rule = all_alerts["SmartRoutingHighDenyRate"]

    # Threshold 0.95 + for: 30m + severity info.
    assert "0.95" in rule["expr"]
    assert "krab_smart_routing_decisions_total" in rule["expr"]
    assert 'outcome="deny"' in rule["expr"]
    assert rule.get("for") == "30m"
    assert rule["labels"]["severity"] == "info"
    assert rule["labels"]["wave"] == "73"
