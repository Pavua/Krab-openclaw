
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from src.userbot_bridge import KraabUserbot
from pyrogram import enums

@pytest.fixture
async def bot():
    with patch("src.userbot_bridge.Client"), \
         patch("src.userbot_bridge.model_manager"), \
         patch("src.userbot_bridge.openclaw_client") as mock_oc:
        
        bot = KraabUserbot()
        bot.me = MagicMock()
        bot.me.id = 12345
        bot.me.username = "yung_nagato"
        bot.client.send_chat_action = AsyncMock()
        bot.client.is_connected = True
        
        # Setup common mock behavior for openclaw_client
        async def mock_stream(*args, **kwargs):
            yield "Test response"
        mock_oc.send_message_stream.return_value = mock_stream()
        
        return bot

@pytest.mark.asyncio
async def test_trigger_logic(bot):
    assert bot._is_trigger("!краб привет") is True
    assert bot._is_trigger("Краб, как дела?") is True
    assert bot._is_trigger("Просто сообщение") is False

@pytest.mark.asyncio
async def test_clean_text(bot):
    assert bot._get_clean_text("!краб Привет") == "Привет"
    assert bot._get_clean_text("Краб, Привет") == "Привет"
    assert bot._get_clean_text("краб привет") == "привет"

@pytest.mark.asyncio
async def test_process_message_p0lrd_private(bot):
    # Mock message from p0lrd in private chat
    message = AsyncMock()
    message.from_user.id = 999
    message.from_user.username = "p0lrd"
    message.text = "Привет без триггера"
    message.chat.type = enums.ChatType.PRIVATE
    message.chat.id = 999
    
    with patch("src.userbot_bridge.openclaw_client") as mock_oc:
        async def mock_stream(*args, **kwargs):
            yield "Test response"
        mock_oc.send_message_stream.return_value = mock_stream()
        
        await bot._process_message(message)
        
        mock_oc.send_message_stream.assert_called()
        message.read.assert_called()

@pytest.mark.asyncio
async def test_process_message_stranger_private(bot):
    # Mock message from stranger in private chat
    message = AsyncMock()
    message.from_user.id = 888
    message.from_user.username = "stranger"
    message.text = "Привет без триггера"
    message.chat.type = enums.ChatType.PRIVATE
    
    # stranger is not in allowed users
    with patch("src.userbot_bridge.config") as mock_cfg, \
         patch("src.userbot_bridge.openclaw_client") as mock_oc:
        mock_cfg.ALLOWED_USERS = ["pablito", "p0lrd"] # 'stranger' not here
        mock_cfg.TRIGGER_PREFIXES = ["!краб"]
        
        await bot._process_message(message)
        
        mock_oc.send_message_stream.assert_not_called()

@pytest.mark.asyncio
async def test_process_message_stranger_trigger(bot):
    # Stranger with trigger !краб - SHOULD respond if they are in ALLOWED_USERS
    message = AsyncMock()
    message.from_user.id = 777
    message.from_user.username = "allowed_stranger"
    message.text = "!краб привет"
    message.chat.type = enums.ChatType.PRIVATE
    
    with patch("src.userbot_bridge.config") as mock_cfg, \
         patch("src.userbot_bridge.openclaw_client") as mock_oc:
        mock_cfg.ALLOWED_USERS = ["allowed_stranger"]
        mock_cfg.TRIGGER_PREFIXES = ["!краб"]
        
        async def mock_stream(*args, **kwargs):
            yield "Test response"
        mock_oc.send_message_stream.return_value = mock_stream()
        
        await bot._process_message(message)
        
        mock_oc.send_message_stream.assert_called()
