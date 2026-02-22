# -*- coding: utf-8 -*-
"""
Тесты: таймаут задачи в ChatWorkQueue → on_final_failure вызывается.

Sprint A (R14): критический баг — при asyncio.TimeoutError в wait_for()
old_code шёл в общий except Exception и засчитывал retry,
on_final_failure НЕ вызывался, пользователь видел вечное «🤔 Думаю...».
После исправления: TimeoutError обрабатывается отдельно, on_final_failure
вызывается сразу, retry не делается, status задачи = "timeout".

Стратегия: используем pytest monkeypatch (scope=function) — патч живёт
весь тест включая asyncio task, который запускается через ensure_worker.
"""

import asyncio
import pytest

import src.handlers.ai as ai_module
from src.handlers.ai import ChatQueuedTask, ChatWorkQueue


@pytest.mark.asyncio
async def test_queue_task_timeout_triggers_final_failure(monkeypatch) -> None:
    """
    При таймауте задачи on_final_failure должен быть вызван с TimeoutError.
    monkeypatch живёт до конца теста — asyncio task получает маленький таймаут.
    """
    monkeypatch.setattr(ai_module, "AUTO_REPLY_QUEUE_TASK_TIMEOUT_SECONDS", 0.05)

    failure_exc: list[BaseException] = []

    async def on_final_failure(exc: BaseException) -> None:
        failure_exc.append(exc)

    async def slow_runner() -> None:
        await asyncio.sleep(60.0)

    queue = ChatWorkQueue(max_per_chat=10, max_retries=1)

    task = ChatQueuedTask(
        chat_id=1,
        message_id=100,
        received_at=0.0,
        priority=0,
        runner=slow_runner,
        on_final_failure=on_final_failure,
    )
    queue.enqueue(task)
    queue.ensure_worker(chat_id=1)
    # Ждём дольше чем таймаут + overhead воркера.
    await asyncio.sleep(1.2)

    assert len(failure_exc) == 1, "on_final_failure должен быть вызван ровно один раз"
    assert isinstance(failure_exc[0], asyncio.TimeoutError), (
        f"on_final_failure должен получить TimeoutError, а не {type(failure_exc[0])}"
    )


@pytest.mark.asyncio
async def test_queue_task_timeout_no_retry(monkeypatch) -> None:
    """
    Таймаут задачи НЕ должен считаться как retry-возможная ошибка.
    При max_retries=2 задача с таймаутом не добавляется обратно в очередь.
    """
    monkeypatch.setattr(ai_module, "AUTO_REPLY_QUEUE_TASK_TIMEOUT_SECONDS", 0.05)

    fail_count: list[int] = [0]

    async def on_final_failure(exc: BaseException) -> None:
        fail_count[0] += 1

    async def slow_runner() -> None:
        await asyncio.sleep(60.0)

    queue = ChatWorkQueue(max_per_chat=10, max_retries=2)

    task = ChatQueuedTask(
        chat_id=2,
        message_id=200,
        received_at=0.0,
        priority=0,
        runner=slow_runner,
        on_final_failure=on_final_failure,
    )
    queue.enqueue(task)
    queue.ensure_worker(chat_id=2)
    await asyncio.sleep(1.2)

    # on_final_failure должен быть вызван ровно 1 раз (без retry).
    assert fail_count[0] == 1, (
        f"on_final_failure при таймауте должен вызываться 1 раз, вызван {fail_count[0]} раз"
    )


@pytest.mark.asyncio
async def test_queue_task_stats_failed_incremented_on_timeout(monkeypatch) -> None:
    """
    После таймаута задачи счётчик failed растёт, а retried остаётся на 0.
    """
    monkeypatch.setattr(ai_module, "AUTO_REPLY_QUEUE_TASK_TIMEOUT_SECONDS", 0.05)

    async def on_final_failure(exc: BaseException) -> None:
        pass

    async def slow_runner() -> None:
        await asyncio.sleep(60.0)

    queue = ChatWorkQueue(max_per_chat=10, max_retries=2)

    task = ChatQueuedTask(
        chat_id=3,
        message_id=300,
        received_at=0.0,
        priority=0,
        runner=slow_runner,
        on_final_failure=on_final_failure,
    )
    queue.enqueue(task)
    queue.ensure_worker(chat_id=3)
    await asyncio.sleep(1.2)

    stats = queue.get_stats()
    assert stats["failed"] >= 1, f"Счётчик failed должен вырасти: {stats}"
    assert stats["retried"] == 0, f"retried должен оставаться 0 при таймауте: {stats}"
