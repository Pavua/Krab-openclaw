# -*- coding: utf-8 -*-
"""Wave 138: tests для voice STT cost + latency metrics."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core.metrics.voice_stt import record_voice_stt


@pytest.fixture
def patched_facade(monkeypatch):
    """Patch prometheus_metrics facade с MagicMock counter/histogram/cost."""
    import src.core.prometheus_metrics as pm

    total = MagicMock()
    duration = MagicMock()
    cost = MagicMock()
    monkeypatch.setattr(pm, "krab_voice_stt_total", total, raising=False)
    monkeypatch.setattr(pm, "krab_voice_stt_duration_seconds", duration, raising=False)
    monkeypatch.setattr(pm, "krab_voice_stt_cost_eur_total", cost, raising=False)
    return total, duration, cost


def test_record_local_whisper_ok_increments_counter_and_duration(patched_facade):
    total, duration, cost = patched_facade
    record_voice_stt(provider="local_whisper", outcome="ok", duration_seconds=1.7)
    total.labels.assert_called_once_with(provider="local_whisper", outcome="ok")
    total.labels.return_value.inc.assert_called_once()
    duration.labels.assert_called_once_with(provider="local_whisper")
    duration.labels.return_value.observe.assert_called_once_with(1.7)
    # local — free → cost не должен инкрементироваться
    cost.labels.assert_not_called()


def test_record_voice_gateway_timeout_outcome(patched_facade):
    total, duration, cost = patched_facade
    record_voice_stt(provider="voice_gateway", outcome="timeout", duration_seconds=30.0)
    total.labels.assert_called_once_with(provider="voice_gateway", outcome="timeout")
    duration.labels.return_value.observe.assert_called_once_with(30.0)
    cost.labels.assert_not_called()


def test_record_openai_whisper_paid_cost_eur(patched_facade):
    """OpenAI Whisper — paid path, 60 секунд аудио → 0.0055 EUR."""
    total, _duration, cost = patched_facade
    record_voice_stt(
        provider="openai_whisper",
        outcome="ok",
        duration_seconds=2.0,
        audio_seconds=60.0,
    )
    total.labels.assert_called_once_with(provider="openai_whisper", outcome="ok")
    cost.labels.assert_called_once_with(provider="openai_whisper")
    # 60s = 1 min × 0.0055 EUR/min
    args, _ = cost.labels.return_value.inc.call_args
    assert args[0] == pytest.approx(0.0055, rel=1e-6)


def test_record_openai_whisper_partial_minute_cost(patched_facade):
    """30s аудио → половина цены."""
    _total, _duration, cost = patched_facade
    record_voice_stt(
        provider="openai_whisper",
        outcome="ok",
        duration_seconds=1.0,
        audio_seconds=30.0,
    )
    args, _ = cost.labels.return_value.inc.call_args
    assert args[0] == pytest.approx(0.0055 / 2.0, rel=1e-6)


def test_record_unknown_provider_normalized(patched_facade):
    total, _duration, cost = patched_facade
    record_voice_stt(provider="weird_thing", outcome="ok", duration_seconds=0.5)
    total.labels.assert_called_once_with(provider="unknown", outcome="ok")
    cost.labels.assert_not_called()


def test_record_invalid_outcome_normalized_to_error(patched_facade):
    total, _duration, _cost = patched_facade
    record_voice_stt(provider="local_whisper", outcome="garbage", duration_seconds=0.1)
    total.labels.assert_called_once_with(provider="local_whisper", outcome="error")


def test_record_no_duration_skips_histogram(patched_facade):
    total, duration, _cost = patched_facade
    record_voice_stt(provider="local_whisper", outcome="error", duration_seconds=None)
    total.labels.assert_called_once()
    duration.labels.assert_not_called()


def test_record_failsafe_when_facade_none(monkeypatch):
    """None метрики (prometheus_client отсутствует) — no exceptions."""
    import src.core.prometheus_metrics as pm

    monkeypatch.setattr(pm, "krab_voice_stt_total", None, raising=False)
    monkeypatch.setattr(pm, "krab_voice_stt_duration_seconds", None, raising=False)
    monkeypatch.setattr(pm, "krab_voice_stt_cost_eur_total", None, raising=False)
    # Должно молча работать
    record_voice_stt(
        provider="openai_whisper",
        outcome="ok",
        duration_seconds=1.0,
        audio_seconds=10.0,
    )


def test_record_negative_duration_clamped_to_zero(patched_facade):
    _total, duration, _cost = patched_facade
    record_voice_stt(provider="local_whisper", outcome="ok", duration_seconds=-0.5)
    duration.labels.return_value.observe.assert_called_once_with(0.0)


def test_local_whisper_with_audio_seconds_does_not_record_cost(patched_facade):
    """local_whisper — free, даже если передан audio_seconds — cost остаётся пустым."""
    _total, _duration, cost = patched_facade
    record_voice_stt(
        provider="local_whisper",
        outcome="ok",
        duration_seconds=1.0,
        audio_seconds=120.0,
    )
    cost.labels.assert_not_called()
