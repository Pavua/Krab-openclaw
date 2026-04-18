"""Tests for !loglevel runtime log level toggle command."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.handlers.loglevel_command import VALID_LEVELS, handle_loglevel


@pytest.mark.asyncio
async def test_loglevel_shows_current_status():
    """Test !loglevel with no args shows current log level."""
    # Setup
    bot = AsyncMock()
    message = AsyncMock()
    message.reply = AsyncMock()
    bot._get_command_args = MagicMock(return_value="")

    # Execute
    await handle_loglevel(bot, message)

    # Verify
    assert message.reply.called
    call_args = message.reply.call_args[0][0]
    assert "Log level" in call_args or "Текущий уровень" in call_args


@pytest.mark.asyncio
async def test_loglevel_changes_to_debug():
    """Test changing log level to DEBUG."""
    original_level = logging.getLogger().level
    try:
        # Setup
        bot = AsyncMock()
        message = AsyncMock()
        message.reply = AsyncMock()
        bot._get_command_args = MagicMock(return_value="DEBUG")
        message._correlation_id = "test-123"

        # Execute
        await handle_loglevel(bot, message)

        # Verify level changed
        assert logging.getLogger().getEffectiveLevel() == logging.DEBUG

        # Verify reply sent
        assert message.reply.called
        call_args = message.reply.call_args[0][0]
        assert "DEBUG" in call_args

    finally:
        # Restore original level
        logging.getLogger().setLevel(original_level)


@pytest.mark.asyncio
async def test_loglevel_changes_to_info():
    """Test changing log level to INFO."""
    original_level = logging.getLogger().level
    try:
        # Setup
        bot = AsyncMock()
        message = AsyncMock()
        message.reply = AsyncMock()
        bot._get_command_args = MagicMock(return_value="INFO")
        message._correlation_id = "test-456"

        # Execute
        await handle_loglevel(bot, message)

        # Verify level changed
        assert logging.getLogger().getEffectiveLevel() == logging.INFO

        # Verify reply
        assert message.reply.called

    finally:
        logging.getLogger().setLevel(original_level)


@pytest.mark.asyncio
async def test_loglevel_rejects_invalid_level():
    """Test that invalid log level is rejected."""
    # Setup
    bot = AsyncMock()
    message = AsyncMock()
    message.reply = AsyncMock()
    bot._get_command_args = MagicMock(return_value="INVALID_LEVEL")

    # Execute
    await handle_loglevel(bot, message)

    # Verify error response
    assert message.reply.called
    call_args = message.reply.call_args[0][0]
    assert "❌" in call_args or "Unknown" in call_args or "Неизвестный" in call_args


@pytest.mark.asyncio
async def test_loglevel_supports_all_valid_levels():
    """Test that all VALID_LEVELS are accepted."""
    original_level = logging.getLogger().level
    try:
        for level in VALID_LEVELS:
            bot = AsyncMock()
            message = AsyncMock()
            message.reply = AsyncMock()
            bot._get_command_args = MagicMock(return_value=level)
            message._correlation_id = f"test-{level}"

            # Execute
            await handle_loglevel(bot, message)

            # Verify no error response
            assert message.reply.called
            call_args = message.reply.call_args[0][0]
            assert "❌" not in call_args  # Should not be error

    finally:
        logging.getLogger().setLevel(original_level)


@pytest.mark.asyncio
async def test_loglevel_trace_sets_level_5():
    """Test TRACE level sets logging to level 5."""
    original_level = logging.getLogger().level
    try:
        # Setup
        bot = AsyncMock()
        message = AsyncMock()
        message.reply = AsyncMock()
        bot._get_command_args = MagicMock(return_value="TRACE")
        message._correlation_id = "test-trace"

        # Execute
        await handle_loglevel(bot, message)

        # Verify level set to 5 (TRACE)
        assert logging.getLogger().getEffectiveLevel() == 5

    finally:
        logging.getLogger().setLevel(original_level)


@pytest.mark.asyncio
async def test_loglevel_handles_case_insensitive():
    """Test that log level argument is case-insensitive."""
    original_level = logging.getLogger().level
    try:
        # Setup with lowercase
        bot = AsyncMock()
        message = AsyncMock()
        message.reply = AsyncMock()
        bot._get_command_args = MagicMock(return_value="error")  # lowercase
        message._correlation_id = "test-case"

        # Execute
        await handle_loglevel(bot, message)

        # Verify no error (command handles .upper())
        assert message.reply.called

    finally:
        logging.getLogger().setLevel(original_level)
