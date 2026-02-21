import pytest
from unittest.mock import AsyncMock, MagicMock
from src.handlers.telegram_control import TelegramControlHandler

@pytest.mark.asyncio
async def test_summaryx_tech_block():
    """Проверка наличия тех-блока в ответе !summaryx."""
    # Setup mocks
    client = AsyncMock()
    client.get_chat = AsyncMock()
    
    # get_chat_history возвращает асинхронный итератор
    mock_history = MagicMock()
    mock_history.__aiter__.return_value = []
    client.get_chat_history.return_value = mock_history
    
    message = AsyncMock()
    message.chat.id = 123
    message.from_user.id = 456
    
    summary_service = AsyncMock()
    summary_service.summarize.return_value = "Это тестовое саммари."
    
    deps = {
        "black_box": MagicMock(),
        "telegram_chat_resolver": AsyncMock(),
        "telegram_summary_service": summary_service,
        "safe_handler": lambda x: x
    }
    handler = TelegramControlHandler(deps)
    handler._is_target_allowed = MagicMock(return_value=True)
    
    # Execute
    await handler._run_summary(
        client=client,
        message=message,
        target_chat_id=789,
        target_title="Test Chat",
        limit=50,
        focus="AI"
    )
    
    # Verify notification.edit_text was called with tech info
    # In my implementation, notification is the result of message.reply_text
    notification = message.reply_text.return_value
    args, kwargs = notification.edit_text.call_args
    sent_text = args[0]
    
    assert "--- [Tech]" in sent_text
    assert "ID: `789`" in sent_text
    assert "Limit: `50`" in sent_text
    assert "Focus: `AI`" in sent_text
    assert "Prov: `AI.Router`" in sent_text
