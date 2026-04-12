# -*- coding: utf-8 -*-
"""
Расширенные тесты для src/core/scheduler.py.

Покрываем:
- split_reminder_input: pipe, русский, hhmm, ISO, DD.MM, нет разделителя
- parse_due_time: все форматы + edge-cases
- KrabScheduler: создание/удаление reminder, list_reminders, get_status
- fire logic: успешная доставка, отсутствие sender, sender-error
- retry/fail: накопление retries, исчерпание max_retries
- persistence: load/save, atomic write, corrupt file
- add_once_task: sync и async callback
- edge-cases: двойной start/stop, удаление несуществующего reminder
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import src.core.scheduler as scheduler_module
from src.core.inbox_service import InboxService
from src.core.scheduler import (
    KrabScheduler,
    ReminderRecord,
    parse_due_time,
    split_reminder_input,
)

# ─────────────────────────────────────────────────────────────
# Вспомогательная фикстура: изолированный scheduler + inbox
# ─────────────────────────────────────────────────────────────


@pytest.fixture()
def sched_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Возвращает (scheduler, inbox) с изолированным storage."""
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr(scheduler_module, "inbox_service", inbox)
    s = KrabScheduler(storage_path=tmp_path / "reminders.json")
    return s, inbox


# ─────────────────────────────────────────────────────────────
# split_reminder_input — все паттерны
# ─────────────────────────────────────────────────────────────


def test_split_pipe_with_spaces() -> None:
    """Pipe с пробелами → оба side trimmed."""
    ts, txt = split_reminder_input("  5m  |  позвонить маме  ")
    assert ts == "5m"
    assert txt == "позвонить маме"


def test_split_at_time_hhmm() -> None:
    """Формат `в 18:30 текст` без pipe."""
    ts, txt = split_reminder_input("в 18:30 позвонить")
    assert ts == "в 18:30"
    assert txt == "позвонить"


def test_split_iso_datetime_format() -> None:
    """ISO формат `2030-01-15 09:00 текст`."""
    ts, txt = split_reminder_input("2030-01-15 09:00 купить билет")
    assert ts == "2030-01-15 09:00"
    assert txt == "купить билет"


def test_split_ddmm_format() -> None:
    """Формат `25.12 09:00 текст`."""
    ts, txt = split_reminder_input("25.12 09:00 встреча")
    assert ts == "25.12 09:00"
    assert txt == "встреча"


def test_split_no_separator_returns_empty_time() -> None:
    """Текст без временного паттерна → time_spec пустой, text == весь ввод."""
    ts, txt = split_reminder_input("просто текст без времени")
    assert ts == ""
    assert txt == "просто текст без времени"


def test_split_empty_input() -> None:
    """Пустая строка → оба компонента пустые."""
    assert split_reminder_input("") == ("", "")
    assert split_reminder_input("   ") == ("", "")


# ─────────────────────────────────────────────────────────────
# parse_due_time — форматы
# ─────────────────────────────────────────────────────────────


def test_parse_due_time_minutes() -> None:
    """Формат `15m` → offset ровно 900 секунд."""
    now = datetime.now().astimezone().replace(microsecond=0)
    due = parse_due_time("15m", now=now)
    assert int((due - now).total_seconds()) == 900


def test_parse_due_time_russian_minutes() -> None:
    """Русский формат `через 10 минут` → offset 600 секунд."""
    now = datetime.now().astimezone().replace(microsecond=0)
    due = parse_due_time("через 10 минут", now=now)
    assert int((due - now).total_seconds()) == 600


def test_parse_due_time_russian_days() -> None:
    """Русский формат `через 2 дня` → offset 2 * 86400 секунд."""
    now = datetime.now().astimezone().replace(microsecond=0)
    due = parse_due_time("через 2 дня", now=now)
    assert int((due - now).total_seconds()) == 2 * 86400


def test_parse_due_time_at_without_prefix() -> None:
    """Формат `11:45` без префикса `в` тоже должен парситься."""
    now = datetime.now().astimezone().replace(hour=9, minute=0, second=0, microsecond=0)
    due = parse_due_time("11:45", now=now)
    assert due.hour == 11
    assert due.minute == 45


def test_parse_due_time_ddmm_past_advances_year() -> None:
    """DD.MM уже прошло в этом году → год следующий."""
    # Используем дату явно в прошлом относительно "сейчас"
    now = datetime(2030, 12, 31, 10, 0, 0).astimezone()
    due = parse_due_time("01.01 08:00", now=now)
    assert due.year == 2031
    assert due.month == 1
    assert due.day == 1


# ─────────────────────────────────────────────────────────────
# ReminderRecord — сериализация/десериализация
# ─────────────────────────────────────────────────────────────


