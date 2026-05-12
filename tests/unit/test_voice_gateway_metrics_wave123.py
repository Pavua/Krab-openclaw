# -*- coding: utf-8 -*-
"""
Wave 123: тесты Voice Gateway client observability.

Помимо unit-тестов helper'a `record_voice_request` проверяем интеграцию
с `VoiceGatewayClient.session_tts()` — outcome=ok/error/timeout.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.core.metrics import voice_gateway as voice_metrics
from src.core.metrics.voice_gateway import (
    krab_voice_gateway_chars_total,
    krab_voice_gateway_cost_eur_total,
    krab_voice_gateway_requests_total,
    record_voice_request,
)
from src.integrations.voice_gateway_client import VoiceGatewayClient


def _counter_value(counter, **labels) -> float:
    try:
        if labels:
            return counter.labels(**labels)._value.get()  # type: ignore[attr-defined]
        return counter._value.get()  # type: ignore[attr-defined]
    except Exception:
        return 0.0


class TestRecordVoiceRequest:
    """Юнит-тесты helper'a."""

    def test_ok_increments_requests_counter(self) -> None:
        before = _counter_value(krab_voice_gateway_requests_total, outcome="ok")
        record_voice_request(chars=10, outcome="ok", duration_sec=1.5)
        after = _counter_value(krab_voice_gateway_requests_total, outcome="ok")
        assert after - before == pytest.approx(1.0)

    def test_ok_increments_chars_counter(self) -> None:
        before = _counter_value(krab_voice_gateway_chars_total)
        record_voice_request(chars=42, outcome="ok", duration_sec=0.5)
        after = _counter_value(krab_voice_gateway_chars_total)
        assert after - before == pytest.approx(42.0)

    def test_error_does_not_touch_chars_or_cost(self) -> None:
        chars_before = _counter_value(krab_voice_gateway_chars_total)
        cost_before = _counter_value(krab_voice_gateway_cost_eur_total)
        calls_before = _counter_value(krab_voice_gateway_requests_total, outcome="error")
        record_voice_request(chars=100, outcome="error", duration_sec=2.0)
        assert _counter_value(krab_voice_gateway_chars_total) == pytest.approx(chars_before)
        assert _counter_value(krab_voice_gateway_cost_eur_total) == pytest.approx(cost_before)
        assert (
            _counter_value(krab_voice_gateway_requests_total, outcome="error") - calls_before
            == pytest.approx(1.0)
        )

    def test_timeout_outcome_increments_timeout_label(self) -> None:
        before = _counter_value(krab_voice_gateway_requests_total, outcome="timeout")
        record_voice_request(chars=5, outcome="timeout", duration_sec=10.0)
        after = _counter_value(krab_voice_gateway_requests_total, outcome="timeout")
        assert after - before == pytest.approx(1.0)

    def test_cost_uses_env_rate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KRAB_TTS_COST_PER_CHAR", "0.001")
        before = _counter_value(krab_voice_gateway_cost_eur_total)
        record_voice_request(chars=50, outcome="ok", duration_sec=0.5)
        after = _counter_value(krab_voice_gateway_cost_eur_total)
        assert after - before == pytest.approx(0.05)

    def test_fail_safe_on_prom_failure(self) -> None:
        """Если labels() кидает — helper не должен ронять hot-path."""
        broken = MagicMock()
        broken.labels = MagicMock(side_effect=RuntimeError("boom"))
        with patch.object(voice_metrics, "krab_voice_gateway_requests_total", broken):
            record_voice_request(chars=10, outcome="ok", duration_sec=1.0)

    def test_unknown_outcome_normalized_to_error(self) -> None:
        before = _counter_value(krab_voice_gateway_requests_total, outcome="error")
        record_voice_request(chars=0, outcome="weird-status", duration_sec=0.1)
        after = _counter_value(krab_voice_gateway_requests_total, outcome="error")
        assert after - before == pytest.approx(1.0)


class TestSessionTtsWiring:
    """`session_tts()` пишет метрики в зависимости от исхода."""

    @pytest.mark.asyncio
    async def test_ok_path_records_ok(self) -> None:
        client = VoiceGatewayClient(base_url="http://x", api_key="", timeout_sec=1.0)
        calls_before = _counter_value(krab_voice_gateway_requests_total, outcome="ok")
        chars_before = _counter_value(krab_voice_gateway_chars_total)
        with patch.object(
            client,
            "_request_json",
            return_value=(200, {"result": {"audio_url": "u"}}, ""),
        ):
            result = await client.session_tts("s1", text="привет", voice="v", style="n")
        assert result["ok"] is True
        assert (
            _counter_value(krab_voice_gateway_requests_total, outcome="ok") - calls_before
            == pytest.approx(1.0)
        )
        assert _counter_value(krab_voice_gateway_chars_total) - chars_before == pytest.approx(
            len("привет")
        )

    @pytest.mark.asyncio
    async def test_http_error_records_error(self) -> None:
        client = VoiceGatewayClient(base_url="http://x", api_key="", timeout_sec=1.0)
        before = _counter_value(krab_voice_gateway_requests_total, outcome="error")
        with patch.object(client, "_request_json", return_value=(500, {}, "")):
            result = await client.session_tts("s1", text="hi", voice="v", style="n")
        assert result["ok"] is False
        assert (
            _counter_value(krab_voice_gateway_requests_total, outcome="error") - before
            == pytest.approx(1.0)
        )

    @pytest.mark.asyncio
    async def test_network_timeout_records_timeout(self) -> None:
        client = VoiceGatewayClient(base_url="http://x", api_key="", timeout_sec=1.0)
        before = _counter_value(krab_voice_gateway_requests_total, outcome="timeout")
        with patch.object(client, "_request_json", return_value=(None, {}, "request timed out")):
            result = await client.session_tts("s1", text="hi", voice="v", style="n")
        assert result["ok"] is False
        assert (
            _counter_value(krab_voice_gateway_requests_total, outcome="timeout") - before
            == pytest.approx(1.0)
        )
