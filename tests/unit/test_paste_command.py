# -*- coding: utf-8 -*-
"""
Тесты для команды !paste (создание текстового paste-файла).

Покрываем:
  - handle_paste: текст из аргумента, текст из reply, ошибка без аргументов
  - Имя файла по формату paste_YYYY-MM-DD_HH-MM.txt
  - Вызов send_document с правильными параметрами
  - Удаление временного файла после отправки
  - Обработка OSError при записи файла
"""

from __future__ import annotations

import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_paste

# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------


def _make_bot(command_args: str = "") -> MagicMock:
    """Создаёт мок бота с _get_command_args и client.send_document."""
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value=command_args)
    bot.client = MagicMock()
    bot.client.send_document = AsyncMock()
    return bot


def _make_message(reply_text: str | None = None, chat_id: int = 12345) -> AsyncMock:
    """Создаёт мок сообщения с опциональным reply_to_message."""
    msg = AsyncMock()
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    if reply_text is not None:
        msg.reply_to_message = MagicMock()
        msg.reply_to_message.text = reply_text
    else:
        msg.reply_to_message = None
    return msg


# ---------------------------------------------------------------------------
# Базовые сценарии
# ---------------------------------------------------------------------------


class TestHandlePasteBasic:
    """Основные сценарии !paste."""

    @pytest.mark.asyncio
    async def test_paste_with_args_calls_send_document(self, tmp_path):
        """!paste <текст> → send_document вызывается."""
        bot = _make_bot("Привет, это длинный текст")
        msg = _make_message()

        with patch("src.handlers.command_handlers.config") as mock_config:
            mock_config.BASE_DIR = str(tmp_path)
            await handle_paste(bot, msg)

        bot.client.send_document.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_paste_with_args_uses_correct_chat_id(self, tmp_path):
        """!paste отправляет в правильный chat_id."""
        chat_id = 99887766
        bot = _make_bot("Тест")
        msg = _make_message(chat_id=chat_id)

        with patch("src.handlers.command_handlers.config") as mock_config:
            mock_config.BASE_DIR = str(tmp_path)
            await handle_paste(bot, msg)

        call_args = bot.client.send_document.call_args
        assert call_args[0][0] == chat_id

    @pytest.mark.asyncio
    async def test_paste_with_args_caption_is_paste(self, tmp_path):
        """!paste → caption == '📋 Paste'."""
        bot = _make_bot("Какой-то текст")
        msg = _make_message()

        with patch("src.handlers.command_handlers.config") as mock_config:
            mock_config.BASE_DIR = str(tmp_path)
            await handle_paste(bot, msg)

        call_kwargs = bot.client.send_document.call_args[1]
        assert call_kwargs.get("caption") == "📋 Paste"

    @pytest.mark.asyncio
    async def test_paste_from_reply(self, tmp_path):
        """!paste в reply → текст берётся из reply_to_message.text."""
        reply_text = "Текст из reply сообщения"
        captured_content: list[str] = []

        async def capture_send(chat_id, filepath, caption=None):
            # Читаем файл пока он ещё существует (до finally-удаления)
            captured_content.append(pathlib.Path(filepath).read_text(encoding="utf-8"))

        bot = _make_bot("")  # нет аргументов
        bot.client.send_document = capture_send
        msg = _make_message(reply_text=reply_text)

        with patch("src.handlers.command_handlers.config") as mock_config:
            mock_config.BASE_DIR = str(tmp_path)
            await handle_paste(bot, msg)

        assert captured_content, "send_document не был вызван"
        assert captured_content[0] == reply_text

    @pytest.mark.asyncio
    async def test_paste_no_args_no_reply_raises_user_input_error(self):
        """!paste без аргументов и без reply → UserInputError."""
        bot = _make_bot("")
        msg = _make_message()  # reply_to_message = None

        with pytest.raises(UserInputError) as exc_info:
            await handle_paste(bot, msg)

        assert "paste" in exc_info.value.user_message.lower()

    @pytest.mark.asyncio
    async def test_paste_reply_without_text_raises(self):
        """!paste в reply без текста (медиа) → UserInputError."""
        bot = _make_bot("")
        msg = _make_message()
        # reply_to_message есть, но text = None
        msg.reply_to_message = MagicMock()
        msg.reply_to_message.text = None

        with pytest.raises(UserInputError):
            await handle_paste(bot, msg)


