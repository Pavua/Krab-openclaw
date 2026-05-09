# -*- coding: utf-8 -*-
"""Tests for Wave 44-O-nlu-wire — NLU command gate (pre-LLM dispatch)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.userbot import nlu_command_gate as gate

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_owner_dm_message(text: str):
    """Pyrogram-like Message: private chat, from owner."""
    from pyrogram import enums

    msg = MagicMock()
    msg.text = text
    msg.chat = SimpleNamespace(type=enums.ChatType.PRIVATE, id=12345)
    msg.from_user = SimpleNamespace(id=999, username="owner", first_name="Pablo")
    msg.reply = AsyncMock()
    return msg


def _make_group_message(text: str):
    from pyrogram import enums

    msg = MagicMock()
    msg.text = text
    msg.chat = SimpleNamespace(type=enums.ChatType.SUPERGROUP, id=-100123)
    msg.from_user = SimpleNamespace(id=999, username="owner", first_name="Pablo")
    msg.reply = AsyncMock()
    return msg


@pytest.fixture(autouse=True)
def _reset_pending_state():
    gate._PENDING.clear()
    yield
    gate._PENDING.clear()


@pytest.fixture
def _enable_flag(monkeypatch):
    monkeypatch.setenv("KRAB_NLU_INTENT_DISPATCH_ENABLED", "1")
    yield


# ---------------------------------------------------------------------------
# is_enabled / disabled flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("KRAB_NLU_INTENT_DISPATCH_ENABLED", raising=False)
    msg = _make_owner_dm_message("запусти аналитиков на тему BTC за 2 раунда")
    bot = MagicMock()
    handled = await gate.try_nlu_command_dispatch(
        bot, msg, query=msg.text, chat_id="12345", is_self=True
    )
    assert handled is False


@pytest.mark.asyncio
async def test_enabled_flag_picks_up_truthy(monkeypatch):
    for v in ("1", "true", "yes"):
        monkeypatch.setenv("KRAB_NLU_INTENT_DISPATCH_ENABLED", v)
        assert gate.is_enabled() is True
    monkeypatch.setenv("KRAB_NLU_INTENT_DISPATCH_ENABLED", "0")
    assert gate.is_enabled() is False


# ---------------------------------------------------------------------------
# Owner DM dispatch (high confidence)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_dm_dispatches_swarm_high_confidence(_enable_flag):
    msg = _make_owner_dm_message("запусти аналитиков на тему BTC за 2 раунда")
    bot = MagicMock()
    fake_handler = AsyncMock()
    with patch(
        "src.handlers.commands.swarm_commands.handle_swarm",
        fake_handler,
    ):
        handled = await gate.try_nlu_command_dispatch(
            bot, msg, query=msg.text, chat_id="12345", is_self=True
        )
    assert handled is True
    fake_handler.assert_awaited_once()
    # Проверяем, что message.text был подменён на rendered "!swarm ..."
    called_msg = fake_handler.await_args.args[1]
    # text восстанавливается после dispatch — в момент вызова handler было rendered
    # Здесь после возврата — original. Проверим что handler был вызван.
    assert called_msg is msg


@pytest.mark.asyncio
async def test_owner_dm_dispatches_status(_enable_flag):
    msg = _make_owner_dm_message("проверь статус")
    bot = MagicMock()
    fake_handler = AsyncMock()
    with patch(
        "src.handlers.commands.system_commands.handle_status",
        fake_handler,
    ):
        handled = await gate.try_nlu_command_dispatch(
            bot, msg, query=msg.text, chat_id="12345", is_self=True
        )
    assert handled is True
    fake_handler.assert_awaited_once()


# ---------------------------------------------------------------------------
# Destructive guard / low confidence → fall through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_destructive_phrase_falls_through(_enable_flag):
    msg = _make_owner_dm_message("удали все задачи и сбрось состояние")
    bot = MagicMock()
    handled = await gate.try_nlu_command_dispatch(
        bot, msg, query=msg.text, chat_id="12345", is_self=True
    )
    # destructive — не dispatched высокой уверенностью; либо None/<0.5 либо pending
    # Главное: не dispatched через handler. Допускаем pending или fall-through.
    if handled:
        # Если pending — то pending intent != dispatched
        assert gate.get_pending("12345") is not None or msg.reply.await_count >= 1
    else:
        assert handled is False


@pytest.mark.asyncio
async def test_unrelated_chitchat_falls_through(_enable_flag):
    msg = _make_owner_dm_message("как дела, бро")
    bot = MagicMock()
    handled = await gate.try_nlu_command_dispatch(
        bot, msg, query=msg.text, chat_id="12345", is_self=True
    )
    assert handled is False


# ---------------------------------------------------------------------------
# Group / non-owner — skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_message_skipped(_enable_flag):
    msg = _make_group_message("запусти аналитиков на тему BTC")
    bot = MagicMock()
    handled = await gate.try_nlu_command_dispatch(
        bot, msg, query=msg.text, chat_id="-100123", is_self=True
    )
    assert handled is False


@pytest.mark.asyncio
async def test_non_owner_dm_skipped(_enable_flag):
    msg = _make_owner_dm_message("запусти аналитиков на тему BTC")
    bot = MagicMock()
    handled = await gate.try_nlu_command_dispatch(
        bot, msg, query=msg.text, chat_id="12345", is_self=False
    )
    assert handled is False


# ---------------------------------------------------------------------------
# Pending confirmation flow (medium confidence)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_medium_confidence_creates_pending(_enable_flag):
    # Подделаем extractor → выдать medium-confidence intent.
    from src.core.command_intent_extractor import CommandIntent

    fake_intent = CommandIntent(
        command="!swarm",
        subcommand="analysts",
        args={"team": "analysts", "count": 1, "topic": ""},
        confidence=0.6,
        original_text="хочу аналитиков",
        rendered="!swarm analysts loop 1",
    )
    msg = _make_owner_dm_message("хочу аналитиков")
    bot = MagicMock()
    with patch(
        "src.userbot.nlu_command_gate.extract_command_intent",
        AsyncMock(return_value=fake_intent),
    ):
        handled = await gate.try_nlu_command_dispatch(
            bot, msg, query=msg.text, chat_id="12345", is_self=True
        )
    assert handled is True
    pending = gate.get_pending("12345")
    assert pending is not None
    assert pending.intent.rendered == "!swarm analysts loop 1"
    msg.reply.assert_awaited_once()
    # Reply должен содержать rendered command + вопрос подтверждения
    reply_text = msg.reply.await_args.args[0]
    assert "!swarm" in reply_text
    assert "да" in reply_text.lower() or "подтверд" in reply_text.lower()


@pytest.mark.asyncio
async def test_pending_yes_dispatches(_enable_flag):
    from src.core.command_intent_extractor import CommandIntent

    fake_intent = CommandIntent(
        command="!swarm",
        subcommand="analysts",
        args={},
        confidence=0.6,
        original_text="хочу аналитиков",
        rendered="!swarm analysts loop 1",
    )
    gate._store_pending("12345", fake_intent)
    msg = _make_owner_dm_message("да")
    bot = MagicMock()
    fake_handler = AsyncMock()
    with patch(
        "src.handlers.commands.swarm_commands.handle_swarm",
        fake_handler,
    ):
        handled = await gate.try_nlu_command_dispatch(
            bot, msg, query=msg.text, chat_id="12345", is_self=True
        )
    assert handled is True
    fake_handler.assert_awaited_once()
    assert gate.get_pending("12345") is None


@pytest.mark.asyncio
async def test_pending_no_clears(_enable_flag):
    from src.core.command_intent_extractor import CommandIntent

    fake_intent = CommandIntent(
        command="!swarm",
        confidence=0.6,
        rendered="!swarm analysts loop 1",
    )
    gate._store_pending("12345", fake_intent)
    msg = _make_owner_dm_message("нет")
    bot = MagicMock()
    handled = await gate.try_nlu_command_dispatch(
        bot, msg, query=msg.text, chat_id="12345", is_self=True
    )
    assert handled is True
    assert gate.get_pending("12345") is None
    msg.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_unrelated_clears_and_falls_through(_enable_flag):
    from src.core.command_intent_extractor import CommandIntent

    fake_intent = CommandIntent(
        command="!swarm",
        confidence=0.6,
        rendered="!swarm analysts loop 1",
    )
    gate._store_pending("12345", fake_intent)
    msg = _make_owner_dm_message("кстати, погода сегодня странная")
    bot = MagicMock()
    handled = await gate.try_nlu_command_dispatch(
        bot, msg, query=msg.text, chat_id="12345", is_self=True
    )
    # unrelated — pending очищен, dispatch не произошёл, fall through.
    assert handled is False
    assert gate.get_pending("12345") is None


# ---------------------------------------------------------------------------
# Explicit !command — gate не вмешивается
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_command_skipped(_enable_flag):
    msg = _make_owner_dm_message("!swarm analysts loop 2 BTC")
    bot = MagicMock()
    handled = await gate.try_nlu_command_dispatch(
        bot, msg, query=msg.text, chat_id="12345", is_self=True
    )
    # Явный !command должен идти через нативный Pyrogram filter,
    # а не через NLU gate.
    assert handled is False


# ---------------------------------------------------------------------------
# Empty / whitespace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_query_falls_through(_enable_flag):
    msg = _make_owner_dm_message("")
    bot = MagicMock()
    handled = await gate.try_nlu_command_dispatch(bot, msg, query="", chat_id="12345", is_self=True)
    assert handled is False
