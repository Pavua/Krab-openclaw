# -*- coding: utf-8 -*-
"""
MessagePriorityDispatcher — классификация приоритета входящих сообщений (Chado Wave 16).

Приоритеты:
  P0_INSTANT  — немедленный ответ (DM, упоминание, команда, reply на себя)
  P1_NORMAL   — обычный (активный чат)
  P2_LOW      — низкий (muted, неизвестный режим)
"""

from __future__ import annotations

from enum import IntEnum


class Priority(IntEnum):
    P0_INSTANT = 0
    P1_NORMAL = 1
    P2_LOW = 2


def classify_priority(
    text: str,
    chat_type: str,  # "PRIVATE", "GROUP", "SUPERGROUP", "BOT", ...
    *,
    is_dm: bool,
    is_reply_to_self: bool,
    has_mention: bool,
    chat_mode: str,  # "active", "mention-only", "muted"
) -> tuple[Priority, str]:
    """Определить приоритет и причину.

    Returns (Priority, reason_str).
    """
    # P0: личный чат
    if is_dm or chat_type.upper() == "PRIVATE":
        return Priority.P0_INSTANT, "dm"

    # P0: reply на собственное сообщение
    if is_reply_to_self:
        return Priority.P0_INSTANT, "reply_to_self"

    # P0: явное упоминание
    if has_mention:
        return Priority.P0_INSTANT, "mention"

    # P0: команда (starts with !)
    stripped = (text or "").lstrip()
    if stripped.startswith("!"):
        return Priority.P0_INSTANT, "command"

    # P2: muted
    if chat_mode == "muted":
        return Priority.P2_LOW, "muted"

    # P1: всё остальное активное
    if chat_mode == "active":
        return Priority.P1_NORMAL, "active"

    # P2: mention-only без упоминания, или неизвестный режим
    return Priority.P2_LOW, f"mode_{chat_mode}_no_trigger"


def get_mode_for_chat(chat_id: int | str, *, is_group: bool = True) -> str:
    """Получить текущий filter mode для чата из персистентного конфига.

    Delegate к chat_filter_config.get_chat_mode — единый источник истины.
    Используется вышестоящими компонентами для передачи chat_mode в classify_priority.

    Returns:
        "active" | "mention-only" | "muted"
    """
    from .chat_filter_config import chat_filter_config  # lazy import — избегаем цикла

    return chat_filter_config.get_chat_mode(chat_id, is_group=is_group)
