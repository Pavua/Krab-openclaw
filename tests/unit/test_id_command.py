# -*- coding: utf-8 -*-
"""
Тесты для команды !id — показать ID текущего чата, себя и (если reply) сообщения/автора.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.handlers.command_handlers import handle_id

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_me(user_id: int = 999000999) -> MagicMock:
    """Mock-объект «себя» (get_me)."""
    me = MagicMock()
    me.id = user_id
    return me


def _make_bot(me_id: int = 999000999) -> MagicMock:
    """Mock-бот с client.get_me."""
    bot = MagicMock()
    bot.client = MagicMock()
    bot.client.get_me = AsyncMock(return_value=_make_me(me_id))
    return bot


def _make_message(
    chat_id: int = -1001234567890,
    reply_msg_id: int | None = None,
    reply_from_id: int | None = None,
) -> MagicMock:
    """Mock-сообщение с optional reply_to_message."""
    msg = MagicMock()
    msg.reply = AsyncMock()
    msg.chat = MagicMock()
    msg.chat.id = chat_id

    if reply_msg_id is not None:
        reply = MagicMock()
        reply.id = reply_msg_id
        if reply_from_id is not None:
            reply.from_user = MagicMock()
            reply.from_user.id = reply_from_id
        else:
            reply.from_user = None
        msg.reply_to_message = reply
    else:
        msg.reply_to_message = None

    return msg


# ---------------------------------------------------------------------------
# Тесты: базовый вызов без reply
# ---------------------------------------------------------------------------


class TestIdCommandBasic:
    """!id без reply — показывает chat_id и user_id."""

    @pytest.mark.asyncio
    async def test_ответ_содержит_заголовок(self) -> None:
        """Ответ содержит '🆔 IDs'."""
        bot = _make_bot()
        msg = _make_message()

        await handle_id(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "🆔 IDs" in reply_text

    @pytest.mark.asyncio
    async def test_ответ_содержит_chat_id(self) -> None:
        """Ответ содержит ID текущего чата."""
        bot = _make_bot()
        msg = _make_message(chat_id=-1001234567890)

        await handle_id(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "-1001234567890" in reply_text

    @pytest.mark.asyncio
    async def test_ответ_содержит_user_id(self) -> None:
        """Ответ содержит user_id бота."""
        bot = _make_bot(me_id=123456789)
        msg = _make_message()

        await handle_id(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "123456789" in reply_text

    @pytest.mark.asyncio
    async def test_формат_chat_line(self) -> None:
        """Строка chat_id в формате 'Chat: `<id>`'."""
        bot = _make_bot()
        msg = _make_message(chat_id=-100987654321)

        await handle_id(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Chat: `-100987654321`" in reply_text

    @pytest.mark.asyncio
    async def test_формат_user_line(self) -> None:
        """Строка user_id в формате 'User: `<id>`'."""
        bot = _make_bot(me_id=555111555)
        msg = _make_message()

        await handle_id(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "User: `555111555`" in reply_text

    @pytest.mark.asyncio
    async def test_без_reply_нет_message_id(self) -> None:
        """Без reply — строка 'Message:' отсутствует."""
        bot = _make_bot()
        msg = _make_message()

        await handle_id(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Message:" not in reply_text

    @pytest.mark.asyncio
    async def test_без_reply_нет_author_id(self) -> None:
        """Без reply — строка 'Author:' отсутствует."""
        bot = _make_bot()
        msg = _make_message()

        await handle_id(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Author:" not in reply_text

    @pytest.mark.asyncio
    async def test_reply_вызывается_один_раз(self) -> None:
        """message.reply вызывается ровно один раз."""
        bot = _make_bot()
        msg = _make_message()

        await handle_id(bot, msg)

        msg.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_вызов_get_me(self) -> None:
        """bot.client.get_me вызывается для получения своего ID."""
        bot = _make_bot()
        msg = _make_message()

        await handle_id(bot, msg)

        bot.client.get_me.assert_awaited_once()


# ---------------------------------------------------------------------------
# Тесты: вызов в reply
# ---------------------------------------------------------------------------


class TestIdCommandReply:
    """!id в reply — показывает message_id и user_id автора."""

    @pytest.mark.asyncio
    async def test_reply_содержит_message_id(self) -> None:
        """ID реплайнутого сообщения присутствует."""
        bot = _make_bot()
        msg = _make_message(reply_msg_id=42, reply_from_id=111222333)

        await handle_id(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Message: `42`" in reply_text

    @pytest.mark.asyncio
    async def test_reply_содержит_author_id(self) -> None:
        """User_id автора реплайнутого сообщения присутствует."""
        bot = _make_bot()
        msg = _make_message(reply_msg_id=77, reply_from_id=555666777)

        await handle_id(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Author: `555666777`" in reply_text

    @pytest.mark.asyncio
    async def test_reply_содержит_chat_id(self) -> None:
        """При reply всё равно выводится chat_id."""
        bot = _make_bot()
        msg = _make_message(chat_id=-1001111111111, reply_msg_id=10, reply_from_id=9)

        await handle_id(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "-1001111111111" in reply_text

    @pytest.mark.asyncio
    async def test_reply_содержит_user_id_себя(self) -> None:
        """При reply всё равно выводится свой user_id."""
        bot = _make_bot(me_id=987654321)
        msg = _make_message(reply_msg_id=5, reply_from_id=1)

        await handle_id(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "987654321" in reply_text

    @pytest.mark.asyncio
    async def test_reply_без_from_user_нет_author_line(self) -> None:
        """Если у реплайнутого сообщения нет from_user (анонимный/канал) — строка Author: не выводится."""
        bot = _make_bot()
        msg = _make_message(reply_msg_id=100, reply_from_id=None)

        await handle_id(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Message: `100`" in reply_text
        assert "Author:" not in reply_text

    @pytest.mark.asyncio
    async def test_reply_порядок_строк(self) -> None:
        """Порядок: 🆔 IDs → Chat → User → Message → Author."""
        bot = _make_bot(me_id=10)
        msg = _make_message(chat_id=-100, reply_msg_id=42, reply_from_id=99)

        await handle_id(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        lines = reply_text.split("\n")

        assert lines[0] == "🆔 IDs"
        assert "Chat:" in lines[1]
        assert "User:" in lines[2]
        assert "Message:" in lines[3]
        assert "Author:" in lines[4]


# ---------------------------------------------------------------------------
# Тесты: крайние случаи
# ---------------------------------------------------------------------------


class TestIdCommandEdgeCases:
    """Граничные случаи !id."""

    @pytest.mark.asyncio
    async def test_личный_чат_положительный_chat_id(self) -> None:
        """В личном чате chat_id — положительное число."""
        bot = _make_bot()
        msg = _make_message(chat_id=123456789)

        await handle_id(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Chat: `123456789`" in reply_text

    @pytest.mark.asyncio
    async def test_большой_chat_id(self) -> None:
        """Большой отрицательный chat_id корректно отображается."""
        bot = _make_bot()
        msg = _make_message(chat_id=-1002345678901)

        await handle_id(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "-1002345678901" in reply_text

    @pytest.mark.asyncio
    async def test_нулевой_message_id_в_reply(self) -> None:
        """message_id=0 (edge case) корректно выводится."""
        bot = _make_bot()
        msg = _make_message(reply_msg_id=0, reply_from_id=1)

        await handle_id(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Message: `0`" in reply_text

    @pytest.mark.asyncio
    async def test_id_в_backticks(self) -> None:
        """Все ID обёрнуты в backticks для моноширинного шрифта."""
        bot = _make_bot(me_id=111)
        msg = _make_message(chat_id=-222, reply_msg_id=333, reply_from_id=444)

        await handle_id(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "`-222`" in reply_text
        assert "`111`" in reply_text
        assert "`333`" in reply_text
        assert "`444`" in reply_text
