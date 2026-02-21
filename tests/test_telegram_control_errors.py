# -*- coding: utf-8 -*-
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.handlers.telegram_control import TelegramControlHandler

@pytest.mark.asyncio
async def test_control_error_formatting():
    """Проверка унифицированного формата ошибок."""
    deps = {
        "safe_handler": lambda x: x,
        "black_box": MagicMock(),
        "router": MagicMock()
    }
    handler = TelegramControlHandler(deps)
    message = AsyncMock()
    
    await handler._reply_control_error(
        message, 
        error_code="TEST_CODE", 
        explanation="Test explanation", 
        next_step="Do something"
    )
    
    expected_text = "❌ **Ошибка [TEST_CODE]**\n\nTest explanation\n\n💡 **Что делать:**\nDo something"
    message.reply_text.assert_called_once_with(expected_text)

@pytest.mark.asyncio
async def test_run_summary_access_denied():
    """Проверка ошибки доступа в _run_summary."""
    deps = {
        "safe_handler": lambda x: x,
        "black_box": MagicMock(),
        "router": MagicMock()
    }
    handler = TelegramControlHandler(deps)
    handler._is_target_allowed = MagicMock(return_value=False)
    
    client = AsyncMock()
    message = AsyncMock()
    
    await handler._run_summary(client, message, 123, "Test Chat", 100, "")
    
    # Проверяем, что вызвана ошибка доступа
    args, _ = message.reply_text.call_args
    assert "CTRL_ACCESS_DENIED" in args[0]
    assert "запрещен политикой безопасности" in args[0]
