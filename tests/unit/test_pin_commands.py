# -*- coding: utf-8 -*-
"""
Тесты owner-only команд !pin и !unpin.

Покрываем:
1) !pin — закрепляет reply-сообщение;
2) !pin silent — закрепляет без уведомления;
3) !pin без reply — UserInputError;
4) !unpin — открепляет reply-сообщение;
5) !unpin all — открепляет все сообщения;
6) !unpin без reply — UserInputError;
7) не-owner получает UserInputError для обеих команд;
8) Pyrogram API raises — ответ с ❌.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.core.access_control import AccessLevel, AccessProfile
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_pin, handle_unpin


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------

def _make_bot(args: str = "", *, access_level: AccessLevel = AccessLevel.OWNER) -> SimpleNamespace:
    """Минимальный mock KraabUserbot."""
    bot = SimpleNamespace(
        me=SimpleNamespace(id=999),
        client=SimpleNamespace(
            pin_chat_message=AsyncMock(),
            unpin_chat_message=AsyncMock(),
            unpin_all_chat_messages=AsyncMock(),
        ),
        _get_command_args=lambda _: args,
        _get_access_profile=lambda user: AccessProfile(level=access_level, source="test"),
    )
    return bot


def _make_message(
    *,
    reply_to: SimpleNamespace | None = None,
    from_user_id: int = 1,
    chat_id: int = 100,
) -> SimpleNamespace:
    """Минимальный mock pyrogram.Message."""
    return SimpleNamespace(
        from_user=SimpleNamespace(id=from_user_id),
        chat=SimpleNamespace(id=chat_id),
        reply_to_message=reply_to,
        reply=AsyncMock(),
        edit=AsyncMock(),
    )


def _make_reply_msg(msg_id: int = 42) -> SimpleNamespace:
    return SimpleNamespace(id=msg_id)


# ---------------------------------------------------------------------------
# handle_pin
# ---------------------------------------------------------------------------

class TestHandlePin:
    @pytest.mark.asyncio
    async def test_pin_calls_pyrogram_api(self) -> None:
        """!pin закрепляет сообщение через client.pin_chat_message."""
        bot = _make_bot("")
        target = _make_reply_msg(42)
        message = _make_message(reply_to=target)

        await handle_pin(bot, message)

        bot.client.pin_chat_message.assert_awaited_once_with(
            chat_id=100,
            message_id=42,
            disable_notification=False,
        )

    @pytest.mark.asyncio
    async def test_pin_silent_flag(self) -> None:
        """!pin silent передаёт disable_notification=True."""
        bot = _make_bot("silent")
        target = _make_reply_msg(55)
        message = _make_message(reply_to=target)

        await handle_pin(bot, message)

        bot.client.pin_chat_message.assert_awaited_once_with(
            chat_id=100,
            message_id=55,
            disable_notification=True,
        )

    @pytest.mark.asyncio
    async def test_pin_reply_contains_confirmation(self) -> None:
        """После успешного закрепления бот отвечает подтверждением."""
        bot = _make_bot()
        message = _make_message(reply_to=_make_reply_msg())

        await handle_pin(bot, message)

        message.reply.assert_awaited_once()
        text = message.reply.await_args.args[0]
        assert "закреплено" in text.lower()

    @pytest.mark.asyncio
    async def test_pin_silent_reply_mentions_no_notification(self) -> None:
        """При silent ответ упоминает отсутствие уведомления."""
        bot = _make_bot("silent")
        message = _make_message(reply_to=_make_reply_msg())

        await handle_pin(bot, message)

        text = message.reply.await_args.args[0]
        assert "без уведомления" in text.lower()

    @pytest.mark.asyncio
    async def test_pin_no_reply_raises_user_input_error(self) -> None:
        """!pin без reply_to_message → UserInputError."""
        bot = _make_bot()
        message = _make_message(reply_to=None)

        with pytest.raises(UserInputError):
            await handle_pin(bot, message)

        bot.client.pin_chat_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pin_non_owner_raises_user_input_error(self) -> None:
        """Не-owner получает UserInputError."""
        bot = _make_bot(access_level=AccessLevel.FULL)
        message = _make_message(reply_to=_make_reply_msg())

        with pytest.raises(UserInputError):
            await handle_pin(bot, message)

        bot.client.pin_chat_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pin_pyrogram_exception_returns_error_text(self) -> None:
        """Если pyrogram бросает исключение — ответ содержит ❌."""
        bot = _make_bot()
        bot.client.pin_chat_message = AsyncMock(side_effect=RuntimeError("Forbidden"))
        message = _make_message(reply_to=_make_reply_msg())

        await handle_pin(bot, message)

        text = message.reply.await_args.args[0]
        assert "❌" in text
        assert "Forbidden" in text

    @pytest.mark.asyncio
    async def test_pin_uses_edit_when_self_message(self) -> None:
        """Если сообщение отправлено самим ботом — редактирует вместо reply."""
        bot = _make_bot()
        message = _make_message(reply_to=_make_reply_msg(), from_user_id=bot.me.id)

        await handle_pin(bot, message)

        message.edit.assert_awaited_once()
        message.reply.assert_not_awaited()


# ---------------------------------------------------------------------------
# handle_unpin
# ---------------------------------------------------------------------------

class TestHandleUnpin:
    @pytest.mark.asyncio
    async def test_unpin_calls_pyrogram_api(self) -> None:
        """!unpin открепляет конкретное сообщение через client.unpin_chat_message."""
        bot = _make_bot("")
        target = _make_reply_msg(77)
        message = _make_message(reply_to=target)

        await handle_unpin(bot, message)

        bot.client.unpin_chat_message.assert_awaited_once_with(
            chat_id=100,
            message_id=77,
        )

    @pytest.mark.asyncio
    async def test_unpin_reply_contains_confirmation(self) -> None:
        """После откреплния бот отвечает подтверждением."""
        bot = _make_bot()
        message = _make_message(reply_to=_make_reply_msg())

        await handle_unpin(bot, message)

        message.reply.assert_awaited_once()
        text = message.reply.await_args.args[0]
        assert "откреплено" in text.lower()

    @pytest.mark.asyncio
    async def test_unpin_all_calls_unpin_all_api(self) -> None:
        """!unpin all вызывает client.unpin_all_chat_messages."""
        bot = _make_bot("all")
        message = _make_message()

        await handle_unpin(bot, message)

        bot.client.unpin_all_chat_messages.assert_awaited_once_with(chat_id=100)
        bot.client.unpin_chat_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unpin_all_reply_contains_confirmation(self) -> None:
        """!unpin all — ответ содержит подтверждение снятия всех закреплений."""
        bot = _make_bot("all")
        message = _make_message()

        await handle_unpin(bot, message)

        text = message.reply.await_args.args[0]
        assert "все" in text.lower()
        assert "откреплены" in text.lower()

    @pytest.mark.asyncio
    async def test_unpin_no_reply_raises_user_input_error(self) -> None:
        """!unpin без reply и без 'all' → UserInputError."""
        bot = _make_bot("")
        message = _make_message(reply_to=None)

        with pytest.raises(UserInputError):
            await handle_unpin(bot, message)

        bot.client.unpin_chat_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unpin_non_owner_raises_user_input_error(self) -> None:
        """Не-owner получает UserInputError."""
        bot = _make_bot(access_level=AccessLevel.PARTIAL)
        message = _make_message(reply_to=_make_reply_msg())

        with pytest.raises(UserInputError):
            await handle_unpin(bot, message)

        bot.client.unpin_chat_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unpin_pyrogram_exception_returns_error_text(self) -> None:
        """Если pyrogram бросает исключение — ответ содержит ❌."""
        bot = _make_bot()
        bot.client.unpin_chat_message = AsyncMock(side_effect=RuntimeError("Forbidden"))
        message = _make_message(reply_to=_make_reply_msg())

        await handle_unpin(bot, message)

        text = message.reply.await_args.args[0]
        assert "❌" in text

    @pytest.mark.asyncio
    async def test_unpin_all_pyrogram_exception_returns_error_text(self) -> None:
        """!unpin all — если pyrogram бросает → ответ содержит ❌."""
        bot = _make_bot("all")
        bot.client.unpin_all_chat_messages = AsyncMock(side_effect=RuntimeError("No rights"))
        message = _make_message()

        await handle_unpin(bot, message)

        text = message.reply.await_args.args[0]
        assert "❌" in text

    @pytest.mark.asyncio
    async def test_unpin_uses_edit_when_self_message(self) -> None:
        """Если сообщение отправлено самим ботом — редактирует вместо reply."""
        bot = _make_bot()
        message = _make_message(reply_to=_make_reply_msg(), from_user_id=bot.me.id)

        await handle_unpin(bot, message)

        message.edit.assert_awaited_once()
        message.reply.assert_not_awaited()
