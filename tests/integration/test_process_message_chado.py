"""
Integration tests: Chado blueprint wiring in _process_message.

Tests verify:
- group mention-only mode skips non-mention messages
- muted mode skips everything (non-command)
- DMs always processed
- !commands always processed in groups
- active mode listens to all
- ChatWindow.touch() called per message
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from unittest.mock import AsyncMock, MagicMock, patch

from pyrogram import enums
from pyrogram.types import Message

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_message(
    *,
    text: str = "",
    chat_type: enums.ChatType = enums.ChatType.PRIVATE,
    from_user_id: int = 111,
    reply_to_user_id: int = 0,
    is_bot: bool = False,
) -> MagicMock:
    """Build a mock Pyrogram Message."""
    msg = MagicMock(spec=Message)
    msg.text = text
    msg.caption = None
    msg.chat = MagicMock()
    msg.chat.id = 9999
    msg.chat.type = chat_type
    msg.from_user = MagicMock()
    msg.from_user.id = from_user_id
    msg.from_user.is_bot = is_bot
    msg.reply = AsyncMock()

    if reply_to_user_id:
        msg.reply_to_message = MagicMock()
        msg.reply_to_message.from_user = MagicMock()
        msg.reply_to_message.from_user.id = reply_to_user_id
    else:
        msg.reply_to_message = None

    return msg


def _make_bot() -> MagicMock:
    """Build minimal KraabUserbot mock."""
    with patch("src.userbot_bridge.Client"):
        from src.userbot_bridge import KraabUserbot
        bot = KraabUserbot()
    bot.client = AsyncMock()
    bot.me = MagicMock()
    bot.me.id = 42  # Krab's own user_id
    # Stub out heavy internal methods
    bot._process_message_serialized = AsyncMock()
    bot._get_chat_processing_lock = MagicMock(
        return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock())
    )
    bot._consume_batched_followup_message_id = MagicMock(return_value=False)
    bot._get_access_profile = MagicMock(return_value=MagicMock(is_trusted=True))
    bot._is_allowed_sender = MagicMock(return_value=True)
    bot.is_auto_translate_enabled = MagicMock(return_value=False)
    bot._refresh_chat_capabilities_background = AsyncMock()
    bot._log_background_task_exception_cb = MagicMock()
    # set krab identity
    from src.core.krab_identity import set_krab_user_id
    set_krab_user_id(42)
    return bot


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_group_mention_only_skips_without_mention():
    """Group chat without mention → skipped in mention-only mode."""
    from src.core.chat_filter_config import chat_filter_config

    chat_id = "9999"
    chat_filter_config.set_mode(chat_id, "mention-only")

    bot = _make_bot()
    msg = _make_message(
        text="просто сообщение без упоминания",
        chat_type=enums.ChatType.GROUP,
        from_user_id=555,
    )

    await bot._process_message(msg)

    # LLM should NOT be called
    bot._process_message_serialized.assert_not_awaited()

    # Cleanup
    chat_filter_config.reset(chat_id)


@pytest.mark.asyncio
async def test_group_muted_skips_all():
    """Muted chat → all non-command messages skipped."""
    from src.core.chat_filter_config import chat_filter_config

    chat_id = "9999"
    chat_filter_config.set_mode(chat_id, "muted")

    bot = _make_bot()
    msg = _make_message(
        text="Краб привет",  # mention present — but muted wins
        chat_type=enums.ChatType.GROUP,
        from_user_id=555,
    )

    await bot._process_message(msg)

    bot._process_message_serialized.assert_not_awaited()

    # Cleanup
    chat_filter_config.reset(chat_id)


@pytest.mark.asyncio
async def test_dm_processed_normally():
    """DM → always processed regardless of filter mode."""
    from src.core.chat_filter_config import chat_filter_config

    chat_id = "9999"
    # Even if someone mistakenly sets muted on a DM chat_id,
    # group-filter block only fires for group chats.

    bot = _make_bot()
    msg = _make_message(
        text="Привет Краб",
        chat_type=enums.ChatType.PRIVATE,
        from_user_id=555,
    )

    await bot._process_message(msg)

    # Should reach serialized processing
    bot._process_message_serialized.assert_awaited_once()


@pytest.mark.asyncio
async def test_group_command_always_processed():
    """!command in group → processed even in mention-only mode."""
    from src.core.chat_filter_config import chat_filter_config

    chat_id = "9999"
    chat_filter_config.set_mode(chat_id, "mention-only")

    bot = _make_bot()
    msg = _make_message(
        text="!stats",
        chat_type=enums.ChatType.GROUP,
        from_user_id=555,
    )

    await bot._process_message(msg)

    # Commands always pass through
    bot._process_message_serialized.assert_awaited_once()

    # Cleanup
    chat_filter_config.reset(chat_id)


@pytest.mark.asyncio
async def test_active_mode_listens_all():
    """active mode → all msgs processed."""
    from src.core.chat_filter_config import chat_filter_config

    chat_id = "9999"
    chat_filter_config.set_mode(chat_id, "active")

    bot = _make_bot()
    msg = _make_message(
        text="случайное сообщение без упоминания",
        chat_type=enums.ChatType.GROUP,
        from_user_id=555,
    )

    await bot._process_message(msg)

    bot._process_message_serialized.assert_awaited_once()

    # Cleanup
    chat_filter_config.reset(chat_id)


@pytest.mark.asyncio
async def test_group_mention_passes_through():
    """Group with explicit Krab mention → passes through in mention-only mode."""
    from src.core.chat_filter_config import chat_filter_config

    chat_id = "9999"
    chat_filter_config.set_mode(chat_id, "mention-only")

    bot = _make_bot()
    msg = _make_message(
        text="Краб, что думаешь?",
        chat_type=enums.ChatType.GROUP,
        from_user_id=555,
    )

    await bot._process_message(msg)

    bot._process_message_serialized.assert_awaited_once()

    # Cleanup
    chat_filter_config.reset(chat_id)


@pytest.mark.asyncio
async def test_chat_window_touched_on_every_message():
    """ChatWindow.touch() called (message_count increments) per message."""
    from src.core.chat_filter_config import chat_filter_config
    from src.core.chat_window_manager import chat_window_manager

    chat_id = "9999"
    chat_filter_config.set_mode(chat_id, "active")

    # Remove window to start fresh
    chat_window_manager.remove(chat_id)
    initial_count = 0

    bot = _make_bot()
    msg = _make_message(
        text="test touch",
        chat_type=enums.ChatType.PRIVATE,
        from_user_id=555,
    )

    await bot._process_message(msg)

    window = chat_window_manager.peek(chat_id)
    assert window is not None, "ChatWindow should be created"
    assert window.message_count > initial_count, "touch() should increment message_count"

    # Cleanup
    chat_filter_config.reset(chat_id)


@pytest.mark.asyncio
async def test_reply_to_self_passes_through_mention_only():
    """Reply to Krab's message → passes through in mention-only mode."""
    from src.core.chat_filter_config import chat_filter_config

    chat_id = "9999"
    chat_filter_config.set_mode(chat_id, "mention-only")

    bot = _make_bot()
    msg = _make_message(
        text="окей, продолжай",
        chat_type=enums.ChatType.GROUP,
        from_user_id=555,
        reply_to_user_id=42,  # 42 == Krab's own ID
    )

    await bot._process_message(msg)

    bot._process_message_serialized.assert_awaited_once()

    # Cleanup
    chat_filter_config.reset(chat_id)


@pytest.mark.asyncio
async def test_priority_classify_dm_command():
    """classify_priority returns P0_INSTANT for DM."""
    from src.core.message_priority_dispatcher import Priority, classify_priority

    prio, reason = classify_priority(
        "!health",
        chat_type="PRIVATE",
        is_dm=True,
        is_reply_to_self=False,
        has_mention=False,
        chat_mode="active",
    )
    assert prio == Priority.P0_INSTANT
    assert reason == "dm"


@pytest.mark.asyncio
async def test_priority_classify_group_no_mention():
    """classify_priority returns P2_LOW for group non-mention non-command."""
    from src.core.message_priority_dispatcher import Priority, classify_priority

    prio, reason = classify_priority(
        "случайное",
        chat_type="GROUP",
        is_dm=False,
        is_reply_to_self=False,
        has_mention=False,
        chat_mode="mention-only",
    )
    assert prio == Priority.P2_LOW
    assert reason == "mode_mention-only_no_trigger"
