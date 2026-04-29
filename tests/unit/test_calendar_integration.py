# -*- coding: utf-8 -*-
"""Tests for src/core/calendar_integration.py (Idea 19)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from src.core.calendar_integration import (
    CalendarEvent,
    CalendarIntegration,
)

# Фиксированный «сейчас» для детерминизма тестов
_NOW = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)


def _now_fn():
    return _NOW


def _make_raw(
    title: str,
    start_offset_min: int,
    duration_min: int,
    *,
    location: str = "",
    calendar: str = "Personal",
) -> dict[str, Any]:
    """Создать «сырое» событие со сдвигом от _NOW."""
    start = _NOW + timedelta(minutes=start_offset_min)
    end = start + timedelta(minutes=duration_min)
    return {
        "title": title,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "location": location,
        "calendar": calendar,
    }


def _make_fetcher(events: list[dict[str, Any]]):
    async def fetcher(params: dict[str, Any]) -> list[dict[str, Any]]:
        return events

    return fetcher


@pytest.mark.asyncio
async def test_get_upcoming_filters_and_sorts() -> None:
    """get_upcoming возвращает события в окне, отсортированные по start."""
    raw = [
        _make_raw("late", start_offset_min=300, duration_min=30),
        _make_raw("soon", start_offset_min=60, duration_min=45),
        _make_raw("middle", start_offset_min=180, duration_min=15),
    ]
    integ = CalendarIntegration(_make_fetcher(raw), now_fn=_now_fn)
    events = await integ.get_upcoming(hours_ahead=24)

    assert [e.title for e in events] == ["soon", "middle", "late"]
    assert all(e.start >= _NOW for e in events)
    assert events[0].duration_minutes == 45


@pytest.mark.asyncio
async def test_find_conflicts_with_buffer() -> None:
    """Событие в 15:30–16:00 конфликтует с новым в 16:10 при buffer=15 мин."""
    # Существующее событие: 15:30–16:00 (т.е. +210..+240 от _NOW=12:00)
    existing = _make_raw("standup", start_offset_min=210, duration_min=30)
    integ = CalendarIntegration(_make_fetcher([existing]), now_fn=_now_fn)

    # Новое: 16:10–16:40 — голый overlap нет, но с buffer 15м конец standup
    # «уходит» до 16:15, что > 16:10 → конфликт
    new_start = _NOW + timedelta(hours=4, minutes=10)
    new_end = new_start + timedelta(minutes=30)

    conflicts = await integ.find_conflicts(new_start, new_end, buffer_min=15)
    assert len(conflicts) == 1
    assert conflicts[0].title == "standup"


@pytest.mark.asyncio
async def test_find_conflicts_no_overlap() -> None:
    """Далёкое событие — конфликта нет даже с буфером."""
    existing = _make_raw("morning_call", start_offset_min=60, duration_min=30)
    integ = CalendarIntegration(_make_fetcher([existing]), now_fn=_now_fn)

    # Новое сильно позже — через 10 часов
    new_start = _NOW + timedelta(hours=10)
    new_end = new_start + timedelta(minutes=30)

    conflicts = await integ.find_conflicts(new_start, new_end, buffer_min=15)
    assert conflicts == []


@pytest.mark.asyncio
async def test_format_for_brief_renders_markdown() -> None:
    """format_for_brief формирует ожидаемые строки."""
    raw = [
        _make_raw("Sync", start_offset_min=120, duration_min=30, location="Zoom"),
        _make_raw("Lunch", start_offset_min=240, duration_min=60),
    ]
    integ = CalendarIntegration(_make_fetcher(raw), now_fn=_now_fn)
    events = await integ.get_upcoming(hours_ahead=24)

    out = CalendarIntegration.format_for_brief(events)
    assert "📅" in out
    assert "Sync" in out
    assert "Zoom" in out
    assert "Lunch" in out
    # Sync длится 30м, Lunch 60м
    assert "(30м)" in out
    assert "(60м)" in out


@pytest.mark.asyncio
async def test_empty_and_failed_fetch_graceful() -> None:
    """Пустой список и упавший fetcher не ломают модуль."""
    # Пустой
    integ_empty = CalendarIntegration(_make_fetcher([]), now_fn=_now_fn)
    events = await integ_empty.get_upcoming(hours_ahead=12)
    assert events == []
    assert "событий нет" in CalendarIntegration.format_for_brief(events)

    # Сломанный fetcher
    async def broken(params: dict[str, Any]) -> list[dict[str, Any]]:
        raise RuntimeError("calendar_mcp_unavailable")

    integ_broken = CalendarIntegration(broken, now_fn=_now_fn)
    events_broken = await integ_broken.get_upcoming(hours_ahead=12)
    assert events_broken == []

    # Naive datetime в CalendarEvent → ValueError
    with pytest.raises(ValueError):
        CalendarEvent(
            title="bad",
            start=datetime(2026, 5, 1, 12, 0),  # naive
            end=datetime(2026, 5, 1, 13, 0, tzinfo=timezone.utc),
        )
