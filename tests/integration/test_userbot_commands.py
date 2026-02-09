
import sys
import os
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure src is in path
sys.path.append(os.getcwd())

with patch('src.userbot_bridge.Client') as MockClient:
    mock_client_instance = MockClient.return_value
    mock_client_instance.on_message.return_value = lambda x: x 

    from src.userbot_bridge import KraabUserbot

    @pytest.mark.asyncio
    async def test_diagnose_command():
        with patch('src.userbot_bridge.Client') as MockClientInner:
            client_instance = MockClientInner.return_value
            client_instance.on_message.return_value = lambda x: x
            
            bot = KraabUserbot()
            bot.client = AsyncMock()
            bot.me = MagicMock()
            bot.me.id = 12345
            
            message = AsyncMock()
            message.from_user.id = 12345
            message.reply = AsyncMock()
            processing_msg = AsyncMock()
            message.reply.return_value = processing_msg
            
            # Mock httpx to avoid real network calls during unit test
            # But the user asked for verification. Ideally we want integration test.
            # Allowing network calls is better for "diagnose" if backend is up.
            # But here we are testing the BOT COMMAND logic.
            
            # Let's mock the httpx calls to ensure deterministic output for THIS test.
            with patch('httpx.AsyncClient') as MockHttp:
                 client_mock = AsyncMock()
                 MockHttp.return_value.__aenter__.return_value = client_mock
                 # Status 200 for both calls
                 client_mock.get.return_value.status_code = 200
                 
                 await bot._handle_diagnose(message)
            
            # Verify reply sent
            message.reply.assert_called()
            args = message.reply.call_args[0][0]
            assert "Запускаю диагностику" in args
            
            # Verify status update (edit)
            processing_msg.edit.assert_called()
            report = processing_msg.edit.call_args[0][0]
            assert "Config:" in report
            assert "OpenClaw: ✅ OK" in report

    @pytest.mark.asyncio
    async def test_web_screen_command():
         with patch('src.userbot_bridge.Client') as MockClientInner:
            client_instance = MockClientInner.return_value
            client_instance.on_message.return_value = lambda x: x 
            
            bot = KraabUserbot()
            bot.client = AsyncMock()
            
            message = AsyncMock()
            message.text = "!web screen"
            message.reply_photo = AsyncMock()
            message.reply = AsyncMock()
            
            # Patch src.web_session.web_manager because _handle_web imports it from .web_session
            # Since .web_session resolves to src.web_session, we patch that.
            with patch('src.web_session.web_manager') as mock_wm, patch('os.remove') as mock_remove:
                mock_wm.take_screenshot = AsyncMock(return_value="/tmp/test.png")
                mock_wm.start = AsyncMock()
                
                await bot._handle_web(message)
                
                mock_wm.take_screenshot.assert_called()
                message.reply_photo.assert_called_with("/tmp/test.png")
                mock_remove.assert_called_with("/tmp/test.png")

