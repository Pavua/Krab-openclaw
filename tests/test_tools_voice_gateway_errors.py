# -*- coding: utf-8 -*-
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from src.handlers.tools import register_handlers

class MockMessage:
    def __init__(self, text, chat_id=-100123):
        self.text = text
        self.message_id = 1
        self.chat = MagicMock()
        self.chat.id = chat_id
        self.command = text.split()
        self.reply_text = AsyncMock()
        self.edit_text = AsyncMock()

@pytest.fixture
def deps_mock():
    vg = MagicMock()
    vg.start_session = AsyncMock()
    vg.stop_session = AsyncMock()
    vg.get_session = AsyncMock()
    vg.set_notify_mode = AsyncMock()
    vg.set_translation_mode = AsyncMock()
    
    return {
        "voice_gateway_client": vg,
        "perceptor": MagicMock(),
        "safe_handler": lambda x: x,
        "router": MagicMock(),
        "config_manager": MagicMock(),
    }

def setup_handlers(deps):
    app = MagicMock()
    handlers = {}
    def mock_on_message(filters=None, group=0):
        def decorator(f):
            handlers[f.__name__] = f
            return f
        return decorator
    app.on_message = mock_on_message
    register_handlers(app, deps)
    return handlers

@pytest.mark.asyncio
async def test_error_helper_unavailable(deps_mock):
    """Проверка ошибки 'unavailable'."""
    deps_mock["voice_gateway_client"] = None
    handlers = setup_handlers(deps_mock)
    
    msg = MockMessage("!callstatus")
    await handlers["callstatus_command"](None, msg)
    
    msg.reply_text.assert_called()
    args = msg.reply_text.call_args[0][0]
    assert "VGW_UNAVAILABLE" in args
    assert "Connection Refused" in args

@pytest.mark.asyncio
async def test_error_helper_no_session(deps_mock):
    """Проверка ошибки 'no_session'."""
    handlers = setup_handlers(deps_mock)
    
    with patch("src.handlers.tools.active_call_sessions", {}):
        msg = MockMessage("!callstatus")
        await handlers["callstatus_command"](None, msg)
        
        msg.reply_text.assert_called()
        args = msg.reply_text.call_args[0][0]
        assert "VGW_SESSION_ERR" in args
        assert "Активная voice-сессия не найдена" in args

@pytest.mark.asyncio
async def test_error_helper_markdown_safety(deps_mock):
    """Проверка экранирования бэктиков в деталях ошибки."""
    handlers = setup_handlers(deps_mock)
    vg = deps_mock["voice_gateway_client"]
    vg.get_session.return_value = {"ok": False, "error": "Error with `backticks`"}
    
    with patch("src.handlers.tools.active_call_sessions", {-100123: "sid-123"}):
        msg = MockMessage("!callstatus")
        await handlers["callstatus_command"](None, msg)
        
        msg.reply_text.assert_called_once()
        args = msg.reply_text.call_args[0][0]
        assert "Error with 'backticks'" in args
        assert "VGW_INTERNAL" in args

@pytest.mark.asyncio
async def test_error_helper_update_fail_safety(deps_mock):
    """Проверка кода VGW_UPDATE_ERR при сбое !calllang."""
    handlers = setup_handlers(deps_mock)
    vg = deps_mock["voice_gateway_client"]
    vg.set_translation_mode.return_value = {"ok": False, "error": "Fail `here`"}
    
    with patch("src.handlers.tools.active_call_sessions", {-100123: "sid-123"}):
        msg = MockMessage("!calllang auto_to_ru")
        await handlers["calllang_command"](None, msg)
        
        msg.reply_text.assert_called_once()
        args = msg.reply_text.call_args[0][0]
        assert "VGW_UPDATE_ERR" in args
        assert "Fail 'here'" in args
