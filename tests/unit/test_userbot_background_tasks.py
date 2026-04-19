# -*- coding: utf-8 -*-
"""
Тесты BackgroundTasksMixin (src/userbot/background_tasks.py).

Покрываем:
1) _cancel_background_task — cancel + await + обнуление атрибута;
2) _get_chat_processing_lock — lazy-creation, idempotency, edge cases ключа;
3) _register_chat_background_task + _get_active_chat_background_task — register / done / stale;
4) _log_background_task_exception_cb — логирование done_callback;
5) _mark_incoming_item_background_started — inbox ack, edge cases;
6) _keep_typing_alive — loop завершается при set stop_event;
7) _background_task_reaper — отмена зависших задач.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.userbot_bridge import KraabUserbot


def _make_bot() -> KraabUserbot:
    """Минимальный stub KraabUserbot без вызова __init__."""
    bot = KraabUserbot.__new__(KraabUserbot)
    bot.current_role = "default"
    bot.me = SimpleNamespace(id=777)
    return bot


# ---------------------------------------------------------------------------
# _cancel_background_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_background_task_cancels_and_clears() -> None:
    """Задача отменяется и атрибут обнуляется."""
    bot = _make_bot()

    async def _noop() -> None:
        await asyncio.sleep(10)

    task = asyncio.create_task(_noop())
    bot._some_task = task
    await bot._cancel_background_task("_some_task")

    assert bot._some_task is None
    assert task.cancelled()


@pytest.mark.asyncio
async def test_cancel_background_task_noop_when_none() -> None:
    """Атрибут None — метод не падает."""
    bot = _make_bot()
    bot._some_task = None
    # Не должно выбросить исключение
    await bot._cancel_background_task("_some_task")


@pytest.mark.asyncio
async def test_cancel_background_task_noop_when_attribute_missing() -> None:
    """Атрибута нет вообще — метод не падает."""
    bot = _make_bot()
    await bot._cancel_background_task("_nonexistent_task")


@pytest.mark.asyncio
async def test_cancel_background_task_swallows_exception_from_task() -> None:
    """Если задача бросает исключение при await — оно поглощается."""
    bot = _make_bot()

    async def _failing() -> None:
        raise ValueError("ошибка задачи")

    task = asyncio.create_task(_failing())
    # Даём задаче упасть
    await asyncio.sleep(0)
    bot._failing_task = task
    # Не должно пропагировать ValueError
    await bot._cancel_background_task("_failing_task")
    assert bot._failing_task is None


# ---------------------------------------------------------------------------
# _get_chat_processing_lock
# ---------------------------------------------------------------------------


def test_get_chat_processing_lock_creates_lock() -> None:
    """Для нового chat_id создаётся asyncio.Lock."""
    bot = _make_bot()
    lock = bot._get_chat_processing_lock("123")
    assert isinstance(lock, asyncio.Lock)


def test_get_chat_processing_lock_idempotent() -> None:
    """Повторный вызов с тем же chat_id возвращает тот же объект."""
    bot = _make_bot()
    lock1 = bot._get_chat_processing_lock("123")
    lock2 = bot._get_chat_processing_lock("123")
    assert lock1 is lock2


def test_get_chat_processing_lock_different_chats_different_locks() -> None:
    """Разные chat_id — разные locks."""
    bot = _make_bot()
    lock_a = bot._get_chat_processing_lock("aaa")
    lock_b = bot._get_chat_processing_lock("bbb")
    assert lock_a is not lock_b


def test_get_chat_processing_lock_empty_chat_id_uses_unknown() -> None:
    """Пустой / None chat_id → ключ 'unknown'."""
    bot = _make_bot()
    lock_empty = bot._get_chat_processing_lock("")
    lock_none = bot._get_chat_processing_lock(None)  # type: ignore[arg-type]
    # Оба должны возвращать один и тот же lock под ключом 'unknown'
    assert lock_empty is lock_none


# ---------------------------------------------------------------------------
# _register_chat_background_task + _get_active_chat_background_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_and_get_active_task() -> None:
    """После регистрации задача возвращается как активная."""
    bot = _make_bot()

    async def _long() -> None:
        await asyncio.sleep(10)

    task = asyncio.create_task(_long())
    bot._register_chat_background_task("chat1", task)

    active = bot._get_active_chat_background_task("chat1")
    assert active is task
    task.cancel()


@pytest.mark.asyncio
async def test_get_active_task_returns_none_for_done_task() -> None:
    """Завершённая задача не возвращается как активная."""
    bot = _make_bot()

    async def _instant() -> None:
        return

    task = asyncio.create_task(_instant())
    bot._register_chat_background_task("chat2", task)
    await asyncio.sleep(0)  # Даём задаче завершиться

    active = bot._get_active_chat_background_task("chat2")
    assert active is None


@pytest.mark.asyncio
async def test_register_task_cleanup_callback_clears_on_done() -> None:
    """done_callback очищает записи после завершения задачи."""
    bot = _make_bot()

    async def _quick() -> None:
        return

    task = asyncio.create_task(_quick())
    bot._register_chat_background_task("chat3", task)
    # Даём задаче завершиться и callback'у отработать
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # После завершения задачи _get_active_chat_background_task должна вернуть None
    active = bot._get_active_chat_background_task("chat3")
    assert active is None


@pytest.mark.asyncio
async def test_get_active_task_stale_timeout(monkeypatch) -> None:
    """Задача старше stale_timeout_sec считается зависшей и отменяется."""
    bot = _make_bot()
    monkeypatch.setattr(
        "src.userbot.background_tasks.config",
        SimpleNamespace(USERBOT_BACKGROUND_TASK_STALE_TIMEOUT_SEC=60),
    )

    async def _long() -> None:
        await asyncio.sleep(100)

    task = asyncio.create_task(_long())
    bot._register_chat_background_task("stale_chat", task)

    # Подделываем started_at задолго в прошлое
    bot._chat_background_task_started_at["stale_chat"] = time.monotonic() - 200

    active = bot._get_active_chat_background_task("stale_chat")
    assert active is None
    # cancel() вызван — задача в состоянии cancelling/cancelled
    # task.cancel() возвращает True если запрос на отмену был принят
    assert task.cancelling() > 0 or task.cancelled()


# ---------------------------------------------------------------------------
# _log_background_task_exception_cb
# ---------------------------------------------------------------------------


def test_log_background_exception_cb_logs_exception() -> None:
    """Callback логирует исключение из упавшей задачи."""
    task_mock = MagicMock()
    task_mock.cancelled.return_value = False
    task_mock.exception.return_value = RuntimeError("сбой")
    task_mock.get_name = lambda: "test_task"

    with patch("src.userbot.background_tasks.logger") as mock_logger:
        KraabUserbot._log_background_task_exception_cb(task_mock)
        mock_logger.warning.assert_called_once()
        call_kwargs = mock_logger.warning.call_args
        assert "background_task_exception" in call_kwargs[0]


def test_log_background_exception_cb_noop_for_cancelled() -> None:
    """Отменённые задачи не логируются."""
    task_mock = MagicMock()
    task_mock.cancelled.return_value = True

    with patch("src.userbot.background_tasks.logger") as mock_logger:
        KraabUserbot._log_background_task_exception_cb(task_mock)
        mock_logger.warning.assert_not_called()


def test_log_background_exception_cb_noop_when_no_exception() -> None:
    """Задача без исключения — callback молчит."""
    task_mock = MagicMock()
    task_mock.cancelled.return_value = False
    task_mock.exception.return_value = None

    with patch("src.userbot.background_tasks.logger") as mock_logger:
        KraabUserbot._log_background_task_exception_cb(task_mock)
        mock_logger.warning.assert_not_called()


# ---------------------------------------------------------------------------
# _mark_incoming_item_background_started
# ---------------------------------------------------------------------------


def test_mark_incoming_item_skipped_when_none() -> None:
    """None incoming_item_result → ok=False, skipped=True."""
    bot = _make_bot()
    result = bot._mark_incoming_item_background_started(incoming_item_result=None)
    assert result["ok"] is False
    assert result.get("skipped") is True


def test_mark_incoming_item_skipped_when_not_ok() -> None:
    """incoming_item_result без ok=True → skip."""
    bot = _make_bot()
    result = bot._mark_incoming_item_background_started(incoming_item_result={"ok": False})
    assert result["ok"] is False


def test_mark_incoming_item_skipped_when_incomplete_identity() -> None:
    """Нет chat_id или message_id → identity_incomplete."""
    bot = _make_bot()
    incoming = {
        "ok": True,
        "item": {"metadata": {"chat_id": "123"}},  # нет message_id
    }
    result = bot._mark_incoming_item_background_started(incoming_item_result=incoming)
    assert result["ok"] is False
    assert "identity" in result.get("reason", "")


def test_mark_incoming_item_calls_inbox_service(monkeypatch) -> None:
    """При корректных данных вызывается inbox_service.set_status_by_dedupe."""
    bot = _make_bot()
    incoming = {
        "ok": True,
        "item": {"metadata": {"chat_id": "123", "message_id": "456"}},
    }
    fake_result = {"ok": True, "updated": True}
    mock_inbox = MagicMock()
    mock_inbox.set_status_by_dedupe.return_value = fake_result

    import src.userbot.background_tasks as bt_module

    monkeypatch.setattr(bt_module, "inbox_service", mock_inbox)

    result = bot._mark_incoming_item_background_started(incoming_item_result=incoming)
    mock_inbox.set_status_by_dedupe.assert_called_once()
    assert result == fake_result


# ---------------------------------------------------------------------------
# _keep_typing_alive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keep_typing_alive_stops_on_event() -> None:
    """Цикл stop_event завершает typing loop без зависания."""
    client = AsyncMock()
    stop_event = asyncio.Event()

    async def _set_after_delay() -> None:
        await asyncio.sleep(0.05)
        stop_event.set()

    setter = asyncio.create_task(_set_after_delay())
    # Таймаут 2 сек — если loop не останавливается, тест зафейлится
    await asyncio.wait_for(
        KraabUserbot._keep_typing_alive(client, 123, "typing", stop_event),
        timeout=2.0,
    )
    await setter


@pytest.mark.asyncio
async def test_keep_typing_alive_sends_cancel_on_exit() -> None:
    """Session 11 fix #6: при выходе из loop шлём явный ChatAction.CANCEL."""
    client = AsyncMock()
    stop_event = asyncio.Event()
    stop_event.set()  # сразу выставлен → loop выйдет после первой итерации
    await asyncio.wait_for(
        KraabUserbot._keep_typing_alive(client, 123, "typing", stop_event),
        timeout=2.0,
    )
    # Последний вызов send_chat_action должен быть с CANCEL action.
    assert client.send_chat_action.called
    last_call = client.send_chat_action.call_args_list[-1]
    action = last_call.args[1] if len(last_call.args) > 1 else last_call.kwargs.get("action")
    assert "CANCEL" in str(action).upper()


