# -*- coding: utf-8 -*-
"""Тесты DailyBriefBuilder (Idea 18 — утренний брифинг)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from src.core.daily_brief import DAILY_BRIEF_MAX_CHARS, DailyBriefBuilder


class _FakeInbox:
    """Минимальный stub inbox_service с нужными методами."""

    def __init__(
        self,
        *,
        items: list[dict[str, Any]] | None = None,
        stale: list[dict[str, Any]] | None = None,
    ) -> None:
        self._items = list(items or [])
        self._stale = list(stale or [])

    def list_items(
        self, *, status: str = "", kind: str = "", limit: int = 20
    ) -> list[dict[str, Any]]:
        rows = self._items
        if kind:
            rows = [r for r in rows if r.get("kind") == kind]
        return rows[:limit]

    def filter_by_age(
        self,
        *,
        older_than_date: str,
        kind: str = "",
        status: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return self._stale[:limit]


def _fixed_now() -> datetime:
    """Среда 09.04.2026 08:00 UTC — гарантированно НЕ воскресенье."""
    return datetime(2026, 4, 8, 8, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_empty_brief_returns_empty_string() -> None:
    """Если ни один источник не дал данных — brief пустой и не отправляется."""
    builder = DailyBriefBuilder(
        inbox=_FakeInbox(),
        cron_lister=lambda: [],
        now_fn=_fixed_now,
    )
    text = await builder.build_brief()
    assert text == ""


@pytest.mark.asyncio
async def test_all_sections_present_when_data_available() -> None:
    """При наличии данных секции рендерятся: calendar, inbox stale, cron, sentry."""
    now = _fixed_now()
    cron_jobs = [
        {
            "id": "abc12345",
            "cron_spec": "0 7 * * *",
            "prompt": "morning ping",
            "last_run_at": (now - timedelta(hours=2)).isoformat(),
        }
    ]
    stale_inbox = [
        {
            "severity": "warning",
            "title": "stale task pending review",
            "created_at_utc": "2026-04-01T10:00:00+00:00",
        }
    ]
    sentry_items = [
        {
            "kind": "sentry_alert",
            "source": "sentry",
            "severity": "error",
            "title": "AssertionError in handler X",
            "created_at_utc": (now - timedelta(hours=3)).isoformat(),
        }
    ]
    inbox = _FakeInbox(items=sentry_items, stale=stale_inbox)

    async def fake_calendar() -> list[dict[str, str]]:
        return [
            {"calendar_name": "Work", "title": "Standup", "start_label": "Mon 09:00:00"},
            {"calendar_name": "Личное", "title": "Стоматолог", "start_label": "Mon 17:30:00"},
        ]

    builder = DailyBriefBuilder(
        calendar_fetcher=fake_calendar,
        cron_lister=lambda: cron_jobs,
        inbox=inbox,
        now_fn=lambda: now,
    )
    text = await builder.build_brief()

    assert "Daily Brief" in text
    assert "Сегодня в календаре" in text
    assert "Standup" in text
    assert "Стоматолог" in text
    assert "Inbox stale" in text
    assert "stale task pending review" in text
    assert "Cron overnight" in text
    assert "abc12345" in text
    assert "Sentry critical" in text
    assert "AssertionError" in text
    # Не воскресенье — weekly digest секции нет
    assert "Weekly digest" not in text


@pytest.mark.asyncio
async def test_cap_enforced_when_payload_huge(monkeypatch: pytest.MonkeyPatch) -> None:
    """При огромном объёме данных brief обрезается по DAILY_BRIEF_MAX_CHARS."""
    # Опускаем cap до 1500 чтобы детерминированно проверить срез без зависимости
    # от лимитов отдельных секций.
    monkeypatch.setattr("src.core.daily_brief.DAILY_BRIEF_MAX_CHARS", 1500)
    now = _fixed_now()
    # Calendar fetcher даёт большой payload — он не имеет hard-лимита кроме MAX_CALENDAR=8,
    # но title клиппится до 80 символов. Чтобы упереться в 3000 — нагнетаем
    # через множество секций сразу.
    huge_stale = [
        {
            "severity": "warning",
            "title": "X" * 200,
            "created_at_utc": "2026-04-01T10:00:00+00:00",
        }
        for _ in range(40)
    ]
    huge_sentry = [
        {
            "kind": "sentry_alert",
            "source": "sentry",
            "severity": "error",
            "title": "Y" * 200,
            "created_at_utc": (now - timedelta(hours=1)).isoformat(),
        }
        for _ in range(20)
    ]
    inbox = _FakeInbox(items=huge_sentry, stale=huge_stale)

    async def big_calendar() -> list[dict[str, str]]:
        return [
            {"calendar_name": "Cal" + str(i), "title": "Z" * 200, "start_label": "Mon 09:00:00"}
            for i in range(20)
        ]

    cron_jobs = [
        {
            "id": f"job{i:02d}",
            "cron_spec": "*/5 * * * *",
            "prompt": "P" * 200,
            "last_run_at": (now - timedelta(hours=1)).isoformat(),
        }
        for i in range(20)
    ]
    builder = DailyBriefBuilder(
        calendar_fetcher=big_calendar,
        inbox=inbox,
        cron_lister=lambda: cron_jobs,
        now_fn=lambda: now,
    )
    text = await builder.build_brief()

    assert len(text) <= DAILY_BRIEF_MAX_CHARS
    assert "обрезан по лимиту" in text


@pytest.mark.asyncio
async def test_formatting_clean_no_double_blank_no_trailing_whitespace() -> None:
    """Markdown аккуратный: нет тройных пустых строк и trailing spaces в строках."""
    now = _fixed_now()
    inbox = _FakeInbox(
        stale=[
            {
                "severity": "warning",
                "title": "alpha",
                "created_at_utc": "2026-04-01T10:00:00+00:00",
            }
        ]
    )
    cron_jobs = [
        {
            "id": "j1",
            "cron_spec": "*/30 * * * *",
            "prompt": "ping",
            "last_run_at": (now - timedelta(hours=1)).isoformat(),
        }
    ]
    builder = DailyBriefBuilder(
        inbox=inbox,
        cron_lister=lambda: cron_jobs,
        now_fn=lambda: now,
    )
    text = await builder.build_brief()

    assert "\n\n\n" not in text
    for line in text.splitlines():
        assert line == line.rstrip(), f"trailing whitespace в строке: {line!r}"
    # Должен оканчиваться ровно одним \n
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


@pytest.mark.asyncio
async def test_dates_correct_sunday_includes_weekly() -> None:
    """В воскресенье добавляется Weekly digest секция, в будни — нет."""
    # Воскресенье 12.04.2026
    sunday = datetime(2026, 4, 12, 9, 0, 0, tzinfo=timezone.utc)
    weekly_item = {
        "kind": "proactive_action",
        "source": "krab-internal",
        "severity": "info",
        "title": "Weekly Digest",
        "created_at_utc": "2026-04-12T09:00:00+00:00",
        "metadata": {
            "action_type": "weekly_digest",
            "total_rounds": 7,
            "cost_week_usd": 1.234,
            "attention_count": 2,
            "digest_ts": "2026-04-12T09:00:00+00:00",
        },
    }
    inbox = _FakeInbox(items=[weekly_item])

    builder_sunday = DailyBriefBuilder(
        inbox=inbox,
        cron_lister=lambda: [],
        now_fn=lambda: sunday,
    )
    text_sun = await builder_sunday.build_brief()
    assert "Weekly digest" in text_sun
    assert "rounds=7" in text_sun
    assert "cost=$1.234" in text_sun
    assert "2026-04-12" in text_sun  # дата в локали корректно отрендерена в заголовке

    # Понедельник — weekly не добавляется (даже если в inbox есть)
    monday = datetime(2026, 4, 13, 9, 0, 0, tzinfo=timezone.utc)
    builder_monday = DailyBriefBuilder(
        inbox=inbox,
        cron_lister=lambda: [],
        now_fn=lambda: monday,
    )
    text_mon = await builder_monday.build_brief()
    assert "Weekly digest" not in text_mon


@pytest.mark.asyncio
async def test_sentry_section_filters_by_severity_and_age() -> None:
    """Sentry секция игнорирует non-error и старые items."""
    now = _fixed_now()
    items = [
        # Свежий, error, sentry — войдёт
        {
            "kind": "sentry_alert",
            "source": "sentry",
            "severity": "error",
            "title": "fresh critical",
            "created_at_utc": (now - timedelta(hours=2)).isoformat(),
        },
        # Старше 12 часов — не войдёт
        {
            "kind": "sentry_alert",
            "source": "sentry",
            "severity": "error",
            "title": "stale critical",
            "created_at_utc": (now - timedelta(days=2)).isoformat(),
        },
        # Не error — не войдёт
        {
            "kind": "sentry_alert",
            "source": "sentry",
            "severity": "warning",
            "title": "warning event",
            "created_at_utc": now.isoformat(),
        },
        # Не sentry — не войдёт
        {
            "kind": "other",
            "source": "krab",
            "severity": "error",
            "title": "non-sentry error",
            "created_at_utc": now.isoformat(),
        },
    ]
    builder = DailyBriefBuilder(
        inbox=_FakeInbox(items=items),
        cron_lister=lambda: [],
        now_fn=lambda: now,
    )
    text = await builder.build_brief()
    assert "fresh critical" in text
    assert "stale critical" not in text
    assert "warning event" not in text
    assert "non-sentry error" not in text
