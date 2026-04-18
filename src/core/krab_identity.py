"""
Krab identity helpers — определение own-messages и упоминаний.

Chado blueprint: identity layer отдельно от userbot_bridge,
чтобы любой модуль мог вызвать без circular imports.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from .logger import get_logger

logger = get_logger(__name__)

# Env-overridable owner user_id
_OWNER_ID: Optional[int] = None


def get_krab_user_id() -> Optional[int]:
    """Вернуть user_id Краба из env KRAB_SELF_USER_ID (заполняется при старте)."""
    global _OWNER_ID
    if _OWNER_ID is not None:
        return _OWNER_ID
    raw = os.environ.get("KRAB_SELF_USER_ID", "")
    if raw.isdigit():
        _OWNER_ID = int(raw)
        return _OWNER_ID
    return None


def set_krab_user_id(user_id: int) -> None:
    """Установить user_id Краба (вызывается при старте userbot_bridge)."""
    global _OWNER_ID
    _OWNER_ID = user_id
    os.environ["KRAB_SELF_USER_ID"] = str(user_id)
    logger.debug("krab_user_id_set", user_id=user_id)


def is_message_from_self(sender_id: int) -> bool:
    """Вернуть True если sender_id совпадает с Крабом."""
    krab_id = get_krab_user_id()
    if krab_id is None:
        return False
    return sender_id == krab_id


# Ключевые слова для обнаружения обращения к Крабу
_MENTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bкраб\b", re.IGNORECASE),
    re.compile(r"\bkrab\b", re.IGNORECASE),
    re.compile(r"@krab", re.IGNORECASE),
    re.compile(r"@краб", re.IGNORECASE),
]

# Дополнительные паттерны из env (разделитель |)
_extra_raw = os.environ.get("KRAB_MENTION_EXTRA_PATTERNS", "")
if _extra_raw:
    for _pat in _extra_raw.split("|"):
        _pat = _pat.strip()
        if _pat:
            try:
                _MENTION_PATTERNS.append(re.compile(_pat, re.IGNORECASE))
            except re.error:
                pass


def is_krab_mentioned(text: str) -> bool:
    """
    Вернуть True если текст содержит обращение к Крабу.
    Используется в group message filter (mention-only mode).
    """
    if not text:
        return False
    return any(p.search(text) for p in _MENTION_PATTERNS)