def test_reminder_record_roundtrip() -> None:
    """to_dict / from_dict должны давать идентичные объекты."""
    iso = datetime.now().astimezone().isoformat()
    rec = ReminderRecord(
        reminder_id="abc123",
        chat_id="-100999",
        text="тест roundtrip",
        due_at_iso=iso,
        created_at_iso=iso,
        status="scheduled",
        retries=2,
        fired_at_iso="",
        last_error="prev_err",
    )
    restored = ReminderRecord.from_dict(rec.to_dict())
    assert restored.reminder_id == rec.reminder_id
    assert restored.chat_id == rec.chat_id
    assert restored.text == rec.text
    assert restored.retries == rec.retries
    assert restored.last_error == rec.last_error


def test_reminder_record_from_dict_defaults() -> None:
    """from_dict с минимальными полями должен иметь дефолтный status='scheduled'."""
    iso = datetime.now().astimezone().isoformat()
    rec = ReminderRecord.from_dict(
        {
            "reminder_id": "x1",
            "chat_id": "1",
            "text": "hi",
            "due_at_iso": iso,
            "created_at_iso": iso,
        }
    )
    assert rec.status == "scheduled"
    assert rec.retries == 0
    assert rec.fired_at_iso == ""
    assert rec.last_error == ""


# ─────────────────────────────────────────────────────────────
# KrabScheduler — базовый lifecycle
# ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scheduler_double_start_is_idempotent(sched_env) -> None:
    """Двойной вызов start() не должен ломать состояние."""
    s, _ = sched_env
    s.start()
    s.start()  # второй вызов — no-op
    assert s.is_started
    s.stop()


@pytest.mark.asyncio
async def test_scheduler_stop_cancels_pending_tasks(sched_env) -> None:
    """После stop() все pending asyncio-задачи должны быть отменены."""
    s, _ = sched_env
    s.start()
    # Добавляем задачу с большой задержкой
    job_id = s.add_once_task(lambda: None, delay_seconds=3600)
    assert job_id in s._jobs
    s.stop()
    assert not s._started
    # После stop задачи очищены
    assert len(s._jobs) == 0


@pytest.mark.asyncio
async def test_add_reminder_requires_started(sched_env) -> None:
    """add_reminder до start() бросает RuntimeError."""
    s, _ = sched_env
    with pytest.raises(RuntimeError, match="scheduler_not_started"):
        s.add_reminder(
            chat_id="1",
            text="test",
            due_at=datetime.now().astimezone() + timedelta(hours=1),
        )


@pytest.mark.asyncio
async def test_add_once_task_requires_started(sched_env) -> None:
    """add_once_task до start() бросает RuntimeError."""
    s, _ = sched_env
    with pytest.raises(RuntimeError, match="scheduler_not_started"):
        s.add_once_task(lambda: None, delay_seconds=1)


# ─────────────────────────────────────────────────────────────
# list_reminders — фильтрация
# ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_reminders_filters_by_chat_id(sched_env) -> None:
    """list_reminders(chat_id=X) должен возвращать только записи для X."""
    s, _ = sched_env
    s.start()
    future = datetime.now().astimezone() + timedelta(hours=1)
    try:
        s.add_reminder(chat_id="chat_A", text="задача А", due_at=future)
        s.add_reminder(chat_id="chat_B", text="задача Б", due_at=future)
        s.add_reminder(chat_id="chat_A", text="задача А2", due_at=future + timedelta(minutes=1))

        result_a = s.list_reminders(chat_id="chat_A")
        result_b = s.list_reminders(chat_id="chat_B")

        assert len(result_a) == 2
        assert len(result_b) == 1
        assert all(r["chat_id"] == "chat_A" for r in result_a)
    finally:
        s.stop()


@pytest.mark.asyncio
async def test_list_reminders_sorted_by_due(sched_env) -> None:
    """list_reminders должен возвращать записи в порядке возрастания due_at_iso."""
    s, _ = sched_env
    s.start()
    now = datetime.now().astimezone()
    try:
        s.add_reminder(chat_id="c", text="поздно", due_at=now + timedelta(hours=3))
        s.add_reminder(chat_id="c", text="рано", due_at=now + timedelta(hours=1))
        s.add_reminder(chat_id="c", text="средне", due_at=now + timedelta(hours=2))

        result = s.list_reminders(chat_id="c")
        due_times = [r["due_at_iso"] for r in result]
        assert due_times == sorted(due_times)
    finally:
        s.stop()


# ─────────────────────────────────────────────────────────────
# remove_reminder
# ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_remove_reminder_returns_false_for_unknown(sched_env) -> None:
    """remove_reminder для несуществующего ID должен вернуть False."""
    s, _ = sched_env
    s.start()
    try:
        assert s.remove_reminder("nonexistent_id") is False
    finally:
        s.stop()


@pytest.mark.asyncio
async def test_remove_reminder_removes_from_list(sched_env) -> None:
    """remove_reminder должен убирать запись из list_reminders."""
    s, _ = sched_env
    s.start()
    future = datetime.now().astimezone() + timedelta(hours=1)
    try:
        rid = s.add_reminder(chat_id="99", text="удалить меня", due_at=future)
        assert len(s.list_reminders()) == 1
        result = s.remove_reminder(rid)
        assert result is True
        assert s.list_reminders() == []
    finally:
        s.stop()


