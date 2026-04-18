"""
Group identity helpers — префикс для ответов Краба в группах.

Chado blueprint: group-aware identity отдельно от userbot_bridge.
"""
from __future__ import annotations

import os

# Префикс-имя Краба для ответов в группах
KRAB_GROUP_PREFIX = os.environ.get("KRAB_GROUP_PREFIX", "🦀 Краб")


def format_group_reply(text: str, prefix: str = KRAB_GROUP_PREFIX) -> str:
    """
    Добавить префикс к тексту ответа в группе (если ещё нет).
    Используется когда Краб отвечает в групповом чате, чтобы
    было понятно кто именно ответил.
    """
    if not text:
        return text
    if text.startswith(prefix):
        return text
    return f"{prefix}: {text}"


def strip_group_prefix(text: str, prefix: str = KRAB_GROUP_PREFIX) -> str:
    """Удалить группо-префикс из текста (для нормализации команд)."""
    stripped = f"{prefix}: "
    if text.startswith(stripped):
        return text[len(stripped):]
    return text
