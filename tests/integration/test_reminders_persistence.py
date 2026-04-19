# -*- coding: utf-8 -*-
"""Integration tests для reminders_queue persistence + recovery."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from src.core.reminders_queue import RemindersQueue, ReminderStatus, ReminderTrigger

# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def queue_path(tmp_path: Path) -> Path:
    return tmp_path / "reminders.json"


@pytest.fixture
def q(queue_path: Path) -> RemindersQueue:
    """Свежая очередь с изолированным tmp-файлом."""
    return RemindersQueue(state_path=queue_path)


# ─── TestPersistence ──────────────────────────────────────────────────────────


class TestPersistence:
    def test_add_time_reminder_writes_file(self, q: RemindersQueue, queue_path: Path) -> None:
        """После add_time_reminder файл должен существовать и содержать запись."""
        rid = q.add_time_reminder("u1", fire_at=int(time.time()) + 3600, action="ping")
        assert queue_path.exists(), "state file должен быть создан после add"
        data = json.loads(queue_path.read_text())
        ids = [r["id"] for r in data.get("reminders", [])]
        assert rid in ids

    def test_add_event_reminder_writes_file(self, q: RemindersQueue, queue_path: Path) -> None:
        rid = q.add_event_reminder("u1", "-100123", "keyword", "do_something")
        data = json.loads(queue_path.read_text())
        ids = [r["id"] for r in data["reminders"]]
        assert rid in ids

    def test_survives_fresh_instance_same_path(self, q: RemindersQueue, queue_path: Path) -> None:
        """Reminder добавленный в q1, виден в q2 (тот же path, reload)."""
        rid = q.add_time_reminder("u1", fire_at=int(time.time()) + 3600, action="carry_over")

        q2 = RemindersQueue(state_path=queue_path)  # _load() в __init__
        pending_ids = [r.id for r in q2.list_pending()]
        assert rid in pending_ids, "reminder должен быть виден после reload"

    def test_multiple_reminders_all_persisted(self, q: RemindersQueue, queue_path: Path) -> None:
        ids = [
            q.add_time_reminder("u1", fire_at=int(time.time()) + i * 60, action=f"a{i}")
            for i in range(1, 6)
        ]
        data = json.loads(queue_path.read_text())
        saved_ids = {r["id"] for r in data["reminders"]}
        assert set(ids) <= saved_ids

    def test_cancelled_status_persisted(self, q: RemindersQueue, queue_path: Path) -> None:
        rid = q.add_time_reminder("u1", fire_at=int(time.time()) + 3600, action="to-cancel")
        assert q.cancel(rid) is True

        q2 = RemindersQueue(state_path=queue_path)
        r = q2.get(rid)
        assert r is not None
        assert r.status == ReminderStatus.CANCELLED

    def test_cancelled_not_in_list_pending(self, q: RemindersQueue, queue_path: Path) -> None:
        rid = q.add_time_reminder("u1", fire_at=int(time.time()) + 3600, action="gone")
        q.cancel(rid)

        q2 = RemindersQueue(state_path=queue_path)
        pending_ids = [r.id for r in q2.list_pending()]
        assert rid not in pending_ids

    def test_enum_fields_round_trip(self, q: RemindersQueue, queue_path: Path) -> None:
        """Enum-поля trigger_type и status правильно десериализуются."""
        rid = q.add_event_reminder("u2", "-100999", "pat", "act")
        q2 = RemindersQueue(state_path=queue_path)
        r = q2.get(rid)
        assert r is not None
        assert r.trigger_type == ReminderTrigger.EVENT
        assert r.status == ReminderStatus.PENDING


# ─── TestRecovery ─────────────────────────────────────────────────────────────


class TestRecovery:
    def test_missing_file_starts_empty(self, tmp_path: Path) -> None:
        q = RemindersQueue(state_path=tmp_path / "nonexistent.json")
        assert q.list_pending() == []

    def test_corrupted_json_fails_gracefully(self, tmp_path: Path) -> None:
        path = tmp_path / "reminders.json"
        path.write_text("{not valid json!!!", encoding="utf-8")
        q = RemindersQueue(state_path=path)  # не должен упасть
        assert q.list_pending() == []

    def test_empty_json_object_fails_gracefully(self, tmp_path: Path) -> None:
        path = tmp_path / "reminders.json"
        path.write_text("{}", encoding="utf-8")
        q = RemindersQueue(state_path=path)
        assert q.list_pending() == []

    def test_partial_reminder_entry_ignored(self, tmp_path: Path) -> None:
        """Запись с отсутствующими обязательными полями — не крашит загрузку."""
        path = tmp_path / "reminders.json"
        path.write_text(
            json.dumps({"reminders": [{"id": "abc", "owner_user_id": "u"}]}),
            encoding="utf-8",
        )
        q = RemindersQueue(state_path=path)
        # Упавшая запись должна просто пропуститься — нет краша
        assert isinstance(q.list_pending(), list)

    def test_can_add_after_corrupted_load(self, tmp_path: Path) -> None:
        path = tmp_path / "reminders.json"
        path.write_text("garbage", encoding="utf-8")
        q = RemindersQueue(state_path=path)
        rid = q.add_time_reminder("u1", fire_at=int(time.time()) + 100, action="post-corrupt")
        assert rid in [r.id for r in q.list_pending()]


# ─── TestExpiry ───────────────────────────────────────────────────────────────


class TestExpiry:
    @pytest.mark.asyncio
    async def test_past_time_reminder_fires(self, q: RemindersQueue) -> None:
        fired: list[str] = []

        async def cb(reminder):
            fired.append(reminder.id)

        q.set_fire_callback(cb)
        rid = q.add_time_reminder("u1", fire_at=int(time.time()) - 1, action="past")

        await q.check_time_reminders()
        assert rid in fired

    @pytest.mark.asyncio
    async def test_future_reminder_does_not_fire(self, q: RemindersQueue) -> None:
        fired: list[str] = []

        async def cb(reminder):
            fired.append(reminder.id)

        q.set_fire_callback(cb)
        q.add_time_reminder("u1", fire_at=int(time.time()) + 9999, action="future")

        await q.check_time_reminders()
        assert not fired

    @pytest.mark.asyncio
    async def test_fired_reminder_not_refired(self, q: RemindersQueue) -> None:
        """Reminder, сработавший один раз, НЕ должен сработать повторно."""
        fired: list[str] = []

        async def cb(reminder):
            fired.append(reminder.id)

        q.set_fire_callback(cb)
        q.add_time_reminder("u1", fire_at=int(time.time()) - 1, action="once")

        await q.check_time_reminders()
        assert len(fired) == 1

        fired.clear()
        await q.check_time_reminders()
        assert not fired, "повторный fire — ошибка: reminder должен быть FIRED уже"

    @pytest.mark.asyncio
    async def test_fired_status_persisted(self, q: RemindersQueue, queue_path: Path) -> None:
        async def cb(reminder):
            pass

        q.set_fire_callback(cb)
        rid = q.add_time_reminder("u1", fire_at=int(time.time()) - 1, action="save-fired")
        await q.check_time_reminders()

        q2 = RemindersQueue(state_path=queue_path)
        r = q2.get(rid)
        assert r is not None
        assert r.status == ReminderStatus.FIRED

    @pytest.mark.asyncio
    async def test_failing_callback_marks_failed(self, q: RemindersQueue, queue_path: Path) -> None:
        async def bad_cb(reminder):
            raise RuntimeError("callback error")

        q.set_fire_callback(bad_cb)
        rid = q.add_time_reminder("u1", fire_at=int(time.time()) - 1, action="fail-me")
        await q.check_time_reminders()

        r = q.get(rid)
        assert r is not None
        assert r.status == ReminderStatus.FAILED
        assert "callback error" in r.last_error


# ─── TestEventReminders ───────────────────────────────────────────────────────


class TestEventReminders:
    def test_event_match_case_insensitive(self, q: RemindersQueue) -> None:
        rid = q.add_event_reminder("u1", "-100123", "UPLOAD", "remind_me")
        matched = q.check_event_match("-100123", "user posted upload here")
        assert len(matched) == 1
        assert matched[0].id == rid

    def test_event_match_different_chat_no_match(self, q: RemindersQueue) -> None:
        q.add_event_reminder("u1", "-100123", "keyword", "action")
        matched = q.check_event_match("-100999", "contains keyword")
        assert matched == []

    def test_event_invalid_regex_no_crash(self, q: RemindersQueue) -> None:
        """Битый regex в pattern — не должен крашить check_event_match."""
        q.add_event_reminder("u1", "-100123", "(unclosed", "action")
        matched = q.check_event_match("-100123", "test message")
        assert matched == []

    def test_event_match_multiple_pending(self, q: RemindersQueue) -> None:
        rid1 = q.add_event_reminder("u1", "-100123", "alpha", "a1")
        rid2 = q.add_event_reminder("u1", "-100123", "beta", "a2")
        _rid3 = q.add_event_reminder("u1", "-100123", "gamma", "a3")

        matched_ids = {r.id for r in q.check_event_match("-100123", "alpha and beta")}
        assert rid1 in matched_ids
        assert rid2 in matched_ids

    def test_event_match_chat_id_coercion(self, q: RemindersQueue) -> None:
        """chat_id может прийти как int — сравнение должно работать."""
        rid = q.add_event_reminder("u1", -100123, "topic", "notify")
        matched = q.check_event_match("-100123", "topic is here")
        assert any(r.id == rid for r in matched)

    @pytest.mark.asyncio
    async def test_fire_event_reminder_updates_status(
        self, q: RemindersQueue, queue_path: Path
    ) -> None:
        fired: list[str] = []

        async def cb(reminder):
            fired.append(reminder.id)

        q.set_fire_callback(cb)
        rid = q.add_event_reminder("u1", "-100123", "go", "action")
        r = q.get(rid)
        assert r is not None

        await q.fire_event_reminder(r)

        assert rid in fired
        assert r.status == ReminderStatus.FIRED

        # Status сохранён на диск
        q2 = RemindersQueue(state_path=queue_path)
        r2 = q2.get(rid)
        assert r2 is not None
        assert r2.status == ReminderStatus.FIRED


# ─── TestConcurrency ──────────────────────────────────────────────────────────


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_parallel_fire_all_reminders(self, q: RemindersQueue) -> None:
        """10 просроченных reminders — все должны сработать за один check."""
        ran: list[str] = []

        async def cb(reminder):
            await asyncio.sleep(0.005)
            ran.append(reminder.id)

        q.set_fire_callback(cb)
        for i in range(10):
            q.add_time_reminder("u1", fire_at=int(time.time()) - i - 1, action=f"r{i}")

        fired_ids = await q.check_time_reminders()
        assert len(fired_ids) == 10
        assert len(ran) == 10

    @pytest.mark.asyncio
    async def test_no_duplicate_fires_after_two_checks(self, q: RemindersQueue) -> None:
        """Два последовательных check — reminder срабатывает ровно один раз."""
        count: dict[str, int] = {}

        async def cb(reminder):
            count[reminder.id] = count.get(reminder.id, 0) + 1

        q.set_fire_callback(cb)
        rid = q.add_time_reminder("u1", fire_at=int(time.time()) - 1, action="once-only")

        await q.check_time_reminders()
        await q.check_time_reminders()

        assert count.get(rid, 0) == 1, f"ожидали 1 вызов, получили {count.get(rid)}"

    @pytest.mark.asyncio
    async def test_cancel_during_check_does_not_fire(self, q: RemindersQueue) -> None:
        """Отменённый reminder не должен сработать, даже если fire_at в прошлом."""
        fired: list[str] = []

        async def cb(reminder):
            fired.append(reminder.id)

        q.set_fire_callback(cb)
        rid = q.add_time_reminder(
            "u1", fire_at=int(time.time()) - 1, action="cancelled-before-check"
        )
        q.cancel(rid)

        await q.check_time_reminders()
        assert rid not in fired
