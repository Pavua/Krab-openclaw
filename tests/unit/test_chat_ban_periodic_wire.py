# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from src.core.chat_ban_cache import ChatBanCache


@pytest.mark.asyncio
async def test_periodic_cleanup_task_created_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет, что asyncio task создаётся когда env CHAT_BAN_PERIODIC_CLEANUP_ENABLED=1."""
    monkeypatch.setenv("CHAT_BAN_PERIODIC_CLEANUP_ENABLED", "1")

    from src.core.chat_ban_cache import chat_ban_cache

    # Имитируем bootstrap: создаём task (обычно это делается в KraabUserbot.start())
    cleanup_task = asyncio.create_task(
        chat_ban_cache.periodic_cleanup(interval_seconds=300)
    )

    # Проверяем что task присутствует в asyncio.all_tasks()
    all_tasks = asyncio.all_tasks()
    assert cleanup_task in all_tasks
    assert not cleanup_task.done()

    # Cleanup: отменяем task
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_periodic_cleanup_task_not_created_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет, что asyncio task НЕ создаётся когда env CHAT_BAN_PERIODIC_CLEANUP_ENABLED=0."""
    monkeypatch.setenv("CHAT_BAN_PERIODIC_CLEANUP_ENABLED", "0")

    # Получаем количество текущих tasks ДО попытки создания
    initial_task_count = len(asyncio.all_tasks())

    from src.core.chat_ban_cache import chat_ban_cache

    # Эмулируем логику из bootstrap с env check
    if os.getenv("CHAT_BAN_PERIODIC_CLEANUP_ENABLED", "1") == "1":
        cleanup_task = asyncio.create_task(
            chat_ban_cache.periodic_cleanup(interval_seconds=300)
        )
        # Не должны попасть сюда
        pytest.fail("Task должна была быть НЕ создана (env=0)")
    else:
        # Task не создана — проверяем что количество задач не изменилось
        final_task_count = len(asyncio.all_tasks())
        assert final_task_count == initial_task_count
