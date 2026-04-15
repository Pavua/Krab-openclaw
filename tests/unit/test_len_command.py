# -*- coding: utf-8 -*-
"""
Тесты команды !len / !count — статистика текста.

Покрываем:
- базовый подсчёт символов, слов, строк
- текст из reply-сообщения
- caption из reply-медиа
- пустой ввод → UserInputError
- reply без текста → UserInputError
- аргумент приоритетнее reply
- многострочный текст
- unicode / emoji
- склонения (1 символ, 2 слова, 5 строк и т.д.)
- контрольные значения
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_len


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
) -> MagicMock:
    msg = MagicMock()
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
# Вспомогательная функция подсчёта
# ---------------------------------------------------------------------------


def _stats(text: str) -> tuple[int, int, int]:
    """Возвращает (chars, words, lines) — аналог логики хендлера."""
    chars = len(text)
    words = len(text.split())
    lines = len(text.splitlines()) or 1
    return chars, words, lines


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


class TestHandleLen:
    """Тесты !len / !count."""

    @pytest.mark.asyncio
    async def test_базовый_подсчёт(self) -> None:
        """!len hello world → 11 символов, 2 слова, 1 строка."""
        bot = _make_bot(command_args="hello world")
        msg = _make_message()

        await handle_len(bot, msg)

        msg.reply.assert_awaited_once()
        response = msg.reply.call_args[0][0]
        assert "11" in response
        assert "2" in response
        assert "1" in response
        assert "📏" in response

    @pytest.mark.asyncio
    async def test_формат_ответа(self) -> None:
        """Формат ответа содержит 'символ', 'слов', 'строк'."""
        bot = _make_bot(command_args="тест")
        msg = _make_message()

        await handle_len(bot, msg)

        response = msg.reply.call_args[0][0]
        assert "символ" in response
        assert "слов" in response or "слово" in response or "слова" in response
        assert "строк" in response or "строка" in response or "строки" in response

    @pytest.mark.asyncio
    async def test_reply_текст(self) -> None:
        """!len без аргументов в reply на текстовое сообщение."""
        bot = _make_bot(command_args="")
        msg = _make_message(reply_text="привет мир")

        await handle_len(bot, msg)

        response = msg.reply.call_args[0][0]
        chars, words, lines = _stats("привет мир")
        assert str(chars) in response
        assert str(words) in response

    @pytest.mark.asyncio
    async def test_reply_caption(self) -> None:
        """!len без аргументов в reply на медиа с подписью."""
        bot = _make_bot(command_args="")
        msg = _make_message(reply_text="", reply_caption="подпись к фото")

        await handle_len(bot, msg)

        response = msg.reply.call_args[0][0]
        chars, _, _ = _stats("подпись к фото")
        assert str(chars) in response

    @pytest.mark.asyncio
    async def test_пустой_ввод_вызывает_ошибку(self) -> None:
        """Нет аргументов и нет reply → UserInputError."""
        bot = _make_bot(command_args="")
        msg = _make_message()

        with pytest.raises(UserInputError):
            await handle_len(bot, msg)

    @pytest.mark.asyncio
    async def test_reply_без_текста_вызывает_ошибку(self) -> None:
        """Reply без текста и без caption → UserInputError."""
        bot = _make_bot(command_args="")
        msg = _make_message(reply_text="", reply_caption="")

        with pytest.raises(UserInputError):
            await handle_len(bot, msg)

    @pytest.mark.asyncio
    async def test_аргумент_приоритетнее_reply(self) -> None:
        """Если есть аргументы И reply — используются аргументы."""
        bot = _make_bot(command_args="один два три")
        msg = _make_message(reply_text="совсем другой текст здесь")

        await handle_len(bot, msg)

        response = msg.reply.call_args[0][0]
        chars, _, _ = _stats("один два три")
        assert str(chars) in response

    @pytest.mark.asyncio
    async def test_многострочный_текст(self) -> None:
        """Многострочный текст корректно считает строки."""
        text = "первая строка\nвторая строка\nтретья строка"
        bot = _make_bot(command_args=text)
        msg = _make_message()

        await handle_len(bot, msg)

        response = msg.reply.call_args[0][0]
        assert "3" in response  # 3 строки

    @pytest.mark.asyncio
    async def test_одна_строка_по_умолчанию(self) -> None:
        """Однострочный текст → 1 строка."""
        bot = _make_bot(command_args="просто текст без переносов")
        msg = _make_message()

        await handle_len(bot, msg)

        response = msg.reply.call_args[0][0]
        # Проверяем что "1" есть в ответе (строки)
        assert "1" in response

    @pytest.mark.asyncio
    async def test_unicode_emoji(self) -> None:
        """Unicode и emoji корректно считаются."""
        text = "Привет 🦀"
        bot = _make_bot(command_args=text)
        msg = _make_message()

        await handle_len(bot, msg)

        response = msg.reply.call_args[0][0]
        chars, _, _ = _stats(text)
        assert str(chars) in response

    @pytest.mark.asyncio
    async def test_одно_слово(self) -> None:
        """Одно слово — 1 слово."""
        bot = _make_bot(command_args="краб")
        msg = _make_message()

        await handle_len(bot, msg)

        response = msg.reply.call_args[0][0]
        assert "1" in response  # 1 слово
        assert "слово" in response

    @pytest.mark.asyncio
    async def test_склонение_один_символ(self) -> None:
        """1 символ → 'символ' (не 'символа' и не 'символов')."""
        bot = _make_bot(command_args="я")
        msg = _make_message()

        await handle_len(bot, msg)

        response = msg.reply.call_args[0][0]
        assert "1 символ" in response

    @pytest.mark.asyncio
    async def test_склонение_два_символа(self) -> None:
        """2 символа → 'символа'."""
        bot = _make_bot(command_args="ab")
        msg = _make_message()

        await handle_len(bot, msg)

        response = msg.reply.call_args[0][0]
        assert "2 символа" in response

    @pytest.mark.asyncio
    async def test_склонение_пять_символов(self) -> None:
        """5 символов → 'символов'."""
        bot = _make_bot(command_args="abcde")
        msg = _make_message()

        await handle_len(bot, msg)

        response = msg.reply.call_args[0][0]
        assert "5 символов" in response

    @pytest.mark.asyncio
    async def test_склонение_одиннадцать(self) -> None:
        """11 символов → 'символов' (исключение: 11-19)."""
        bot = _make_bot(command_args="abcdefghijk")  # 11 символов
        msg = _make_message()

        await handle_len(bot, msg)

        response = msg.reply.call_args[0][0]
        assert "11 символов" in response

    @pytest.mark.asyncio
    async def test_два_слова(self) -> None:
        """2 слова → 'слова'."""
        bot = _make_bot(command_args="ab cd")
        msg = _make_message()

        await handle_len(bot, msg)

        response = msg.reply.call_args[0][0]
        assert "2 слова" in response

    @pytest.mark.asyncio
    async def test_пять_слов(self) -> None:
        """5 слов → 'слов'."""
        bot = _make_bot(command_args="один два три четыре пять")
        msg = _make_message()

        await handle_len(bot, msg)

        response = msg.reply.call_args[0][0]
        assert "5 слов" in response

    @pytest.mark.asyncio
    async def test_контрольное_значение(self) -> None:
        """Контрольные значения для 'hello world'."""
        bot = _make_bot(command_args="hello world")
        msg = _make_message()

        await handle_len(bot, msg)

        response = msg.reply.call_args[0][0]
        # "hello world" = 11 символов, 2 слова, 1 строка
        assert "11" in response
        assert "2" in response

    @pytest.mark.asyncio
    async def test_только_пробелы_вызывает_ошибку(self) -> None:
        """Только пробелы в аргументах → UserInputError."""
        bot = _make_bot(command_args="   ")
        msg = _make_message()

        with pytest.raises(UserInputError):
            await handle_len(bot, msg)

    @pytest.mark.asyncio
    async def test_сообщение_об_ошибке_содержит_подсказку(self) -> None:
        """Сообщение UserInputError содержит !len и !count."""
        bot = _make_bot(command_args="")
        msg = _make_message()

        with pytest.raises(UserInputError) as exc_info:
            await handle_len(bot, msg)

        assert "!len" in exc_info.value.user_message
        assert "!count" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_многострочный_пять_строк(self) -> None:
        """5 строк → 'строк' (склонение)."""
        text = "а\nб\nв\nг\nд"
        bot = _make_bot(command_args=text)
        msg = _make_message()

        await handle_len(bot, msg)

        response = msg.reply.call_args[0][0]
        assert "5 строк" in response

    @pytest.mark.asyncio
    async def test_две_строки(self) -> None:
        """2 строки → 'строки' (склонение)."""
        text = "первая\nвторая"
        bot = _make_bot(command_args=text)
        msg = _make_message()

        await handle_len(bot, msg)

        response = msg.reply.call_args[0][0]
        assert "2 строки" in response