# ---------------------------------------------------------------------------
# Имя файла
# ---------------------------------------------------------------------------


class TestHandlePasteFilename:
    """Проверка формата имени файла."""

    @pytest.mark.asyncio
    async def test_filename_format(self, tmp_path):
        """Имя файла соответствует шаблону paste_YYYY-MM-DD_HH-MM.txt."""
        import re

        bot = _make_bot("текст")
        msg = _make_message()

        with patch("src.handlers.command_handlers.config") as mock_config:
            mock_config.BASE_DIR = str(tmp_path)
            await handle_paste(bot, msg)

        call_args = bot.client.send_document.call_args
        filepath = call_args[0][1]
        filename = pathlib.Path(filepath).name
        pattern = r"^paste_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}\.txt$"
        assert re.match(pattern, filename), f"Некорректное имя файла: {filename}"

    @pytest.mark.asyncio
    async def test_file_has_correct_extension(self, tmp_path):
        """Файл имеет расширение .txt."""
        bot = _make_bot("текст")
        msg = _make_message()

        with patch("src.handlers.command_handlers.config") as mock_config:
            mock_config.BASE_DIR = str(tmp_path)
            await handle_paste(bot, msg)

        call_args = bot.client.send_document.call_args
        filepath = call_args[0][1]
        assert filepath.endswith(".txt")


# ---------------------------------------------------------------------------
# Содержимое файла
# ---------------------------------------------------------------------------


def _make_capturing_bot(command_args: str = "") -> tuple[MagicMock, list[str]]:
    """Создаёт бота с capture send_document — перехватывает содержимое файла до удаления."""
    captured: list[str] = []

    async def capture_send(chat_id, filepath, caption=None):
        captured.append(pathlib.Path(filepath).read_text(encoding="utf-8"))

    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value=command_args)
    bot.client = MagicMock()
    bot.client.send_document = capture_send
    return bot, captured


class TestHandlePasteContent:
    """Проверка содержимого временного файла."""

    @pytest.mark.asyncio
    async def test_file_content_matches_args(self, tmp_path):
        """Файл содержит текст из аргументов команды."""
        text = "Это важный длинный текст для paste"
        bot, captured = _make_capturing_bot(text)
        msg = _make_message()

        with patch("src.handlers.command_handlers.config") as mock_config:
            mock_config.BASE_DIR = str(tmp_path)
            await handle_paste(bot, msg)

        assert captured, "send_document не был вызван"
        assert captured[0] == text

    @pytest.mark.asyncio
    async def test_file_content_unicode(self, tmp_path):
        """Файл корректно хранит Unicode (русский, emoji)."""
        text = "Привет мир 🦀 — тест Unicode"
        bot, captured = _make_capturing_bot(text)
        msg = _make_message()

        with patch("src.handlers.command_handlers.config") as mock_config:
            mock_config.BASE_DIR = str(tmp_path)
            await handle_paste(bot, msg)

        assert captured, "send_document не был вызван"
        assert captured[0] == text

    @pytest.mark.asyncio
    async def test_file_content_multiline(self, tmp_path):
        """Многострочный текст сохраняется корректно."""
        text = "Строка 1\nСтрока 2\nСтрока 3"
        bot, captured = _make_capturing_bot(text)
        msg = _make_message()

        with patch("src.handlers.command_handlers.config") as mock_config:
            mock_config.BASE_DIR = str(tmp_path)
            await handle_paste(bot, msg)

        assert captured, "send_document не был вызван"
        assert captured[0] == text


