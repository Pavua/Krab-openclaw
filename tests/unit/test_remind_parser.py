# -*- coding: utf-8 -*-
"""
Tests for src.core.remind_parser.parse_remind_args.

Covers:
- relative time parsing (hours / minutes / days / seconds, EN + RU variants)
- absolute today HH:MM with rolling-to-tomorrow when past
- "tomorrow HH:MM" / "завтра HH:MM"
- event-based "when ... then/=> ..."
- event-based russian "когда ... сделай/пришли/напомни ..."
- invalid/empty input returns None
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from src.core.remind_parser import parse_remind_args

# ---------------------------------------------------------------------------
# Relative time
# ---------------------------------------------------------------------------


def test_relative_hours() -> None:
    """'2h do X' → fire_at ≈ now + 2h."""
    before = int(time.time())
    spec = parse_remind_args("2h проверь BTC")
    after = int(time.time())
    assert spec is not None
    assert spec["type"] == "time"
    assert spec["action"] == "проверь BTC"
    # fire_at within expected window
    assert before + 2 * 3600 <= spec["fire_at"] <= after + 2 * 3600 + 1


def test_relative_minutes() -> None:
    """'30m ...' → now + 30*60."""
    before = int(time.time())
    spec = parse_remind_args("30m сделай стендап")
    assert spec is not None
    assert spec["type"] == "time"
    assert spec["action"] == "сделай стендап"
    assert before + 30 * 60 <= spec["fire_at"] <= before + 30 * 60 + 5


def test_relative_days() -> None:
    """'1d daily summary' → now + 86400."""
    before = int(time.time())
    spec = parse_remind_args('1d "daily summary"')
    assert spec is not None
    assert spec["type"] == "time"
    # action stays verbatim (quotes included)
    assert "daily summary" in spec["action"]
    assert before + 86400 <= spec["fire_at"] <= before + 86400 + 5


def test_relative_russian_units() -> None:
    """'3дн X' и '15мин X' — русские короткие формы."""
    before = int(time.time())
    spec_days = parse_remind_args("3дн проверить бэкап")
    spec_min = parse_remind_args("15мин напомнить")
    assert spec_days is not None
    assert spec_days["type"] == "time"
    assert before + 3 * 86400 <= spec_days["fire_at"] <= before + 3 * 86400 + 5

    assert spec_min is not None
    assert spec_min["type"] == "time"
    assert before + 15 * 60 <= spec_min["fire_at"] <= before + 15 * 60 + 5


def test_relative_russian_days_keyword_variant() -> None:
    """'2час X' — русский форма 'час'."""
    before = int(time.time())
    spec = parse_remind_args("2час позвонить")
    assert spec is not None
    assert spec["type"] == "time"
    assert before + 2 * 3600 <= spec["fire_at"] <= before + 2 * 3600 + 5


# ---------------------------------------------------------------------------
# Absolute today
# ---------------------------------------------------------------------------


def test_absolute_today_future_time() -> None:
    """'23:59 X' — в будущем сегодня."""
    # Выбираем час, который всегда позже текущего (23:59 практически всегда в будущем).
    now = datetime.now(timezone.utc).astimezone()
    if now.hour == 23 and now.minute >= 58:
        # Тест нерепрезентативен в эти 2 минуты — пропускаем
        pytest.skip("Slot too close to midnight to assert 'today-future'.")
    spec = parse_remind_args("23:59 забрать посылку")
    assert spec is not None
    assert spec["type"] == "time"
    assert spec["action"] == "забрать посылку"
    # Должен быть в пределах сегодняшнего дня
    fire = datetime.fromtimestamp(spec["fire_at"]).astimezone()
    assert fire.date() == now.date()
    assert fire.hour == 23 and fire.minute == 59


def test_absolute_today_past_rolls_tomorrow() -> None:
    """Если HH:MM уже прошло → rolls to tomorrow."""
    now = datetime.now(timezone.utc).astimezone()
    # Время ровно сейчас минус 1 час (гарантированно в прошлом)
    past_time = now - timedelta(hours=1)
    spec = parse_remind_args(f"{past_time.hour:02d}:{past_time.minute:02d} test action")
    assert spec is not None
    assert spec["type"] == "time"
    fire = datetime.fromtimestamp(spec["fire_at"]).astimezone()
    # Fire должен быть позже текущего момента (через ~23 часа)
    assert fire > now


# ---------------------------------------------------------------------------
# Tomorrow
# ---------------------------------------------------------------------------


def test_tomorrow_english() -> None:
    """'tomorrow 9:00 X' — завтра в 09:00."""
    now = datetime.now(timezone.utc).astimezone()
    spec = parse_remind_args("tomorrow 9:00 встреча")
    assert spec is not None
    assert spec["type"] == "time"
    assert spec["action"] == "встреча"
    fire = datetime.fromtimestamp(spec["fire_at"]).astimezone()
    assert fire.date() == (now + timedelta(days=1)).date()
    assert fire.hour == 9 and fire.minute == 0


def test_russian_tomorrow() -> None:
    """'завтра 10:00 X'."""
    now = datetime.now(timezone.utc).astimezone()
    spec = parse_remind_args("завтра 10:00 встреча")
    assert spec is not None
    assert spec["type"] == "time"
    fire = datetime.fromtimestamp(spec["fire_at"]).astimezone()
    assert fire.date() == (now + timedelta(days=1)).date()
    assert fire.hour == 10 and fire.minute == 0


# ---------------------------------------------------------------------------
# Event-based
# ---------------------------------------------------------------------------


def test_event_when_then() -> None:
    """'when upload photos then notify me' → event."""
    spec = parse_remind_args("when upload photos then notify me")
    assert spec is not None
    assert spec["type"] == "event"
    assert spec["pattern"] == "upload photos"
    assert spec["action"] == "notify me"


def test_event_when_arrow() -> None:
    """'when <pattern> => <action>'."""
    spec = parse_remind_args("when BTC drops => sell")
    assert spec is not None
    assert spec["type"] == "event"
    assert spec["pattern"] == "BTC drops"
    assert spec["action"] == "sell"


def test_event_russian_сделай() -> None:
    """'когда upload сделай X' → event."""
    spec = parse_remind_args("когда upload сделай exports")
    assert spec is not None
    assert spec["type"] == "event"
    assert spec["pattern"] == "upload"
    assert spec["action"] == "exports"


def test_event_russian_пришли() -> None:
    """'когда X пришли Y' → event."""
    spec = parse_remind_args("когда отчёт готов пришли уведомление")
    assert spec is not None
    assert spec["type"] == "event"
    assert spec["pattern"] == "отчёт готов"
    assert spec["action"] == "уведомление"


def test_event_russian_напомни() -> None:
    """'когда X напомни Y' → event."""
    spec = parse_remind_args("когда коллега ответит напомни пингануть")
    assert spec is not None
    assert spec["type"] == "event"
    assert spec["pattern"] == "коллега ответит"
    assert spec["action"] == "пингануть"


# ---------------------------------------------------------------------------
# Invalid / empty
# ---------------------------------------------------------------------------


def test_invalid_returns_none() -> None:
    """Неизвестный формат → None."""
    assert parse_remind_args("абракадабра без времени") is None
    assert parse_remind_args("") is None
    assert parse_remind_args(None) is None  # type: ignore[arg-type]


def test_invalid_hour_range_returns_none() -> None:
    """Недопустимые часы/минуты → None."""
    assert parse_remind_args("25:00 do X") is None
    assert parse_remind_args("14:70 do X") is None


def test_event_requires_action() -> None:
    """'when X then' (без action) не должен парситься."""
    assert parse_remind_args("when upload photos then ") is None
