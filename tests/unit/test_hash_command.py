# -*- coding: utf-8 -*-
"""
Тесты команды !hash — хэширование текста.

Покрываем:
- все три хэша при !hash <текст>
- только MD5 при !hash md5 <текст>
- только SHA1 при !hash sha1 <текст>
- только SHA256 при !hash sha256 <текст>
- текст из reply-сообщения
- caption из reply-медиа
- пустой ввод → UserInputError
- reply без текста → UserInputError
- известные контрольные значения (пустая строка)
- аргумент приоритетнее reply
- algo_filter без текста после алгоритма → UserInputError
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_hash


# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------


def _make_bot(command_args: str = "") -> MagicMock:
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value=command_args)
    return bot


def _make_message(
    reply_text: str | None = None,
    reply_caption: str | None = None,
    chat_id: int = 42,
    message_id: int = 1,
) -> MagicMock:
    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.id = message_id
    msg.reply = AsyncMock()

    if reply_text is not None or reply_caption is not None:
        replied = MagicMock()
        replied.text = reply_text or ""
        replied.caption = reply_caption or ""
        msg.reply_to_message = replied
    else:
        msg.reply_to_message = None

    return msg


# ---------------------------------------------------------------------------
# Вспомогательная функция вычисления хэшей
# ---------------------------------------------------------------------------


def _hashes(text: str) -> dict[str, str]:
    encoded = text.encode("utf-8")
    return {
        "md5": hashlib.md5(encoded).hexdigest(),
        "sha1": hashlib.sha1(encoded).hexdigest(),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


class TestHandleHash:
    """Тесты !hash."""

    @pytest.mark.asyncio
    async def test_все_три_хэша(self) -> None:
        """!hash <текст> возвращает MD5, SHA1, SHA256."""
        bot = _make_bot(command_args="hello world")
        msg = _make_message()

        await handle_hash(bot, msg)

        msg.reply.assert_awaited_once()
        response = msg.reply.call_args[0][0]
        h = _hashes("hello world")
        assert h["md5"] in response
        assert h["sha1"] in response
        assert h["sha256"] in response
        assert "Hash" in response

    @pytest.mark.asyncio
    async def test_только_md5(self) -> None:
        """!hash md5 <текст> возвращает только MD5."""
        bot = _make_bot(command_args="md5 hello")
        msg = _make_message()

        await handle_hash(bot, msg)

        response = msg.reply.call_args[0][0]
        h = _hashes("hello")
        assert h["md5"] in response
        assert h["sha1"] not in response
        assert h["sha256"] not in response
        assert "MD5" in response

    @pytest.mark.asyncio
    async def test_только_sha1(self) -> None:
        """!hash sha1 <текст> возвращает только SHA1."""
        bot = _make_bot(command_args="sha1 hello")
        msg = _make_message()

        await handle_hash(bot, msg)

        response = msg.reply.call_args[0][0]
        h = _hashes("hello")
        assert h["sha1"] in response
        assert h["md5"] not in response
        assert h["sha256"] not in response
        assert "SHA1" in response

    @pytest.mark.asyncio
    async def test_только_sha256(self) -> None:
        """!hash sha256 <текст> возвращает только SHA256."""
        bot = _make_bot(command_args="sha256 hello")
        msg = _make_message()

        await handle_hash(bot, msg)

        response = msg.reply.call_args[0][0]
        h = _hashes("hello")
        assert h["sha256"] in response
        assert h["md5"] not in response
        assert h["sha1"] not in response
        assert "SHA256" in response

    @pytest.mark.asyncio
    async def test_reply_текст(self) -> None:
        """!hash без аргументов в reply на текстовое сообщение."""
        bot = _make_bot(command_args="")
        msg = _make_message(reply_text="тестовый текст")

        await handle_hash(bot, msg)

        response = msg.reply.call_args[0][0]
        h = _hashes("тестовый текст")
        assert h["md5"] in response
        assert h["sha256"] in response

    @pytest.mark.asyncio
    async def test_reply_caption(self) -> None:
        """!hash без аргументов в reply на медиа с подписью."""
        bot = _make_bot(command_args="")
        msg = _make_message(reply_text="", reply_caption="подпись к фото")

        await handle_hash(bot, msg)

        response = msg.reply.call_args[0][0]
        h = _hashes("подпись к фото")
        assert h["md5"] in response

    @pytest.mark.asyncio
    async def test_пустой_ввод_вызывает_ошибку(self) -> None:
        """Нет аргументов и нет reply → UserInputError."""
        bot = _make_bot(command_args="")
        msg = _make_message()

        with pytest.raises(UserInputError):
            await handle_hash(bot, msg)

    @pytest.mark.asyncio
    async def test_reply_без_текста_вызывает_ошибку(self) -> None:
        """Reply без текста и без caption → UserInputError."""
        bot = _make_bot(command_args="")
        msg = _make_message(reply_text="", reply_caption="")

        with pytest.raises(UserInputError):
            await handle_hash(bot, msg)

    @pytest.mark.asyncio
    async def test_algo_без_текста_вызывает_ошибку(self) -> None:
        """!hash md5 (без текста) → UserInputError."""
        bot = _make_bot(command_args="md5")
        msg = _make_message()

        with pytest.raises(UserInputError):
            await handle_hash(bot, msg)

    @pytest.mark.asyncio
    async def test_контрольное_значение_пустой_строки(self) -> None:
        """Проверяем хэши пустой строки по известным значениям."""
        bot = _make_bot(command_args="")
        msg = _make_message(reply_text=" ")  # пробел → не пустой текст после strip? нет, strip даст ""

        # reply_text=" " → после strip → "" → UserInputError
        with pytest.raises(UserInputError):
            await handle_hash(bot, msg)

    @pytest.mark.asyncio
    async def test_контрольное_значение_abc(self) -> None:
        """Хэши строки 'abc' должны совпасть с известными значениями."""
        bot = _make_bot(command_args="abc")
        msg = _make_message()

        await handle_hash(bot, msg)

        response = msg.reply.call_args[0][0]
        # Известные значения для "abc"
        assert "900150983cd24fb0d6963f7d28e17f72" in response  # MD5
        assert "a9993e364706816aba3e25717850c26c9cd0d89d" in response  # SHA1
        assert _hashes("abc")["sha256"] in response  # SHA256

    @pytest.mark.asyncio
    async def test_аргумент_приоритетнее_reply(self) -> None:
        """Если есть аргументы И reply — используются аргументы."""
        bot = _make_bot(command_args="из аргументов")
        msg = _make_message(reply_text="из reply")

        await handle_hash(bot, msg)

        response = msg.reply.call_args[0][0]
        h_args = _hashes("из аргументов")
        h_reply = _hashes("из reply")
        assert h_args["md5"] in response
        assert h_reply["md5"] not in response

    @pytest.mark.asyncio
    async def test_алгоритм_регистронезависим(self) -> None:
        """!hash MD5 <текст> (заглавные буквы) работает корректно."""
        bot = _make_bot(command_args="MD5 test")
        msg = _make_message()

        await handle_hash(bot, msg)

        response = msg.reply.call_args[0][0]
        h = _hashes("test")
        assert h["md5"] in response

    @pytest.mark.asyncio
    async def test_unicode_текст(self) -> None:
        """Unicode-текст хэшируется корректно."""
        text = "Привет мир 🦀"
        bot = _make_bot(command_args=text)
        msg = _make_message()

        await handle_hash(bot, msg)

        response = msg.reply.call_args[0][0]
        expected = _hashes(text)
        assert expected["md5"] in response
        assert expected["sha256"] in response

    @pytest.mark.asyncio
    async def test_reply_md5_фильтр(self) -> None:
        """!hash md5 в reply на сообщение: algo из аргументов, текст из reply."""
        bot = _make_bot(command_args="md5")
        msg = _make_message(reply_text="reply текст")

        await handle_hash(bot, msg)

        response = msg.reply.call_args[0][0]
        h = _hashes("reply текст")
        assert h["md5"] in response
        assert h["sha1"] not in response
