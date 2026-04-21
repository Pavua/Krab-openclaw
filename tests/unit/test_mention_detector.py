# -*- coding: utf-8 -*-
"""Тесты для src/core/mention_detector.py."""
from __future__ import annotations

import pytest

from src.core.mention_detector import (
    detect_command,
    detect_mention,
    detect_reply_to_self,
)

# ---------------------------------------------------------------------------
# Вспомогательные фабрики (dict-based, без зависимости от pyrogram)
# ---------------------------------------------------------------------------


def _msg(
    text: str = "",
    mentioned: bool = False,
    entities: list | None = None,
    reply_to_message=None,
) -> dict:
    return {
        "text": text,
        "mentioned": mentioned,
        "entities": entities or [],
        "reply_to_message": reply_to_message,
    }


def _user(uid: int) -> dict:
    return {"id": uid}


def _entity_with_user(uid: int) -> dict:
    return {"user": _user(uid)}


def _entity_with_user_id(uid: int) -> dict:
    return {"user_id": uid}


def _reply_from(uid: int) -> dict:
    return {"from_user": _user(uid)}


# ---------------------------------------------------------------------------
# detect_mention
# ---------------------------------------------------------------------------


class TestDetectMention:
    def test_pyrogram_flag_true(self):
        """pyrogram-флаг mentioned=True → True."""
        msg = _msg(text="hello", mentioned=True)
        assert detect_mention(msg) is True

    def test_pyrogram_flag_false_no_match(self):
        """Флаг False и нет других триггеров → False."""
        msg = _msg(text="hello", mentioned=False)
        assert detect_mention(msg, own_username="KrabBot") is False

    def test_username_in_text_exact(self):
        """@KrabBot в тексте совпадает с own_username='KrabBot'."""
        msg = _msg(text="Hey @KrabBot, do this!")
        assert detect_mention(msg, own_username="KrabBot") is True

    def test_username_in_text_case_insensitive(self):
        """@krabbot (lower) совпадает с own_username='KrabBot' (upper)."""
        msg = _msg(text="ping @krabbot pls")
        assert detect_mention(msg, own_username="KrabBot") is True

    def test_username_with_at_prefix_in_own_username(self):
        """own_username='@KrabBot' (с @) тоже работает."""
        msg = _msg(text="Hi @KrabBot!")
        assert detect_mention(msg, own_username="@KrabBot") is True

    def test_username_not_in_text(self):
        """Текст не содержит username → False."""
        msg = _msg(text="Hi @OtherBot!")
        assert detect_mention(msg, own_username="KrabBot") is False

    def test_entity_with_user_object(self):
        """Entity с полем user.id == own_user_id → True."""
        entity = _entity_with_user(12345)
        msg = _msg(text="yo", entities=[entity])
        assert detect_mention(msg, own_user_id=12345) is True

    def test_entity_with_user_id_direct(self):
        """Entity с полем user_id == own_user_id → True."""
        entity = _entity_with_user_id(99999)
        msg = _msg(text="yo", entities=[entity])
        assert detect_mention(msg, own_user_id=99999) is True

    def test_entity_wrong_user_id(self):
        """Entity с другим user_id → False."""
        entity = _entity_with_user(11111)
        msg = _msg(text="yo", entities=[entity])
        assert detect_mention(msg, own_user_id=22222) is False

    def test_no_mention_all_absent(self):
        """Флаг False, нет username, нет entities → False."""
        msg = _msg(text="Just a regular message.")
        assert detect_mention(msg) is False

    def test_none_message(self):
        """None message → False, без исключений."""
        assert detect_mention(None) is False

    def test_no_own_username_no_match(self):
        """Без own_username текстовая проверка не срабатывает."""
        msg = _msg(text="@KrabBot hello")
        assert detect_mention(msg, own_username=None) is False

    def test_empty_text(self):
        """Пустой текст → False."""
        msg = _msg(text="")
        assert detect_mention(msg, own_username="KrabBot") is False


# ---------------------------------------------------------------------------
# detect_reply_to_self
# ---------------------------------------------------------------------------


class TestDetectReplyToSelf:
    def test_reply_to_self_positive(self):
        """reply_to_message.from_user.id == own_user_id → True."""
        reply = _reply_from(42)
        msg = _msg(reply_to_message=reply)
        assert detect_reply_to_self(msg, own_user_id=42) is True

    def test_reply_to_other(self):
        """reply_to_message от другого пользователя → False."""
        reply = _reply_from(99)
        msg = _msg(reply_to_message=reply)
        assert detect_reply_to_self(msg, own_user_id=42) is False

    def test_no_reply(self):
        """Нет reply_to_message → False."""
        msg = _msg()
        assert detect_reply_to_self(msg, own_user_id=42) is False

    def test_none_message(self):
        """None message → False."""
        assert detect_reply_to_self(None, own_user_id=42) is False

    def test_none_own_user_id(self):
        """None own_user_id → False (нет базы для сравнения)."""
        reply = _reply_from(42)
        msg = _msg(reply_to_message=reply)
        assert detect_reply_to_self(msg, own_user_id=None) is False

    def test_reply_from_user_missing(self):
        """reply_to_message без from_user → False, без исключений."""
        reply = {"from_user": None}
        msg = _msg(reply_to_message=reply)
        assert detect_reply_to_self(msg, own_user_id=42) is False


# ---------------------------------------------------------------------------
# detect_command
# ---------------------------------------------------------------------------


class TestDetectCommand:
    def test_exclamation_mark(self):
        """!ask → True."""
        assert detect_command("!ask something") is True

    def test_slash_command(self):
        """/start → True."""
        assert detect_command("/start") is True

    def test_not_a_command(self):
        """Обычный текст → False."""
        assert detect_command("hello world") is False

    def test_none_text(self):
        """None → False."""
        assert detect_command(None) is False

    def test_empty_string(self):
        """Пустая строка → False."""
        assert detect_command("") is False

    def test_leading_whitespace(self):
        """Пробелы перед ! → True (lstrip применяется)."""
        assert detect_command("  !help") is True

    def test_leading_whitespace_slash(self):
        """Пробелы перед / → True."""
        assert detect_command("  /cmd") is True
