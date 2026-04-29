# -*- coding: utf-8 -*-
"""
Bug 4 (403 MESSAGE_AUTHOR_REQUIRED) — root-cause fix tests.

Сценарий: в group chats при is_self=False и _show_progress_notices=False
`temp_message` совпадает с входящим (чужим) сообщением. Edit чужого сообщения
запрещён → Telegram возвращает 403 MESSAGE_AUTHOR_REQUIRED.

Тесты покрывают:
1. Guard в _deliver_response_parts (placeholder branch) — reply вместо edit, когда
   `temp_message is source_message`.
2. Guard в _deliver_response_parts (главный edit_and_reply branch) — то же самое.
3. Normal path (`temp_message is not source_message`) — остаётся через _safe_edit.
4. Defense in depth: _safe_edit ловит MESSAGE_AUTHOR_REQUIRED и fallback'ит на
   send_message с reply_to_message_id.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_message(text: str = "", chat_id: int = 100, msg_id: int = 42) -> MagicMock:
    """Минимальный mock Pyrogram Message."""
    msg = MagicMock()
    msg.text = text or None
    msg.caption = None
    msg.chat = SimpleNamespace(id=chat_id)
    msg.id = msg_id
    msg.reply = AsyncMock()
    msg.edit = AsyncMock()
    msg.delete = AsyncMock()
    return msg


async def _mock_run(_chat_id, fn):
    """_telegram_send_queue.run replacement."""
    result = fn()
    if asyncio.iscoroutine(result):
        return await result
    return result


def _make_bot():
    """Stub KraabUserbot без __init__."""
    from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

    bot = KraabUserbot.__new__(KraabUserbot)
    bot.client = MagicMock()
    bot.client.send_message = AsyncMock()
    # Stub helpers, используемые в _deliver_response_parts.
    bot._should_send_full_text_reply = MagicMock(return_value=True)
    bot._should_send_voice_reply = MagicMock(return_value=False)
    bot._split_message = MagicMock(side_effect=lambda text: [text])
    bot._maybe_record_smart_trigger_response = MagicMock()
    bot._maybe_schedule_autodel = MagicMock()
    return bot


# ---------------------------------------------------------------------------
# 1. Guard: placeholder-only branch (voice text suppressed)
# ---------------------------------------------------------------------------


class TestPlaceholderOnlyBranchGuard:
    """Когда text-reply подавлён и temp_message is source_message → reply вместо edit."""

    @pytest.mark.asyncio
    async def test_guard_uses_reply_when_temp_is_source(self):
        bot = _make_bot()
        bot._should_send_full_text_reply = MagicMock(return_value=False)
        source = _make_message(text="привет краб", chat_id=-100, msg_id=777)
        replied = MagicMock()
        replied.id = 8001
        bot._safe_reply_or_send_new = AsyncMock(return_value=replied)
        bot._safe_edit = AsyncMock()

        result = await bot._deliver_response_parts(
            source_message=source,
            temp_message=source,  # Bug 4 trigger
            is_self=False,
            query="q",
            full_response="r",
        )

        bot._safe_edit.assert_not_called()
        bot._safe_reply_or_send_new.assert_awaited_once()
        assert result["delivery_mode"] == "placeholder_only"
        assert result["text_message_ids"] == ["8001"]


# ---------------------------------------------------------------------------
# 2. Guard: main edit_and_reply branch (single part)
# ---------------------------------------------------------------------------


class TestEditAndReplyBranchGuard:
    """Главный путь доставки: temp_message is source_message → reply вместо edit."""

    @pytest.mark.asyncio
    async def test_guard_uses_reply_in_main_path(self):
        bot = _make_bot()
        source = _make_message(text="hi", chat_id=-200, msg_id=555)
        replied = MagicMock()
        replied.id = 9001
        bot._safe_reply_or_send_new = AsyncMock(return_value=replied)
        bot._safe_edit = AsyncMock()

        result = await bot._deliver_response_parts(
            source_message=source,
            temp_message=source,  # Bug 4 trigger
            is_self=False,
            query="q",
            full_response="single response",
        )

        bot._safe_edit.assert_not_called()
        bot._safe_reply_or_send_new.assert_awaited()
        assert result["delivery_mode"] == "edit_and_reply"
        assert "9001" in result["text_message_ids"]


# ---------------------------------------------------------------------------
# 3. Normal path: temp_message != source_message → _safe_edit используется
# ---------------------------------------------------------------------------


class TestNormalPathStillEdits:
    """Если temp_message — отдельное сообщение Краба, edit-путь сохраняется."""

    @pytest.mark.asyncio
    async def test_normal_path_uses_edit(self):
        bot = _make_bot()
        source = _make_message(text="hi", chat_id=-300, msg_id=111)
        temp = _make_message(text="...", chat_id=-300, msg_id=222)
        edited = MagicMock()
        edited.id = 222
        bot._safe_edit = AsyncMock(return_value=edited)
        bot._safe_reply_or_send_new = AsyncMock()

        result = await bot._deliver_response_parts(
            source_message=source,
            temp_message=temp,  # отдельное сообщение Краба
            is_self=False,
            query="q",
            full_response="reply text",
        )

        bot._safe_edit.assert_awaited()
        # _safe_reply_or_send_new НЕ должен использоваться для первой части в single-part случае
        bot._safe_reply_or_send_new.assert_not_awaited()
        assert result["delivery_mode"] == "edit_and_reply"

    @pytest.mark.asyncio
    async def test_normal_path_placeholder_branch_uses_edit(self):
        bot = _make_bot()
        bot._should_send_full_text_reply = MagicMock(return_value=False)
        source = _make_message(text="hi", chat_id=-400, msg_id=333)
        temp = _make_message(text="...", chat_id=-400, msg_id=444)
        edited = MagicMock()
        edited.id = 444
        bot._safe_edit = AsyncMock(return_value=edited)
        bot._safe_reply_or_send_new = AsyncMock()

        await bot._deliver_response_parts(
            source_message=source,
            temp_message=temp,
            is_self=False,
            query="q",
            full_response="r",
        )

        bot._safe_edit.assert_awaited_once()
        bot._safe_reply_or_send_new.assert_not_awaited()


# ---------------------------------------------------------------------------
# 4. Defense in depth: _safe_edit ловит MESSAGE_AUTHOR_REQUIRED
# ---------------------------------------------------------------------------


class TestSafeEditAuthorRequiredFallback:
    """Если guard был обойдён — _safe_edit делает fallback на send_message + reply_to."""

    def test_detector_recognizes_author_required(self):
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        exc = Exception("[403 MESSAGE_AUTHOR_REQUIRED] Telegram says: ...")
        assert KraabUserbot._is_message_author_required_error(exc) is True

    def test_detector_ignores_unrelated_errors(self):
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        assert (
            KraabUserbot._is_message_author_required_error(Exception("FLOOD_WAIT"))
            is False
        )

    @pytest.mark.asyncio
    async def test_safe_edit_falls_back_to_reply_on_author_required(self):
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        bot = KraabUserbot.__new__(KraabUserbot)
        bot.client = MagicMock()
        sent = MagicMock()
        sent.id = 12345
        bot.client.send_message = AsyncMock(return_value=sent)

        msg = _make_message(text="чужое сообщение", chat_id=-500, msg_id=666)
        msg.edit = AsyncMock(side_effect=Exception("[403 MESSAGE_AUTHOR_REQUIRED]"))

        with patch("src.userbot_bridge._telegram_send_queue") as mock_q:
            mock_q.run = AsyncMock(side_effect=_mock_run)
            result = await KraabUserbot._safe_edit(bot, msg, "новый текст")

        # Должен вызвать send_message с reply_to_message_id=666
        bot.client.send_message.assert_called_once()
        call_kwargs = bot.client.send_message.call_args.kwargs
        call_args = bot.client.send_message.call_args.args
        # Поддерживаем оба варианта: kwargs или positional.
        reply_to = call_kwargs.get("reply_to_message_id")
        assert reply_to == 666 or 666 in call_args
        assert result is sent
