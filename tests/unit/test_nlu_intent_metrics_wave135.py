# -*- coding: utf-8 -*-
"""Wave 135: tests для NLU command intent telemetry."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core.metrics.nlu_intent import record_nlu_intent


@pytest.fixture
def patched_facade(monkeypatch):
    """Patch prometheus_metrics facade с MagicMock counter/histogram."""
    import src.core.prometheus_metrics as pm

    counter = MagicMock()
    histogram = MagicMock()
    monkeypatch.setattr(pm, "krab_nlu_commands_dispatched_total", counter, raising=False)
    monkeypatch.setattr(pm, "krab_nlu_confidence_score", histogram, raising=False)
    return counter, histogram


def test_record_nlu_intent_dispatched_increments_counter_and_observes_confidence(
    patched_facade,
):
    counter, histogram = patched_facade
    record_nlu_intent(cmd="!costs", outcome="dispatched", confidence=0.92)
    counter.labels.assert_called_once_with(cmd="costs", outcome="dispatched")
    counter.labels.return_value.inc.assert_called_once()
    histogram.observe.assert_called_once_with(0.92)


def test_record_nlu_intent_skipped_outcome(patched_facade):
    counter, histogram = patched_facade
    record_nlu_intent(cmd="cron", outcome="skipped", confidence=0.55)
    counter.labels.assert_called_once_with(cmd="cron", outcome="skipped")
    histogram.observe.assert_called_once_with(0.55)


def test_record_nlu_intent_error_outcome(patched_facade):
    counter, histogram = patched_facade
    record_nlu_intent(cmd="!memory", outcome="error", confidence=0.81)
    counter.labels.assert_called_once_with(cmd="memory", outcome="error")
    counter.labels.return_value.inc.assert_called_once()


def test_record_nlu_intent_unknown_outcome_coerced_to_skipped(patched_facade):
    counter, _ = patched_facade
    record_nlu_intent(cmd="status", outcome="weird_unknown", confidence=0.7)
    counter.labels.assert_called_once_with(cmd="status", outcome="skipped")


def test_record_nlu_intent_clamps_confidence_to_unit_interval(patched_facade):
    _, histogram = patched_facade
    record_nlu_intent(cmd="x", outcome="dispatched", confidence=1.5)
    record_nlu_intent(cmd="x", outcome="dispatched", confidence=-0.4)
    observed = [call.args[0] for call in histogram.observe.call_args_list]
    assert observed == [1.0, 0.0]


def test_record_nlu_intent_none_confidence_skips_histogram(patched_facade):
    counter, histogram = patched_facade
    record_nlu_intent(cmd="quota", outcome="dispatched", confidence=None)
    counter.labels.assert_called_once_with(cmd="quota", outcome="dispatched")
    histogram.observe.assert_not_called()


def test_record_nlu_intent_failsafe_when_facade_none(monkeypatch):
    """None metrics (prometheus_client отсутствует) — no exceptions."""
    import src.core.prometheus_metrics as pm

    monkeypatch.setattr(pm, "krab_nlu_commands_dispatched_total", None, raising=False)
    monkeypatch.setattr(pm, "krab_nlu_confidence_score", None, raising=False)
    # должно молча работать
    record_nlu_intent(cmd="costs", outcome="dispatched", confidence=0.9)


def test_record_nlu_intent_strips_bang_prefix_and_lowercases(patched_facade):
    counter, _ = patched_facade
    record_nlu_intent(cmd="!Costs", outcome="dispatched", confidence=0.85)
    counter.labels.assert_called_once_with(cmd="costs", outcome="dispatched")
