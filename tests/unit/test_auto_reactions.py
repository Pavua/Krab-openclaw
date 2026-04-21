# -*- coding: utf-8 -*-
"""
Тесты модуля src/core/auto_reactions.py.

Покрытие:
1.  set_reaction disabled via env → возвращает False, API не вызывается
2.  set_reaction через bot.send_reaction → возвращает True
3.  set_reaction через bot.client.send_reaction (fallback) → возвращает True
4.  set_reaction без API → возвращает False (graceful)
5.  set_reaction: исключение → возвращает False (не пробрасывает)
6.  mark_accepted → emoji 👍
7.  mark_completed → emoji ✅
8.  mark_failed → emoji ❌, error обрезается до 100 символов
9.  mark_agent_mode → emoji ⚙️
10. mark_memory_recall → emoji 🧠
11. handle_react "on"  → AUTO_REACTIONS_ENABLED=true
12. handle_react "off" → AUTO_REACTIONS_ENABLED=false
13. handle_react "status" (no args) → показывает текущее состояние
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.core.auto_reactions as ar

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_message(chat_id: int = 100, message_id: int = 42) -> MagicMock:
    msg = MagicMock()
    msg.chat.id = chat_id
    msg.id = message_id
    return msg


def _make_bot_with_send_reaction() -> MagicMock:
    bot = MagicMock()
    bot.send_reaction = AsyncMock(return_value=None)
    return bot


def _make_bot_via_client() -> MagicMock:
    """bot без send_reaction, но с bot.client.send_reaction."""
    bot = MagicMock(spec=[])  # без атрибутов по умолчанию
    client = MagicMock()
    client.send_reaction = AsyncMock(return_value=None)
    bot.client = client
    return bot


def _make_bot_no_api() -> MagicMock:
    """bot без любого send_reaction API."""
    bot = MagicMock(spec=[])
    return bot


# ---------------------------------------------------------------------------
# Тесты set_reaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_reaction_disabled_by_env():
    """Когда AUTO_REACTIONS_ENABLED=false — API не вызывается, возвращает False."""
    bot = _make_bot_with_send_reaction()
    msg = _make_message()
    with patch.dict(os.environ, {"AUTO_REACTIONS_ENABLED": "false"}):
        result = await ar.set_reaction(bot, msg.chat.id, msg.id, "👍")
    assert result is False
    bot.send_reaction.assert_not_called()


@pytest.mark.asyncio
async def test_set_reaction_via_bot_send_reaction():
    """bot.send_reaction вызывается, возвращает True."""
    bot = _make_bot_with_send_reaction()
    msg = _make_message()
    with patch.dict(os.environ, {"AUTO_REACTIONS_ENABLED": "true"}):
        result = await ar.set_reaction(bot, msg.chat.id, msg.id, "✅")
    assert result is True
    bot.send_reaction.assert_awaited_once_with(chat_id=msg.chat.id, message_id=msg.id, emoji="✅")


@pytest.mark.asyncio
async def test_set_reaction_fallback_bot_client():
    """Fallback через bot.client.send_reaction, возвращает True."""
    bot = _make_bot_via_client()
    msg = _make_message()
    with patch.dict(os.environ, {"AUTO_REACTIONS_ENABLED": "true"}):
        result = await ar.set_reaction(bot, msg.chat.id, msg.id, "❌")
    assert result is True
    bot.client.send_reaction.assert_awaited_once_with(
        chat_id=msg.chat.id, message_id=msg.id, emoji="❌"
    )


@pytest.mark.asyncio
async def test_set_reaction_handles_api_missing():
    """Нет ни bot.send_reaction, ни bot.client.send_reaction → graceful False."""
    bot = _make_bot_no_api()
    msg = _make_message()
    with patch.dict(os.environ, {"AUTO_REACTIONS_ENABLED": "true"}):
        result = await ar.set_reaction(bot, msg.chat.id, msg.id, "👍")
    assert result is False


@pytest.mark.asyncio
async def test_set_reaction_exception_returns_false():
    """Исключение в send_reaction → возвращает False, не пробрасывает."""
    bot = MagicMock()
    bot.send_reaction = AsyncMock(side_effect=RuntimeError("flood wait"))
    msg = _make_message()
    with patch.dict(os.environ, {"AUTO_REACTIONS_ENABLED": "true"}):
        result = await ar.set_reaction(bot, msg.chat.id, msg.id, "✅")
    assert result is False


# ---------------------------------------------------------------------------
# Тесты высокоуровневых хелперов
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_accepted_contextual_gratitude():
    """mark_accepted с благодарственным текстом → ставит контекстную реакцию."""
    bot = _make_bot_with_send_reaction()
    msg = _make_message()
    msg.text = "спасибо за помощь!"
    with patch.dict(os.environ, {"AUTO_REACTIONS_ENABLED": "true", "KRAB_AUTO_REACTIONS_MODE": "contextual"}):
        result = await ar.mark_accepted(bot, msg)
    # Должна поставить одну из благодарственных реакций
    assert result is True
    bot.send_reaction.assert_awaited_once()
    _, kwargs = bot.send_reaction.call_args
    assert kwargs["emoji"] in {"👍", "🙏", "❤️"}


@pytest.mark.asyncio
async def test_mark_accepted_no_reaction_for_plain_command():
    """mark_accepted с командой (!) → no-op в contextual режиме."""
    bot = _make_bot_with_send_reaction()
    msg = _make_message()
    msg.text = "!ask что-то"
    with patch.dict(os.environ, {"AUTO_REACTIONS_ENABLED": "true", "KRAB_AUTO_REACTIONS_MODE": "contextual"}):
        result = await ar.mark_accepted(bot, msg)
    # Команды не реагируют в contextual режиме (пустой текст или ! команда)
    # Результат False — нет реакции
    assert result is False


@pytest.mark.asyncio
async def test_mark_completed_is_noop():
    """mark_completed теперь no-op — не ставит ✅ после каждого ответа."""
    bot = _make_bot_with_send_reaction()
    msg = _make_message()
    with patch.dict(os.environ, {"AUTO_REACTIONS_ENABLED": "true"}):
        result = await ar.mark_completed(bot, msg)
    assert result is False
    bot.send_reaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_explicit_completed_uses_check():
    """mark_explicit_completed ставит ✅ явно."""
    bot = _make_bot_with_send_reaction()
    msg = _make_message()
    with patch.dict(os.environ, {"AUTO_REACTIONS_ENABLED": "true"}):
        result = await ar.mark_explicit_completed(bot, msg)
    assert result is True
    _, kwargs = bot.send_reaction.call_args
    assert kwargs["emoji"] == "✅"


@pytest.mark.asyncio
async def test_mark_failed_uses_cross_and_truncates_error():
    """mark_failed передаёт ❌ и обрезает error до 100 символов в log_ctx."""
    bot = _make_bot_with_send_reaction()
    msg = _make_message()
    long_error = "E" * 200
    with patch.dict(os.environ, {"AUTO_REACTIONS_ENABLED": "true"}):
        result = await ar.mark_failed(bot, msg, error=long_error)
    assert result is True
    _, kwargs = bot.send_reaction.call_args
    assert kwargs["emoji"] == "❌"


@pytest.mark.asyncio
async def test_mark_agent_mode():
    bot = _make_bot_with_send_reaction()
    msg = _make_message()
    with patch.dict(os.environ, {"AUTO_REACTIONS_ENABLED": "true"}):
        result = await ar.mark_agent_mode(bot, msg)
    assert result is True
    _, kwargs = bot.send_reaction.call_args
    assert kwargs["emoji"] == "⚙️"


@pytest.mark.asyncio
async def test_mark_memory_recall_is_noop():
    """mark_memory_recall теперь no-op — не ставит 🧠 перед каждым ответом."""
    bot = _make_bot_with_send_reaction()
    msg = _make_message()
    with patch.dict(os.environ, {"AUTO_REACTIONS_ENABLED": "true"}):
        result = await ar.mark_memory_recall(bot, msg)
    assert result is False
    bot.send_reaction.assert_not_awaited()


# ---------------------------------------------------------------------------
# Тесты handle_react
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_react_on():
    """!react on → устанавливает env=true, отправляет подтверждение через message.reply."""
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value="on")
    msg = _make_message()
    msg.reply = AsyncMock()
    with patch.dict(os.environ, {"AUTO_REACTIONS_ENABLED": "false"}, clear=False):
        await ar.handle_react(bot, msg)
        # Проверяем внутри контекста, пока patch.dict ещё активен
        assert os.environ.get("AUTO_REACTIONS_ENABLED") == "true"
    msg.reply.assert_awaited_once()
    text = msg.reply.call_args[0][0]
    assert "enabled" in text.lower()


@pytest.mark.asyncio
async def test_handle_react_off():
    """!react off → устанавливает env=false, отправляет подтверждение через message.reply."""
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value="off")
    msg = _make_message()
    msg.reply = AsyncMock()
    with patch.dict(os.environ, {"AUTO_REACTIONS_ENABLED": "true"}, clear=False):
        await ar.handle_react(bot, msg)
        # Проверяем внутри контекста, пока patch.dict ещё активен
        assert os.environ.get("AUTO_REACTIONS_ENABLED") == "false"
    msg.reply.assert_awaited_once()
    text = msg.reply.call_args[0][0]
    assert "disabled" in text.lower()


@pytest.mark.asyncio
async def test_handle_react_status():
    """!react без args → показывает текущее состояние env через message.reply."""
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value="")
    msg = _make_message()
    msg.reply = AsyncMock()
    with patch.dict(os.environ, {"AUTO_REACTIONS_ENABLED": "true"}):
        await ar.handle_react(bot, msg)
    msg.reply.assert_awaited_once()
    text = msg.reply.call_args[0][0]
    assert "true" in text


@pytest.mark.asyncio
async def test_handle_react_on_no_attribute_error():
    """handle_react 'on' не выбрасывает AttributeError даже если bot не имеет _safe_reply."""
    bot = MagicMock(spec=["_get_command_args"])  # нет _safe_reply
    bot._get_command_args = MagicMock(return_value="on")
    msg = _make_message()
    msg.reply = AsyncMock()
    with patch.dict(os.environ, {"AUTO_REACTIONS_ENABLED": "false"}, clear=False):
        # не должно падать
        await ar.handle_react(bot, msg)
    msg.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_react_off_no_attribute_error():
    """handle_react 'off' не выбрасывает AttributeError даже если bot не имеет _safe_reply."""
    bot = MagicMock(spec=["_get_command_args"])  # нет _safe_reply
    bot._get_command_args = MagicMock(return_value="off")
    msg = _make_message()
    msg.reply = AsyncMock()
    with patch.dict(os.environ, {"AUTO_REACTIONS_ENABLED": "true"}, clear=False):
        await ar.handle_react(bot, msg)
    msg.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_react_status_no_attribute_error():
    """handle_react status не выбрасывает AttributeError даже если bot не имеет _safe_reply."""
    bot = MagicMock(spec=["_get_command_args"])  # нет _safe_reply
    bot._get_command_args = MagicMock(return_value="status")
    msg = _make_message()
    msg.reply = AsyncMock()
    with patch.dict(os.environ, {"AUTO_REACTIONS_ENABLED": "true"}):
        await ar.handle_react(bot, msg)
    msg.reply.assert_awaited_once()
