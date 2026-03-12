# -*- coding: utf-8 -*-
"""
Тесты runtime scheduler.

Проверяем критичные свойства:
1) корректный парсинг времени в `!remind`;
2) фактическое выполнение one-shot задач;
3) доставку reminder через привязанный sender callback.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import src.core.scheduler as scheduler_module
from src.core.inbox_service import InboxService
from src.core.scheduler import KrabScheduler, parse_due_time, split_reminder_input


def test_split_reminder_input_pipe_and_phrase() -> None:
    """Парсер аргументов `!remind` должен поддерживать оба формата ввода."""
    assert split_reminder_input("10m | купить воду") == ("10m", "купить воду")
    assert split_reminder_input("через 20 минут проверить почту") == (
        "через 20 минут",
        "проверить почту",
    )


def test_parse_due_time_short_delay() -> None:
    """Короткий формат `10m` должен давать корректный offset."""
    now = datetime.now().astimezone().replace(microsecond=0)
    due = parse_due_time("10m", now=now)
    assert int((due - now).total_seconds()) == 600


def test_parse_due_time_at_hhmm() -> None:
    """Формат `в HH:MM` планируется на ближайший подходящий слот."""
    now = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    due = parse_due_time("в 11:30", now=now)
    assert due.hour == 11
    assert due.minute == 30
    assert due.date() == now.date()


@pytest.mark.asyncio
async def test_scheduler_once_task_executes(tmp_path: Path) -> None:
    """`add_once_task` должен реально выполнить callback."""
    scheduler = KrabScheduler(storage_path=tmp_path / "reminders.json")
    fired = asyncio.Event()
    scheduler.start()
    try:
        scheduler.add_once_task(lambda: fired.set(), delay_seconds=0.05)
        await asyncio.wait_for(fired.wait(), timeout=1.0)
    finally:
        scheduler.stop()


@pytest.mark.asyncio
async def test_scheduler_reminder_delivers_via_bound_sender(tmp_path: Path) -> None:
    """Reminder должен доходить до sender и удаляться из pending."""
    scheduler = KrabScheduler(storage_path=tmp_path / "reminders.json")
    sent: list[tuple[str, str]] = []
    delivered = asyncio.Event()
    inbox = InboxService(state_path=tmp_path / "inbox.json")

    async def _sender(chat_id: str, text: str) -> None:
        sent.append((chat_id, text))
        delivered.set()

    original_inbox = scheduler_module.inbox_service
    scheduler_module.inbox_service = inbox
    scheduler.start()
    scheduler.bind_sender(_sender)
    try:
        scheduler.add_reminder(
            chat_id="-10012345",
            text="проверить cron",
            due_at=datetime.now().astimezone() + timedelta(seconds=0.05),
        )
        await asyncio.wait_for(delivered.wait(), timeout=2.0)
        assert sent
        assert sent[0][0] == "-10012345"
        assert "⏰ Напоминание" in sent[0][1]
        assert scheduler.list_reminders() == []
        assert inbox.get_summary()["open_items"] == 0
        done_items = inbox.list_items(status="done", kind="reminder", limit=5)
        assert done_items
        assert done_items[0]["metadata"]["chat_id"] == "-10012345"
    finally:
        scheduler.stop()
        scheduler_module.inbox_service = original_inbox
