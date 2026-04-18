"""Tests for !uptime command enhancement."""
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_format_uptime_str_seconds():
    """Test formatting uptime in seconds."""
    from src.handlers.command_handlers import _format_uptime_str

    result = _format_uptime_str(45)
    assert result == "0м"  # 45 seconds = 0 minutes


def test_format_uptime_str_minutes():
    """Test formatting uptime in minutes."""
    from src.handlers.command_handlers import _format_uptime_str

    result = _format_uptime_str(180)  # 3 minutes
    assert result == "3м"


def test_format_uptime_str_hours():
    """Test formatting uptime in hours."""
    from src.handlers.command_handlers import _format_uptime_str

    result = _format_uptime_str(7200)  # 2 hours
    assert result == "2ч 0м"


def test_format_uptime_str_days():
    """Test formatting uptime in days and hours."""
    from src.handlers.command_handlers import _format_uptime_str

    result = _format_uptime_str(90061)  # 1д 1ч 1м
    assert "1д" in result and "1ч" in result


@pytest.mark.asyncio
async def test_handle_uptime_basic_response():
    """Test that handle_uptime returns a formatted message."""
    from src.handlers.command_handlers import handle_uptime

    # Mock bot and message
    bot = MagicMock()
    bot._session_start_time = time.time() - 3600  # 1 hour ago

    message = AsyncMock()
    message.reply = AsyncMock()

    # Mock httpx responses
    with patch("src.handlers.command_handlers.httpx.AsyncClient") as mock_client:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"uptime_seconds": 7200}

        mock_client_instance = AsyncMock()
        mock_client_instance.__aenter__.return_value = mock_client_instance
        mock_client_instance.__aexit__.return_value = None
        mock_client_instance.get = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_client_instance

        await handle_uptime(bot, message)

        # Verify message was sent
        assert message.reply.called


@pytest.mark.asyncio
async def test_handle_uptime_gateway_unreachable():
    """Test graceful handling when OpenClaw gateway is unreachable."""
    from src.handlers.command_handlers import handle_uptime

    bot = MagicMock()
    bot._session_start_time = time.time()
    message = AsyncMock()
    message.reply = AsyncMock()

    with patch("src.handlers.command_handlers.httpx.AsyncClient") as mock_client:
        mock_client_instance = AsyncMock()
        mock_client_instance.__aenter__.return_value = mock_client_instance
        mock_client_instance.__aexit__.return_value = None
        mock_client_instance.get = AsyncMock(side_effect=Exception("Connection failed"))
        mock_client.return_value = mock_client_instance

        await handle_uptime(bot, message)

        # Should call message.reply without raising
        assert message.reply.called
        call_args = message.reply.call_args[0][0]
        assert "OpenClaw: ❌" in call_args


@pytest.mark.asyncio
async def test_handle_uptime_lm_studio_offline():
    """Test LM Studio offline status handling."""
    from src.handlers.command_handlers import handle_uptime

    bot = MagicMock()
    bot._session_start_time = time.time()
    message = AsyncMock()
    message.reply = AsyncMock()

    with patch("src.handlers.command_handlers.httpx.AsyncClient") as mock_client:
        mock_client_instance = AsyncMock()
        mock_client_instance.__aenter__.return_value = mock_client_instance
        mock_client_instance.__aexit__.return_value = None

        # First call (OpenClaw) succeeds, second call (LM Studio) fails
        mock_client_instance.get = AsyncMock(side_effect=Exception("LM Studio down"))
        mock_client.return_value = mock_client_instance

        await handle_uptime(bot, message)

        call_args = message.reply.call_args[0][0]
        assert "LM Studio: 💤" in call_args
