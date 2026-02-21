import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch
from src.handlers.telegram_control import TelegramControlHandler

@pytest.mark.asyncio
async def test_summaryx_throttling_logic():
    """Проверка логики кулдауна (R8)."""
    # Setup mocks
    summary_service = AsyncMock()
    summary_service.clamp_limit.return_value = 50
    
    deps = {
        "black_box": MagicMock(),
        "resolver": AsyncMock(),
        "summary_service": summary_service,
        "safe_handler": lambda x: x,
        "router": MagicMock()
    }
    handler = TelegramControlHandler(deps)
    handler.summary_cooldown_sec = 10 # Короткий кулдаун для теста
    
    # 1. Первый вызов (успех)
    message = AsyncMock()
    message.text = "!summaryx 50"
    message.from_user.id = 111
    message.chat.type = "private"
    
    with patch("src.handlers.telegram_control.is_superuser", return_value=False):
        # Мокаем _run_summary чтобы не уходить вглубь
        handler._run_summary = AsyncMock()
        
        # Вызываем корректный метод
        await handler.summaryx_command(None, message)
        
        assert 111 in handler._summary_cooldowns
        
        # 2. Второй вызов (throttled)
        handler._reply_control_error = AsyncMock()
        await handler.summaryx_command(None, message)
        
        # Проверяем что вызван _reply_control_error с кодом CTRL_THROTTLED
        handler._reply_control_error.assert_called_once()
        args, kwargs = handler._reply_control_error.call_args
        assert kwargs["error_code"] == "CTRL_THROTTLED"

    # 3. Вызов от суперюзера (bypass)
    with patch("src.handlers.telegram_control.is_superuser", return_value=True):
        handler._reply_control_error.reset_mock()
        await handler.summaryx_command(None, message)
        
        # Суперюзер не должен получать CTRL_THROTTLED
        for call_args in handler._reply_control_error.call_args_list:
             assert call_args.kwargs.get("error_code") != "CTRL_THROTTLED"

@pytest.mark.asyncio
async def test_summaryx_invalid_params_code():
    """Проверка кода ошибки при неверных параметрах (R8)."""
    handler = TelegramControlHandler({"safe_handler": lambda x: x, "black_box": MagicMock(), "router": MagicMock()})
    handler._reply_control_error = AsyncMock()
    
    message = AsyncMock()
    message.text = "!summaryx abc" # Ошибка парсинга числа
    message.from_user.id = 222
    
    with patch("src.handlers.telegram_control.is_superuser", return_value=True):
        await handler.summaryx_command(None, message)
        
        handler._reply_control_error.assert_called_once()
        assert handler._reply_control_error.call_args.kwargs["error_code"] == "CTRL_INVALID_PARAMS"
