# -*- coding: utf-8 -*-
"""
krab_identity — определение упоминаний Краба в сообщениях (Chado Wave 16).

Поддерживает:
  - Русское имя: «Краб», «краб»
  - Английское: «Krab», «krab», «@Krab», «@krab»
  - Эмодзи-якорь: «🦀»
  - Актуальный username userbot session (устанавливается через
    `set_krab_username()` на старте). Например: `@yung_nagato`.
"""

from __future__ import annotations

import re

# Runtime user id userbot-аккаунта (устанавливается при старте)
_krab_user_id: int | None = None
# Runtime username userbot-аккаунта (устанавливается при старте)
_krab_username_pattern: re.Pattern[str] | None = None


def set_krab_user_id(uid: int) -> None:
    """Зафиксировать Telegram user id Краба (вызывается один раз при старте)."""
    global _krab_user_id
    _krab_user_id = uid


def get_krab_user_id() -> int | None:
    """Вернуть user id Краба, или None если ещё не установлен."""
    return _krab_user_id


def set_krab_username(username: str | None) -> None:
    """Зафиксировать username userbot-аккаунта (например `yung_nagato`).

    После вызова mention `@yung_nagato` будет распознаваться как обращение
    к Крабу. Без префикса '@' на входе — добавим автоматически. None / пустое
    → сбрасывает pattern.
    """
    global _krab_username_pattern
    if not username:
        _krab_username_pattern = None
        return
    clean = username.strip().lstrip("@")
    if not clean:
        _krab_username_pattern = None
        return
    # \b после @<name> чтобы '@yung_nagato_clone' не считалось упоминанием
    _krab_username_pattern = re.compile(rf"@{re.escape(clean)}\b", re.IGNORECASE)


# Паттерны для обнаружения упоминания Краба (статические)
_KRAB_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bкраб\b", re.IGNORECASE),  # русское
    re.compile(r"\bkrab\b", re.IGNORECASE),  # английское
    re.compile(r"@[Kk]rab\b"),  # @krab mention
    re.compile(r"🦀"),  # эмодзи-якорь
]


def is_krab_mentioned(text: str) -> bool:
    """True если текст содержит упоминание Краба.

    Учитывает статические паттерны + dynamic `@<username>` (если установлен
    через `set_krab_username()` на startup).
    """
    if not text:
        return False
    if any(p.search(text) for p in _KRAB_PATTERNS):
        return True
    if _krab_username_pattern is not None and _krab_username_pattern.search(text):
        return True
    return False


def extract_mentions(text: str) -> list[str]:
    """Вернуть список найденных паттернов-упоминаний (для отладки)."""
    found: list[str] = []
    for p in _KRAB_PATTERNS:
        m = p.search(text)
        if m:
            found.append(m.group(0))
    if _krab_username_pattern is not None:
        m = _krab_username_pattern.search(text)
        if m:
            found.append(m.group(0))
    return found
