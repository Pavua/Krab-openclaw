# -*- coding: utf-8 -*-
"""
Тесты owner-only команд !archive и !unarchive.

Покрываем:
1) !archive — архивирует текущий чат;
2) !archive list — возвращает список архивированных чатов;
3) !archive list — пустой архив;
4) !archive list — ошибка API;
5) !archive — ошибка API;
6) !unarchive — разархивирует текущий чат;
7) !unarchive — ошибка API;
8) не-owner получает UserInputError для обеих команд;
9) edit вместо reply когда сообщение от самого бота.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.access_control import AccessLevel, AccessProfile
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_archive, handle_unarchive


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------

def _make_dialog(chat_id: int, title: str) -> SimpleNamespace:
    """Минимальный mock pyrogram.Dialog."""
    chat = SimpleNamespace(id=chat_id, title=title, first_name=None)
    return SimpleNamespace(chat=chat)


def _make_async_dialogs_iter(dialogs: list) -> MagicMock:
    """Создаёт async-итератор для get_dialogs."""
    async def _gen():
        for d in dialogs:
            yield d

    mock = MagicMock()
    mock.__aiter__ = lambda self: _gen()
    return mock


def _make_bot(
    args: str = "",
    *,
    access_level: AccessLevel = AccessLevel.OWNER,
    dialogs: list | None = None,
    archive_error: Exception | None = None,
    unarchive_error: Exception | None = None,
    dialogs_error: Exception | None = None,
) -> SimpleNamespace:
    """Минимальный mock KraabUserbot."""
    if dialogs_error:
        get_dialogs_mock = MagicMock(side_effect=dialogs_error)
    else:
        _items = dialogs if dialogs is not None else []
        get_dialogs_mock = MagicMock(return_value=_make_async_dialogs_iter(_items))

    bot = SimpleNamespace(
        me=SimpleNamespace(id=999),
        client=SimpleNamespace(
            archive_chats=AsyncMock(side_effect=archive_error),
            unarchive_chats=AsyncMock(side_effect=unarchive_error),
            get_dialogs=get_dialogs_mock,
        ),
        _get_command_args=lambda _: args,
        _get_access_profile=lambda user: AccessProfile(level=access_level, source="test"),
    )
    return bot


def _make_message(
    *,
    from_user_id: int = 1,
    chat_id: int = 100,
) -> SimpleNamespace:
    """Минимальный mock pyrogram.Message."""
    return SimpleNamespace(
        from_user=SimpleNamespace(id=from_user_id),
        chat=SimpleNamespace(id=chat_id),
        reply=AsyncMock(),
        edit=AsyncMock(),
    )


# ---------------------------------------------------------------------------
# handle_archive — базовый сценарий
# ---------------------------------------------------------------------------

class TestHandleArchive:
    @pytest.mark.asyncio
    async def test_archive_calls_pyrogram_api(self) -> None:
        """!archive вызывает client.archive_chats с текущим chat_id."""
        bot = _make_bot("")
        message = _make_message(chat_id=42)

        await handle_archive(bot, message)

        bot.client.archive_chats.assert_awaited_once_with(42)

    @pytest.mark.asyncio
    async def test_archive_reply_contains_confirmation(self) -> None:
        """После архивации бот отвечает подтверждением."""
        bot = _make_bot("")
        message = _make_message()

        await handle_archive(bot, message)

        message.reply.assert_awaited_once()
        text = message.reply.await_args.args[0]
        assert "архив" in text.lower()

    @pytest.mark.asyncio
    async def test_archive_api_error_replies_with_error_text(self) -> None:
        """Если pyrogram бросает исключение — ответ содержит ❌."""
        bot = _make_bot("", archive_error=RuntimeError("Forbidden"))
        message = _make_message()

        await handle_archive(bot, message)

        text = message.reply.await_args.args[0]
        assert "❌" in text
        assert "Forbidden" in text

    @pytest.mark.asyncio
    async def test_archive_non_owner_raises_user_input_error(self) -> None:
        """Не-owner получает UserInputError."""
        bot = _make_bot(access_level=AccessLevel.FULL)
        message = _make_message()

        with pytest.raises(UserInputError):
            await handle_archive(bot, message)

        bot.client.archive_chats.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_archive_uses_edit_when_self_message(self) -> None:
        """Если сообщение отправлено самим ботом — редактирует вместо reply."""
        bot = _make_bot()
        message = _make_message(from_user_id=bot.me.id)

        await handle_archive(bot, message)

        message.edit.assert_awaited_once()
        message.reply.assert_not_awaited()

    # --- !archive list ---

    @pytest.mark.asyncio
    async def test_archive_list_shows_archived_chats(self) -> None:
        """!archive list выводит список архивированных чатов."""
        dialogs = [
            _make_dialog(11, "Чат первый"),
            _make_dialog(22, "Чат второй"),
        ]
        bot = _make_bot("list", dialogs=dialogs)
        message = _make_message()

        await handle_archive(bot, message)

        text = message.reply.await_args.args[0]
        assert "11" in text
        assert "Чат первый" in text
        assert "22" in text
        assert "Чат второй" in text

    @pytest.mark.asyncio
    async def test_archive_list_does_not_call_archive_api(self) -> None:
        """!archive list не архивирует — только читает список."""
        bot = _make_bot("list", dialogs=[])
        message = _make_message()

        await handle_archive(bot, message)

        bot.client.archive_chats.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_archive_list_empty_archive(self) -> None:
        """!archive list при пустом архиве сообщает об этом."""
        bot = _make_bot("list", dialogs=[])
        message = _make_message()

        await handle_archive(bot, message)

        text = message.reply.await_args.args[0]
        assert "пуст" in text.lower()

    @pytest.mark.asyncio
    async def test_archive_list_api_error_replies_with_error_text(self) -> None:
        """Если get_dialogs бросает исключение — ответ содержит ❌."""
        bot = _make_bot("list", dialogs_error=RuntimeError("No access"))
        message = _make_message()

        await handle_archive(bot, message)

        text = message.reply.await_args.args[0]
        assert "❌" in text

    @pytest.mark.asyncio
    async def test_archive_list_limits_to_20_chats(self) -> None:
        """!archive list возвращает не более 20 чатов."""
        dialogs = [_make_dialog(i, f"Chat {i}") for i in range(30)]
        bot = _make_bot("list", dialogs=dialogs)
        message = _make_message()

        await handle_archive(bot, message)

        text = message.reply.await_args.args[0]
        # Чат 20+ не должен быть в выводе
        assert "Chat 20" not in text

    @pytest.mark.asyncio
    async def test_archive_list_uses_first_name_fallback(self) -> None:
        """Для личных чатов без title берётся first_name."""
        async def _gen():
            chat = SimpleNamespace(id=55, title=None, first_name="Иван")
            yield SimpleNamespace(chat=chat)

        mock = MagicMock()
        mock.__aiter__ = lambda self: _gen()

        bot = _make_bot("list")
        bot.client.get_dialogs = MagicMock(return_value=mock)
        message = _make_message()

        await handle_archive(bot, message)

        text = message.reply.await_args.args[0]
        assert "Иван" in text


# ---------------------------------------------------------------------------
# handle_unarchive
# ---------------------------------------------------------------------------

class TestHandleUnarchive:
    @pytest.mark.asyncio
    async def test_unarchive_calls_pyrogram_api(self) -> None:
        """!unarchive вызывает client.unarchive_chats с текущим chat_id."""
        bot = _make_bot()
        message = _make_message(chat_id=77)

        await handle_unarchive(bot, message)

        bot.client.unarchive_chats.assert_awaited_once_with(77)

    @pytest.mark.asyncio
    async def test_unarchive_reply_contains_confirmation(self) -> None:
        """После разархивации бот отвечает подтверждением."""
        bot = _make_bot()
        message = _make_message()

        await handle_unarchive(bot, message)

        message.reply.assert_awaited_once()
        text = message.reply.await_args.args[0]
        assert "архив" in text.lower()

    @pytest.mark.asyncio
    async def test_unarchive_api_error_replies_with_error_text(self) -> None:
        """Если pyrogram бросает исключение — ответ содержит ❌."""
        bot = _make_bot(unarchive_error=RuntimeError("No rights"))
        message = _make_message()

        await handle_unarchive(bot, message)

        text = message.reply.await_args.args[0]
        assert "❌" in text
        assert "No rights" in text

    @pytest.mark.asyncio
    async def test_unarchive_non_owner_raises_user_input_error(self) -> None:
        """Не-owner получает UserInputError."""
        bot = _make_bot(access_level=AccessLevel.PARTIAL)
        message = _make_message()

        with pytest.raises(UserInputError):
            await handle_unarchive(bot, message)

        bot.client.unarchive_chats.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unarchive_uses_edit_when_self_message(self) -> None:
        """Если сообщение отправлено самим ботом — редактирует вместо reply."""
        bot = _make_bot()
        message = _make_message(from_user_id=bot.me.id)

        await handle_unarchive(bot, message)

        message.edit.assert_awaited_once()
        message.reply.assert_not_awaited()
