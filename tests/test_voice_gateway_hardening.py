
# -*- coding: utf-8 -*-
"""
Tests for Voice Gateway Hardening.
Covers offline scenarios and enhanced diagnostics responses.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.handlers.tools import register_handlers
from src.core.voice_gateway_client import VoiceGatewayClient

class MockMessage:
    def __init__(self, text, chat_id=-100123):
        self.text = text
        self.chat = MagicMock()
        self.chat.id = chat_id
        self.command = text.split()
        self.reply_text = AsyncMock()

@pytest.fixture
def deps_offline():
    return {
        "voice_gateway": None,
        "voice_gateway_client": None,
        "safe_handler": lambda x: x,
        "router": MagicMock(),
    }

@pytest.fixture
def deps_mock_online():
    client = MagicMock(spec=VoiceGatewayClient)
    client.get_session = AsyncMock()
    client.get_diagnostics = AsyncMock()
    return {
        "voice_gateway": client,
        "voice_gateway_client": client,
        "safe_handler": lambda x: x,
        "router": MagicMock(),
    }

def get_handlers(deps):
    app = MagicMock()
    handlers = {}
    def mock_on_message(filters=None, group=0):
        def decorator(f):
            handlers[f.__name__] = f
            return f
        return decorator
    app.on_message = mock_on_message
    # Mock other pyrogram decorators
    app.on_chat_member_updated = lambda: lambda f: f
    app.on_callback_query = lambda *args, **kwargs: lambda f: f
    
    register_handlers(app, deps)
    return handlers

@pytest.mark.asyncio
async def test_callstatus_uninitialized_client(deps_offline):
    handlers = get_handlers(deps_offline)
    msg = MockMessage("!callstatus")
    await handlers["callstatus_command"](None, msg)
    assert "не инициализирован" in msg.reply_text.call_args[0][0]

@pytest.mark.asyncio
async def test_callstatus_active_enhanced(deps_mock_online):
    handlers = get_handlers(deps_mock_online)
    client = deps_mock_online["voice_gateway"]
    client.get_session.return_value = {
        "ok": True,
        "result": {
            "id": "test-sid",
            "status": "active",
            "source": "mic",
            "translation_mode": "auto_to_ru"
        }
    }
    
    with patch("src.handlers.tools.active_call_sessions", {-100123: "test-sid"}):
        msg = MockMessage("!callstatus")
        await handlers["callstatus_command"](None, msg)
        
        args, _ = msg.reply_text.call_args
        assert "(🟢 OK)" in args[0]
        assert "source: `mic`" in args[0]

@pytest.mark.asyncio
async def test_calldiag_with_next_steps(deps_mock_online):
    handlers = get_handlers(deps_mock_online)
    client = deps_mock_online["voice_gateway"]
    client.get_diagnostics.return_value = {
        "ok": True,
        "result": {
            "session_id": "test-sid",
            "status": "active",
            "pipeline": {"cache_hits": 5, "cache_misses": 2},
            "counters": {"stt_partial": 10, "tts_ready": 8},
            "latency_ms": {"stt_partial": 120, "translation_partial": 300, "tts_ready": 450}
        }
    }
    
    with patch("src.handlers.tools.active_call_sessions", {-100123: "test-sid"}):
        msg = MockMessage("!calldiag")
        await handlers["calldiag_command"](None, msg)
        
        args, _ = msg.reply_text.call_args
        assert "💡 **Что делать дальше:**" in args[0]
        assert "stt.partial" in args[0]
        assert "tts.ready" in args[0]

@pytest.mark.asyncio
async def test_voice_gateway_server_error(deps_mock_online):
    """Test behavior when server returns an error response."""
    handlers = get_handlers(deps_mock_online)
    client = deps_mock_online["voice_gateway"]
    client.get_session.return_value = {"ok": False, "error": "HTTP 500: Internal Server Error"}
    
    with patch("src.handlers.tools.active_call_sessions", {-100123: "test-sid"}):
        msg = MockMessage("!callstatus")
        await handlers["callstatus_command"](None, msg)
        assert "Не удалось получить статус: HTTP 500" in msg.reply_text.call_args[0][0]
