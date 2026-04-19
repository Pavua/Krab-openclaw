# -*- coding: utf-8 -*-
"""
Тесты RemindersQueue — очередь напоминаний (time + event).

Покрываем:
1) add_time_reminder
2) add_event_reminder
3) cancel
4) list_pending (фильтр по owner)
5) check_time_reminders — срабатывает при достижении deadline
6) check_event_match — regex-совпадения
7) persistence: load/save через tmp_path
8) fired reminders не срабатывают повторно
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from src.core.reminders_queue import (
    Reminder,
    RemindersQueue,
    ReminderStatus,
    ReminderTrigger,
)

# ─── Фикстуры ─────────────────────────────────────────────────────────────────


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    """Путь к временному файлу состояния."""
    return tmp_path / "reminders_queue.json"


@pytest.fixture
def queue(state_path: Path) -> RemindersQueue:
    """Свежая очередь с изолированным state-файлом."""
    return RemindersQueue(state_path=state_path)


# ─── add_time_reminder ────────────────────────────────────────────────────────


class TestAddTimeReminder:
    def test_add_time_reminder_returns_id(self, queue: RemindersQueue) -> None:
        rid = queue.add_time_reminder(
            owner_id="42", fire_at=int(time.time()) + 60, action="check X"
        )
        assert isinstance(rid, str)
        assert len(rid) == 12  # uuid hex[:12]

    def test_add_time_reminder_stored(self, queue: RemindersQueue) -> None:
        future = int(time.time()) + 3600
        rid = queue.add_time_reminder(owner_id="42", fire_at=future, action="check X")
        r = queue.get(rid)
        assert r is not None
        assert r.owner_user_id == "42"
        assert r.trigger_type == ReminderTrigger.TIME
        assert r.fire_at == future
        assert r.action_payload == "check X"
        assert r.action_type == "notify"
        assert r.status == ReminderStatus.PENDING

    def test_add_time_reminder_custom_action_type(self, queue: RemindersQueue) -> None:
        rid = queue.add_time_reminder(
            owner_id="42", fire_at=1, action="gen report", action_type="ai_query"
        )
        assert queue.get(rid).action_type == "ai_query"


# ─── add_event_reminder ───────────────────────────────────────────────────────


class TestAddEventReminder:
    def test_add_event_reminder(self, queue: RemindersQueue) -> None:
        rid = queue.add_event_reminder(
            owner_id="42", chat_id="-100123", pattern=r"bitcoin", action="notify me"
        )
        r = queue.get(rid)
        assert r is not None
        assert r.trigger_type == ReminderTrigger.EVENT
        assert r.watch_chat_id == "-100123"
        assert r.match_pattern == "bitcoin"
        assert r.status == ReminderStatus.PENDING


# ─── cancel ───────────────────────────────────────────────────────────────────


class TestCancel:
    def test_cancel_existing(self, queue: RemindersQueue) -> None:
        rid = queue.add_time_reminder(owner_id="42", fire_at=int(time.time()) + 60, action="x")
        assert queue.cancel(rid) is True
        assert queue.get(rid).status == ReminderStatus.CANCELLED

    def test_cancel_nonexistent(self, queue: RemindersQueue) -> None:
        assert queue.cancel("deadbeef0000") is False

    def test_cancel_already_cancelled(self, queue: RemindersQueue) -> None:
        rid = queue.add_time_reminder(owner_id="42", fire_at=int(time.time()) + 60, action="x")
        queue.cancel(rid)
        # повторная отмена → False (уже не pending)
        assert queue.cancel(rid) is False


# ─── list_pending ─────────────────────────────────────────────────────────────


class TestListPending:
    def test_list_pending_all(self, queue: RemindersQueue) -> None:
        queue.add_time_reminder(owner_id="42", fire_at=int(time.time()) + 60, action="a")
        queue.add_time_reminder(owner_id="99", fire_at=int(time.time()) + 60, action="b")
        assert len(queue.list_pending()) == 2

    def test_list_pending_filter_by_owner(self, queue: RemindersQueue) -> None:
        queue.add_time_reminder(owner_id="42", fire_at=int(time.time()) + 60, action="a")
        queue.add_time_reminder(owner_id="99", fire_at=int(time.time()) + 60, action="b")
        own42 = queue.list_pending(owner_id="42")
        assert len(own42) == 1
        assert own42[0].owner_user_id == "42"

    def test_list_pending_excludes_cancelled(self, queue: RemindersQueue) -> None:
        rid = queue.add_time_reminder(owner_id="42", fire_at=int(time.time()) + 60, action="a")
        queue.cancel(rid)
        assert queue.list_pending() == []


# ─── check_time_reminders ────────────────────────────────────────────────────


class TestCheckTimeReminders:
    def test_fires_at_deadline(self, queue: RemindersQueue) -> None:
        # Напоминание на "прошлое" — должно сразу сработать
        past = int(time.time()) - 10
        rid = queue.add_time_reminder(owner_id="42", fire_at=past, action="x")
        fired = asyncio.run(queue.check_time_reminders())
        assert rid in fired
        assert queue.get(rid).status == ReminderStatus.FIRED
        assert queue.get(rid).fired_at is not None

    def test_future_not_fired(self, queue: RemindersQueue) -> None:
        future = int(time.time()) + 3600
        rid = queue.add_time_reminder(owner_id="42", fire_at=future, action="x")
        fired = asyncio.run(queue.check_time_reminders())
        assert fired == []
        assert queue.get(rid).status == ReminderStatus.PENDING

    def test_callback_invoked(self, queue: RemindersQueue) -> None:
        captured: list[Reminder] = []

        async def cb(r: Reminder) -> None:
            captured.append(r)

        queue.set_fire_callback(cb)
        past = int(time.time()) - 1
        rid = queue.add_time_reminder(owner_id="42", fire_at=past, action="x")
        asyncio.run(queue.check_time_reminders())
        assert len(captured) == 1
        assert captured[0].id == rid

    def test_callback_failure_marks_failed(self, queue: RemindersQueue) -> None:
        async def bad_cb(r: Reminder) -> None:
            raise RuntimeError("boom")

        queue.set_fire_callback(bad_cb)
        past = int(time.time()) - 1
        rid = queue.add_time_reminder(owner_id="42", fire_at=past, action="x")
        asyncio.run(queue.check_time_reminders())
        r = queue.get(rid)
        assert r.status == ReminderStatus.FAILED
        assert "boom" in r.last_error

    def test_fired_do_not_refire(self, queue: RemindersQueue) -> None:
        count = {"n": 0}

        async def cb(r: Reminder) -> None:
            count["n"] += 1

        queue.set_fire_callback(cb)
        past = int(time.time()) - 1
        queue.add_time_reminder(owner_id="42", fire_at=past, action="x")
        asyncio.run(queue.check_time_reminders())
        asyncio.run(queue.check_time_reminders())
        assert count["n"] == 1  # сработал ровно один раз


# ─── check_event_match ────────────────────────────────────────────────────────


class TestCheckEventMatch:
    def test_regex_match_case_insensitive(self, queue: RemindersQueue) -> None:
        rid = queue.add_event_reminder(
            owner_id="42", chat_id="-100", pattern=r"bitcoin", action="notify"
        )
        matched = queue.check_event_match("-100", "Today BITCOIN rally started")
        assert len(matched) == 1
        assert matched[0].id == rid

    def test_no_match(self, queue: RemindersQueue) -> None:
        queue.add_event_reminder(owner_id="42", chat_id="-100", pattern=r"bitcoin", action="n")
        assert queue.check_event_match("-100", "Ethereum soaring") == []

    def test_chat_id_filter(self, queue: RemindersQueue) -> None:
        queue.add_event_reminder(owner_id="42", chat_id="-100", pattern=r"foo", action="n")
        # другое chat_id — не должен матчиться
        assert queue.check_event_match("-200", "foo bar") == []

    def test_invalid_regex_skipped(self, queue: RemindersQueue) -> None:
        # битый паттерн — не падаем, просто пропускаем
        queue.add_event_reminder(owner_id="42", chat_id="-100", pattern=r"[unclosed", action="n")
        assert queue.check_event_match("-100", "text here") == []

    def test_cancelled_not_matched(self, queue: RemindersQueue) -> None:
        rid = queue.add_event_reminder(
            owner_id="42", chat_id="-100", pattern=r"bitcoin", action="n"
        )
        queue.cancel(rid)
        assert queue.check_event_match("-100", "bitcoin news") == []

    def test_fire_event_reminder(self, queue: RemindersQueue) -> None:
        captured: list[Reminder] = []

        async def cb(r: Reminder) -> None:
            captured.append(r)

        queue.set_fire_callback(cb)
        rid = queue.add_event_reminder(owner_id="42", chat_id="-100", pattern=r"foo", action="n")
        matched = queue.check_event_match("-100", "foo bar")
        assert len(matched) == 1
        asyncio.run(queue.fire_event_reminder(matched[0]))
        assert len(captured) == 1
        assert queue.get(rid).status == ReminderStatus.FIRED


# ─── Persistence ──────────────────────────────────────────────────────────────


class TestPersistence:
    def test_save_then_load(self, state_path: Path) -> None:
        q1 = RemindersQueue(state_path=state_path)
        rid_t = q1.add_time_reminder(owner_id="42", fire_at=12345, action="xyz")
        rid_e = q1.add_event_reminder(owner_id="42", chat_id="-100", pattern="pat", action="act")
        # Новый инстанс — должен подтянуть те же reminders
        q2 = RemindersQueue(state_path=state_path)
        r_t = q2.get(rid_t)
        r_e = q2.get(rid_e)
        assert r_t is not None
        assert r_t.trigger_type == ReminderTrigger.TIME
        assert r_t.fire_at == 12345
        assert r_t.action_payload == "xyz"
        assert r_e is not None
        assert r_e.trigger_type == ReminderTrigger.EVENT
        assert r_e.watch_chat_id == "-100"
        assert r_e.match_pattern == "pat"

    def test_load_corrupted_file(self, state_path: Path) -> None:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("{not valid json", encoding="utf-8")
        # Не должно упасть — просто пустая очередь
        q = RemindersQueue(state_path=state_path)
        assert q.list_pending() == []

    def test_status_survives_roundtrip(self, state_path: Path) -> None:
        q1 = RemindersQueue(state_path=state_path)
        rid = q1.add_time_reminder(owner_id="42", fire_at=1, action="x")
        q1.cancel(rid)
        q2 = RemindersQueue(state_path=state_path)
        assert q2.get(rid).status == ReminderStatus.CANCELLED

    def test_saved_json_structure(self, state_path: Path) -> None:
        q = RemindersQueue(state_path=state_path)
        q.add_time_reminder(owner_id="42", fire_at=1, action="x")
        data = json.loads(state_path.read_text(encoding="utf-8"))
        assert "reminders" in data
        assert isinstance(data["reminders"], list)
        assert len(data["reminders"]) == 1
        assert data["reminders"][0]["trigger_type"] == "time"


# ─── Reminder dataclass ───────────────────────────────────────────────────────


class TestReminderDataclass:
    def test_from_dict_coerces_enums(self) -> None:
        r = Reminder.from_dict(
            {
                "id": "abc123",
                "owner_user_id": "42",
                "created_at": 1,
                "trigger_type": "time",
                "status": "pending",
            }
        )
        assert r.trigger_type == ReminderTrigger.TIME
        assert r.status == ReminderStatus.PENDING
