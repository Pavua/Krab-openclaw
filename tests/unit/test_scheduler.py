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
            item
            for item in inbox.list_items(limit=20)
            if item["kind"] != "proactive_action" and item["status"] in {"open", "acked"}
        ]
        assert open_non_proactive == [], (
            f"Expected no open reminder items; found: {open_non_proactive}"
        )
        done_items = inbox.list_items(status="done", kind="reminder", limit=5)
        assert done_items
        assert done_items[0]["metadata"]["chat_id"] == "-10012345"
    finally:
        scheduler.stop()
        scheduler_module.inbox_service = original_inbox


# ──────────────────────────────────────────────────────────────────────────────
# parse_due_time — edge-cases
# ──────────────────────────────────────────────────────────────────────────────


def test_parse_due_time_empty_raises() -> None:
    """Пустая строка должна бросать ValueError."""
    with pytest.raises(ValueError, match="time_spec_empty"):
        parse_due_time("")


def test_parse_due_time_unknown_format_raises() -> None:
    """Неизвестный формат должен бросать ValueError."""
    with pytest.raises(ValueError, match="time_spec_parse_failed"):
        parse_due_time("завтра утром")


def test_parse_due_time_seconds() -> None:
    """Формат `30s` — offset 30 секунд."""
    now = datetime.now().astimezone().replace(microsecond=0)
    due = parse_due_time("30s", now=now)
    assert int((due - now).total_seconds()) == 30


def test_parse_due_time_hours() -> None:
    """Формат `2h` — offset 7200 секунд."""
    now = datetime.now().astimezone().replace(microsecond=0)
    due = parse_due_time("2h", now=now)
    assert int((due - now).total_seconds()) == 7200


def test_parse_due_time_days() -> None:
    """Формат `1d` — offset 86400 секунд."""
    now = datetime.now().astimezone().replace(microsecond=0)
    due = parse_due_time("1d", now=now)
    assert int((due - now).total_seconds()) == 86400


def test_parse_due_time_russian_secs() -> None:
    """Русский формат `через 5 секунд` — offset 5 секунд."""
    now = datetime.now().astimezone().replace(microsecond=0)
    due = parse_due_time("через 5 секунд", now=now)
    assert int((due - now).total_seconds()) == 5


def test_parse_due_time_russian_hours() -> None:
    """Русский формат `через 3 часа` — offset 10800 секунд."""
    now = datetime.now().astimezone().replace(microsecond=0)
    due = parse_due_time("через 3 часа", now=now)
    assert int((due - now).total_seconds()) == 10800


def test_parse_due_time_at_hhmm_past_advances_to_next_day() -> None:
    """Если HH:MM уже прошёл сегодня — планируется на следующий день."""
    now = datetime.now().astimezone().replace(hour=23, minute=0, second=0, microsecond=0)
    due = parse_due_time("в 10:00", now=now)
    assert due.hour == 10
    assert due.minute == 0
    assert due.date() > now.date()


def test_parse_due_time_iso_format() -> None:
    """Формат `YYYY-MM-DD HH:MM` должен парситься корректно."""
    now = datetime.now().astimezone().replace(microsecond=0)
    due = parse_due_time("2030-06-15 14:30", now=now)
    assert due.year == 2030
    assert due.month == 6
    assert due.day == 15
    assert due.hour == 14
    assert due.minute == 30


def test_parse_due_time_ddmm_format() -> None:
    """Формат `DD.MM HH:MM` должен парситься корректно."""
    now = datetime.now().astimezone().replace(microsecond=0)
    due = parse_due_time("25.12 09:00", now=now)
    assert due.month == 12
    assert due.day == 25
    assert due.hour == 9
    assert due.minute == 0


# ──────────────────────────────────────────────────────────────────────────────
# _retry_or_fail — логика retry/exhausted
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_or_fail_increments_retries_and_reschedules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """После неудачной попытки reminder должен получить incremented retries и reschedule."""
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr(scheduler_module, "inbox_service", inbox)

    scheduler = KrabScheduler(storage_path=tmp_path / "reminders.json")
    scheduler.start()
    scheduler.bind_sender(None)  # type: ignore[arg-type]
    try:
        rid = scheduler.add_reminder(
            chat_id="111",
            text="тест retry",
            due_at=datetime.now().astimezone() + timedelta(seconds=0.05),
        )
        # Дожидаемся первого срабатывания (sender=None → retry_or_fail)
        await asyncio.sleep(0.3)
        rec = scheduler._reminders.get(rid)
        if rec:
            assert rec.retries >= 1
            assert rec.last_error == "sender_not_bound"
    finally:
        scheduler.stop()


