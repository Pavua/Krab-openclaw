# -*- coding: utf-8 -*-
"""
Тесты: auto_reactions пропускает реакции в DM-чатах (REACTION_INVALID avoidance).
Wave 29-OO.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from pyrogram.enums import ChatType

import src.core.auto_reactions as ar

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_message_with_chat_type(chat_type: ChatType, msg_id: int = 42) -> MagicMock:
    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.id = 999
    msg.chat.type = chat_type
    msg.id = msg_id
    return msg


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_reaction = AsyncMock(return_value=None)
    return bot


# ---------------------------------------------------------------------------
# Тесты _can_react
# ---------------------------------------------------------------------------


def test_can_react_private_returns_false():
    """_can_react возвращает False для PRIVATE-чата."""
    msg = _make_message_with_chat_type(ChatType.PRIVATE)
    assert ar._can_react(msg) is False


def test_can_react_supergroup_returns_true():
    """_can_react возвращает True для SUPERGROUP."""
    msg = _make_message_with_chat_type(ChatType.SUPERGROUP)
    assert ar._can_react(msg) is True


def test_can_react_group_returns_true():
    """_can_react возвращает True для GROUP."""
    msg = _make_message_with_chat_type(ChatType.GROUP)
    assert ar._can_react(msg) is True


def test_can_react_channel_returns_true():
    """_can_react возвращает True для CHANNEL."""
    msg = _make_message_with_chat_type(ChatType.CHANNEL)
    assert ar._can_react(msg) is True


def test_can_react_no_chat_returns_false():
    """_can_react возвращает False если chat отсутствует."""
    msg = MagicMock()
    msg.chat = None
    assert ar._can_react(msg) is False


def test_can_react_no_chat_type_returns_false():
    """_can_react возвращает False если chat.type отсутствует."""
    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.type = None
    assert ar._can_react(msg) is False


# ---------------------------------------------------------------------------
# Тесты mark_*() в DM — send_reaction не вызывается
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_accepted_dm_skipped():
    """mark_accepted в DM-чате → send_reaction не вызывается, возвращает False."""
    bot = _make_bot()
    msg = _make_message_with_chat_type(ChatType.PRIVATE)
    with __import__("unittest.mock", fromlist=["patch"]).patch.dict(
        os.environ, {"AUTO_REACTIONS_ENABLED": "true"}
    ):
        result = await ar.mark_accepted(bot, msg)
    assert result is False
    bot.send_reaction.assert_not_called()


@pytest.mark.asyncio
async def test_mark_completed_dm_skipped():
    """mark_completed в DM-чате → send_reaction не вызывается, возвращает False."""
    bot = _make_bot()
    msg = _make_message_with_chat_type(ChatType.PRIVATE)
    with __import__("unittest.mock", fromlist=["patch"]).patch.dict(
        os.environ, {"AUTO_REACTIONS_ENABLED": "true"}
    ):
        result = await ar.mark_completed(bot, msg)
    assert result is False
    bot.send_reaction.assert_not_called()


@pytest.mark.asyncio
async def test_mark_failed_dm_skipped():
    """mark_failed в DM-чате → send_reaction не вызывается, возвращает False."""
    bot = _make_bot()
    msg = _make_message_with_chat_type(ChatType.PRIVATE)
    with __import__("unittest.mock", fromlist=["patch"]).patch.dict(
        os.environ, {"AUTO_REACTIONS_ENABLED": "true"}
    ):
        result = await ar.mark_failed(bot, msg, error="тест")
    assert result is False
    bot.send_reaction.assert_not_called()


@pytest.mark.asyncio
async def test_mark_agent_mode_dm_skipped():
    """mark_agent_mode в DM-чате → send_reaction не вызывается, возвращает False."""
    bot = _make_bot()
    msg = _make_message_with_chat_type(ChatType.PRIVATE)
    with __import__("unittest.mock", fromlist=["patch"]).patch.dict(
        os.environ, {"AUTO_REACTIONS_ENABLED": "true"}
    ):
        result = await ar.mark_agent_mode(bot, msg)
    assert result is False
    bot.send_reaction.assert_not_called()


@pytest.mark.asyncio
async def test_mark_memory_recall_dm_skipped():
    """mark_memory_recall в DM-чате → send_reaction не вызывается, возвращает False."""
    bot = _make_bot()
    msg = _make_message_with_chat_type(ChatType.PRIVATE)
    with __import__("unittest.mock", fromlist=["patch"]).patch.dict(
        os.environ, {"AUTO_REACTIONS_ENABLED": "true"}
    ):
        result = await ar.mark_memory_recall(bot, msg)
    assert result is False
    bot.send_reaction.assert_not_called()


# ---------------------------------------------------------------------------
# Тесты mark_*() в SUPERGROUP — send_reaction вызывается
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_accepted_supergroup_calls_send_reaction():
    """mark_accepted в SUPERGROUP → send_reaction вызывается."""
    bot = _make_bot()
    msg = _make_message_with_chat_type(ChatType.SUPERGROUP)
    with __import__("unittest.mock", fromlist=["patch"]).patch.dict(
        os.environ, {"AUTO_REACTIONS_ENABLED": "true"}
    ):
        result = await ar.mark_accepted(bot, msg)
    assert result is True
    bot.send_reaction.assert_awaited_once()
    _, kwargs = bot.send_reaction.call_args
    assert kwargs["emoji"] == "👍"


@pytest.mark.asyncio
async def test_mark_memory_recall_supergroup_calls_send_reaction():
    """mark_memory_recall в SUPERGROUP → вызывается с emoji 🧠."""
    bot = _make_bot()
    msg = _make_message_with_chat_type(ChatType.SUPERGROUP)
    with __import__("unittest.mock", fromlist=["patch"]).patch.dict(
        os.environ, {"AUTO_REACTIONS_ENABLED": "true"}
    ):
        result = await ar.mark_memory_recall(bot, msg)
    assert result is True
    _, kwargs = bot.send_reaction.call_args
    assert kwargs["emoji"] == "🧠"


@pytest.mark.asyncio
async def test_mark_completed_group_calls_send_reaction():
    """mark_completed в GROUP → вызывается с emoji ✅."""
    bot = _make_bot()
    msg = _make_message_with_chat_type(ChatType.GROUP)
    with __import__("unittest.mock", fromlist=["patch"]).patch.dict(
        os.environ, {"AUTO_REACTIONS_ENABLED": "true"}
    ):
        result = await ar.mark_completed(bot, msg)
    assert result is True
    _, kwargs = bot.send_reaction.call_args
    assert kwargs["emoji"] == "✅"
