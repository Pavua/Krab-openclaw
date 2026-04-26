# -*- coding: utf-8 -*-
"""Тесты !chatpolicy command handler (Smart Routing Phase 4, Session 26)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.core.access_control import AccessLevel, AccessProfile
from src.core.chat_response_policy import ChatResponsePolicyStore
from src.core.exceptions import UserInputError
from src.handlers.commands.policy_commands import handle_chatpolicy


@pytest.fixture
def store(tmp_path):
    return ChatResponsePolicyStore(path=tmp_path / "p.json")


def _make_msg(text: str, chat_id: int = 100) -> SimpleNamespace:
    msg = SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=1, username="owner"),
        reply=AsyncMock(),
    )
    return msg


def _make_bot(level: AccessLevel = AccessLevel.OWNER) -> SimpleNamespace:
    profile = AccessProfile(level=level, source="test")
    bot = SimpleNamespace(_get_access_profile=lambda u: profile)
    return bot


@pytest.mark.asyncio
async def test_non_owner_rejected(store):
    bot = _make_bot(level=AccessLevel.GUEST)
    msg = _make_msg("!chatpolicy")
    with patch(
        "src.core.chat_response_policy.get_store", return_value=store
    ), pytest.raises(UserInputError) as exc:
        await handle_chatpolicy(bot, msg)
    assert "owner" in (exc.value.user_message or "").lower()


@pytest.mark.asyncio
async def test_show_default_current_chat(store):
    bot = _make_bot()
    msg = _make_msg("!chatpolicy", chat_id=555)
    with patch(
        "src.core.chat_response_policy.get_store", return_value=store
    ):
        await handle_chatpolicy(bot, msg)
    msg.reply.assert_awaited_once()
    body = msg.reply.await_args.args[0]
    assert "555" in body
    assert "normal" in body


@pytest.mark.asyncio
async def test_set_mode_cautious(store):
    bot = _make_bot()
    msg = _make_msg("!chatpolicy set cautious", chat_id=42)
    with patch(
        "src.core.chat_response_policy.get_store", return_value=store
    ):
        await handle_chatpolicy(bot, msg)
    assert store.get_policy("42").mode.value == "cautious"
    body = msg.reply.await_args.args[0]
    assert "cautious" in body


@pytest.mark.asyncio
async def test_set_mode_invalid(store):
    bot = _make_bot()
    msg = _make_msg("!chatpolicy set wild")
    with patch(
        "src.core.chat_response_policy.get_store", return_value=store
    ), pytest.raises(UserInputError):
        await handle_chatpolicy(bot, msg)


@pytest.mark.asyncio
async def test_threshold_set_and_clear(store):
    bot = _make_bot()
    with patch(
        "src.core.chat_response_policy.get_store", return_value=store
    ):
        await handle_chatpolicy(bot, _make_msg("!chatpolicy threshold 0.65", chat_id=7))
        assert store.get_policy("7").threshold_override == 0.65
        await handle_chatpolicy(bot, _make_msg("!chatpolicy threshold clear", chat_id=7))
        assert store.get_policy("7").threshold_override is None


@pytest.mark.asyncio
async def test_threshold_out_of_range(store):
    bot = _make_bot()
    msg = _make_msg("!chatpolicy threshold 9.0")
    with patch(
        "src.core.chat_response_policy.get_store", return_value=store
    ), pytest.raises(UserInputError):
        await handle_chatpolicy(bot, msg)


@pytest.mark.asyncio
async def test_add_and_clear_blocked_topic(store):
    bot = _make_bot()
    with patch(
        "src.core.chat_response_policy.get_store", return_value=store
    ):
        await handle_chatpolicy(
            bot, _make_msg("!chatpolicy add-blocked-topic crypto", chat_id=12)
        )
        assert "crypto" in store.get_policy("12").blocked_topics
        await handle_chatpolicy(
            bot, _make_msg("!chatpolicy clear-blocked-topic crypto", chat_id=12)
        )
        assert "crypto" not in store.get_policy("12").blocked_topics


@pytest.mark.asyncio
async def test_list_empty(store):
    bot = _make_bot()
    msg = _make_msg("!chatpolicy list")
    with patch(
        "src.core.chat_response_policy.get_store", return_value=store
    ):
        await handle_chatpolicy(bot, msg)
    body = msg.reply.await_args.args[0]
    assert "Нет custom" in body or "нет" in body.lower()


@pytest.mark.asyncio
async def test_list_with_entries(store):
    store.update_policy("1", mode="silent")
    store.update_policy("2", mode="chatty")
    bot = _make_bot()
    msg = _make_msg("!chatpolicy list")
    with patch(
        "src.core.chat_response_policy.get_store", return_value=store
    ):
        await handle_chatpolicy(bot, msg)
    body = msg.reply.await_args.args[0]
    assert "silent" in body and "chatty" in body


@pytest.mark.asyncio
async def test_reset_existing(store):
    store.update_policy("9", mode="silent")
    bot = _make_bot()
    msg = _make_msg("!chatpolicy reset", chat_id=9)
    with patch(
        "src.core.chat_response_policy.get_store", return_value=store
    ):
        await handle_chatpolicy(bot, msg)
    assert store.list_all() == []


@pytest.mark.asyncio
async def test_unknown_subcommand(store):
    bot = _make_bot()
    msg = _make_msg("!chatpolicy frobnicate")
    with patch(
        "src.core.chat_response_policy.get_store", return_value=store
    ), pytest.raises(UserInputError):
        await handle_chatpolicy(bot, msg)