@pytest.mark.asyncio
async def test_keep_typing_alive_sends_cancel_on_task_cancel() -> None:
    """При task.cancel() (exception path) finally всё равно шлёт CANCEL."""
    client = AsyncMock()
    stop_event = asyncio.Event()

    task = asyncio.create_task(KraabUserbot._keep_typing_alive(client, 456, "typing", stop_event))
    # Даём loop стартовать и послать хотя бы один TYPING.
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    # Последний call — CANCEL, даже при cancellation.
    last_call = client.send_chat_action.call_args_list[-1]
    action = last_call.args[1] if len(last_call.args) > 1 else last_call.kwargs.get("action")
    assert "CANCEL" in str(action).upper()


# ---------------------------------------------------------------------------
# _background_task_reaper (базовый smoke test)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_background_task_reaper_cancels_stale(monkeypatch) -> None:
    """Reaper за один цикл отменяет зависшие задачи."""
    import src.userbot.background_tasks as bt_module

    bot = _make_bot()
    monkeypatch.setattr(
        bt_module.config, "USERBOT_BACKGROUND_TASK_STALE_TIMEOUT_SEC", 60, raising=False
    )

    async def _long() -> None:
        await asyncio.sleep(100)

    stale_task = asyncio.create_task(_long())
    bot._chat_background_tasks = {"stale": stale_task}
    bot._chat_background_task_started_at = {"stale": time.monotonic() - 200}

    # Reaper делает asyncio.sleep(60) — патчим через mock'ированный sleep
    original_sleep = asyncio.sleep

    async def _fast_sleep(delay: float) -> None:
        await original_sleep(0.01)

    monkeypatch.setattr(bt_module.asyncio, "sleep", _fast_sleep)

    # Запускаем reaper
    reaper = asyncio.create_task(bot._background_task_reaper())

    # Ждём один проход через ускоренный sleep
    await original_sleep(0.1)

    # Reaper должен вызвать cancel() на stale_task — задача либо cancelling, либо уже cancelled
    # task.done() включает оба состояния (cancelled и exception)
    assert stale_task.cancelling() > 0 or stale_task.done()

    reaper.cancel()
    try:
        await reaper
    except asyncio.CancelledError:
        pass
