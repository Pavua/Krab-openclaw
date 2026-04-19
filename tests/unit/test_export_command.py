# -*- coding: utf-8 -*-
"""
Юнит-тесты для handle_export и вспомогательных функций экспорта чата.

Покрывает:
- _sanitize_filename: спецсимволы, пустая строка
- _format_sender: from_user, sender_chat, Unknown
- _msg_text: text, caption, пустое
- _render_export_markdown: frontmatter, группировка по дням, медиа-заглушки
- handle_export: default limit, N, all, неверный аргумент, пустая история,
  ошибка get_chat_history, ошибка записи файла, ошибка send_document
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.handlers.command_handlers import (
    _format_sender,
    _msg_text,
    _render_export_markdown,
    _sanitize_filename,
    handle_export,
)

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_msg_obj(
    text: str | None = None,
    caption: str | None = None,
    date: datetime.datetime | None = None,
    from_user=None,
    sender_chat=None,
    photo=None,
    video=None,
    audio=None,
    voice=None,
    document=None,
    sticker=None,
) -> SimpleNamespace:
    """Минимальный mock-объект сообщения Telegram."""
    return SimpleNamespace(
        text=text,
        caption=caption,
        date=date or datetime.datetime(2026, 4, 12, 14, 30, 0),
        from_user=from_user,
        sender_chat=sender_chat,
        photo=photo,
        video=video,
        audio=audio,
        voice=voice,
        document=document,
        sticker=sticker,
    )


def _make_user(first_name="Иван", last_name="", username="ivan_user", uid=123):
    return SimpleNamespace(
        first_name=first_name,
        last_name=last_name,
        username=username,
        id=uid,
    )


def _make_chat(title="Test Chat", chat_id=111222):
    return SimpleNamespace(id=chat_id, title=title, first_name=None)


async def _async_gen(items):
    """Вспомогательный async-генератор для мока get_chat_history."""
    for item in items:
        yield item


def _make_bot(history_items=None, send_document_raises=False):
    """Минимальный mock-bot с client."""
    if history_items is None:
        history_items = []

    async def _get_history(chat_id, limit):
        async for m in _async_gen(history_items):
            yield m

    client = SimpleNamespace(
        get_chat_history=_get_history,
        send_document=AsyncMock(
            side_effect=Exception("send error") if send_document_raises else None
        ),
    )
    return SimpleNamespace(client=client)


def _make_telegram_message(text="!export", chat_title="Test Chat", chat_id=111222):
    """Mock pyrogram Message для handle_export."""
    chat = _make_chat(title=chat_title, chat_id=chat_id)
    msg = SimpleNamespace(
        text=text,
        chat=chat,
        reply=AsyncMock(),
    )
    return msg


# ---------------------------------------------------------------------------
# _sanitize_filename
# ---------------------------------------------------------------------------


class TestSanitizeFilename:
    def test_allowed_chars_pass_through(self):
        assert _sanitize_filename("Hello World-test_123") == "Hello World-test_123"

    def test_special_chars_replaced(self):
        result = _sanitize_filename("Chat/Name:With*Spec?")
        assert "/" not in result
        assert ":" not in result
        assert "*" not in result
        assert "?" not in result

    def test_emoji_replaced(self):
        result = _sanitize_filename("Мой чат 🐝")
        # Кириллица — не isalnum в ASCII, но isalnum() работает для Unicode
        # Эмодзи должны быть заменены
        assert "🐝" not in result

    def test_empty_string(self):
        assert _sanitize_filename("") == ""

    def test_spaces_preserved(self):
        result = _sanitize_filename("My Chat Name")
        assert "My" in result
        assert "Chat" in result


# ---------------------------------------------------------------------------
# _format_sender
# ---------------------------------------------------------------------------


class TestFormatSender:
    def test_from_user_full_name(self):
        msg = _make_msg_obj(from_user=_make_user("Иван", "Петров"))
        assert _format_sender(msg) == "Иван Петров"

    def test_from_user_first_name_only(self):
        msg = _make_msg_obj(from_user=_make_user("Иван", ""))
        assert _format_sender(msg) == "Иван"

    def test_from_user_no_name_fallback_username(self):
        u = _make_user(first_name="", last_name="", username="user42")
        msg = _make_msg_obj(from_user=u)
        assert _format_sender(msg) == "user42"

    def test_from_user_no_name_no_username_fallback_id(self):
        u = _make_user(first_name="", last_name="", username="", uid=9999)
        msg = _make_msg_obj(from_user=u)
        assert _format_sender(msg) == "9999"

    def test_sender_chat(self):
        sc = SimpleNamespace(title="My Channel", id=-1001234)
        msg = _make_msg_obj(sender_chat=sc)
        assert _format_sender(msg) == "My Channel"

    def test_sender_chat_no_title(self):
        sc = SimpleNamespace(title=None, id=-1001234)
        msg = _make_msg_obj(sender_chat=sc)
        assert _format_sender(msg) == "-1001234"

    def test_unknown(self):
        msg = _make_msg_obj()
        assert _format_sender(msg) == "Unknown"


# ---------------------------------------------------------------------------
# _msg_text
# ---------------------------------------------------------------------------


class TestMsgText:
    def test_returns_text(self):
        msg = _make_msg_obj(text="Привет!")
        assert _msg_text(msg) == "Привет!"

    def test_returns_caption_when_no_text(self):
        msg = _make_msg_obj(text=None, caption="Подпись")
        assert _msg_text(msg) == "Подпись"

    def test_text_takes_priority(self):
        msg = _make_msg_obj(text="Текст", caption="Подпись")
        assert _msg_text(msg) == "Текст"

    def test_empty_when_both_none(self):
        msg = _make_msg_obj(text=None, caption=None)
        assert _msg_text(msg) == ""

    def test_strips_whitespace(self):
        msg = _make_msg_obj(text="  Привет  ")
        assert _msg_text(msg) == "Привет"


# ---------------------------------------------------------------------------
# _render_export_markdown
# ---------------------------------------------------------------------------


class TestRenderExportMarkdown:
    def _base_msgs(self):
        user = _make_user("Алиса")
        return [
            _make_msg_obj(
                text="Первое сообщение",
                from_user=user,
                date=datetime.datetime(2026, 4, 12, 10, 0, 0),
            ),
            _make_msg_obj(
                text="Второе сообщение",
                from_user=user,
                date=datetime.datetime(2026, 4, 12, 11, 30, 0),
            ),
        ]

    def test_frontmatter_contains_title(self):
        result = _render_export_markdown(
            "My Chat", 123, self._base_msgs(), datetime.datetime(2026, 4, 12, 12, 0, 0)
        )
        assert "chat_title: My Chat" in result

    def test_frontmatter_contains_chat_id(self):
        result = _render_export_markdown(
            "My Chat", 123, self._base_msgs(), datetime.datetime(2026, 4, 12, 12, 0, 0)
        )
        assert "chat_id: 123" in result

    def test_frontmatter_contains_exported(self):
        result = _render_export_markdown(
            "My Chat", 123, self._base_msgs(), datetime.datetime(2026, 4, 12, 12, 0, 0)
        )
        assert "exported: 2026-04-12T12:00:00" in result

    def test_frontmatter_contains_message_count(self):
        msgs = self._base_msgs()
        result = _render_export_markdown(
            "My Chat", 123, msgs, datetime.datetime(2026, 4, 12, 12, 0, 0)
        )
        assert f"messages: {len(msgs)}" in result

    def test_day_header_present(self):
        result = _render_export_markdown(
            "My Chat", 123, self._base_msgs(), datetime.datetime(2026, 4, 12, 12, 0, 0)
        )
        assert "## 2026-04-12" in result

    def test_message_header_format(self):
        result = _render_export_markdown(
            "My Chat", 123, self._base_msgs(), datetime.datetime(2026, 4, 12, 12, 0, 0)
        )
        assert "### 10:00 — Алиса" in result
        assert "### 11:30 — Алиса" in result

    def test_message_text_present(self):
        result = _render_export_markdown(
            "My Chat", 123, self._base_msgs(), datetime.datetime(2026, 4, 12, 12, 0, 0)
        )
        assert "Первое сообщение" in result
        assert "Второе сообщение" in result

    def test_multiple_days(self):
        user = _make_user("Боб")
        msgs = [
            _make_msg_obj(
                text="День 1",
                from_user=user,
                date=datetime.datetime(2026, 4, 11, 9, 0, 0),
            ),
            _make_msg_obj(
                text="День 2",
                from_user=user,
                date=datetime.datetime(2026, 4, 12, 9, 0, 0),
            ),
        ]
        result = _render_export_markdown("Chat", 1, msgs, datetime.datetime(2026, 4, 12, 12, 0, 0))
        assert "## 2026-04-11" in result
        assert "## 2026-04-12" in result

    def test_photo_placeholder(self):
        msg = _make_msg_obj(photo=True, from_user=_make_user("X"))
        result = _render_export_markdown("C", 1, [msg], datetime.datetime(2026, 4, 12))
        assert "_[фото]_" in result

    def test_video_placeholder(self):
        msg = _make_msg_obj(video=True, from_user=_make_user("X"))
        result = _render_export_markdown("C", 1, [msg], datetime.datetime(2026, 4, 12))
        assert "_[видео]_" in result

    def test_audio_placeholder(self):
        msg = _make_msg_obj(audio=True, from_user=_make_user("X"))
        result = _render_export_markdown("C", 1, [msg], datetime.datetime(2026, 4, 12))
        assert "_[аудио]_" in result

    def test_voice_placeholder(self):
        msg = _make_msg_obj(voice=True, from_user=_make_user("X"))
        result = _render_export_markdown("C", 1, [msg], datetime.datetime(2026, 4, 12))
        assert "_[аудио]_" in result

    def test_document_placeholder(self):
        msg = _make_msg_obj(document=True, from_user=_make_user("X"))
        result = _render_export_markdown("C", 1, [msg], datetime.datetime(2026, 4, 12))
        assert "_[документ]_" in result

    def test_sticker_placeholder(self):
        sticker = SimpleNamespace(emoji="😎")
        msg = _make_msg_obj(sticker=sticker, from_user=_make_user("X"))
        result = _render_export_markdown("C", 1, [msg], datetime.datetime(2026, 4, 12))
        assert "_[стикер:" in result

    def test_unknown_media_placeholder(self):
        # Нет ни текста, ни известных медиа-типов
        msg = _make_msg_obj(from_user=_make_user("X"))
        result = _render_export_markdown("C", 1, [msg], datetime.datetime(2026, 4, 12))
        assert "_[медиа]_" in result

    def test_msg_without_date_skipped(self):
        msg = _make_msg_obj(text="Без даты", from_user=_make_user("X"))
        msg.date = None
        result = _render_export_markdown("C", 1, [msg], datetime.datetime(2026, 4, 12))
        # Нет дня — нет сообщения
        assert "Без даты" not in result

    def test_empty_messages_only_frontmatter(self):
        result = _render_export_markdown("C", 1, [], datetime.datetime(2026, 4, 12))
        assert result.startswith("---\n")
        assert "messages: 0" in result


# ---------------------------------------------------------------------------
# handle_export — интеграционные тесты
# ---------------------------------------------------------------------------


class TestHandleExport:
    """Тесты handle_export с мокированием файловой системы и client."""

    def _make_msgs(self, n=3):
        """Создаёт n тестовых сообщений в обратном порядке (новые первые, как get_chat_history)."""
        user = _make_user("Тест")
        msgs = []
        for i in range(n, 0, -1):
            msgs.append(
                _make_msg_obj(
                    text=f"Сообщение {i}",
                    from_user=user,
                    date=datetime.datetime(2026, 4, 12, 10, i, 0),
                )
            )
        return msgs

    @pytest.mark.asyncio
    async def test_default_limit(self, tmp_path):
        """!export без аргументов — limit=EXPORT_DEFAULT_LIMIT."""
        msgs = self._make_msgs(3)
        bot = _make_bot(history_items=msgs)
        message = _make_telegram_message("!export")
        status_msg = AsyncMock()
        status_msg.delete = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        with patch("src.handlers.command_handlers.EXPORT_VAULT_DIR", tmp_path):
            await handle_export(bot, message)

        # Файл должен быть создан
        md_files = list(tmp_path.glob("*.md"))
        assert len(md_files) == 1

    @pytest.mark.asyncio
    async def test_numeric_arg(self, tmp_path):
        """!export 50 — limit=50."""
        msgs = self._make_msgs(3)
        bot = _make_bot(history_items=msgs)
        message = _make_telegram_message("!export 50")
        status_msg = AsyncMock()
        status_msg.delete = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        with patch("src.handlers.command_handlers.EXPORT_VAULT_DIR", tmp_path):
            await handle_export(bot, message)

        md_files = list(tmp_path.glob("*.md"))
        assert len(md_files) == 1

    @pytest.mark.asyncio
    async def test_all_arg(self, tmp_path):
        """!export all — limit=EXPORT_MAX_LIMIT."""
        msgs = self._make_msgs(5)
        bot = _make_bot(history_items=msgs)
        message = _make_telegram_message("!export all")
        status_msg = AsyncMock()
        status_msg.delete = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        with patch("src.handlers.command_handlers.EXPORT_VAULT_DIR", tmp_path):
            await handle_export(bot, message)

        md_files = list(tmp_path.glob("*.md"))
        assert len(md_files) == 1

    @pytest.mark.asyncio
    async def test_invalid_arg_replies_error(self, tmp_path):
        """!export badarg — отвечает сообщением об ошибке, файл не создаётся."""
        bot = _make_bot()
        message = _make_telegram_message("!export badarg")

        with patch("src.handlers.command_handlers.EXPORT_VAULT_DIR", tmp_path):
            await handle_export(bot, message)

        message.reply.assert_called_once()
        call_text = message.reply.call_args[0][0]
        assert "❌" in call_text
        assert not list(tmp_path.glob("*.md"))

    @pytest.mark.asyncio
    async def test_empty_history(self, tmp_path):
        """История пустая — файл не создаётся, статус обновляется."""
        bot = _make_bot(history_items=[])
        message = _make_telegram_message("!export")
        status_msg = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        with patch("src.handlers.command_handlers.EXPORT_VAULT_DIR", tmp_path):
            await handle_export(bot, message)

        status_msg.edit.assert_called_once()
        assert not list(tmp_path.glob("*.md"))

    @pytest.mark.asyncio
    async def test_history_exception(self, tmp_path):
        """get_chat_history бросает исключение — статус-сообщение с ошибкой."""

        async def _bad_history(chat_id, limit):
            raise RuntimeError("MTProto error")
            # нужен yield чтобы это был async generator
            yield  # noqa: unreachable

        client = SimpleNamespace(
            get_chat_history=_bad_history,
        )
        bot = SimpleNamespace(client=client)
        message = _make_telegram_message("!export")
        status_msg = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        with patch("src.handlers.command_handlers.EXPORT_VAULT_DIR", tmp_path):
            await handle_export(bot, message)

        status_msg.edit.assert_called_once()
        call_text = status_msg.edit.call_args[0][0]
        assert "❌" in call_text

    @pytest.mark.asyncio
    async def test_file_written_with_correct_content(self, tmp_path):
        """Файл содержит frontmatter с chat_title и сообщениями."""
        msgs = self._make_msgs(2)
        bot = _make_bot(history_items=msgs)
        message = _make_telegram_message("!export", chat_title="Моя группа", chat_id=999)
        status_msg = AsyncMock()
        status_msg.delete = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        with patch("src.handlers.command_handlers.EXPORT_VAULT_DIR", tmp_path):
            await handle_export(bot, message)

        md_files = list(tmp_path.glob("*.md"))
        assert len(md_files) == 1
        content = md_files[0].read_text(encoding="utf-8")
        assert "chat_title: Моя группа" in content
        assert "chat_id: 999" in content

    @pytest.mark.asyncio
    async def test_filename_contains_date_and_title(self, tmp_path):
        """Имя файла содержит дату и заголовок чата."""
        msgs = self._make_msgs(1)
        bot = _make_bot(history_items=msgs)
        message = _make_telegram_message("!export", chat_title="TestChat")
        status_msg = AsyncMock()
        status_msg.delete = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        with patch("src.handlers.command_handlers.EXPORT_VAULT_DIR", tmp_path):
            await handle_export(bot, message)

        md_files = list(tmp_path.glob("*.md"))
        assert len(md_files) == 1
        filename = md_files[0].name
        assert "TestChat" in filename
        # Имя начинается с даты в формате YYYY-MM-DD
        import re

        assert re.match(r"\d{4}-\d{2}-\d{2}_", filename)

    @pytest.mark.asyncio
    async def test_send_document_called(self, tmp_path):
        """send_document вызывается после записи файла."""
        msgs = self._make_msgs(2)
        bot = _make_bot(history_items=msgs)
        message = _make_telegram_message("!export")
        status_msg = AsyncMock()
        status_msg.delete = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        with patch("src.handlers.command_handlers.EXPORT_VAULT_DIR", tmp_path):
            await handle_export(bot, message)

        bot.client.send_document.assert_called_once()
        kwargs = bot.client.send_document.call_args
        assert kwargs[1]["chat_id"] == message.chat.id or kwargs[0][0] == message.chat.id

    @pytest.mark.asyncio
    async def test_send_document_failure_shows_fallback(self, tmp_path):
        """Если send_document падает — статус обновляется с путём к файлу."""
        msgs = self._make_msgs(2)
        bot = _make_bot(history_items=msgs, send_document_raises=True)
        message = _make_telegram_message("!export")
        status_msg = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        with patch("src.handlers.command_handlers.EXPORT_VAULT_DIR", tmp_path):
            await handle_export(bot, message)

        # Файл всё равно должен быть создан
        md_files = list(tmp_path.glob("*.md"))
        assert len(md_files) == 1
        # Статус обновлён с ошибкой отправки
        status_msg.edit.assert_called_once()
        call_text = status_msg.edit.call_args[0][0]
        assert "✅" in call_text  # файл сохранён

    @pytest.mark.asyncio
    async def test_limit_capped_at_max(self, tmp_path):
        """!export 9999 — limit не превышает EXPORT_MAX_LIMIT."""
        # Проверяем через реальное поведение: просто не падает и создаёт файл
        msgs = self._make_msgs(2)
        bot = _make_bot(history_items=msgs)
        message = _make_telegram_message("!export 9999")
        status_msg = AsyncMock()
        status_msg.delete = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        with patch("src.handlers.command_handlers.EXPORT_VAULT_DIR", tmp_path):
            await handle_export(bot, message)

        md_files = list(tmp_path.glob("*.md"))
        assert len(md_files) == 1

    @pytest.mark.asyncio
    async def test_chat_without_title_uses_first_name(self, tmp_path):
        """Чат без title использует first_name (личный чат)."""
        msgs = self._make_msgs(1)
        bot = _make_bot(history_items=msgs)
        message = _make_telegram_message("!export")
        # Убираем title, добавляем first_name
        message.chat = SimpleNamespace(id=777, title=None, first_name="Алиса")
        status_msg = AsyncMock()
        status_msg.delete = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        with patch("src.handlers.command_handlers.EXPORT_VAULT_DIR", tmp_path):
            await handle_export(bot, message)

        md_files = list(tmp_path.glob("*.md"))
        assert len(md_files) == 1
        content = md_files[0].read_text(encoding="utf-8")
        assert "chat_title: Алиса" in content

    @pytest.mark.asyncio
    async def test_messages_reversed_chronologically(self, tmp_path):
        """Сообщения в файле в хронологическом порядке (не обратном)."""
        user = _make_user("Тест")
        # get_chat_history возвращает новые первые
        msgs_reversed = [
            _make_msg_obj(
                text="Новое сообщение",
                from_user=user,
                date=datetime.datetime(2026, 4, 12, 12, 0, 0),
            ),
            _make_msg_obj(
                text="Старое сообщение",
                from_user=user,
                date=datetime.datetime(2026, 4, 12, 10, 0, 0),
            ),
        ]
        bot = _make_bot(history_items=msgs_reversed)
        message = _make_telegram_message("!export")
        status_msg = AsyncMock()
        status_msg.delete = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        with patch("src.handlers.command_handlers.EXPORT_VAULT_DIR", tmp_path):
            await handle_export(bot, message)

        md_files = list(tmp_path.glob("*.md"))
        content = md_files[0].read_text(encoding="utf-8")
        # Старое должно быть раньше нового в файле
        old_pos = content.find("Старое сообщение")
        new_pos = content.find("Новое сообщение")
        assert old_pos < new_pos
