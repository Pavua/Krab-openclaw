# -*- coding: utf-8 -*-
"""
tests/unit/test_reply_to_metadata.py

Проверяет, что текст parent-сообщения (reply_to_text) корректно
извлекается и попадает в [context] блок — fix бага «не вижу reply».
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.sender_context import _extract_reply_info, build_context_block

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_from_user(user_id=123, username="testuser", first_name="Test"):
    return SimpleNamespace(id=user_id, username=username, first_name=first_name, last_name="")


_SENTINEL = object()


def _make_reply(text=None, caption=None, from_user=_SENTINEL, **media_kwargs):
    """Создаёт объект reply_to_message."""
    resolved_from_user = _make_from_user() if from_user is _SENTINEL else from_user
    obj = SimpleNamespace(
        text=text,
        caption=caption,
        from_user=resolved_from_user,
        photo=None,
        audio=None,
        voice=None,
        video=None,
        document=None,
        sticker=None,
        animation=None,
    )
    for k, v in media_kwargs.items():
        setattr(obj, k, v)
    return obj


def _make_message(reply_to_message=None, from_user=None, text="hello", chat_type="private"):
    chat = SimpleNamespace(type=SimpleNamespace(value=chat_type), title="", id=1)
    return SimpleNamespace(
        text=text,
        caption=None,
        from_user=from_user or _make_from_user(user_id=1, username="owner"),
        chat=chat,
        reply_to_message=reply_to_message,
        forward_from=None,
        forward_sender_name=None,
        forward_from_chat=None,
        entities=[],
        mentioned=False,
    )


# ---------------------------------------------------------------------------
# Тесты _extract_reply_info
# ---------------------------------------------------------------------------


class TestExtractReplyInfo:
    def test_no_reply_returns_empty(self):
        msg = _make_message(reply_to_message=None)
        info = _extract_reply_info(msg)
        assert info["is_reply"] is False
        assert info["reply_to_text"] == ""

    def test_reply_with_text(self):
        reply = _make_reply(text="Привет, как дела?")
        msg = _make_message(reply_to_message=reply)
        info = _extract_reply_info(msg)
        assert info["is_reply"] is True
        assert info["reply_to_text"] == "Привет, как дела?"

    def test_reply_with_caption(self):
        reply = _make_reply(caption="Подпись к фото")
        msg = _make_message(reply_to_message=reply)
        info = _extract_reply_info(msg)
        assert info["reply_to_text"] == "Подпись к фото"

    def test_reply_text_truncated_to_500(self):
        long_text = "А" * 600
        reply = _make_reply(text=long_text)
        msg = _make_message(reply_to_message=reply)
        info = _extract_reply_info(msg)
        # Должно быть ровно 500 + "…"
        assert len(info["reply_to_text"]) == 501  # 500 + ellipsis
        assert info["reply_to_text"].endswith("…")

    def test_reply_media_only_no_caption(self):
        # photo есть, text/caption нет
        reply = _make_reply(text=None, caption=None, photo=SimpleNamespace(file_id="x"))
        msg = _make_message(reply_to_message=reply)
        info = _extract_reply_info(msg)
        assert info["is_reply"] is True
        assert info["reply_to_text"] == "[media]"

    def test_reply_voice_media(self):
        reply = _make_reply(text=None, caption=None, voice=SimpleNamespace(file_id="v"))
        msg = _make_message(reply_to_message=reply)
        info = _extract_reply_info(msg)
        assert info["reply_to_text"] == "[media]"

    def test_reply_without_from_user(self):
        """Graceful handling: reply без from_user (анонимное сообщение)."""
        reply = _make_reply(text="Анонимный текст", from_user=None)
        msg = _make_message(reply_to_message=reply)
        info = _extract_reply_info(msg)
        assert info["is_reply"] is True
        assert info["reply_to_text"] == "Анонимный текст"
        # Пользовательские поля пустые
        assert info["reply_to_user_id"] == ""
        assert info["reply_to_username"] == ""

    def test_reply_empty_text_no_media(self):
        """Пустой текст и нет медиа → reply_to_text пустой."""
        reply = _make_reply(text="", caption=None)
        msg = _make_message(reply_to_message=reply)
        info = _extract_reply_info(msg)
        assert info["is_reply"] is True
        assert info["reply_to_text"] == ""


# ---------------------------------------------------------------------------
# Тесты build_context_block
# ---------------------------------------------------------------------------


class TestBuildContextBlockReply:
    def test_context_includes_reply_to_text(self):
        reply = _make_reply(text="что думаешь об этой идее?")
        msg = _make_message(reply_to_message=reply)
        block = build_context_block(msg, is_owner=True)
        assert "reply_to_text: что думаешь об этой идее?" in block

    def test_context_includes_reply_hint(self):
        reply = _make_reply(text="некоторый текст")
        msg = _make_message(reply_to_message=reply)
        block = build_context_block(msg, is_owner=True)
        assert "reply_hint:" in block
        assert "reply_to_text" in block  # подсказка ссылается на поле

    def test_context_no_reply_omits_reply_to_text(self):
        msg = _make_message(reply_to_message=None)
        block = build_context_block(msg, is_owner=True)
        assert "reply_to_text:" not in block
        assert "reply_hint:" not in block

    def test_context_media_reply_shows_placeholder(self):
        reply = _make_reply(text=None, caption=None, photo=SimpleNamespace(file_id="img"))
        msg = _make_message(reply_to_message=reply)
        block = build_context_block(msg, is_owner=True)
        assert "reply_to_text: [media]" in block