@pytest.mark.asyncio
async def test_retry_or_fail_marks_failed_after_max_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """После max_retries reminder должен получить статус 'failed'."""
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr(scheduler_module, "inbox_service", inbox)

    scheduler = KrabScheduler(storage_path=tmp_path / "reminders.json")
    scheduler._max_retries = 2  # искусственно снижаем порог
    scheduler.start()
    try:
        future_iso = (datetime.now().astimezone() + timedelta(hours=1)).isoformat()
        from src.core.scheduler import ReminderRecord

        rec = ReminderRecord(
            reminder_id="fail_test",
            chat_id="999",
            text="fail me",
            due_at_iso=future_iso,
            created_at_iso=datetime.now().astimezone().isoformat(),
            status="scheduled",
            retries=2,  # уже на пороге
        )
        scheduler._reminders["fail_test"] = rec
        # Вызываем _retry_or_fail напрямую
        await scheduler._retry_or_fail(rec, "test_error")
        assert rec.status == "failed"
        assert rec.retries == 3
    finally:
        scheduler.stop()


# ──────────────────────────────────────────────────────────────────────────────
# _fire_reminder — delivery и отсутствие sender
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fire_reminder_no_sender_triggers_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Если sender не привязан, _fire_reminder должен вызвать _retry_or_fail."""
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr(scheduler_module, "inbox_service", inbox)

    scheduler = KrabScheduler(storage_path=tmp_path / "reminders.json")
    scheduler.start()
    # Sender намеренно не привязан
    try:
        future_iso = (datetime.now().astimezone() + timedelta(hours=1)).isoformat()
        from src.core.scheduler import ReminderRecord

        rec = ReminderRecord(
            reminder_id="fire_no_sender",
            chat_id="222",
            text="нет сендера",
            due_at_iso=future_iso,
            created_at_iso=datetime.now().astimezone().isoformat(),
            status="scheduled",
        )
        scheduler._reminders["fire_no_sender"] = rec
        await scheduler._fire_reminder("fire_no_sender")
        # Ожидаем, что retries увеличен — _retry_or_fail был вызван
        assert rec.retries == 1
        assert "sender_not_bound" in rec.last_error
    finally:
        scheduler.stop()


@pytest.mark.asyncio
async def test_fire_reminder_missing_record_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_fire_reminder с несуществующим reminder_id не должен падать."""
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr(scheduler_module, "inbox_service", inbox)

    scheduler = KrabScheduler(storage_path=tmp_path / "reminders.json")
    scheduler.start()
    try:
        # Не должно бросить исключение
        await scheduler._fire_reminder("nonexistent_id")
    finally:
        scheduler.stop()


# ──────────────────────────────────────────────────────────────────────────────
# _persist — atomic write
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_persist_writes_only_scheduled_reminders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_persist должен сохранять только scheduled reminders, игнорировать failed/done."""
    import json as json_module

    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr(scheduler_module, "inbox_service", inbox)

    storage = tmp_path / "reminders.json"
    scheduler = KrabScheduler(storage_path=storage)
    scheduler.start()
    try:
        from src.core.scheduler import ReminderRecord

        future_iso = (datetime.now().astimezone() + timedelta(hours=1)).isoformat()
        scheduler._reminders["r_scheduled"] = ReminderRecord(
            reminder_id="r_scheduled",
            chat_id="1",
            text="active",
            due_at_iso=future_iso,
            created_at_iso=datetime.now().astimezone().isoformat(),
            status="scheduled",
        )
        scheduler._reminders["r_failed"] = ReminderRecord(
            reminder_id="r_failed",
            chat_id="2",
            text="failed one",
            due_at_iso=future_iso,
            created_at_iso=datetime.now().astimezone().isoformat(),
            status="failed",
        )
        scheduler._persist()
        data = json_module.loads(storage.read_text(encoding="utf-8"))
        ids = [r["reminder_id"] for r in data["reminders"]]
        assert "r_scheduled" in ids
        assert "r_failed" not in ids
    finally:
        scheduler.stop()


@pytest.mark.asyncio
async def test_persist_creates_parent_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_persist должен создавать отсутствующие родительские директории."""
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr(scheduler_module, "inbox_service", inbox)

    nested_path = tmp_path / "a" / "b" / "c" / "reminders.json"
    scheduler = KrabScheduler(storage_path=nested_path)
    scheduler.start()
    try:
        scheduler._persist()
        assert nested_path.exists()
    finally:
        scheduler.stop()


