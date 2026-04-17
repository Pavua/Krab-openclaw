# -*- coding: utf-8 -*-
"""
Parse '!remind' arguments to trigger spec.

Поддерживаемые форматы:
- Relative time: "2h проверь BTC", "30m ...", "1d ...", "15с ...", "2час ...", "10мин ...", "3дн ..."
- Absolute today: "17:30 забрать посылку" (rolls to tomorrow if time in past)
- Tomorrow: "tomorrow 9:00 встреча" или "завтра 10:00 ..."
- Event-based: "when upload photos then notify", "when <pattern> => <action>"
- Russian event: "когда upload photos сделай ...", "когда X пришли/напомни Y"

Возвращает None если ни один паттерн не подошёл.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

# Множители для relative time unit → секунды
_UNIT_MULTIPLIERS: dict[str, int] = {
    "h": 3600,
    "час": 3600,
    "часа": 3600,
    "часов": 3600,
    "m": 60,
    "мин": 60,
    "минут": 60,
    "минуты": 60,
    "с": 1,
    "сек": 1,
    "s": 1,
    "d": 86400,
    "дн": 86400,
    "день": 86400,
    "дней": 86400,
}


def parse_remind_args(args: str) -> Optional[dict]:
    """
    Parse `!remind` arguments into a trigger spec.

    Returns:
        {"type": "time", "fire_at": unix_ts, "action": text}
        {"type": "event", "pattern": regex, "action": text}
        None — если не удалось распарсить

    Поддерживает:
        !remind 2h проверь BTC
        !remind 30m сделай стендап
        !remind 1d daily summary
        !remind 17:30 забрать посылку
        !remind tomorrow 9:00 встреча
        !remind завтра 10:00 ...
        !remind when upload photos then notify me
        !remind когда upload сделай что-то
    """
    args = (args or "").strip()
    if not args:
        return None

    # Relative time (english and russian short forms):
    # "2h action", "30m action", "1d action", "15с action", "2час action", ...
    m = re.match(
        r"^(\d+)(h|m|d|s|с|сек|мин|минут|минуты|час|часа|часов|дн|день|дней)\s+(.+)$",
        args,
        re.IGNORECASE,
    )
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        action = m.group(3).strip()
        multiplier = _UNIT_MULTIPLIERS.get(unit, 60)
        fire_at = int(time.time()) + n * multiplier
        return {"type": "time", "fire_at": fire_at, "action": action}

    # Tomorrow: "tomorrow HH:MM action" / "завтра HH:MM action"
    m = re.match(
        r"^(?:tomorrow|завтра)\s+(\d{1,2})[:.](\d{2})\s+(.+)$",
        args,
        re.IGNORECASE,
    )
    if m:
        h, mn, action = int(m.group(1)), int(m.group(2)), m.group(3).strip()
        if not (0 <= h <= 23 and 0 <= mn <= 59):
            return None
        now = datetime.now(timezone.utc).astimezone()
        target = (now + timedelta(days=1)).replace(hour=h, minute=mn, second=0, microsecond=0)
        return {"type": "time", "fire_at": int(target.timestamp()), "action": action}

    # Absolute today "17:30 action" (rolls to tomorrow if already past)
    m = re.match(r"^(\d{1,2})[:.](\d{2})\s+(.+)$", args)
    if m:
        h, mn, action = int(m.group(1)), int(m.group(2)), m.group(3).strip()
        if not (0 <= h <= 23 and 0 <= mn <= 59):
            return None
        now = datetime.now(timezone.utc).astimezone()
        target = now.replace(hour=h, minute=mn, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return {"type": "time", "fire_at": int(target.timestamp()), "action": action}

    # Event-based: "when <pattern> then <action>" or "when <pattern> => <action>"
    m = re.match(r"^when\s+(.+?)\s+(?:then|=>)\s+(.+)$", args, re.IGNORECASE)
    if m:
        pattern = m.group(1).strip()
        action = m.group(2).strip()
        if pattern and action:
            return {"type": "event", "pattern": pattern, "action": action}

    # Russian natural: "когда X сделай Y" | "когда X пришли Y" | "когда X напомни Y"
    m = re.match(
        r"^когда\s+(.+?)\s+(?:сделай|пришли|напомни)\s+(.+)$",
        args,
        re.IGNORECASE,
    )
    if m:
        pattern = m.group(1).strip()
        action = m.group(2).strip()
        if pattern and action:
            return {"type": "event", "pattern": pattern, "action": action}

    return None
