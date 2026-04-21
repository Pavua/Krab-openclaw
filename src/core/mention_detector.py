# -*- coding: utf-8 -*-
"""Robust mention + reply-to-self detection for incoming Telegram messages.

Abstracts away pyrogram Message specifics: tests can pass dicts or
simple objects with attributes.

Public API:
- detect_mention(message, *, own_username=None, own_user_id=None) -> bool
  Checks:
  - message.mentioned  (pyrogram sets this flag on group mentions)
  - text contains "@<own_username>" (case-insensitive)
  - entities include MessageEntityMention with matching user_id
- detect_reply_to_self(message, *, own_user_id=None) -> bool
  Checks:
  - message.reply_to_message is not None
  - message.reply_to_message.from_user.id == own_user_id
- detect_command(text) -> bool
  Simple: text.startswith(("!", "/"))
"""

from __future__ import annotations

from typing import Any


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """Defensive attribute/key access — supports both objects and dicts."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _message_text(message: Any) -> str:
    """Extract raw text from message (text or caption)."""
    text = _attr(message, "text") or _attr(message, "caption") or ""
    return str(text) if text else ""


def detect_mention(
    message: Any,
    *,
    own_username: str | None = None,
    own_user_id: int | None = None,
) -> bool:
    """True если сообщение содержит упоминание нашего аккаунта.

    Проверяет три канала:
    1. pyrogram-флаг ``message.mentioned``
    2. Текст содержит ``@<own_username>`` (case-insensitive)
    3. Entities типа MessageEntityMention или MessageEntityTextUrl
       с совпадающим user_id
    """
    if message is None:
        return False

    # 1. pyrogram native flag
    if _attr(message, "mentioned"):
        return True

    text = _message_text(message)

    # 2. username в тексте
    if own_username and text:
        username_clean = own_username.lstrip("@")
        if f"@{username_clean}".lower() in text.lower():
            return True

    # 3. entities с user_id
    if own_user_id is not None:
        entities = _attr(message, "entities") or []
        for entity in entities:
            # pyrogram: entity.user — объект User
            user = _attr(entity, "user")
            if user is not None:
                uid = _attr(user, "id")
                if uid is not None and int(uid) == int(own_user_id):
                    return True
            # некоторые реализации хранят user_id напрямую
            uid_direct = _attr(entity, "user_id")
            if uid_direct is not None and int(uid_direct) == int(own_user_id):
                return True

    return False


def detect_reply_to_self(
    message: Any,
    *,
    own_user_id: int | None = None,
) -> bool:
    """True если сообщение является reply на наше собственное сообщение.

    Проверяет:
    - message.reply_to_message не None
    - reply_to_message.from_user.id == own_user_id
    """
    if message is None or own_user_id is None:
        return False

    reply = _attr(message, "reply_to_message")
    if reply is None:
        return False

    from_user = _attr(reply, "from_user")
    if from_user is None:
        return False

    uid = _attr(from_user, "id")
    if uid is None:
        return False

    return int(uid) == int(own_user_id)


def detect_command(text: str | None) -> bool:
    """True если текст начинается с команды (! или /)."""
    if not text:
        return False
    stripped = text.lstrip()
    return stripped.startswith(("!", "/"))