# ---------------------------------------------------------------------------
# Очистка файла
# ---------------------------------------------------------------------------


class TestHandlePasteCleanup:
    """Проверка удаления временного файла."""

    @pytest.mark.asyncio
    async def test_temp_file_deleted_after_send(self, tmp_path):
        """Временный файл удаляется после успешной отправки."""
        bot = _make_bot("текст для paste")
        msg = _make_message()

        sent_path: list[str] = []

        async def capture_send(chat_id, filepath, caption=None):
            sent_path.append(filepath)

        bot.client.send_document = capture_send

        with patch("src.handlers.command_handlers.config") as mock_config:
            mock_config.BASE_DIR = str(tmp_path)
            await handle_paste(bot, msg)

        assert sent_path, "send_document не был вызван"
        assert not pathlib.Path(sent_path[0]).exists(), f"Временный файл не удалён: {sent_path[0]}"

    @pytest.mark.asyncio
    async def test_temp_file_deleted_even_on_send_error(self, tmp_path):
        """Временный файл удаляется даже если send_document упал."""
        bot = _make_bot("текст")
        msg = _make_message()

        sent_path: list[str] = []

        async def failing_send(chat_id, filepath, caption=None):
            sent_path.append(filepath)
            raise RuntimeError("Telegram error")

        bot.client.send_document = failing_send

        with patch("src.handlers.command_handlers.config") as mock_config:
            mock_config.BASE_DIR = str(tmp_path)
            # RuntimeError не перехватывается handler'ом — пробросится выше
            with pytest.raises(RuntimeError):
                await handle_paste(bot, msg)

        assert sent_path, "send_document не был вызван"
        assert not pathlib.Path(sent_path[0]).exists(), (
            f"Временный файл не удалён после ошибки: {sent_path[0]}"
        )


# ---------------------------------------------------------------------------
# Обработка ошибок записи файла
# ---------------------------------------------------------------------------


class TestHandlePasteOSError:
    """Проверка обработки ошибок файловой системы."""

    @pytest.mark.asyncio
    async def test_oserror_on_write_sends_error_reply(self, tmp_path):
        """OSError при write_text → message.reply с текстом ошибки."""
        bot = _make_bot("текст")
        msg = _make_message()

        with (
            patch("src.handlers.command_handlers.config") as mock_config,
            patch("pathlib.Path.write_text", side_effect=OSError("disk full")),
        ):
            mock_config.BASE_DIR = str(tmp_path)
            await handle_paste(bot, msg)

        msg.reply.assert_awaited_once()
        reply_text = msg.reply.call_args[0][0]
        assert "❌" in reply_text or "ошибка" in reply_text.lower()

    @pytest.mark.asyncio
    async def test_oserror_on_write_does_not_call_send_document(self, tmp_path):
        """OSError при write_text → send_document не вызывается."""
        bot = _make_bot("текст")
        msg = _make_message()

        with (
            patch("src.handlers.command_handlers.config") as mock_config,
            patch("pathlib.Path.write_text", side_effect=OSError("no space")),
        ):
            mock_config.BASE_DIR = str(tmp_path)
            await handle_paste(bot, msg)

        bot.client.send_document.assert_not_awaited()


# ---------------------------------------------------------------------------
# Аргументы имеют приоритет над reply
# ---------------------------------------------------------------------------


class TestHandlePastePriority:
    """Проверка приоритета аргументов над reply."""

    @pytest.mark.asyncio
    async def test_args_take_priority_over_reply(self, tmp_path):
        """Если есть и args, и reply — используются args."""
        arg_text = "Аргумент команды"
        reply_text = "Текст из reply"
        bot, captured = _make_capturing_bot(arg_text)
        msg = _make_message(reply_text=reply_text)

        with patch("src.handlers.command_handlers.config") as mock_config:
            mock_config.BASE_DIR = str(tmp_path)
            await handle_paste(bot, msg)

        assert captured, "send_document не был вызван"
        assert captured[0] == arg_text
        assert captured[0] != reply_text
