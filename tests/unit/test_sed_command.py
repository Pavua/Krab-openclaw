# -*- coding: utf-8 -*-
"""
Тесты для !sed — IRC-style regex замена в сообщениях.
Покрывает: _parse_sed_expr, handle_sed (своё/чужое сообщение).
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import _parse_sed_expr, handle_sed


# ---------------------------------------------------------------------------
# _parse_sed_expr — unit-тесты парсера
# ---------------------------------------------------------------------------


class TestParseSedExprBasic:
    """Базовые случаи парсинга s/old/new/ выражения."""

    def test_simple(self) -> None:
        compiled, replacement, count = _parse_sed_expr("s/old/new/")
        assert replacement == "new"
        assert count == 1  # без g — первое вхождение
        assert compiled.pattern == "old"

    def test_no_trailing_sep(self) -> None:
        """s/old/new без завершающего / тоже должно работать."""
        compiled, replacement, count = _parse_sed_expr("s/old/new")
        assert replacement == "new"
        assert count == 1

    def test_global_flag(self) -> None:
        compiled, replacement, count = _parse_sed_expr("s/foo/bar/g")
        assert count == 0  # 0 = все совпадения (re.sub count=0)
        assert replacement == "bar"

    def test_case_insensitive_flag(self) -> None:
        compiled, replacement, count = _parse_sed_expr("s/hello/world/i")
        assert compiled.flags & re.IGNORECASE
        assert count == 1

    def test_global_and_case_insensitive(self) -> None:
        compiled, replacement, count = _parse_sed_expr("s/abc/xyz/gi")
        assert count == 0
        assert compiled.flags & re.IGNORECASE

    def test_alternative_separator(self) -> None:
        """sed поддерживает произвольный разделитель."""
        compiled, replacement, count = _parse_sed_expr("s|foo|bar|")
        assert compiled.pattern == "foo"
        assert replacement == "bar"

    def test_empty_replacement(self) -> None:
        """Замена на пустую строку — удаление паттерна."""
        compiled, replacement, count = _parse_sed_expr("s/word//")
        assert replacement == ""

    def test_regex_pattern(self) -> None:
        compiled, replacement, count = _parse_sed_expr(r"s/\d+/NUM/g")
        assert count == 0
        result = compiled.sub(replacement, "a1b2c3")
        assert result == "aNUMbNUMcNUM"


class TestParseSedExprErrors:
    """Ошибочные форматы выражений."""

    def test_not_s_command(self) -> None:
        with pytest.raises(ValueError, match="начинаться с 's'"):
            _parse_sed_expr("p/old/new/")

    def test_empty_expression(self) -> None:
        with pytest.raises(ValueError):
            _parse_sed_expr("s")

    def test_unknown_flag(self) -> None:
        with pytest.raises(ValueError, match="Неизвестные флаги"):
            _parse_sed_expr("s/a/b/x")

    def test_invalid_regex(self) -> None:
        with pytest.raises(ValueError, match="Ошибка в regex"):
            _parse_sed_expr("s/[invalid/new/")

    def test_missing_replacement(self) -> None:
        """s/only_one_part — нет replacement."""
        with pytest.raises(ValueError):
            _parse_sed_expr("s/onlyone")


class TestParseSedExprSubstitution:
    """Проверяем, что паттерн реально применяется корректно."""

    def test_first_occurrence_only(self) -> None:
        compiled, replacement, count = _parse_sed_expr("s/a/X/")
        result, n = compiled.subn(replacement, "aaa", count=count)
        assert result == "Xaa"
        assert n == 1

    def test_global_all_occurrences(self) -> None:
        compiled, replacement, count = _parse_sed_expr("s/a/X/g")
        result, n = compiled.subn(replacement, "aaa", count=count)
        assert result == "XXX"
        assert n == 3

    def test_case_insensitive_replaces(self) -> None:
        compiled, replacement, count = _parse_sed_expr("s/hello/Hi/i")
        result, n = compiled.subn(replacement, "HELLO world")
        assert result == "Hi world"

    def test_captures_in_replacement(self) -> None:
        """Backreferences \\1 в замене (без g — только первое вхождение)."""
        compiled, replacement, count = _parse_sed_expr(r"s/(foo)/[\1]/")
        result, _ = compiled.subn(replacement, "foo bar baz", count=count)
        assert result == "[foo] bar baz"


# ---------------------------------------------------------------------------
# handle_sed — интеграционные тесты обработчика
# ---------------------------------------------------------------------------


def _make_bot(me_id: int = 42) -> MagicMock:
    """Создаёт мок бота с client.get_me()."""
    bot = MagicMock()
    me = SimpleNamespace(id=me_id)
    bot.client.get_me = AsyncMock(return_value=me)
    bot.client.edit_message_text = AsyncMock()
    return bot


def _make_message(
    cmd_args: list[str],
    reply_text: str | None = "оригинальный текст",
    reply_from_id: int | None = 99,  # чужое сообщение
) -> MagicMock:
    """Создаёт мок Telegram сообщения с reply_to_message."""
    msg = MagicMock()
    msg.text = "!" + " ".join(cmd_args)
    msg.command = cmd_args  # pyrogram: ['sed', 's/old/new/']
    msg.reply = AsyncMock()
    msg.delete = AsyncMock()

    if reply_text is not None:
        reply_msg = MagicMock()
        reply_msg.text = reply_text
        reply_msg.caption = None
        reply_msg.id = 100
        reply_msg.chat = SimpleNamespace(id=-1001)
        if reply_from_id is not None:
            reply_msg.from_user = SimpleNamespace(id=reply_from_id)
        else:
            reply_msg.from_user = None
        msg.reply_to_message = reply_msg
    else:
        msg.reply_to_message = None

    return msg


class TestHandleSedHelp:
    """Справка при вызове без аргументов."""

    @pytest.mark.asyncio
    async def test_no_args_shows_help(self) -> None:
        bot = _make_bot()
        msg = _make_message(["sed"])  # только имя команды, без выражения
        await handle_sed(bot, msg)
        msg.reply.assert_called_once()
        text = msg.reply.call_args[0][0]
        assert "!sed" in text
        assert "s/old/new/" in text


class TestHandleSedErrors:
    """Ошибочные вызовы — UserInputError."""

    @pytest.mark.asyncio
    async def test_no_reply_target(self) -> None:
        bot = _make_bot()
        msg = _make_message(["sed", "s/a/b/"], reply_text=None)
        with pytest.raises(UserInputError) as exc_info:
            await handle_sed(bot, msg)
        assert "reply" in exc_info.value.user_message.lower()

    @pytest.mark.asyncio
    async def test_invalid_sed_expr(self) -> None:
        bot = _make_bot()
        msg = _make_message(["sed", "p/a/b/"])
        with pytest.raises(UserInputError):
            await handle_sed(bot, msg)

    @pytest.mark.asyncio
    async def test_reply_has_no_text(self) -> None:
        bot = _make_bot()
        msg = _make_message(["sed", "s/a/b/"])
        msg.reply_to_message.text = ""
        msg.reply_to_message.caption = None
        with pytest.raises(UserInputError) as exc_info:
            await handle_sed(bot, msg)
        assert "не содержит текста" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_pattern_not_found(self) -> None:
        bot = _make_bot()
        msg = _make_message(["sed", "s/xyz/abc/"], reply_text="нет такого слова")
        await handle_sed(bot, msg)
        msg.reply.assert_called_once()
        assert "не найден" in msg.reply.call_args[0][0]


class TestHandleSedOwnMessage:
    """Замена в собственном сообщении — edit_message_text."""

    @pytest.mark.asyncio
    async def test_edits_own_message(self) -> None:
        bot = _make_bot(me_id=42)
        msg = _make_message(
            ["sed", "s/опечатка/слово/"],
            reply_text="тут опечатка в тексте",
            reply_from_id=42,  # то же что me_id
        )
        await handle_sed(bot, msg)
        bot.client.edit_message_text.assert_called_once()
        kwargs = bot.client.edit_message_text.call_args
        assert "слово" in kwargs[1]["text"]

    @pytest.mark.asyncio
    async def test_edits_own_message_global(self) -> None:
        bot = _make_bot(me_id=42)
        msg = _make_message(
            ["sed", "s/а/А/g"],
            reply_text="апельсин аист арбуз",
            reply_from_id=42,
        )
        await handle_sed(bot, msg)
        bot.client.edit_message_text.assert_called_once()
        new_text = bot.client.edit_message_text.call_args[1]["text"]
        assert new_text == "Апельсин Аист Арбуз"

    @pytest.mark.asyncio
    async def test_deletes_trigger_message(self) -> None:
        """После успешного редактирования команда-триггер удаляется."""
        bot = _make_bot(me_id=42)
        msg = _make_message(
            ["sed", "s/old/new/"],
            reply_text="old text",
            reply_from_id=42,
        )
        await handle_sed(bot, msg)
        msg.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_edit_error_replies(self) -> None:
        """Если edit упал — пишем сообщение об ошибке."""
        bot = _make_bot(me_id=42)
        bot.client.edit_message_text.side_effect = Exception("Forbidden")
        msg = _make_message(
            ["sed", "s/a/b/"],
            reply_text="a text",
            reply_from_id=42,
        )
        await handle_sed(bot, msg)
        msg.reply.assert_called_once()
        assert "Не удалось" in msg.reply.call_args[0][0]


class TestHandleSedOtherMessage:
    """Замена в чужом сообщении — отвечаем исправлением."""

    @pytest.mark.asyncio
    async def test_replies_with_correction(self) -> None:
        bot = _make_bot(me_id=42)
        msg = _make_message(
            ["sed", "s/старый/новый/"],
            reply_text="это старый текст",
            reply_from_id=99,  # чужой
        )
        await handle_sed(bot, msg)
        msg.reply.assert_called_once()
        text = msg.reply.call_args[0][0]
        assert "✏️ Исправление:" in text
        assert "новый" in text
        # Редактирование не вызывается
        bot.client.edit_message_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_case_insensitive_other(self) -> None:
        bot = _make_bot(me_id=42)
        msg = _make_message(
            ["sed", "s/HELLO/Привет/i"],
            reply_text="hello world",
            reply_from_id=7,
        )
        await handle_sed(bot, msg)
        text = msg.reply.call_args[0][0]
        assert "Привет" in text

    @pytest.mark.asyncio
    async def test_global_flag_other(self) -> None:
        bot = _make_bot(me_id=42)
        msg = _make_message(
            ["sed", "s/x/Y/g"],
            reply_text="x plus x equals xx",
            reply_from_id=7,
        )
        await handle_sed(bot, msg)
        text = msg.reply.call_args[0][0]
        assert "Y plus Y equals YY" in text

    @pytest.mark.asyncio
    async def test_uses_caption_if_no_text(self) -> None:
        """Для медиа-сообщений — берём caption."""
        bot = _make_bot(me_id=42)
        msg = _make_message(
            ["sed", "s/foo/bar/"],
            reply_text=None,
            reply_from_id=7,
        )
        msg.reply_to_message = MagicMock()
        msg.reply_to_message.text = None
        msg.reply_to_message.caption = "foto with foo caption"
        msg.reply_to_message.from_user = SimpleNamespace(id=7)
        msg.reply_to_message.id = 101
        msg.reply_to_message.chat = SimpleNamespace(id=-1001)
        await handle_sed(bot, msg)
        text = msg.reply.call_args[0][0]
        assert "bar" in text


class TestHandleSedFirstOccurrence:
    """Без флага g заменяется только первое вхождение."""

    @pytest.mark.asyncio
    async def test_first_only(self) -> None:
        bot = _make_bot(me_id=42)
        msg = _make_message(
            ["sed", "s/cat/dog/"],
            reply_text="I have a cat and a cat",
            reply_from_id=7,
        )
        await handle_sed(bot, msg)
        text = msg.reply.call_args[0][0]
        assert "I have a dog and a cat" in text
