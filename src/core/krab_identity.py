# -*- coding: utf-8 -*-
"""
krab_identity — определение упоминаний Краба в сообщениях (Chado Wave 16).

Поддерживает:
  - Русское имя: «Краб», «краб»
  - Английское: «Krab», «krab», «@Krab», «@krab»
  - Эмодзи-якорь: «🦀»
"""

from __future__ import annotations

import re

# Паттерны для обнаружения упоминания Краба
_KRAB_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bкраб\b", re.IGNORECASE),        # русское
    re.compile(r"\bkrab\b", re.IGNORECASE),         # английское
    re.compile(r"@[Kk]rab\b"),                      # @mention
    re.compile(r"🦀"),                              # эмодзи-якорь
]


def is_krab_mentioned(text: str) -> bool:
    """True если текст содержит упоминание Краба."""
    if not text:
        return False
    return any(p.search(text) for p in _KRAB_PATTERNS)


def extract_mentions(text: str) -> list[str]:
    """Вернуть список найденных паттернов-упоминаний (для отладки)."""
    found: list[str] = []
    for p in _KRAB_PATTERNS:
        m = p.search(text)
        if m:
            found.append(m.group(0))
    return found
