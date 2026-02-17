# -*- coding: utf-8 -*-
"""Тесты FIFO-очереди автоответов по чатам."""

from __future__ import annotations

import asyncio

import pytest

from src.handlers.ai import ChatQueuedTask, ChatWorkQueue


@pytest.mark.asyncio
async def test_queue_processes_tasks_in_fifo_order() -> None:
    """Задачи в одном чате должны выполняться строго по порядку поступления."""
    queue = ChatWorkQueue(max_per_chat=10)
    processed: list[int] = []

    for idx in range(3):
        async def _runner(value: int = idx) -> None:
            await asyncio.sleep(0.01)
            processed.append(value)

        accepted, _size = queue.enqueue(
            ChatQueuedTask(
                chat_id=1,
                message_id=idx + 1,
                received_at=0.0,
                priority=0,
                runner=_runner,
            )
        )
        assert accepted is True

    queue.ensure_worker(1)
    await asyncio.sleep(0.2)

    assert processed == [0, 1, 2]
    stats = queue.get_stats()
    assert stats["processed"] == 3
    assert stats["failed"] == 0
    assert stats["queued_total"] == 0


@pytest.mark.asyncio
async def test_queue_respects_max_per_chat_limit() -> None:
    """Очередь не должна принимать задачи сверх лимита на чат."""
    queue = ChatWorkQueue(max_per_chat=1)

    async def _runner() -> None:
        return None

    first, _ = queue.enqueue(
        ChatQueuedTask(chat_id=77, message_id=1, received_at=0.0, priority=0, runner=_runner)
    )
    second, _ = queue.enqueue(
        ChatQueuedTask(chat_id=77, message_id=2, received_at=0.0, priority=0, runner=_runner)
    )

    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_queue_retries_once_then_succeeds() -> None:
    """При временной ошибке задача должна быть автоматически повторена."""
    queue = ChatWorkQueue(max_per_chat=10, max_retries=1)
    attempts = {"n": 0}
    processed: list[int] = []

    async def _runner() -> None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("transient")
        processed.append(1)

    accepted, _ = queue.enqueue(
        ChatQueuedTask(chat_id=99, message_id=1, received_at=0.0, priority=0, runner=_runner)
    )
    assert accepted is True

    queue.ensure_worker(99)
    await asyncio.sleep(0.2)

    assert processed == [1]
    stats = queue.get_stats()
    assert stats["processed"] == 1
    assert stats["failed"] == 0
    assert stats["retried"] == 1