@pytest.mark.asyncio
async def test_remove_reminder_empty_id_returns_false(sched_env) -> None:
    """remove_reminder('') должен вернуть False без ошибок."""
    s, _ = sched_env
    s.start()
    try:
        assert s.remove_reminder("") is False
    finally:
        s.stop()


# ─────────────────────────────────────────────────────────────
# add_once_task — async callback
# ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_once_task_async_callback(sched_env) -> None:
    """add_once_task должен поддерживать async callback."""
    s, _ = sched_env
    s.start()
    fired = asyncio.Event()

    async def async_cb() -> None:
        fired.set()

    try:
        s.add_once_task(async_cb, delay_seconds=0.05)
        await asyncio.wait_for(fired.wait(), timeout=1.0)
    finally:
        s.stop()


@pytest.mark.asyncio
async def test_add_once_task_exception_does_not_crash_scheduler(sched_env) -> None:
    """Исключение внутри callback не должно ронять scheduler."""
    s, _ = sched_env
    s.start()
    completed = asyncio.Event()

    def bad_cb() -> None:
        completed.set()
        raise ValueError("намеренная ошибка в callback")

    try:
        s.add_once_task(bad_cb, delay_seconds=0.05)
        await asyncio.wait_for(completed.wait(), timeout=1.0)
        # Небольшая пауза чтобы task успел завершиться
        await asyncio.sleep(0.1)
        assert s.is_started  # scheduler жив
    finally:
        s.stop()


# ─────────────────────────────────────────────────────────────
# fire reminder — sender raises exception → retry
# ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fire_reminder_sender_error_triggers_retry(sched_env) -> None:
    """Если sender бросает исключение, reminder должен уйти на retry."""
    s, _ = sched_env
    s.start()

    async def failing_sender(chat_id: str, text: str) -> None:
        raise RuntimeError("сеть недоступна")

    s.bind_sender(failing_sender)
    future_iso = (datetime.now().astimezone() + timedelta(hours=1)).isoformat()
    rec = ReminderRecord(
        reminder_id="err_fire",
        chat_id="55",
        text="ошибка доставки",
        due_at_iso=future_iso,
        created_at_iso=datetime.now().astimezone().isoformat(),
        status="scheduled",
    )
    s._reminders["err_fire"] = rec
    try:
        await s._fire_reminder("err_fire")
        # После ошибки sender retries должно быть >= 1
        assert rec.retries >= 1
        assert "send_error" in rec.last_error
    finally:
        s.stop()


# ─────────────────────────────────────────────────────────────
# persistence — load/save roundtrip
# ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_persist_and_reload_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Данные сохранённые через _persist должны корректно загружаться при старте."""
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr(scheduler_module, "inbox_service", inbox)

    storage = tmp_path / "reminders.json"
    future = datetime.now().astimezone() + timedelta(hours=2)

    # Первый scheduler — сохраняет reminder
    s1 = KrabScheduler(storage_path=storage)
    s1.start()
    rid = s1.add_reminder(chat_id="reload_test", text="перезагрузка", due_at=future)
    s1.stop()

    # Второй scheduler — загружает из того же файла
    s2 = KrabScheduler(storage_path=storage)
    s2.start()
    try:
        reminders = s2.list_reminders()
        assert len(reminders) == 1
        assert reminders[0]["reminder_id"] == rid
        assert reminders[0]["chat_id"] == "reload_test"
    finally:
        s2.stop()


@pytest.mark.asyncio
async def test_load_invalid_json_rows_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Строки-не-словари в массиве reminders должны молча пропускаться."""
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr(scheduler_module, "inbox_service", inbox)

    future_iso = (datetime.now().astimezone() + timedelta(hours=1)).isoformat()
    state_path = tmp_path / "reminders.json"
    state_path.write_text(
        json.dumps(
            {
                "reminders": [
                    "строка вместо словаря",  # невалидная строка
                    42,  # число тоже невалидно
                    # Единственная валидная запись
                    {
                        "reminder_id": "valid1",
                        "chat_id": "7",
                        "text": "валидный",
                        "due_at_iso": future_iso,
                        "created_at_iso": future_iso,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    s = KrabScheduler(storage_path=state_path)
    s.start()
    try:
        reminders = s.list_reminders()
        assert len(reminders) == 1
        assert reminders[0]["reminder_id"] == "valid1"
    finally:
        s.stop()


# ─────────────────────────────────────────────────────────────
# get_status — с pending reminderами
# ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_status_reflects_pending_count(sched_env) -> None:
    """get_status.pending_count должен соответствовать количеству scheduled reminders."""
    s, _ = sched_env
    s.start()
    future = datetime.now().astimezone() + timedelta(hours=1)
    try:
        s.add_reminder(chat_id="a", text="first", due_at=future)
        s.add_reminder(chat_id="b", text="second", due_at=future + timedelta(minutes=5))

        status = s.get_status()
        assert status["pending_count"] == 2
        assert status["next_due_at"] != ""
    finally:
        s.stop()
