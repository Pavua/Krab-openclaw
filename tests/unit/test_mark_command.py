# -*- coding: utf-8 -*-
"""
Тесты owner-only команды !mark (пометка чатов как прочитанных/непрочитанных).

Покрываем:
1) !mark read          — вызывает read_chat_history для текущего чата;
2) !mark unread        — вызывает mark_chat_unread для текущего чата;
3) !mark readall       — итерирует get_dialogs и вызывает read_chat_history для каждого;
4) !mark readall с частичными ошибками — счётчик fail_count отображается;
5) !mark <пустой/неверный> — UserInputError с подсказкой;
6) Не-owner — UserInputError;
7) !mark read: pyrogram exception → ответ с ❌;
8) !mark unread: pyrogram exception → ответ с ❌;
9) !mark readall: get_dialogs exception → ответ с ❌;
10) Если команду отправил сам бот — edit вместо reply (read, unread);
11) !mark readall — ответ содержит количество обработанных чатов.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.access_control import AccessLevel, AccessProfile
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_mark


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------

def _make_dialog(chat_id: int) -> SimpleNamespace:
    """Mock pyrogram.Dialog с chat.id."""
    return SimpleNamespace(chat=SimpleNamespace(id=chat_id))


async def _async_dialogs(*dialogs):
    """Async-генератор диалогов для mock get_dialogs."""
    for d in dialogs:
        yield d


def _make_bot(
    args: str = "",
    *,
    access_level: AccessLevel = AccessLevel.OWNER,
    dialogs: list | None = None,
) -> SimpleNamespace:
    """Минимальный mock KraabUserbot."""
    dialog_list = dialogs if dialogs is not None else []

    bot = SimpleNamespace(
        me=SimpleNamespace(id=999),
        client=SimpleNamespace(
            read_chat_history=AsyncMock(),
            mark_chat_unread=AsyncMock(),
            get_dialogs=MagicMock(return_value=_async_dialogs(*dialog_list)),
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
# !mark read
# ---------------------------------------------------------------------------

class TestHandleMarkRead:
    @pytest.mark.asyncio
    async def test_read_calls_pyrogram_api(self) -> None:
        """!mark read вызывает client.read_chat_history с текущим chat_id."""
        bot = _make_bot("read")
        message = _make_message(chat_id=200)

        await handle_mark(bot, message)

        bot.client.read_chat_history.assert_awaited_once_with(chat_id=200)

    @pytest.mark.asyncio
    async def test_read_reply_contains_confirmation(self) -> None:
        """После !mark read бот отвечает подтверждением с ✅."""
        bot = _make_bot("read")
        message = _make_message()

        await handle_mark(bot, message)

        message.reply.assert_awaited_once()
        text = message.reply.await_args.args[0]
        assert "✅" in text
        assert "прочитанный" in text.lower()

    @pytest.mark.asyncio
    async def test_read_uses_edit_when_self_message(self) -> None:
        """Если команду отправил сам бот — edit вместо reply."""
        bot = _make_bot("read")
        message = _make_message(from_user_id=bot.me.id)

        await handle_mark(bot, message)

        message.edit.assert_awaited_once()
        message.reply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_read_pyrogram_exception_returns_error_text(self) -> None:
        """Если pyrogram бросает исключение — ответ содержит ❌."""
        bot = _make_bot("read")
        bot.client.read_chat_history = AsyncMock(side_effect=RuntimeError("Forbidden"))
        message = _make_message()

        await handle_mark(bot, message)

        text = message.reply.await_args.args[0]
        assert "❌" in text
        assert "Forbidden" in text


# ---------------------------------------------------------------------------
# !mark unread
# ---------------------------------------------------------------------------

class TestHandleMarkUnread:
    @pytest.mark.asyncio
    async def test_unread_calls_pyrogram_api(self) -> None:
        """!mark unread вызывает client.mark_chat_unread с текущим chat_id."""
        bot = _make_bot("unread")
        message = _make_message(chat_id=300)

        await handle_mark(bot, message)

        bot.client.mark_chat_unread.assert_awaited_once_with(chat_id=300)

    @pytest.mark.asyncio
    async def test_unread_reply_contains_confirmation(self) -> None:
        """После !mark unread бот отвечает подтверждением с 🔵."""
        bot = _make_bot("unread")
        message = _make_message()

        await handle_mark(bot, message)

        message.reply.assert_awaited_once()
        text = message.reply.await_args.args[0]
        assert "🔵" in text
        assert "непрочитанный" in text.lower()

    @pytest.mark.asyncio
    async def test_unread_uses_edit_when_self_message(self) -> None:
        """Если команду отправил сам бот — edit вместо reply."""
        bot = _make_bot("unread")
        message = _make_message(from_user_id=bot.me.id)

        await handle_mark(bot, message)

        message.edit.assert_awaited_once()
        message.reply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unread_pyrogram_exception_returns_error_text(self) -> None:
        """Если pyrogram бросает исключение — ответ содержит ❌."""
        bot = _make_bot("unread")
        bot.client.mark_chat_unread = AsyncMock(side_effect=RuntimeError("No rights"))
        message = _make_message()

        await handle_mark(bot, message)

        text = message.reply.await_args.args[0]
        assert "❌" in text
        assert "No rights" in text


# ---------------------------------------------------------------------------
# !mark readall
# ---------------------------------------------------------------------------

class TestHandleMarkReadAll:
    @pytest.mark.asyncio
    async def test_readall_iterates_all_dialogs(self) -> None:
        """!mark readall вызывает read_chat_history для каждого диалога."""
        dialogs = [_make_dialog(10), _make_dialog(20), _make_dialog(30)]
        bot = _make_bot("readall", dialogs=dialogs)
        message = _make_message()

        await handle_mark(bot, message)

        assert bot.client.read_chat_history.await_count == 3
        called_ids = [
            call.kwargs["chat_id"]
            for call in bot.client.read_chat_history.await_args_list
        ]
        assert set(called_ids) == {10, 20, 30}

    @pytest.mark.asyncio
    async def test_readall_reply_contains_count(self) -> None:
        """Ответ !mark readall содержит количество обработанных чатов."""
        dialogs = [_make_dialog(1), _make_dialog(2)]
        bot = _make_bot("readall", dialogs=dialogs)
        message = _make_message()

        await handle_mark(bot, message)

        text = message.reply.await_args.args[0]
        assert "2" in text
        assert "✅" in text

    @pytest.mark.asyncio
    async def test_readall_partial_failures_shown(self) -> None:
        """При частичных ошибках — fail_count отображается в ответе."""
        dialogs = [_make_dialog(1), _make_dialog(2), _make_dialog(3)]
        bot = _make_bot("readall", dialogs=dialogs)

        # Второй вызов падает
        call_count = 0

        async def _read_with_fail(chat_id):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("Flood wait")

        bot.client.read_chat_history = AsyncMock(side_effect=_read_with_fail)
        message = _make_message()

        await handle_mark(bot, message)

        text = message.reply.await_args.args[0]
        # Успешных должно быть 2, 1 провальный
        assert "2" in text
        assert "1" in text
        assert "⚠️" in text

    @pytest.mark.asyncio
    async def test_readall_empty_dialogs(self) -> None:
        """!mark readall без диалогов — 0 чатов в ответе."""
        bot = _make_bot("readall", dialogs=[])
        message = _make_message()

        await handle_mark(bot, message)

        bot.client.read_chat_history.assert_not_awaited()
        text = message.reply.await_args.args[0]
        assert "0" in text

    @pytest.mark.asyncio
    async def test_readall_get_dialogs_exception(self) -> None:
        """Если get_dialogs бросает исключение — ответ содержит ❌."""
        bot = _make_bot("readall")

        async def _failing_gen():
            raise RuntimeError("Network error")
            yield  # noqa: unreachable — нужен для async-генератора

        bot.client.get_dialogs = MagicMock(return_value=_failing_gen())
        message = _make_message()

        await handle_mark(bot, message)

        text = message.reply.await_args.args[0]
        assert "❌" in text
        assert "Network error" in text

    @pytest.mark.asyncio
    async def test_readall_uses_edit_when_self_message(self) -> None:
        """!mark readall — edit вместо reply если команда от самого бота."""
        dialogs = [_make_dialog(5)]
        bot = _make_bot("readall", dialogs=dialogs)
        message = _make_message(from_user_id=bot.me.id)

        await handle_mark(bot, message)

        message.edit.assert_awaited_once()
        message.reply.assert_not_awaited()


# ---------------------------------------------------------------------------
# Доступ и невалидные подкоманды
# ---------------------------------------------------------------------------

class TestHandleMarkAccess:
    @pytest.mark.asyncio
    async def test_non_owner_raises_user_input_error(self) -> None:
        """Не-owner получает UserInputError для любой подкоманды."""
        bot = _make_bot("read", access_level=AccessLevel.FULL)
        message = _make_message()

        with pytest.raises(UserInputError):
            await handle_mark(bot, message)

        bot.client.read_chat_history.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_owner_partial_access_blocked(self) -> None:
        """Частичный доступ тоже блокируется."""
        bot = _make_bot("unread", access_level=AccessLevel.PARTIAL)
        message = _make_message()

        with pytest.raises(UserInputError):
            await handle_mark(bot, message)

        bot.client.mark_chat_unread.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_subcommand_raises_user_input_error(self) -> None:
        """Неизвестная подкоманда — UserInputError с подсказкой."""
        bot = _make_bot("unknown")
        message = _make_message()

        with pytest.raises(UserInputError) as exc_info:
            await handle_mark(bot, message)

        msg = exc_info.value.user_message
        assert "read" in msg
        assert "unread" in msg
        assert "readall" in msg

    @pytest.mark.asyncio
    async def test_empty_args_raises_user_input_error(self) -> None:
        """Пустые аргументы — UserInputError с подсказкой."""
        bot = _make_bot("")
        message = _make_message()

        with pytest.raises(UserInputError) as exc_info:
            await handle_mark(bot, message)

        msg = exc_info.value.user_message
        assert "!mark" in msg
