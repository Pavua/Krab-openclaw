# -*- coding: utf-8 -*-
"""Тесты нормализации Voice event schema."""

from src.core.voice_gateway_client import VoiceGatewayClient


def test_normalize_stream_event_basic_mapping() -> None:
    raw = {
        "type": "stt.partial",
        "data": {
            "session_id": "vs_123",
            "text": "hola",
            "latency_ms": 140,
            "source": "twilio_media",
        },
    }
    event = VoiceGatewayClient.normalize_stream_event(raw)
    assert event["schema_version"] == "1.0"
    assert event["session_id"] == "vs_123"
    assert event["event_type"] == "stt.partial"
    assert event["source"] == "twilio_media"
    assert event["latency_ms"] == 140
    assert event["severity"] == "info"


def test_normalize_stream_event_error_severity() -> None:
    raw = {
        "type": "call.error",
        "data": {
            "id": "vs_404",
            "message": "upstream timeout",
        },
    }
    event = VoiceGatewayClient.normalize_stream_event(raw)
    assert event["session_id"] == "vs_404"
    assert event["severity"] == "high"


def test_normalize_stream_event_defaults() -> None:
    event = VoiceGatewayClient.normalize_stream_event({})
    assert event["event_type"] == "unknown"
    assert event["source"] == "voice_gateway"
    assert event["severity"] in {"low", "info", "high"}
    assert event["latency_ms"] == 0
