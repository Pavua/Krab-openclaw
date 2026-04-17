# -*- coding: utf-8 -*-
"""
Тесты интеграции RemindersQueue в userbot_bridge (Wave 7-D follow-up).

Покрываем:
1) set_fire_callback сохраняет кастомный callback
2) check_time_reminders вызывает callback для истекшего reminder
3) event-hook flow: check_event_match + fire_event_reminder → callback
4) callback ошибка не роняет loop (reminder помечен FAILED, callback future не падает)
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.core.reminders_queue import (
    Reminder,
    RemindersQueue,
    ReminderStatus,
)


@pytest.fixture
def queue(tmp_path: Path) -> RemindersQueue:
    """Изолированный RemindersQueue с tmp state-path."""
    return RemindersQueue(state_path=tmp_path / "reminders_integration.json")


@pytest.mark.asyncio
async def test_fire_callback_called_on_time_reminder(queue: RemindersQueue) -> None:
    """Callback получает Reminder с правильным payload когда deadline прошёл."""
    callback_calls: list[Reminder] = []

    async def mock_cb(reminder: Reminder) -> None:
        callback_calls.append(reminder)

    queue.set_fire_callback(mock_cb)

    # Reminder на "прошлое" — должен сразу сработать
    rid = queue.add_time_reminder(
        owner_id="user1", fire_at=int(time.time()) - 1, action="test payload"
    )

    fired = await queue.check_time_reminders()

    assert rid in fired
    assert len(callback_calls) == 1
    assert callback_calls[0].action_payload == "test payload"
    assert callback_calls[0].owner_user_id == "user1"
    assert queue.get(rid).status == ReminderStatus.FIRED


@pytest.mark.asyncio
async def test_event_hook_flow_end_to_end(queue: RemindersQueue) -> None:
    """
    Полный event flow: check_event_match → fire_event_reminder → callback.

    Имитирует то что делает userbot_bridge._process_message для event-reminders.
    """
    callback_calls: list[Reminder] = []

    async def mock_cb(reminder: Reminder) -> None:
        callback_calls.append(reminder)

    queue.set_fire_callback(mock_cb)

    rid = queue.add_event_reminder(
        owner_id="user42",
        chat_id="-100500",
        pattern=r"btc|bitcoin",
        action="notify about btc mention",
    )

    # Имитируем incoming message в том же чате с совпадающим текстом
    matched = queue.check_event_match("-100500", "btc rally just started!")
    assert len(matched) == 1
    assert matched[0].id == rid

    # Фейеримся (как делает _process_message через asyncio.create_task)
    await queue.fire_event_reminder(matched[0])

    assert len(callback_calls) == 1
    assert callback_calls[0].action_payload == "notify about btc mention"
    assert queue.get(rid).status == ReminderStatus.FIRED


@pytest.mark.asyncio
async def test_event_hook_no_match_no_fire(queue: RemindersQueue) -> None:
    """Несовпадающий текст → callback не вызывается, reminder остаётся PENDING."""
    callback_calls: list[Reminder] = []

    async def mock_cb(reminder: Reminder) -> None:
        callback_calls.append(reminder)

    queue.set_fire_callback(mock_cb)

    rid = queue.add_event_reminder(owner_id="u", chat_id="-100", pattern=r"bitcoin", action="n")
    matched = queue.check_event_match("-100", "just talking about the weather")

    assert matched == []
    assert callback_calls == []
    assert queue.get(rid).status == ReminderStatus.PENDING


@pytest.mark.asyncio
async def test_callback_failure_does_not_crash_loop(queue: RemindersQueue) -> None:
    """Ошибка callback изолирована: reminder помечается FAILED, check не выбрасывает."""

    async def bad_cb(reminder: Reminder) -> None:
        raise RuntimeError("send_message exploded")

    queue.set_fire_callback(bad_cb)

    rid = queue.add_time_reminder(owner_id="user1", fire_at=int(time.time()) - 1, action="x")

    # Не должно выбросить — check_time_reminders проглатывает ошибки callback
    fired = await queue.check_time_reminders()

    # Reminder не в fired (хотя callback вызвался), потому что статус FAILED
    # check_time_reminders возвращает только те, что успешно FIRED.
    # Детали: в текущей реализации fired.append(r.id) только в try ветке.
    assert rid not in fired
    r = queue.get(rid)
    assert r.status == ReminderStatus.FAILED
    assert "send_message exploded" in r.last_error


@pytest.mark.asyncio
async def test_set_fire_callback_replaces_previous(queue: RemindersQueue) -> None:
    """set_fire_callback поддерживает замену — вызывается последний установленный."""
    first_calls: list[Reminder] = []
    second_calls: list[Reminder] = []

    async def first_cb(r: Reminder) -> None:
        first_calls.append(r)

    async def second_cb(r: Reminder) -> None:
        second_calls.append(r)

    queue.set_fire_callback(first_cb)
    queue.set_fire_callback(second_cb)  # заменяет first_cb

    queue.add_time_reminder(owner_id="u", fire_at=int(time.time()) - 1, action="x")
    await queue.check_time_reminders()

    assert first_calls == []
    assert len(second_calls) == 1
