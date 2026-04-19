# -*- coding: utf-8 -*-
"""
Тесты для parse_mode="markdown" по умолчанию в _safe_reply_or_send_new
и _safe_edit (Session 11, feature req #1).

Проверяем:
1. По умолчанию используется ParseMode.MARKDOWN (звёздочки как bold, не литералы).
2. При ошибке парсинга markdown → retry без parse_mode.
3. Backward compat: явный parse_mode не затронут.
4. Helper _is_markdown_parse_error корректно детектит RPC-ошибки.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pyrogram import enums


def _make_message(text: str = "", chat_id: int = 100) -> MagicMock:
    """Минимальный mock Pyrogram Message."""
    msg = MagicMock()
    msg.text = text or None
    msg.caption = None
    msg.chat = SimpleNamespace(id=chat_id)
    msg.id = 42
    msg.reply = AsyncMock(return_value=MagicMock())
    msg.edit = AsyncMock(return_value=MagicMock())
    return msg


def _make_bot() -> MagicMock:
    """
    Создаёт mock KraabUserbot с реальными static-методами детекторов ошибок.

    Без этого MagicMock по умолчанию возвращает truthy MagicMock на любой
    self._is_XXX_error(), что ломает логику веток.
    """
    from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

    bot = MagicMock()
    # Подставляем настоящие staticmethods, чтобы ветвление работало корректно.
    bot._is_message_not_modified_error = KraabUserbot._is_message_not_modified_error
    bot._is_message_id_invalid_error = KraabUserbot._is_message_id_invalid_error
    bot._is_message_empty_error = KraabUserbot._is_message_empty_error
    bot._is_message_too_long_error = KraabUserbot._is_message_too_long_error
    bot._is_markdown_parse_error = KraabUserbot._is_markdown_parse_error
    return bot


async def _mock_run(_chat_id, fn):
    """_telegram_send_queue.run replacement — await coroutine результата lambda."""
    result = fn()
    if asyncio.iscoroutine(result):
        return await result
    return result


# ---------------------------------------------------------------------------
# _is_markdown_parse_error — детектор ошибок парсинга markdown
# ---------------------------------------------------------------------------


class TestIsMarkdownParseError:
    """Проверяет, что детектор ловит RPC parse errors и не триггерится на TypeError."""

    def test_detects_cant_parse_entities(self):
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        exc = Exception("Telegram says: Can't parse entities: unexpected char")
        assert KraabUserbot._is_markdown_parse_error(exc) is True

    def test_detects_message_entities_invalid(self):
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        exc = Exception("MESSAGE_ENTITIES_INVALID from Telegram")
        assert KraabUserbot._is_markdown_parse_error(exc) is True

    def test_detects_entities_too_long(self):
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        exc = Exception("ENTITIES_TOO_LONG error")
        assert KraabUserbot._is_markdown_parse_error(exc) is True

    def test_does_not_match_type_error_parse_mode_kwarg(self):
        """TypeError про parse_mode kwarg не должен ловиться (он не про entities)."""
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        exc = TypeError("got an unexpected keyword argument 'parse_mode'")
        assert KraabUserbot._is_markdown_parse_error(exc) is False

    def test_does_not_match_unrelated_error(self):
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        exc = Exception("FLOOD_WAIT_X 30")
        assert KraabUserbot._is_markdown_parse_error(exc) is False


# ---------------------------------------------------------------------------
# _safe_reply_or_send_new — default parse_mode=markdown
# ---------------------------------------------------------------------------


class TestSafeReplyDefaultsToMarkdown:
    """Проверяет что _safe_reply_or_send_new по умолчанию шлёт с parse_mode=markdown."""

    @pytest.mark.asyncio
    async def test_safe_reply_defaults_to_markdown(self):
        """По умолчанию msg.reply получает parse_mode=ParseMode.MARKDOWN."""
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        bot = _make_bot()
        sent = MagicMock()
        msg = _make_message(text="", chat_id=111)
        msg.reply = AsyncMock(return_value=sent)

        with patch("src.userbot_bridge._telegram_send_queue") as mock_q:
            mock_q.run = AsyncMock(side_effect=_mock_run)
            result = await KraabUserbot._safe_reply_or_send_new(bot, msg, "**bold** and _italic_")

        assert result is sent
        # Проверяем что msg.reply был вызван с parse_mode=MARKDOWN
        msg.reply.assert_called_once()
        _args, kwargs = msg.reply.call_args
        assert kwargs.get("parse_mode") == enums.ParseMode.MARKDOWN

    @pytest.mark.asyncio
    async def test_explicit_parse_mode_none_respected(self):
        """Backward compat: если передать parse_mode=None явно — markdown не применяется."""
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        bot = _make_bot()
        sent = MagicMock()
        msg = _make_message(text="", chat_id=112)
        msg.reply = AsyncMock(return_value=sent)

        with patch("src.userbot_bridge._telegram_send_queue") as mock_q:
            mock_q.run = AsyncMock(side_effect=_mock_run)
            await KraabUserbot._safe_reply_or_send_new(
                bot, msg, "text with *stars*", parse_mode=None
            )

        _args, kwargs = msg.reply.call_args
        assert kwargs.get("parse_mode") is None

    @pytest.mark.asyncio
    async def test_safe_reply_fallback_on_parse_error(self):
        """При ошибке 'Can't parse entities' → retry без parse_mode."""
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        bot = _make_bot()
        sent = MagicMock()
        msg = _make_message(text="", chat_id=113)
        # Первый вызов — ошибка парсинга, второй — успех.
        msg.reply = AsyncMock(
            side_effect=[
                Exception("Can't parse entities: unexpected end tag"),
                sent,
            ]
        )

        with patch("src.userbot_bridge._telegram_send_queue") as mock_q:
            mock_q.run = AsyncMock(side_effect=_mock_run)
            result = await KraabUserbot._safe_reply_or_send_new(bot, msg, "**broken markdown")

        assert result is sent
        # Два вызова: первый с MARKDOWN, второй с None
        assert msg.reply.call_count == 2
        first_kwargs = msg.reply.call_args_list[0].kwargs
        second_kwargs = msg.reply.call_args_list[1].kwargs
        assert first_kwargs.get("parse_mode") == enums.ParseMode.MARKDOWN
        assert second_kwargs.get("parse_mode") is None

    @pytest.mark.asyncio
    async def test_fallback_to_send_message_preserves_markdown(self):
        """При generic ошибке reply → fallback на send_message (тоже с markdown)."""
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        bot = _make_bot()
        fallback_msg = MagicMock()
        bot.client.send_message = AsyncMock(return_value=fallback_msg)
        msg = _make_message(text="", chat_id=114)
        msg.reply = AsyncMock(side_effect=Exception("REPLY_FAILED_GENERIC"))

        with patch("src.userbot_bridge._telegram_send_queue") as mock_q:
            mock_q.run = AsyncMock(side_effect=_mock_run)
            await KraabUserbot._safe_reply_or_send_new(bot, msg, "*hi*")

        bot.client.send_message.assert_called_once()
        _args, kwargs = bot.client.send_message.call_args
        assert kwargs.get("parse_mode") == enums.ParseMode.MARKDOWN


