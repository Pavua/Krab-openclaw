# -*- coding: utf-8 -*-
"""
Тесты команды !react (handle_react) из src/handlers/command_handlers.py.

Покрытие:
- handle_react: корректный вызов send_reaction с emoji и target message_id
- handle_react в reply → ставит реакцию на reply_to_message
- handle_react без reply → ставит реакцию на само сообщение
- handle_react без args → UserInputError
- handle_react при TELEGRAM_REACTIONS_ENABLED=False → предупреждение
- handle_react при ошибке send_reaction → reply с ошибкой
- handle_react удаляет команду после успеха
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_react


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _make_bot(reactions_enabled: bool = True) -> MagicMock:
    bot = MagicMock()
    bot.client = MagicMock()
    bot.client.send_reaction = AsyncMock()
    bot._get_command_args = MagicMock(return_value="👍")
    return bot


def _make_message(
    chat_id: int = 100,
    message_id: int = 200,
    reply_to_message=None,
) -> MagicMock:
    msg = MagicMock()
    msg.id = message_id
    msg.chat = SimpleNamespace(id=chat_id)
    msg.reply_to_message = reply_to_message
    msg.reply = AsyncMock()
    msg.delete = AsyncMock()
    return msg


def _make_reply_message(chat_id: int = 100, message_id: int = 300) -> MagicMock:
    reply = MagicMock()
    reply.id = message_id
    reply.chat = SimpleNamespace(id=chat_id)
    return reply


# ---------------------------------------------------------------------------
# Базовые тесты
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_react_sends_reaction_on_reply_message() -> None:
    """Если команда — ответ на сообщение, реакция ставится на reply_to_message."""
    bot = _make_bot()
    bot._get_command_args = MagicMock(return_value="👍")
    reply_msg = _make_reply_message(chat_id=100, message_id=300)
    message = _make_message(reply_to_message=reply_msg)

    with patch("src.handlers.command_handlers.config") as mock_cfg:
        mock_cfg.TELEGRAM_REACTIONS_ENABLED = True
        await handle_react(bot, message)

    bot.client.send_reaction.assert_awaited_once_with(
        chat_id=100,
        message_id=300,
        emoji="👍",
    )


@pytest.mark.asyncio
async def test_handle_react_sends_reaction_on_own_message_without_reply() -> None:
    """Без reply — реакция ставится на само сообщение команды."""
    bot = _make_bot()
    bot._get_command_args = MagicMock(return_value="❤️")
    message = _make_message(chat_id=50, message_id=777, reply_to_message=None)

    with patch("src.handlers.command_handlers.config") as mock_cfg:
        mock_cfg.TELEGRAM_REACTIONS_ENABLED = True
        await handle_react(bot, message)

    bot.client.send_reaction.assert_awaited_once_with(
        chat_id=50,
        message_id=777,
        emoji="❤️",
    )


@pytest.mark.asyncio
async def test_handle_react_deletes_command_on_success() -> None:
    """После успешной реакции команда удаляется (best-effort)."""
    bot = _make_bot()
    bot._get_command_args = MagicMock(return_value="🔥")
    message = _make_message()

    with patch("src.handlers.command_handlers.config") as mock_cfg:
        mock_cfg.TELEGRAM_REACTIONS_ENABLED = True
        await handle_react(bot, message)

    message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_react_no_args_raises_user_input_error() -> None:
    """Без аргументов (пустая строка) → UserInputError."""
    bot = _make_bot()
    bot._get_command_args = MagicMock(return_value="")
    message = _make_message()

    with patch("src.handlers.command_handlers.config") as mock_cfg:
        mock_cfg.TELEGRAM_REACTIONS_ENABLED = True
        with pytest.raises(UserInputError):
            await handle_react(bot, message)


@pytest.mark.asyncio
async def test_handle_react_disabled_by_config_replies_warning() -> None:
    """При TELEGRAM_REACTIONS_ENABLED=False → предупреждение, no send_reaction."""
    bot = _make_bot()
    bot._get_command_args = MagicMock(return_value="👍")
    message = _make_message()

    # handle_react делает локальный `from ..config import config as _cfg`
    # — патчим через src.config.config
    with patch("src.handlers.command_handlers.config") as mock_cfg:
        mock_cfg.TELEGRAM_REACTIONS_ENABLED = False
        await handle_react(bot, message)

    bot.client.send_reaction.assert_not_awaited()
    message.reply.assert_awaited_once()
    call_args = message.reply.call_args[0][0]
    assert "отключен" in call_args.lower() or "reactions" in call_args.lower()


@pytest.mark.asyncio
async def test_handle_react_send_reaction_error_sends_error_reply() -> None:
    """Если send_reaction бросает исключение → reply с описанием ошибки."""
    bot = _make_bot()
    bot._get_command_args = MagicMock(return_value="👍")
    bot.client.send_reaction = AsyncMock(side_effect=Exception("REACTION_INVALID"))
    message = _make_message()

    with patch("src.handlers.command_handlers.config") as mock_cfg:
        mock_cfg.TELEGRAM_REACTIONS_ENABLED = True
        await handle_react(bot, message)

    message.reply.assert_awaited_once()
    error_text = message.reply.call_args[0][0]
    assert "❌" in error_text
    assert "REACTION_INVALID" in error_text


@pytest.mark.asyncio
async def test_handle_react_delete_failure_is_silenced() -> None:
    """Если удаление команды падает — ошибка поглощается, без исключений."""
    bot = _make_bot()
    bot._get_command_args = MagicMock(return_value="🎉")
    message = _make_message()
    message.delete = AsyncMock(side_effect=Exception("ACCESS_DENIED"))

    with patch("src.handlers.command_handlers.config") as mock_cfg:
        mock_cfg.TELEGRAM_REACTIONS_ENABLED = True
        # Не должно бросить исключение
        await handle_react(bot, message)

    bot.client.send_reaction.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_react_whitespace_args_raises_error() -> None:
    """Аргументы только из пробелов → UserInputError."""
    bot = _make_bot()
    bot._get_command_args = MagicMock(return_value="   ")
    message = _make_message()

    with patch("src.handlers.command_handlers.config") as mock_cfg:
        mock_cfg.TELEGRAM_REACTIONS_ENABLED = True
        with pytest.raises(UserInputError):
            await handle_react(bot, message)


@pytest.mark.asyncio
async def test_handle_react_uses_reply_chat_id() -> None:
    """chat_id берётся из reply_to_message.chat, а не из message.chat."""
    bot = _make_bot()
    bot._get_command_args = MagicMock(return_value="👍")
    reply_msg = _make_reply_message(chat_id=999, message_id=888)
    message = _make_message(chat_id=111, reply_to_message=reply_msg)

    with patch("src.handlers.command_handlers.config") as mock_cfg:
        mock_cfg.TELEGRAM_REACTIONS_ENABLED = True
        await handle_react(bot, message)

    bot.client.send_reaction.assert_awaited_once_with(
        chat_id=999,
        message_id=888,
        emoji="👍",
    )
