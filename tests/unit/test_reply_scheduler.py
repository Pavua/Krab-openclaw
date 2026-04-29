# -*- coding: utf-8 -*-
"""Тесты для ReplyScheduler (Idea 5)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.core.reply_scheduler import ReplyScheduler, ScheduledReply


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def scheduler(tmp_path: Path, fixed_now: datetime) -> ReplyScheduler:
    # Изолированное хранилище + замороженные часы
    store = tmp_path / "scheduled_replies.json"
    clock = [fixed_now]
    s = ReplyScheduler(storage_path=store, now_fn=lambda: clock[0])
    # Прокидываем clock как атрибут — тесты могут двигать время
    s._test_clock = clock  # type: ignore[attr-defined]
    return s


def _advance(scheduler: ReplyScheduler, delta: timedelta) -> None:
    clock = scheduler._test_clock  # type: ignore[attr-defined]
    clock[0] = clock[0] + delta


def test_schedule_and_list_pending(scheduler: ReplyScheduler, fixed_now: datetime) -> None:
    """schedule() регистрирует job, list_pending() возвращает её."""
    send_at = fixed_now + timedelta(minutes=10)
    job_id = scheduler.schedule(chat_id=12345, text="привет позже", send_at=send_at)

    assert isinstance(job_id, str) and len(job_id) >= 8

    pending = scheduler.list_pending()
    assert len(pending) == 1
    assert pending[0].job_id == job_id
    assert pending[0].chat_id == 12345
    assert pending[0].text == "привет позже"
    assert pending[0].send_at == send_at


def test_cancel_removes_job(scheduler: ReplyScheduler, fixed_now: datetime) -> None:
    """cancel() удаляет существующую и возвращает False для несуществующей."""
    job_id = scheduler.schedule(
        chat_id=1, text="x", send_at=fixed_now + timedelta(minutes=5)
    )
    assert scheduler.cancel(job_id) is True
    assert scheduler.list_pending() == []
    # Повторный cancel — False (уже нет)
    assert scheduler.cancel(job_id) is False
    # Несуществующий — False
    assert scheduler.cancel("deadbeefdeadbeef") is False


def test_pop_due_timing(scheduler: ReplyScheduler, fixed_now: datetime) -> None:
    """pop_due() возвращает только due jobs и удаляет их атомарно."""
    j1 = scheduler.schedule(chat_id=1, text="due", send_at=fixed_now + timedelta(seconds=30))
    j2 = scheduler.schedule(chat_id=2, text="future", send_at=fixed_now + timedelta(minutes=10))

    # Время не наступило — pop_due пуст
    assert scheduler.pop_due() == []
    assert {j.job_id for j in scheduler.list_pending()} == {j1, j2}

    # Двигаем часы на 1 минуту — j1 должен стать due
    _advance(scheduler, timedelta(minutes=1))
    due = scheduler.pop_due()
    assert len(due) == 1
    assert due[0].job_id == j1
    # j1 удалена, j2 остаётся
    pending = scheduler.list_pending()
    assert len(pending) == 1 and pending[0].job_id == j2

    # Повторный pop_due (без изменения времени) — пуст (атомарность)
    assert scheduler.pop_due() == []


def test_iso_datetime_parsing(scheduler: ReplyScheduler, fixed_now: datetime) -> None:
    """schedule() принимает ISO 8601-строку (включая 'Z' и naive)."""
    # ISO с Z (UTC)
    job1 = scheduler.schedule(chat_id=1, text="z", send_at="2026-04-29T13:00:00Z")
    # ISO с явным offset
    job2 = scheduler.schedule(chat_id=2, text="off", send_at="2026-04-29T15:00:00+02:00")
    # naive (должно быть нормализовано в UTC)
    job3 = scheduler.schedule(chat_id=3, text="naive", send_at="2026-04-29T14:00:00")

    pending = {j.job_id: j for j in scheduler.list_pending()}
    assert pending[job1].send_at == datetime(2026, 4, 29, 13, 0, tzinfo=timezone.utc)
    assert pending[job2].send_at.tzinfo is not None
    # 15:00+02:00 == 13:00 UTC
    assert pending[job2].send_at.utctimetuple()[3] == 13
    assert pending[job3].send_at.tzinfo == timezone.utc

    # Невалидная строка → ValueError
    with pytest.raises(ValueError):
        scheduler.schedule(chat_id=1, text="bad", send_at="not-a-date")

    # Пустой текст → ValueError
    with pytest.raises(ValueError):
        scheduler.schedule(chat_id=1, text="   ", send_at=fixed_now + timedelta(minutes=1))


def test_persistence_across_instances(tmp_path: Path, fixed_now: datetime) -> None:
    """Записи переживают пересоздание инстанса (JSON store)."""
    store = tmp_path / "scheduled_replies.json"
    s1 = ReplyScheduler(storage_path=store, now_fn=lambda: fixed_now)
    job_id = s1.schedule(
        chat_id=99,
        text="persist me",
        send_at=fixed_now + timedelta(hours=1),
        owner_id=42,
        metadata={"reply_to_message_id": 555},
    )
    assert store.exists()

    # Новый инстанс на том же файле
    s2 = ReplyScheduler(storage_path=store, now_fn=lambda: fixed_now)
    pending = s2.list_pending()
    assert len(pending) == 1
    job = pending[0]
    assert job.job_id == job_id
    assert job.chat_id == 99
    assert job.text == "persist me"
    assert job.owner_id == 42
    assert job.metadata == {"reply_to_message_id": 555}
    # send_at сохранил tz-aware
    assert job.send_at.tzinfo is not None

    # Cancel из второго инстанса виден на диске
    assert s2.cancel(job_id) is True
    s3 = ReplyScheduler(storage_path=store, now_fn=lambda: fixed_now)
    assert s3.list_pending() == []


def test_multi_owner_isolation(scheduler: ReplyScheduler, fixed_now: datetime) -> None:
    """list_pending(owner_id=...) фильтрует по владельцу."""
    a1 = scheduler.schedule(
        chat_id=1, text="a-one", send_at=fixed_now + timedelta(minutes=5), owner_id=100
    )
    a2 = scheduler.schedule(
        chat_id=2, text="a-two", send_at=fixed_now + timedelta(minutes=10), owner_id=100
    )
    b1 = scheduler.schedule(
        chat_id=3, text="b-one", send_at=fixed_now + timedelta(minutes=7), owner_id=200
    )
    anon = scheduler.schedule(
        chat_id=4, text="anon", send_at=fixed_now + timedelta(minutes=8)
    )

    # Все pending — 4
    assert len(scheduler.list_pending()) == 4

    # owner=100 — только его записи
    owner100 = scheduler.list_pending(owner_id=100)
    assert {j.job_id for j in owner100} == {a1, a2}
    # Сортировка по send_at
    assert [j.job_id for j in owner100] == [a1, a2]

    # owner=200 — его одна
    owner200 = scheduler.list_pending(owner_id=200)
    assert [j.job_id for j in owner200] == [b1]

    # Anon (owner_id=None) не попадает ни в один фильтр
    assert anon not in {j.job_id for j in scheduler.list_pending(owner_id=100)}
    assert anon not in {j.job_id for j in scheduler.list_pending(owner_id=200)}


def test_scheduled_reply_round_trip() -> None:
    """ScheduledReply.to_dict ↔ from_dict сохраняет данные."""
    when = datetime(2026, 4, 29, 18, 30, tzinfo=timezone.utc)
    created = datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc)
    original = ScheduledReply(
        job_id="abc123",
        chat_id=-1001,
        text="hello",
        send_at=when,
        created_at=created,
        owner_id=7,
        metadata={"k": "v"},
    )
    restored = ScheduledReply.from_dict(original.to_dict())
    assert restored == original
