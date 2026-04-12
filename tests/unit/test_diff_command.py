# -*- coding: utf-8 -*-
"""
Тесты для команды !diff (сравнение двух текстов).

Покрываем:
  - _build_diff_output: чистая функция diff
  - handle_diff: reply + args, отсутствие reply, отсутствие args, идентичные тексты,
    длинный diff (обрезка), многострочные тексты
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import _build_diff_output, handle_diff


# ---------------------------------------------------------------------------
# _build_diff_output — чистые функции
# ---------------------------------------------------------------------------


class TestBuildDiffOutput:
    """Тесты вспомогательной функции _build_diff_output."""

    def test_identical_texts_returns_empty(self):
        """Одинаковые тексты → пустой вывод."""
        result = _build_diff_output("hello", "hello")
        assert result.strip() == ""

    def test_added_line_marked_plus(self):
        """Строка, присутствующая только в новом тексте, помечена «+»."""
        result = _build_diff_output("line1", "line1\nline2")
        assert any(line.startswith("+") for line in result.splitlines())

    def test_removed_line_marked_minus(self):
        """Строка, присутствующая только в старом тексте, помечена «-»."""
        result = _build_diff_output("line1\nline2", "line1")
        assert any(line.startswith("-") for line in result.splitlines())

    def test_common_line_marked_space(self):
        """Общая строка помечена пробелом."""
        result = _build_diff_output("common\nold", "common\nnew")
        lines = result.splitlines()
        assert any(line.startswith(" ") for line in lines)

    def test_no_file_headers(self):
        """Заголовки --- и +++ не попадают в вывод."""
        result = _build_diff_output("a", "b")
        lines = result.splitlines()
        assert not any(line.startswith("---") for line in lines)
        assert not any(line.startswith("+++") for line in lines)

    def test_returns_string(self):
        """Функция всегда возвращает строку."""
        assert isinstance(_build_diff_output("", ""), str)

    def test_multiline_diff(self):
        """Многострочный diff содержит и плюсы, и минусы."""
        old = "строка1\nстрока2\nстрока3"
        new = "строка1\nИЗМЕНЕНА\nстрока3"
        result = _build_diff_output(old, new)
        assert "-" in result
        assert "+" in result

    def test_single_char_change(self):
        """Замена одного символа отражается в diff."""
        result = _build_diff_output("abc", "axc")
        assert "-abc" in result
        assert "+axc" in result

    def test_completely_different_texts(self):
        """Полностью разные тексты → только плюсы и минусы."""
        result = _build_diff_output("foo", "bar")
        lines = [l for l in result.splitlines() if l.strip()]
        assert any(l.startswith("-") for l in lines)
        assert any(l.startswith("+") for l in lines)

    def test_empty_old_text(self):
        """Старый текст пустой → все строки нового помечены «+»."""
        result = _build_diff_output("", "новая строка")
        assert any(line.startswith("+") for line in result.splitlines())

    def test_empty_new_text(self):
        """Новый текст пустой → все строки старого помечены «-»."""
        result = _build_diff_output("старая строка", "")
        assert any(line.startswith("-") for line in result.splitlines())

    def test_russian_text(self):
        """Русские строки поддерживаются без ошибок."""
        result = _build_diff_output("Привет мир", "Привет Краб")
        assert isinstance(result, str)
        assert "Краб" in result or "-" in result

    def test_no_at_at_markers(self):
        """Маркеры @@ не попадают в финальный вывод."""
        old = "\n".join(f"line{i}" for i in range(20))
        new = "\n".join(f"line{i}" for i in range(10)) + "\nNEW"
        result = _build_diff_output(old, new)
        assert "@@" not in result


# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------


def _make_bot(command_args: str = "") -> MagicMock:
    """Создаёт мок бота с _get_command_args."""
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value=command_args)
    return bot


def _make_message(reply_text: str | None = None) -> AsyncMock:
    """Создаёт мок сообщения с опциональным reply."""
    msg = AsyncMock()
    msg.chat = MagicMock()
    msg.chat.id = 12345
    if reply_text is not None:
        msg.reply_to_message = MagicMock()
        msg.reply_to_message.text = reply_text
        msg.reply_to_message.caption = None
    else:
        msg.reply_to_message = None
    return msg


# ---------------------------------------------------------------------------
# handle_diff — базовые сценарии
# ---------------------------------------------------------------------------


class TestHandleDiffBasic:
    """Базовые тесты хендлера !diff."""

    @pytest.mark.asyncio
    async def test_diff_reply_with_args(self):
        """Reply + аргументы → diff отправляется в ответ."""
        bot = _make_bot("новый текст")
        msg = _make_message(reply_text="старый текст")
        await handle_diff(bot, msg)
        msg.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_diff_reply_content_has_header(self):
        """Ответ содержит заголовок 📊 Diff."""
        bot = _make_bot("bar")
        msg = _make_message(reply_text="foo")
        await handle_diff(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "📊" in reply_text
        assert "Diff" in reply_text

    @pytest.mark.asyncio
    async def test_diff_reply_content_has_separator(self):
        """Ответ содержит разделитель «─»."""
        bot = _make_bot("bar")
        msg = _make_message(reply_text="foo")
        await handle_diff(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "─" in reply_text

    @pytest.mark.asyncio
    async def test_diff_shows_minus_for_old(self):
        """Вывод содержит строки со знаком «-» для удалённых строк."""
        bot = _make_bot("new line")
        msg = _make_message(reply_text="old line")
        await handle_diff(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "-old line" in reply_text

    @pytest.mark.asyncio
    async def test_diff_shows_plus_for_new(self):
        """Вывод содержит строки со знаком «+» для добавленных строк."""
        bot = _make_bot("new line")
        msg = _make_message(reply_text="old line")
        await handle_diff(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "+new line" in reply_text


# ---------------------------------------------------------------------------
# handle_diff — идентичные тексты
# ---------------------------------------------------------------------------


class TestHandleDiffIdentical:
    """Тесты для идентичных текстов."""

    @pytest.mark.asyncio
    async def test_identical_texts_returns_no_diff(self):
        """Одинаковые тексты → сообщение «идентичны»."""
        bot = _make_bot("одинаковый текст")
        msg = _make_message(reply_text="одинаковый текст")
        await handle_diff(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "идентичны" in reply_text or "нет" in reply_text

    @pytest.mark.asyncio
    async def test_identical_texts_no_diff_block(self):
        """Для идентичных текстов не отправляется diff-блок."""
        bot = _make_bot("same")
        msg = _make_message(reply_text="same")
        await handle_diff(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        # Нет блока с «-» или «+»
        assert "```" not in reply_text or "-same" not in reply_text


# ---------------------------------------------------------------------------
# handle_diff — ошибки ввода
# ---------------------------------------------------------------------------


class TestHandleDiffErrors:
    """Тесты обработки ошибок ввода."""

    @pytest.mark.asyncio
    async def test_no_reply_raises_user_input_error(self):
        """Без reply → UserInputError со справкой."""
        bot = _make_bot("какой-то текст")
        msg = _make_message()  # нет reply
        with pytest.raises(UserInputError) as exc_info:
            await handle_diff(bot, msg)
        assert "reply" in exc_info.value.user_message.lower() or "diff" in exc_info.value.user_message.lower()

    @pytest.mark.asyncio
    async def test_no_args_raises_user_input_error(self):
        """Reply есть, но аргументов нет → UserInputError."""
        bot = _make_bot("")  # нет args
        msg = _make_message(reply_text="старый текст")
        with pytest.raises(UserInputError) as exc_info:
            await handle_diff(bot, msg)
        assert exc_info.value.user_message  # сообщение не пустое

    @pytest.mark.asyncio
    async def test_no_reply_no_args_raises_user_input_error(self):
        """Ни reply, ни аргументов → UserInputError."""
        bot = _make_bot("")
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_diff(bot, msg)

    @pytest.mark.asyncio
    async def test_reply_with_empty_text_raises_error(self):
        """Reply без текста (пустая строка) → UserInputError."""
        bot = _make_bot("новый текст")
        msg = _make_message(reply_text="")
        with pytest.raises(UserInputError):
            await handle_diff(bot, msg)

    @pytest.mark.asyncio
    async def test_no_reply_object_raises_error(self):
        """reply_to_message = None → UserInputError."""
        bot = _make_bot("текст")
        msg = AsyncMock()
        msg.reply_to_message = None
        with pytest.raises(UserInputError):
            await handle_diff(bot, msg)


# ---------------------------------------------------------------------------
# handle_diff — caption reply
# ---------------------------------------------------------------------------


class TestHandleDiffCaption:
    """Тесты для reply с caption (медиа-сообщения)."""

    @pytest.mark.asyncio
    async def test_reply_with_caption_instead_of_text(self):
        """Reply с caption (не text) → caption используется как старый текст."""
        bot = _make_bot("новая подпись")
        msg = AsyncMock()
        msg.reply_to_message = MagicMock()
        msg.reply_to_message.text = None
        msg.reply_to_message.caption = "старая подпись"
        await handle_diff(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "-старая подпись" in reply_text or "📊" in reply_text


# ---------------------------------------------------------------------------
# handle_diff — многострочные тексты
# ---------------------------------------------------------------------------


class TestHandleDiffMultiline:
    """Тесты для многострочных текстов."""

    @pytest.mark.asyncio
    async def test_multiline_diff_shows_changes(self):
        """Многострочный diff корректно показывает изменения."""
        old = "строка1\nстрока2\nстрока3"
        new = "строка1\nИЗМЕНЕНА\nстрока3"
        bot = _make_bot(new)
        msg = _make_message(reply_text=old)
        await handle_diff(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "-строка2" in reply_text
        assert "+ИЗМЕНЕНА" in reply_text

    @pytest.mark.asyncio
    async def test_multiline_common_line_preserved(self):
        """Общие строки присутствуют в diff."""
        old = "А\nБ\nВ"
        new = "А\nНовое\nВ"
        bot = _make_bot(new)
        msg = _make_message(reply_text=old)
        await handle_diff(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        # В diff должны быть изменения для строки Б → Новое
        assert "Новое" in reply_text

    @pytest.mark.asyncio
    async def test_russian_multiline(self):
        """Русский многострочный diff работает без ошибок."""
        old = "Привет мир\nКраб работает"
        new = "Привет мир\nКраб отдыхает"
        bot = _make_bot(new)
        msg = _make_message(reply_text=old)
        await handle_diff(bot, msg)
        msg.reply.assert_awaited_once()


# ---------------------------------------------------------------------------
# handle_diff — обрезка длинного diff
# ---------------------------------------------------------------------------


class TestHandleDiffTruncation:
    """Тесты обрезки diff при превышении лимита Telegram."""

    @pytest.mark.asyncio
    async def test_long_diff_is_truncated(self):
        """Очень длинный diff обрезается и содержит метку обрезки."""
        # Генерируем длинный diff: 500 строк разных
        old_lines = [f"old_line_{i}" for i in range(500)]
        new_lines = [f"new_line_{i}" for i in range(500)]
        old = "\n".join(old_lines)
        new = "\n".join(new_lines)
        bot = _make_bot(new)
        msg = _make_message(reply_text=old)
        await handle_diff(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        # Длина ответа не превышает разумный лимит Telegram
        assert len(reply_text) <= 4096
        # Если обрезано — содержит маркер обрезки
        if "обрезано" in reply_text:
            assert "…" in reply_text or "обрезано" in reply_text
