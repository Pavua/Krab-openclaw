
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

    from src.handlers import handle_diagnose, handle_web
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

            with patch('httpx.AsyncClient') as MockHttp:
                client_mock = AsyncMock()
                MockHttp.return_value.__aenter__.return_value = client_mock
                client_mock.get = AsyncMock(return_value=MagicMock(status_code=200))

                await handle_diagnose(bot, message)

            message.reply.assert_called()
            args = message.reply.call_args[0][0]
            assert "Запускаю диагностику" in args

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

            with (
                patch('src.web_session.web_manager') as mock_wm,
                patch('os.remove') as mock_remove,
                patch('os.path.exists', return_value=True),
            ):
                mock_wm.take_screenshot = AsyncMock(return_value="/tmp/test.png")
                mock_wm.start = AsyncMock()

                await handle_web(bot, message)

                mock_wm.take_screenshot.assert_called()
                message.reply_photo.assert_called_with("/tmp/test.png")
                mock_remove.assert_called_with("/tmp/test.png")

