# -*- coding: utf-8 -*-
"""
Calendar Integration — чтение upcoming events и conflict detection (Idea 19).

Зачем это существует:

Краб уже умеет напоминать о делах через `!remind` и cron, но «свободен ли я в
этот слот» приходится проверять руками: открывать macOS Calendar, листать день.
Когда оператор пишет «забронируй встречу в 16:00», Краб должен сам понимать,
что в 15:45–16:30 уже стоит созвон.

Решение: тонкий wrapper над **внешним** async fetcher (в проде это будет
`mcp__krab-p0lrd__calendar_events`, в тестах — mock). Модуль не знает про MCP —
он принимает любую async-callable, возвращающую список «сырых» dict-событий,
и нормализует их в `CalendarEvent`. Это держит ядро тестируемым и развязывает
его от транспорта.

### Что даёт модуль
- `get_upcoming(hours_ahead)` — события в окне [now, now+hours_ahead].
- `find_conflicts(start, end, buffer_min)` — пересечения с учётом буфера до/после.
- `format_for_brief(events)` — markdown-блок для daily_brief.

### Инварианты
- Все datetime — timezone-aware UTC. Naive datetime отвергаем (raise).
- Сортировка по `start` ASC.
- Buffer применяется симметрично: событие конфликтует, если
  `event.end + buffer > new.start` AND `event.start - buffer < new.end`.
- Пустой список — нормальный случай, не ошибка.

### Не решает
- Не пишет в календарь (read-only).
- Не делает MCP-вызов сам — это backlog (wire-up в `userbot_bridge` или
  `daily_brief`).
- Не агрегирует несколько календарей — fetcher отвечает за то, что отдать.
- Не понимает recurring rules — берём то, что fetcher уже развернул.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Iterable

logger = logging.getLogger(__name__)

# Тип fetcher: async callable, принимает {"start": iso, "end": iso, "owner_id": ...}
# и возвращает список dict с полями title/start/end/location/attendees/calendar.
CalendarFetcher = Callable[[dict[str, Any]], Awaitable[list[dict[str, Any]]]]


@dataclass(frozen=True)
class CalendarEvent:
    """Нормализованное событие календаря."""

    title: str
    start: datetime
    end: datetime
    location: str = ""
    attendees: tuple[str, ...] = field(default_factory=tuple)
    calendar_name: str = ""

    def __post_init__(self) -> None:
        # Защита от naive datetime — это часто приводит к скрытым багам сравнений
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("calendar_event_naive_datetime_rejected")
        if self.end < self.start:
            raise ValueError("calendar_event_end_before_start")

    @property
    def duration_minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


def _parse_iso(value: str | datetime) -> datetime:
    """Принять либо datetime, либо ISO-строку. Naive → отвергнуть."""
    if isinstance(value, datetime):
        dt = value
    else:
        # fromisoformat в py3.11+ корректно понимает '...Z' через .replace('Z', '+00:00')
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("calendar_event_naive_datetime_rejected")
    return dt.astimezone(timezone.utc)


def _normalize(raw: dict[str, Any]) -> CalendarEvent:
    """Превратить «сырое» событие в CalendarEvent."""
    attendees_raw: Iterable[Any] = raw.get("attendees") or ()
    attendees = tuple(str(x) for x in attendees_raw)
    return CalendarEvent(
        title=str(raw.get("title") or raw.get("summary") or "(без названия)"),
        start=_parse_iso(raw["start"]),
        end=_parse_iso(raw["end"]),
        location=str(raw.get("location") or ""),
        attendees=attendees,
        calendar_name=str(raw.get("calendar_name") or raw.get("calendar") or ""),
    )


class CalendarIntegration:
    """Тонкий слой над async fetcher событий."""

    def __init__(
        self,
        fetcher: CalendarFetcher,
        *,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    async def get_upcoming(
        self,
        hours_ahead: int = 24,
        *,
        owner_id: str | None = None,
    ) -> list[CalendarEvent]:
        """Получить события в окне [now, now+hours_ahead], отсортированные по start."""
        if hours_ahead <= 0:
            raise ValueError("calendar_hours_ahead_must_be_positive")

        now = self._now_fn()
        window_end = now + timedelta(hours=hours_ahead)
        params: dict[str, Any] = {
            "start": now.isoformat(),
            "end": window_end.isoformat(),
        }
        if owner_id is not None:
            params["owner_id"] = owner_id

        try:
            raw_events = await self._fetcher(params)
        except Exception as exc:  # noqa: BLE001
            # Календарь не должен ронять daily_brief — отдаём пустой список и логируем
            logger.warning(
                "calendar_fetch_failed",
                extra={
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "hours_ahead": hours_ahead,
                },
            )
            return []

        events: list[CalendarEvent] = []
        for raw in raw_events or ():
            try:
                events.append(_normalize(raw))
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning(
                    "calendar_event_skipped",
                    extra={"error": str(exc), "error_type": type(exc).__name__},
                )

        events.sort(key=lambda e: e.start)
        logger.info(
            "calendar_upcoming_fetched",
            extra={"count": len(events), "hours_ahead": hours_ahead},
        )
        return events

    async def find_conflicts(
        self,
        new_event_start: datetime,
        new_event_end: datetime,
        *,
        buffer_min: int = 15,
        owner_id: str | None = None,
    ) -> list[CalendarEvent]:
        """Найти события, пересекающиеся с [new_start, new_end] ± buffer."""
        if new_event_start.tzinfo is None or new_event_end.tzinfo is None:
            raise ValueError("calendar_conflict_naive_datetime_rejected")
        if new_event_end < new_event_start:
            raise ValueError("calendar_conflict_end_before_start")
        if buffer_min < 0:
            raise ValueError("calendar_buffer_must_be_non_negative")

        now = self._now_fn()
        # Берём широкое окно: от now до конца нового события + сутки запаса
        window_hours = max(
            24,
            int((new_event_end - now).total_seconds() // 3600) + 24,
        )
        candidates = await self.get_upcoming(window_hours, owner_id=owner_id)

        buffer = timedelta(minutes=buffer_min)
        conflicts: list[CalendarEvent] = []
        for ev in candidates:
            # Стандартное «interval overlap» с симметричным буфером
            if ev.end + buffer > new_event_start and ev.start - buffer < new_event_end:
                conflicts.append(ev)

        logger.info(
            "calendar_conflicts_checked",
            extra={
                "conflict_count": len(conflicts),
                "buffer_min": buffer_min,
            },
        )
        return conflicts

    @staticmethod
    def format_for_brief(events: list[CalendarEvent]) -> str:
        """Markdown-блок для daily_brief. Пустой список → дружелюбная заглушка."""
        if not events:
            return "**📅 Календарь:** на ближайшее время событий нет."

        lines = ["**📅 Ближайшие события:**"]
        for ev in events:
            # Локальный TZ оператора неизвестен модулю — отдаём UTC, форматирование
            # под пользовательский TZ это забота daily_brief
            start_str = ev.start.strftime("%H:%M")
            duration = ev.duration_minutes
            line = f"- `{start_str}` ({duration}м) — {ev.title}"
            if ev.location:
                line += f" @ {ev.location}"
            lines.append(line)
        return "\n".join(lines)
