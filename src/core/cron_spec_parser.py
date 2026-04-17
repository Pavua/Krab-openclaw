# -*- coding: utf-8 -*-
"""
Human-friendly cron spec parser.

Парсит русско/английские выражения времени в стандартный cron-формат
(5 полей: minute hour day month dow).

Примеры:
    "каждый день в 10:00"           → "0 10 * * *"
    "каждые 2 часа"                 → "0 */2 * * *"
    "каждый понедельник в 09:30"    → "30 9 * * 1"
    "every day at 14:30"            → "30 14 * * *"
    "every 4 hours"                 → "0 */4 * * *"
    "every monday at 09:00"         → "0 9 * * 1"
    "0 10 * * *"                    → "0 10 * * *"   (прямой cron)
"""

from __future__ import annotations

import re
from typing import Optional

# Русские дни недели (в разных падежах, т.к. "каждый понедельник" vs "каждую пятницу")
DAYS_RU = {
    "понедельник": 1,
    "вторник": 2,
    "среду": 3,
    "среда": 3,
    "четверг": 4,
    "пятницу": 5,
    "пятница": 5,
    "субботу": 6,
    "суббота": 6,
    "воскресенье": 0,
    "воскресение": 0,
}

DAYS_EN = {
    "monday": 1,
    "tuesday": 2,
    "wednesday": 3,
    "thursday": 4,
    "friday": 5,
    "saturday": 6,
    "sunday": 0,
}


def _looks_like_cron_field(part: str) -> bool:
    """Проверяет, похож ли токен на допустимое cron-поле."""
    if part == "*":
        return True
    if part.startswith("*/") and part[2:].isdigit():
        return True
    if part.isdigit():
        return True
    # Диапазоны и списки: 1-5, 1,3,5
    if re.fullmatch(r"\d+(-\d+)?(,\d+(-\d+)?)*", part):
        return True
    return False


def parse_cron_expression(text: str) -> Optional[str]:
    """
    Парсит human-friendly выражение времени.

    Возвращает 5-field cron spec (строку) или None, если не удалось распарсить.
    """
    if not text or not isinstance(text, str):
        return None
    t = text.lower().strip()
    if not t:
        return None

    # "каждые N часов" / "every N hours"
    m = re.match(r"(?:каждые|every)\s+(\d+)\s+(?:час(?:а|ов)?|hour(?:s)?)", t)
    if m:
        n = int(m.group(1))
        if n <= 0 or n > 23:
            return None
        return f"0 */{n} * * *"

    # "каждые N минут" / "every N minutes"
    m = re.match(r"(?:каждые|every)\s+(\d+)\s+(?:минут(?:ы|у)?|minute(?:s)?)", t)
    if m:
        n = int(m.group(1))
        if n <= 0 or n > 59:
            return None
        return f"*/{n} * * * *"

    # Weekly RU: "каждый понедельник в 10:00", "каждую пятницу в 09:30"
    for ru_day, num in DAYS_RU.items():
        if f"каждый {ru_day}" in t or f"каждую {ru_day}" in t:
            tm = re.search(r"(\d{1,2})[:.](\d{2})", t)
            if tm:
                h, mn = int(tm.group(1)), int(tm.group(2))
                if 0 <= h < 24 and 0 <= mn < 60:
                    return f"{mn} {h} * * {num}"
            return None

    # Weekly EN: "every monday at 10:00"
    for en_day, num in DAYS_EN.items():
        if f"every {en_day}" in t:
            tm = re.search(r"(\d{1,2})[:.](\d{2})", t)
            if tm:
                h, mn = int(tm.group(1)), int(tm.group(2))
                if 0 <= h < 24 and 0 <= mn < 60:
                    return f"{mn} {h} * * {num}"
            return None

    # Daily: "каждый день в HH:MM" / "every day at HH:MM"
    if re.search(r"(?:каждый день|each day|every day|ежедневно|daily)", t):
        tm = re.search(r"(\d{1,2})[:.](\d{2})", t)
        if tm:
            h, mn = int(tm.group(1)), int(tm.group(2))
            if 0 <= h < 24 and 0 <= mn < 60:
                return f"{mn} {h} * * *"
        return None

    # Прямой cron: 5 пробел-разделённых полей
    parts = t.split()
    if len(parts) == 5:
        if all(_looks_like_cron_field(p) for p in parts):
            return " ".join(parts)
        return None

    return None