# ──────────────────────────────────────────────────────────────────────────────
# get_status — диагностический срез
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_status_returns_expected_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_status должен вернуть все ожидаемые ключи."""
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr(scheduler_module, "inbox_service", inbox)

    scheduler = KrabScheduler(storage_path=tmp_path / "reminders.json")
    scheduler.start()
    try:
        status = scheduler.get_status()
        assert "started" in status
        assert "pending_count" in status
        assert "next_due_at" in status
        assert "storage_path" in status
        assert status["started"] is True
        assert status["pending_count"] == 0
    finally:
        scheduler.stop()


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
        json_module.dumps(
            {
                "reminders": [
                    # Пропущен chat_id — невалидная запись
                    {
                        "reminder_id": "bad001",
                        "chat_id": "",
                        "text": "missing chat",
                        "due_at_iso": future_iso,
                    },
                    # Пропущен text — невалидная запись
                    {
                        "reminder_id": "bad002",
                        "chat_id": "123",
                        "text": "",
                        "due_at_iso": future_iso,
                    },
                    # Полностью валидная запись
                    {
                        "reminder_id": "good01",
                        "chat_id": "456",
                        "text": "напомнить",
                        "due_at_iso": future_iso,
                    },
                ]
            }
        ),
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
        json_module.dumps(
            {
                "reminders": [
                    {
                        "reminder_id": "past01",
                        "chat_id": "789",
                        "text": "просроченное",
                        "due_at_iso": "2024-01-01T00:00:00+00:00",  # давно в прошлом
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr(scheduler_module, "inbox_service", inbox)

    scheduler = KrabScheduler(storage_path=state_path)
    scheduler.start()
    try:
        # list_reminders возвращает только scheduled; failed record живёт в _reminders
        pending = scheduler.list_reminders()
        assert not any(r["reminder_id"] == "past01" for r in pending), (
            "Past-due item не должен быть в pending"
        )
        assert "past01" in scheduler._reminders
        assert scheduler._reminders["past01"].status == "failed"
    finally:
        scheduler.stop()


# ──────────────────────────────────────────────────────────────────────────────
# Тесты natural language парсинга (расширение session 7)
# ──────────────────────────────────────────────────────────────────────────────


class TestSplitReminderInputNaturalLanguage:
    """Тесты split_reminder_input для новых форматов."""

    def test_me_in_short_unit(self) -> None:
        """'me in 30m купить молоко' должен игнорировать 'me' и вернуть 'in 30m'."""
        time_spec, text = split_reminder_input("me in 30m купить молоко")
        assert time_spec == "in 30m"
        assert text == "купить молоко"

    def test_me_in_hours(self) -> None:
        """'me in 2h позвонить' — игнорирует 'me', парсит 'in 2h'."""
        time_spec, text = split_reminder_input("me in 2h позвонить")
        assert time_spec == "in 2h"
        assert text == "позвонить"

    def test_in_short_unit_without_me(self) -> None:
        """'in 45m выпить воды' без 'me'."""
        time_spec, text = split_reminder_input("in 45m выпить воды")
        assert time_spec == "in 45m"
        assert text == "выпить воды"

    def test_in_long_minutes(self) -> None:
        """'in 30 minutes купить хлеб' — английская полная форма."""
        time_spec, text = split_reminder_input("in 30 minutes купить хлеб")
        assert time_spec == "in 30 minutes"
        assert text == "купить хлеб"

    def test_in_long_hours(self) -> None:
        """'in 2 hours позвонить' — английская полная форма."""
        time_spec, text = split_reminder_input("in 2 hours позвонить")
        assert time_spec == "in 2 hours"
        assert text == "позвонить"

    def test_in_long_days(self) -> None:
        """'in 1 day встреча' — один день."""
        time_spec, text = split_reminder_input("in 1 day встреча")
        assert time_spec == "in 1 day"
        assert text == "встреча"

    def test_at_hhmm(self) -> None:
        """'at 15:00 позвонить' — английский формат at HH:MM."""
        time_spec, text = split_reminder_input("at 15:00 позвонить")
        assert time_spec == "at 15:00"
        assert text == "позвонить"

    def test_at_hhmm_leading_zero(self) -> None:
        """'at 09:30 встреча' — с ведущим нулём."""
        time_spec, text = split_reminder_input("at 09:30 встреча")
        assert time_spec == "at 09:30"
        assert text == "встреча"

    def test_tomorrow_hhmm(self) -> None:
        """'tomorrow 9:00 встреча' — завтра в 9:00."""
        time_spec, text = split_reminder_input("tomorrow 9:00 встреча")
        assert time_spec == "tomorrow 9:00"
        assert text == "встреча"

    def test_tomorrow_hhmm_with_leading_zero(self) -> None:
        """'tomorrow 09:00 зарядка'."""
        time_spec, text = split_reminder_input("tomorrow 09:00 зарядка")
        assert time_spec == "tomorrow 09:00"
        assert text == "зарядка"

    def test_zavtra_hhmm(self) -> None:
        """'завтра 9:00 встреча' — русский tomorrow."""
        time_spec, text = split_reminder_input("завтра 9:00 встреча")
        assert time_spec == "завтра 9:00"
        assert text == "встреча"

    def test_me_in_case_insensitive(self) -> None:
        """'ME in 30m текст' — игнор регистра для 'me'."""
        time_spec, text = split_reminder_input("ME in 30m текст")
        assert time_spec == "in 30m"
        assert text == "текст"

    def test_pipe_format_unchanged(self) -> None:
        """Pipe-формат по-прежнему работает."""
        time_spec, text = split_reminder_input("10m | купить воду")
        assert time_spec == "10m"
        assert text == "купить воду"

    def test_empty_returns_empty(self) -> None:
        """Пустая строка — ('', '')."""
        assert split_reminder_input("") == ("", "")

    def test_me_in_days(self) -> None:
        """'me in 1d напоминание' — дни."""
        time_spec, text = split_reminder_input("me in 1d напоминание")
        assert time_spec == "in 1d"
        assert text == "напоминание"


class TestParseDueTimeNaturalLanguage:
    """Тесты parse_due_time для новых форматов."""

    def test_in_short_minutes(self) -> None:
        """'in 30m' — offset 1800 секунд."""
        now = datetime.now().astimezone().replace(microsecond=0)
        due = parse_due_time("in 30m", now=now)
        assert int((due - now).total_seconds()) == 1800

    def test_in_short_hours(self) -> None:
        """'in 2h' — offset 7200 секунд."""
        now = datetime.now().astimezone().replace(microsecond=0)
        due = parse_due_time("in 2h", now=now)
        assert int((due - now).total_seconds()) == 7200

    def test_in_short_seconds(self) -> None:
        """'in 45s' — offset 45 секунд."""
        now = datetime.now().astimezone().replace(microsecond=0)
        due = parse_due_time("in 45s", now=now)
        assert int((due - now).total_seconds()) == 45

    def test_in_short_days(self) -> None:
        """'in 1d' — offset 86400 секунд."""
        now = datetime.now().astimezone().replace(microsecond=0)
        due = parse_due_time("in 1d", now=now)
        assert int((due - now).total_seconds()) == 86400

    def test_in_long_minutes(self) -> None:
        """'in 30 minutes' — offset 1800 секунд."""
        now = datetime.now().astimezone().replace(microsecond=0)
        due = parse_due_time("in 30 minutes", now=now)
        assert int((due - now).total_seconds()) == 1800

    def test_in_long_minute_singular(self) -> None:
        """'in 1 minute' — offset 60 секунд."""
        now = datetime.now().astimezone().replace(microsecond=0)
        due = parse_due_time("in 1 minute", now=now)
        assert int((due - now).total_seconds()) == 60

    def test_in_long_hours(self) -> None:
        """'in 2 hours' — offset 7200 секунд."""
        now = datetime.now().astimezone().replace(microsecond=0)
        due = parse_due_time("in 2 hours", now=now)
        assert int((due - now).total_seconds()) == 7200

    def test_in_long_hour_singular(self) -> None:
        """'in 1 hour' — offset 3600 секунд."""
        now = datetime.now().astimezone().replace(microsecond=0)
        due = parse_due_time("in 1 hour", now=now)
        assert int((due - now).total_seconds()) == 3600

    def test_in_long_days(self) -> None:
        """'in 3 days' — offset 259200 секунд."""
        now = datetime.now().astimezone().replace(microsecond=0)
        due = parse_due_time("in 3 days", now=now)
        assert int((due - now).total_seconds()) == 259200

    def test_in_long_day_singular(self) -> None:
        """'in 1 day' — offset 86400 секунд."""
        now = datetime.now().astimezone().replace(microsecond=0)
        due = parse_due_time("in 1 day", now=now)
        assert int((due - now).total_seconds()) == 86400

    def test_in_long_seconds(self) -> None:
        """'in 10 seconds' — offset 10 секунд."""
        now = datetime.now().astimezone().replace(microsecond=0)
        due = parse_due_time("in 10 seconds", now=now)
        assert int((due - now).total_seconds()) == 10

    def test_at_hhmm(self) -> None:
        """'at 15:00' — планируется на 15:00 сегодня или завтра."""
        now = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
        due = parse_due_time("at 15:00", now=now)
        assert due.hour == 15
        assert due.minute == 0

    def test_at_hhmm_past_advances_next_day(self) -> None:
        """'at 10:00' когда сейчас 23:00 — планируется на завтра."""
        now = datetime.now().astimezone().replace(hour=23, minute=0, second=0, microsecond=0)
        due = parse_due_time("at 10:00", now=now)
        assert due.hour == 10
        assert due.date() > now.date()

    def test_tomorrow_hhmm(self) -> None:
        """'tomorrow 9:00' — ровно завтра в 9:00."""
        now = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
        due = parse_due_time("tomorrow 9:00", now=now)
        assert due.hour == 9
        assert due.minute == 0
        assert due.date() == (now + timedelta(days=1)).date()

    def test_tomorrow_hhmm_at_midnight(self) -> None:
        """'tomorrow 0:00' — завтра в полночь."""
        now = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
        due = parse_due_time("tomorrow 0:00", now=now)
        assert due.hour == 0
        assert due.date() == (now + timedelta(days=1)).date()

    def test_zavtra_hhmm(self) -> None:
        """'завтра 9:00' — русский вариант tomorrow 9:00."""
        now = datetime.now().astimezone().replace(hour=8, minute=0, second=0, microsecond=0)
        due = parse_due_time("завтра 9:00", now=now)
        assert due.hour == 9
        assert due.date() == (now + timedelta(days=1)).date()

    def test_in_case_insensitive(self) -> None:
        """'IN 30M' — регистронезависимость."""
        now = datetime.now().astimezone().replace(microsecond=0)
        due = parse_due_time("IN 30M", now=now)
        assert int((due - now).total_seconds()) == 1800

    def test_rus_short_minutes(self) -> None:
        """'5 минут' (без 'через') — краткая рус. форма."""
        now = datetime.now().astimezone().replace(microsecond=0)
        due = parse_due_time("5 минут", now=now)
        assert int((due - now).total_seconds()) == 300

    def test_rus_short_hours(self) -> None:
        """'2 часа' (без 'через') — краткая рус. форма."""
        now = datetime.now().astimezone().replace(microsecond=0)
        due = parse_due_time("2 часа", now=now)
        assert int((due - now).total_seconds()) == 7200

    def test_rus_short_seconds(self) -> None:
        """'30 секунд' (без 'через') — краткая рус. форма."""
        now = datetime.now().astimezone().replace(microsecond=0)
        due = parse_due_time("30 секунд", now=now)
        assert int((due - now).total_seconds()) == 30
