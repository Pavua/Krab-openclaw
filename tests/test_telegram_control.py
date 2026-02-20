
# -*- coding: utf-8 -*-
"""
Тесты для TelegramControlHandler (Unit).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from pyrogram import enums
from pyrogram.errors import ChannelPrivate, PeerIdInvalid, UsernameInvalid

# Import implementation
from src.handlers.telegram_control import TelegramControlHandler
from src.core.telegram_chat_resolver import ChatTarget

# Mock Pyrogram bits
class MockMessage:
    def __init__(self, text, chat_type=enums.ChatType.PRIVATE, user_id=999):
        self.text = text
        self.chat = MagicMock()
        self.chat.id = 12345
        self.chat.type = chat_type
        self.chat.title = "Test Chat"
        self.from_user = MagicMock()
        self.from_user.id = user_id
        self.reply_text = AsyncMock()
        self.edit_text = AsyncMock()
        self.delete = AsyncMock()

class MockClient:
    def __init__(self):
        self.get_chat = AsyncMock()

class MockResolver:
    def __init__(self):
        self.max_picker_items = 5
        self.resolve = AsyncMock()
        self.get_recent_chats = MagicMock(return_value=[
            {"chat_id": 1001, "title": "Group A", "last_ts": "2026-01-01"},
            {"chat_id": 1002, "title": "Channel B", "last_ts": "2026-01-02"},
        ])

class MockSummaryService:
    def __init__(self):
        self.summarize = AsyncMock(return_value="This is a summary.")
        self.clamp_limit = MagicMock(return_value=50)

@pytest.fixture
def deps():
    return {
        "safe_handler": lambda x: x,
        "router": MagicMock(),
        "black_box": MagicMock(),
        "telegram_chat_resolver": MockResolver(),
        "telegram_summary_service": MockSummaryService(),
    }

@pytest.mark.asyncio
async def test_summaryx_picker_private(deps):
    """Проверка, что в ЛС без аргументов target показывается picker."""
    handler = TelegramControlHandler(deps)
    client = MockClient()
    msg = MockMessage("!summaryx 50", chat_type=enums.ChatType.PRIVATE)
    
    await handler.summaryx_command(client, msg)
    
    msg.reply_text.assert_called_once()
    args, kwargs = msg.reply_text.call_args
    assert "Выберите чат" in args[0]
    assert "reply_markup" in kwargs
    assert len(handler.picker_state) == 1

@pytest.mark.asyncio
async def test_summaryx_target_resolved(deps):
    """Проверка, что если target указан, resolution запускается."""
    handler = TelegramControlHandler(deps)
    client = MockClient()
    msg = MockMessage("!summaryx 50 @test", chat_type=enums.ChatType.PRIVATE)
    
    deps["telegram_chat_resolver"].resolve.return_value = ChatTarget(
        chat_id=-1001, title="Resolved", chat_type="channel"
    )
    
    await handler.summaryx_command(client, msg)
    
    # 1. reply_text called (notification)
    assert msg.reply_text.called
    notification_coro = msg.reply_text.return_value
    # Since reply_text is awaited, we get the result. 
    # But usually Mock reply_text returns an AsyncMock which we can inspect.
    # Here checking arguments of call is enough.
    args, _ = msg.reply_text.call_args
    assert "Анализ 50 сообщений `Resolved`" in args[0]

@pytest.mark.asyncio
async def test_summaryx_access_denied(deps):
    """Проверка обработки ошибок доступа (get_chat failure)."""
    handler = TelegramControlHandler(deps)
    client = MockClient()
    client.get_chat.side_effect = ChannelPrivate()
    
    msg = MockMessage("!summaryx 50 @private", chat_type=enums.ChatType.PRIVATE)
    
    deps["telegram_chat_resolver"].resolve.return_value = ChatTarget(
        chat_id=-100666, title="Private", chat_type="channel"
    )
    
    await handler.summaryx_command(client, msg)
    
    args, _ = msg.reply_text.call_args
    assert "Ошибка доступа к чату `Private`" in args[0]

@pytest.mark.asyncio
async def test_summaryx_invalid_args(deps):
    """Проверка валидации аргументов."""
    handler = TelegramControlHandler(deps)
    msg = MockMessage("!summaryx") # No limit
    await handler.summaryx_command(MockClient(), msg)
    
    args, _ = msg.reply_text.call_args
    assert "Формат:" in args[0]

@pytest.mark.asyncio
async def test_chatid_command(deps):
    """Проверка команды !chatid."""
    handler = TelegramControlHandler(deps)
    client = MockClient()
    msg = MockMessage("!chatid", chat_type=enums.ChatType.PRIVATE)
    
    await handler.chatid_command(client, msg)
    
    assert msg.reply_text.called
    args, _ = msg.reply_text.call_args
    assert "`12345` | private | Test Chat" in args[0]

