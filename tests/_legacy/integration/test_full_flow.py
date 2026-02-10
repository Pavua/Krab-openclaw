
import pytest
import asyncio
import sys
import os
sys.path.append(os.getcwd()) # Ensure src is importable
from unittest.mock import AsyncMock, MagicMock, patch
from src.userbot_bridge import KraabUserbot
from pyrogram.types import Message, Chat, User
from pyrogram import enums

@pytest.mark.asyncio
async def test_full_message_flow():
    """
    Simulates a full message processing flow:
    User Message -> Processing -> AI Response (Mock) -> Message Splitting -> Reply
    """
    # 1. Setup Mock Userbot
    # Patch Client so it doesn't try to connect to Telegram
    with patch("src.userbot_bridge.Client") as MockClient:
        bot = KraabUserbot()
        bot.client = AsyncMock() # Mock the client instance
        bot.me = MagicMock()
        bot.me.id = 123456789
        
        # Mock dependencies
        bot.client.send_chat_action = AsyncMock()
        bot.client.send_voice = AsyncMock()
        bot.client.download_media = AsyncMock(return_value=None) # No photo for now
        
        # 2. Simulate Incoming Message
        mock_msg = AsyncMock(spec=Message)
        # Mock attributes must be set on the instance, not the class/spec
        mock_msg.chat = MagicMock()
        mock_msg.chat.id = 987654321
        mock_msg.chat.type = enums.ChatType.PRIVATE
        mock_msg.text = "Краб, расскажи длинную историю"
        
        mock_msg.from_user = MagicMock()
        mock_msg.from_user.id = 123456789 # Self message
        mock_msg.from_user.username = "owner"
        mock_msg.from_user.is_bot = False
        
        # Missing attributes that caused crash
        mock_msg.reply_to_message = None
        mock_msg.caption = None
        mock_msg.photo = None
        
        # Setup reply/edit mocks
        mock_msg.edit = AsyncMock()
        mock_msg.reply = AsyncMock()
        
        # 3. Mock OpenClaw Client to return a HUGE response
        huge_response = "A" * 6000 # 6000 chars (exceeds 4096 limit)
        
        # We need to mock the async generator of send_message_stream
        async def mock_stream(*args, **kwargs):
            yield huge_response
            
        with patch("src.userbot_bridge.openclaw_client.send_message_stream", side_effect=mock_stream):
            
            # 4. Execute _process_message
            await bot._process_message(mock_msg)
            
            # 5. Verify Results
            
            # Verify chat action was sent
            bot.client.send_chat_action.assert_awaited()
            
            # Verify message was split and sent in parts
            # Logic: 
            # 1. await message.edit(parts[0])
            # 2. loop: await message.reply(part)
            
            # Check edit call (first chunk)
            assert mock_msg.edit.call_count >= 1
            args, _ = mock_msg.edit.call_args_list[-1]
            content = args[0]
            assert len(content) <= 4096
            assert "AAAA" in content
            
            # Check reply call (second chunk)
            mock_msg.reply.assert_awaited()
            args_reply, _ = mock_msg.reply.call_args_list[0]
            content_reply = args_reply[0]
            assert len(content_reply) <= 4096
            assert "AAAA" in content_reply # Should contain the overflow
            
            print("\n✅ Verification Successful: Long message was split and sent correctly.")

if __name__ == "__main__":
    # Allow running directly
    loop = asyncio.new_event_loop()
    loop.run_until_complete(test_full_message_flow())
