# -*- coding: utf-8 -*-
"""
Тесты обработчика !regex — тестирование регулярных выражений.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import _format_regex_result, handle_regex

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_bot(args: str = "") -> SimpleNamespace:
    """Мок бота с _get_command_args."""
    return SimpleNamespace(_get_command_args=lambda _msg: args)


def _make_message(reply_text: str | None = None) -> SimpleNamespace:
    """Мок сообщения с необязательным reply_to_message."""
    if reply_text is not None:
        reply = SimpleNamespace(text=reply_text, caption=None)
    else:
        reply = None
    return SimpleNamespace(reply=AsyncMock(), reply_to_message=reply)


# ---------------------------------------------------------------------------
# Тесты чистой функции _format_regex_result
# ---------------------------------------------------------------------------


class TestFormatRegexResult:
    """Тесты форматирования результата regex-матча."""

    def test_нет_совпадений(self):
        result = _format_regex_result(r"\d+", "abc")
        assert "Совпадений не найдено" in result
        assert r"\d+" in result

    def test_одно_совпадение_позиция(self):
        result = _format_regex_result(r"\d+", "abc123def")
        assert "Matches: 1" in result
        assert '"123"' in result
        assert "(3:6)" in result

    def test_несколько_совпадений_count(self):
        result = _format_regex_result(r"\d+", "1 2 3")
        assert "Matches: 3" in result

    def test_именованные_группы(self):
        result = _format_regex_result(r"(?P<word>\w+)", "hello")
        assert "word=" in result
        assert '"hello"' in result

    def test_позиционные_группы(self):
        result = _format_regex_result(r"(\d+)-(\d+)", "10-20")
        assert "group1=" in result
        assert "group2=" in result
        assert '"10"' in result
        assert '"20"' in result

    def test_паттерн_отображается_в_заголовке(self):
        result = _format_regex_result(r"foo", "foobar")
        assert "/foo/" in result

    def test_максимум_10_совпадений_в_ответе(self):
        # 15 совпадений — показываем только 10 + "и ещё N"
        text = " ".join(str(i) for i in range(15))
        result = _format_regex_result(r"\d+", text)
        assert "Matches: 15" in result
        assert "ещё 5" in result

    def test_длинное_совпадение_обрезается(self):
        long_text = "a" * 100
        result = _format_regex_result(r"a+", long_text)
        # Совпадение обрезается до 60 символов + "..."
        assert "..." in result

    def test_несколько_позиций_корректны(self):
        result = _format_regex_result(r"x", "axbxcx")
        assert "(1:2)" in result
        assert "(3:4)" in result
        assert "(5:6)" in result

    def test_unicode_текст(self):
        result = _format_regex_result(r"\w+", "привет мир")
        assert "Matches: 2" in result
        assert '"привет"' in result

    def test_emoji_в_тексте(self):
        # Emoji не захватываются \w, но матч должен работать
        result = _format_regex_result(r"\d", "abc 1 🎉 2")
        assert "Matches: 2" in result

    def test_groups_не_отображаются_без_групп(self):
        result = _format_regex_result(r"\d+", "123")
        # Нет групп — "Groups:" не должно быть в строке с совпадением
        assert "Groups:" not in result

    def test_пустая_группа_отображается(self):
        # Необязательная группа может быть None
        result = _format_regex_result(r"(\d+)?abc", "abc")
        # "None" не должен попасть в вывод как есть — проверяем что не падает
        assert "Matches: 1" in result


# ---------------------------------------------------------------------------
# Тесты handle_regex — базовые сценарии
# ---------------------------------------------------------------------------


class TestHandleRegexBasic:
    """Базовые сценарии команды !regex."""

    @pytest.mark.asyncio
    async def test_pattern_и_текст_в_аргументах(self):
        bot = _make_bot(r"\d+ abc123")
        msg = _make_message()
        await handle_regex(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "Matches: 1" in text
        assert '"123"' in text

    @pytest.mark.asyncio
    async def test_нет_совпадений_в_тексте(self):
        bot = _make_bot(r"\d+ abcdef")
        msg = _make_message()
        await handle_regex(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "Совпадений не найдено" in text

    @pytest.mark.asyncio
    async def test_несколько_совпадений(self):
        bot = _make_bot(r"\d+ foo1 bar2 baz3")
        msg = _make_message()
        await handle_regex(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "Matches: 3" in text

    @pytest.mark.asyncio
    async def test_ответ_reply_вызван_один_раз(self):
        bot = _make_bot(r"\w+ hello")
        msg = _make_message()
        await handle_regex(bot, msg)
        assert msg.reply.call_count == 1

    @pytest.mark.asyncio
    async def test_паттерн_отображается_в_ответе(self):
        bot = _make_bot(r"foo hello foo world")
        msg = _make_message()
        await handle_regex(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "/foo/" in text


# ---------------------------------------------------------------------------
# Тесты handle_regex — режим reply
# ---------------------------------------------------------------------------


class TestHandleRegexReply:
    """Режим reply: паттерн в аргументах, текст из reply."""

    @pytest.mark.asyncio
    async def test_текст_из_reply(self):
        bot = _make_bot(r"\d+")
        msg = _make_message(reply_text="foo 42 bar 99")
        await handle_regex(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "Matches: 2" in text
        assert '"42"' in text

    @pytest.mark.asyncio
    async def test_нет_совпадений_в_reply(self):
        bot = _make_bot(r"\d+")
        msg = _make_message(reply_text="нет цифр здесь")
        await handle_regex(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "Совпадений не найдено" in text

    @pytest.mark.asyncio
    async def test_аргументы_текст_приоритетнее_reply(self):
        """Если в аргументах есть текст, reply не используется."""
        bot = _make_bot(r"\d+ only_args_text")
        msg = _make_message(reply_text="reply 123 text")
        await handle_regex(bot, msg)
        text = msg.reply.await_args.args[0]
        # only_args_text не содержит цифр — нет совпадений
        assert "Совпадений не найдено" in text


# ---------------------------------------------------------------------------
# Тесты handle_regex — обработка ошибок
# ---------------------------------------------------------------------------


class TestHandleRegexErrors:
    """Обработка ошибочных входных данных."""

    @pytest.mark.asyncio
    async def test_пустые_аргументы_без_reply_вызывает_ошибку(self):
        bot = _make_bot("")
        msg = _make_message()
        with pytest.raises(UserInputError) as exc_info:
            await handle_regex(bot, msg)
        assert "regex" in exc_info.value.user_message.lower()

    @pytest.mark.asyncio
    async def test_невалидный_regex_вызывает_ошибку(self):
        bot = _make_bot(r"[invalid some text")
        msg = _make_message()
        with pytest.raises(UserInputError) as exc_info:
            await handle_regex(bot, msg)
        assert "regex" in exc_info.value.user_message.lower()

    @pytest.mark.asyncio
    async def test_паттерн_без_текста_и_без_reply_вызывает_ошибку(self):
        bot = _make_bot(r"\d+")
        msg = _make_message()  # нет reply, нет текста
        with pytest.raises(UserInputError):
            await handle_regex(bot, msg)

    @pytest.mark.asyncio
    async def test_справка_содержит_примеры(self):
        bot = _make_bot("")
        msg = _make_message()
        with pytest.raises(UserInputError) as exc_info:
            await handle_regex(bot, msg)
        err_text = exc_info.value.user_message
        assert "!regex" in err_text
        assert "паттерн" in err_text or "pattern" in err_text.lower()

    @pytest.mark.asyncio
    async def test_ошибка_без_текста_содержит_подсказку(self):
        bot = _make_bot(r"\w+")
        msg = _make_message()
        with pytest.raises(UserInputError) as exc_info:
            await handle_regex(bot, msg)
        assert "текст" in exc_info.value.user_message or "reply" in exc_info.value.user_message


# ---------------------------------------------------------------------------
# Тесты handle_regex — специальные паттерны
# ---------------------------------------------------------------------------


class TestHandleRegexPatterns:
    """Специальные regex паттерны."""

    @pytest.mark.asyncio
    async def test_паттерн_с_группой(self):
        bot = _make_bot(r"(\w+)@(\w+) user@domain")
        msg = _make_message()
        await handle_regex(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "group1=" in text
        assert "group2=" in text

    @pytest.mark.asyncio
    async def test_именованная_группа(self):
        bot = _make_bot(r"(?P<year>\d{4})-(?P<month>\d{2}) 2024-03")
        msg = _make_message()
        await handle_regex(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "year=" in text
        assert "month=" in text

    @pytest.mark.asyncio
    async def test_паттерн_anchors(self):
        bot = _make_bot(r"^\w+ hello world")
        msg = _make_message()
        await handle_regex(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "Matches: 1" in text
        assert '"hello"' in text

    @pytest.mark.asyncio
    async def test_паттерн_alternation(self):
        bot = _make_bot(r"cat|dog I have a cat and a dog")
        msg = _make_message()
        await handle_regex(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "Matches: 2" in text

    @pytest.mark.asyncio
    async def test_паттерн_case_sensitive(self):
        """По умолчанию паттерн case-sensitive."""
        bot = _make_bot(r"Hello Hello world")
        msg = _make_message()
        await handle_regex(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "Matches: 1" in text

    @pytest.mark.asyncio
    async def test_паттерн_с_пробелом_в_тексте(self):
        """Текст из нескольких слов после паттерна."""
        bot = _make_bot(r"\d+ one 2 three 4 five")
        msg = _make_message()
        await handle_regex(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "Matches: 2" in text

    @pytest.mark.asyncio
    async def test_паттерн_email(self):
        bot = _make_bot(r"[\w.+-]+@[\w-]+\.[a-z]+ contact foo@bar.com or test@example.org")
        msg = _make_message()
        await handle_regex(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "Matches: 2" in text

    @pytest.mark.asyncio
    async def test_паттерн_url(self):
        bot = _make_bot(r"https?://\S+ visit https://example.com and http://test.org")
        msg = _make_message()
        await handle_regex(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "Matches: 2" in text

    @pytest.mark.asyncio
    async def test_пустой_паттерн_матчит_всё(self):
        """Пустая строка как паттерн невалидна только если пустой args целиком."""
        # Паттерн "." матчит каждый символ
        bot = _make_bot(r". abc")
        msg = _make_message()
        await handle_regex(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "Matches: 3" in text

    @pytest.mark.asyncio
    async def test_unicode_паттерн(self):
        bot = _make_bot(r"[а-я]+ привет мир hello")
        msg = _make_message()
        await handle_regex(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "Matches: 2" in text

    @pytest.mark.asyncio
    async def test_span_корректен(self):
        bot = _make_bot(r"world hello world")
        msg = _make_message()
        await handle_regex(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "(6:11)" in text

    @pytest.mark.asyncio
    async def test_много_совпадений_показывает_ещё(self):
        """Более 10 совпадений → показываем "и ещё N"."""
        numbers = " ".join(str(i) for i in range(20))
        bot = _make_bot(r"\d+ " + numbers)
        msg = _make_message()
        await handle_regex(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "ещё 10" in text