# ---------------------------------------------------------------------------
# _safe_edit — default parse_mode=markdown
# ---------------------------------------------------------------------------


class TestSafeEditDefaultsToMarkdown:
    """Проверяет что _safe_edit по умолчанию шлёт с parse_mode=markdown."""

    @pytest.mark.asyncio
    async def test_safe_edit_defaults_to_markdown(self):
        """По умолчанию msg.edit получает parse_mode=ParseMode.MARKDOWN."""
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        bot = _make_bot()
        edited = MagicMock()
        msg = _make_message(text="old", chat_id=211)
        msg.edit = AsyncMock(return_value=edited)

        with patch("src.userbot_bridge._telegram_send_queue") as mock_q:
            mock_q.run = AsyncMock(side_effect=_mock_run)
            result = await KraabUserbot._safe_edit(bot, msg, "**new bold**")

        assert result is edited
        msg.edit.assert_called_once()
        _args, kwargs = msg.edit.call_args
        assert kwargs.get("parse_mode") == enums.ParseMode.MARKDOWN

    @pytest.mark.asyncio
    async def test_safe_edit_skips_when_same_text(self):
        """Если current_text == target_text, edit не вызывается."""
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        bot = _make_bot()
        msg = _make_message(text="same text", chat_id=212)

        result = await KraabUserbot._safe_edit(bot, msg, "same text")

        assert result is msg
        msg.edit.assert_not_called()

    @pytest.mark.asyncio
    async def test_safe_edit_fallback_on_parse_error(self):
        """При ошибке парсинга markdown → retry без parse_mode."""
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        bot = _make_bot()
        edited = MagicMock()
        msg = _make_message(text="old", chat_id=213)
        msg.edit = AsyncMock(
            side_effect=[
                Exception("Can't parse entities: invalid markdown"),
                edited,
            ]
        )

        with patch("src.userbot_bridge._telegram_send_queue") as mock_q:
            mock_q.run = AsyncMock(side_effect=_mock_run)
            result = await KraabUserbot._safe_edit(bot, msg, "**invalid _md")

        assert result is edited
        assert msg.edit.call_count == 2
        second_kwargs = msg.edit.call_args_list[1].kwargs
        assert second_kwargs.get("parse_mode") is None

    @pytest.mark.asyncio
    async def test_safe_edit_explicit_none_parse_mode(self):
        """Backward compat: parse_mode=None — markdown не применяется."""
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        bot = _make_bot()
        edited = MagicMock()
        msg = _make_message(text="old", chat_id=214)
        msg.edit = AsyncMock(return_value=edited)

        with patch("src.userbot_bridge._telegram_send_queue") as mock_q:
            mock_q.run = AsyncMock(side_effect=_mock_run)
            await KraabUserbot._safe_edit(bot, msg, "plain *text*", parse_mode=None)

        _args, kwargs = msg.edit.call_args
        assert kwargs.get("parse_mode") is None

    @pytest.mark.asyncio
    async def test_safe_edit_message_not_modified_returns_msg(self):
        """MESSAGE_NOT_MODIFIED — возвращаем исходный msg без ошибки."""
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        bot = _make_bot()
        msg = _make_message(text="old", chat_id=215)
        msg.edit = AsyncMock(side_effect=Exception("MESSAGE_NOT_MODIFIED"))

        with patch("src.userbot_bridge._telegram_send_queue") as mock_q:
            mock_q.run = AsyncMock(side_effect=_mock_run)
            result = await KraabUserbot._safe_edit(bot, msg, "different text")

        assert result is msg
