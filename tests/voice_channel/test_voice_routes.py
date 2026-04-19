# -*- coding: utf-8 -*-
"""
Tests for voice_routes FastAPI router (VA Phase 1.4).

Coverage:
1) GET /v1/voice/status — 200 when handler not initialized
2) GET /v1/voice/status — reports active sessions when handler is set
3) POST /v1/voice/message — returns SSE stream with DONE sentinel
"""

from __future__ import annotations

from typing import List
from unittest.mock import MagicMock

import pytest

# FastAPI TestClient requires httpx; guarded import.
try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

import src.voice_channel.voice_routes as voice_routes_module
from src.voice_channel.voice_routes import router

# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_handler():
    """Reset global handler between tests."""
    voice_routes_module._handler = None
    yield
    voice_routes_module._handler = None


@pytest.fixture
def app():
    """Minimal FastAPI app with voice router."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


def _make_mock_handler(tokens: List[str] | None = None):
    """Create a mock VoiceChannelHandler with controllable stream."""
    tokens = tokens or ["Привет", " мир"]
    handler = MagicMock()
    handler.session_count.return_value = 0

    async def _gen(*args, **kwargs):
        for tok in tokens:
            yield tok

    handler.handle_voice_message.return_value = _gen()
    return handler


# ─── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="fastapi not installed")
class TestVoiceStatusEndpoint:
    """GET /v1/voice/status"""

    def test_status_not_initialized(self, client):
        resp = client.get("/v1/voice/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["handler_initialized"] is False
        assert data["status"] == "not_initialized"
        assert data["active_sessions"] == 0

    def test_status_initialized(self, client):
        handler = _make_mock_handler()
        handler.session_count.return_value = 2
        voice_routes_module.set_handler(handler)

        resp = client.get("/v1/voice/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["handler_initialized"] is True
        assert data["active_sessions"] == 2
        assert data["status"] == "ok"


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="fastapi not installed")
class TestVoiceMessageEndpoint:
    """POST /v1/voice/message"""

    def test_503_when_not_initialized(self, client):
        resp = client.post(
            "/v1/voice/message",
            json={"chat_id": "s1", "text": "hello", "language": "ru"},
        )
        assert resp.status_code == 503

    def test_sse_stream_contains_done(self, client):
        handler = _make_mock_handler(tokens=["tok1", "tok2"])
        voice_routes_module.set_handler(handler)

        resp = client.post(
            "/v1/voice/message",
            json={"chat_id": "s1", "text": "привет", "language": "ru"},
        )
        assert resp.status_code == 200
        body = resp.text
        assert "[DONE]" in body

    def test_sse_stream_contains_tokens(self, client):
        handler = _make_mock_handler(tokens=["Привет", " мир"])
        voice_routes_module.set_handler(handler)

        resp = client.post(
            "/v1/voice/message",
            json={"chat_id": "s2", "text": "test", "language": "ru"},
        )
        body = resp.text
        # Each token should appear as a JSON object in a data: line
        assert "Привет" in body
        assert " мир" in body

    def test_422_on_empty_text(self, client):
        handler = _make_mock_handler()
        voice_routes_module.set_handler(handler)

        resp = client.post(
            "/v1/voice/message",
            json={"chat_id": "s3", "text": "   ", "language": "ru"},
        )
        assert resp.status_code == 422
