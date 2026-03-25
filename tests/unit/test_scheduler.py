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
        # The reminder item is resolved to "done"; new proactive_action trace stays open.
        open_non_proactive = [
            item for item in inbox.list_items(limit=20)
            if item["kind"] != "proactive_action" and item["status"] in {"open", "acked"}
        ]
        assert open_non_proactive == [], f"Expected no open reminder items; found: {open_non_proactive}"
        done_items = inbox.list_items(status="done", kind="reminder", limit=5)
        assert done_items
        assert done_items[0]["metadata"]["chat_id"] == "-10012345"
    finally:
        scheduler.stop()
        scheduler_module.inbox_service = original_inbox


# ──────────────────────────────────────────────────────────────────────────────
# Task 4.4 — _load graceful recovery
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_missing_file_initializes_empty_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Если файл state отсутствует, scheduler должен стартовать с пустым списком."""
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr(scheduler_module, "inbox_service", inbox)

    scheduler = KrabScheduler(storage_path=tmp_path / "nonexistent_reminders.json")
    scheduler.start()
    try:
        assert scheduler.list_reminders() == []
    finally:
        scheduler.stop()


@pytest.mark.asyncio
async def test_load_corrupted_json_initializes_empty_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Corrupted JSON файл не должен ронять scheduler — нужна graceful recovery."""
    import json as json_module

    state_path = tmp_path / "bad_reminders.json"
    state_path.write_text("{ это не json !!!", encoding="utf-8")
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr(scheduler_module, "inbox_service", inbox)

    scheduler = KrabScheduler(storage_path=state_path)
    scheduler.start()
    try:
        assert scheduler.list_reminders() == []
    finally:
        scheduler.stop()


@pytest.mark.asyncio
async def test_load_skips_records_with_missing_required_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Записи без обязательных полей должны пропускаться, валидные — загружаться."""
    import json as json_module

    future_iso = (datetime.now().astimezone() + timedelta(hours=2)).isoformat()
    state_path = tmp_path / "reminders.json"
    state_path.write_text(
        json_module.dumps({
            "reminders": [
                # Пропущен chat_id — невалидная запись
                {"reminder_id": "bad001", "chat_id": "", "text": "missing chat", "due_at_iso": future_iso},
                # Пропущен text — невалидная запись
                {"reminder_id": "bad002", "chat_id": "123", "text": "", "due_at_iso": future_iso},
                # Полностью валидная запись
                {"reminder_id": "good01", "chat_id": "456", "text": "напомнить", "due_at_iso": future_iso},
            ]
        }),
        encoding="utf-8",
    )
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr(scheduler_module, "inbox_service", inbox)

    scheduler = KrabScheduler(storage_path=state_path)
    scheduler.start()
    try:
        reminders = scheduler.list_reminders()
        assert len(reminders) == 1
        assert reminders[0]["reminder_id"] == "good01"
    finally:
        scheduler.stop()


@pytest.mark.asyncio
async def test_load_past_due_reminder_marked_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reminder с датой в прошлом должен получить статус 'failed' при загрузке."""
    import json as json_module

    state_path = tmp_path / "reminders.json"
    state_path.write_text(
        json_module.dumps({
            "reminders": [
                {
                    "reminder_id": "past01",
                    "chat_id": "789",
                    "text": "просроченное",
                    "due_at_iso": "2024-01-01T00:00:00+00:00",  # давно в прошлом
                }
            ]
        }),
        encoding="utf-8",
    )
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr(scheduler_module, "inbox_service", inbox)

    scheduler = KrabScheduler(storage_path=state_path)
    scheduler.start()
    try:
        # list_reminders возвращает только scheduled; failed record живёт в _reminders
        pending = scheduler.list_reminders()
        assert not any(r["reminder_id"] == "past01" for r in pending), "Past-due item не должен быть в pending"
        assert "past01" in scheduler._reminders
        assert scheduler._reminders["past01"].status == "failed"
    finally:
        scheduler.stop()
