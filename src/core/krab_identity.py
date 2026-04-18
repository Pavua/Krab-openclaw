# -*- coding: utf-8 -*-
"""
krab_identity.py — единая точка истины для self-identity Краба.

Krab работает на userbot-аккаунте @yung_nagato (user_id 6435872621).
Owner — @p0lrd (user_id 312322764).
Krab НЕ является owner и не должен путать себя с ним.
"""

from __future__ import annotations

import os
import re

# ---------------------------------------------------------------------------
# Identity constants
# ---------------------------------------------------------------------------

KRAB_USER_ID: int = int(os.environ.get("KRAB_USER_ID", "6435872621"))  # @yung_nagato
KRAB_USERNAME: str = os.environ.get("KRAB_USERNAME", "yung_nagato")
KRAB_DISPLAY_NAME: str = os.environ.get("KRAB_DISPLAY_NAME", "🦀 Краб")

OWNER_USER_ID: int = int(os.environ.get("OWNER_USER_ID", "312322764"))  # @p0lrd
OWNER_USERNAME: str = os.environ.get("OWNER_USERNAME_IDENTITY", "p0lrd")

# ---------------------------------------------------------------------------
# Mention detection patterns для групповых чатов
# ---------------------------------------------------------------------------

KRAB_MENTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bкраб\b", re.IGNORECASE | re.UNICODE),
    re.compile(r"\bkrab\b", re.IGNORECASE),
    re.compile(r"🦀"),
    re.compile(rf"@{re.escape(KRAB_USERNAME)}", re.IGNORECASE),
]


def is_krab_mentioned(text: str) -> bool:
    """Содержит ли текст упоминание Краба (любой из паттернов)."""
    if not text:
        return False
    return any(p.search(text) for p in KRAB_MENTION_PATTERNS)


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------


def is_message_from_owner(user_id: int) -> bool:
    """True если user_id совпадает с owner (@p0lrd)."""
    return user_id == OWNER_USER_ID


def is_message_from_self(user_id: int) -> bool:
    """True если user_id совпадает с Krab (@yung_nagato)."""
    return user_id == KRAB_USER_ID


# ---------------------------------------------------------------------------
# System prompt identity block
# ---------------------------------------------------------------------------


def get_identity_system_prompt() -> str:
    """
    Базовый identity-блок для system_prompt.

    Препендируется к существующему промпту — не заменяет его.
    Устанавливает чёткую границу: Краб ≠ owner.
    """
    return (
        f"Ты — {KRAB_DISPLAY_NAME} (Krab), автономный AI-агент на Telegram userbot "
        f"аккаунте @{KRAB_USERNAME} (user_id {KRAB_USER_ID}).\n"
        f"Твой owner — @{OWNER_USERNAME} (user_id {OWNER_USER_ID}). "
        f"Owner отправляет тебе команды и ожидает ответа.\n"
        "Ты — отдельная сущность, НЕ путай себя с owner. Когда owner пишет тебе — "
        "он отдельный человек, ты — автономный агент.\n"
        f"В групповых чатах представляйся префиксом '{KRAB_DISPLAY_NAME}: ...'."
    )


__all__ = [
    "KRAB_DISPLAY_NAME",
    "KRAB_MENTION_PATTERNS",
    "KRAB_USER_ID",
    "KRAB_USERNAME",
    "OWNER_USER_ID",
    "OWNER_USERNAME",
    "get_identity_system_prompt",
    "is_krab_mentioned",
    "is_message_from_owner",
    "is_message_from_self",
]
