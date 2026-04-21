# -*- coding: utf-8 -*-
"""
group_identity — оформление ответов Краба в групповом контексте (Chado Wave 16).

В группах и супергруппах ответы Краба префиксируются 🦀
чтобы визуально выделяться среди пользовательских сообщений.
В личных чатах (PRIVATE) префикс не добавляется.
"""

from __future__ import annotations

from enum import IntEnum


# Используем IntEnum для совместимости с/без pyrogram в тестах
class _FallbackChatType(IntEnum):
    PRIVATE = 1
    BOT = 2
    GROUP = 3
    SUPERGROUP = 4
    CHANNEL = 5


_KRAB_PREFIX = "🦀 "


def _is_group_context(chat_type: object) -> bool:
    """True для GROUP / SUPERGROUP (pyrogram или fallback enum)."""
    # Поддержка и pyrogram.enums.ChatType, и нашего fallback
    name = getattr(chat_type, "name", str(chat_type)).upper()
    return name in ("GROUP", "SUPERGROUP", "CHANNEL")


def apply_identity_prefix(text: str, chat_type: object) -> str:
    """Добавить 🦀-префикс если контекст групповой.

    Безопасно работает и с pyrogram.enums.ChatType, и с _FallbackChatType,
    и со строками ("GROUP", "PRIVATE", ...).
    """
    if _is_group_context(chat_type):
        return _KRAB_PREFIX + text
    return text


def strip_identity_prefix(text: str) -> str:
    """Убрать 🦀-префикс если он есть (для тестов и нормализации)."""
    if text.startswith(_KRAB_PREFIX):
        return text[len(_KRAB_PREFIX) :]
    return text
