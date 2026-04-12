# -*- coding: utf-8 -*-
"""
Тесты подкоманды `!swarm summary` в handle_swarm.

Покрываем:
1) пустой board и нет артефактов — базовый ответ;
2) задачи есть по статусам — отображаются корректно;
3) артефакты есть — rounds/duration/teams в ответе;
4) смешанный сценарий (задачи + артефакты).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import src.handlers.command_handlers as command_handlers


class _MessageStub:
    def __init__(self, text: str, chat_id: int = 123) -> None:
        self.text = text
        self.chat = SimpleNamespace(id=chat_id)
        self.reply_calls: list[str] = []

    async def reply(self, text: str) -> None:
        self.reply_calls.append(text)


class _BotStub:
    def __init__(self, args: str) -> None:
        self._args = args

    def _get_command_args(self, _: object) -> str:
        return self._args


def _empty_board() -> dict:
    return {"total": 0, "by_status": {}, "by_team": {}}


def _board_with_tasks() -> dict:
    return {
        "total": 5,
        "by_status": {"done": 3, "pending": 1, "failed": 1},
        "by_team": {"coders": 3, "traders": 2},
    }


def _make_artifacts(n: int) -> list[dict]:
    return [
        {
            "team": "traders",
            "topic": f"topic {i}",
            "duration_sec": 10.0,
            "timestamp_iso": "2026-04-12T10:00:00Z",
        }
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_summary_empty() -> None:
    """Пустая сессия — ответ содержит базовые секции."""
    msg = _MessageStub("!swarm summary")
    bot = _BotStub("summary")

    with (
        patch(
            "src.core.swarm_task_board.swarm_task_board.get_board_summary",
            return_value=_empty_board(),
        ),
        patch("src.core.swarm_artifact_store.swarm_artifact_store.list_artifacts", return_value=[]),
    ):
        await command_handlers.handle_swarm(bot, msg)

    assert msg.reply_calls, "должен быть хотя бы один ответ"
    reply = msg.reply_calls[0]
    assert "Summary" in reply
    assert "Итого задач: 0" in reply
    assert "Раундов сохранено: 0" in reply


@pytest.mark.asyncio
async def test_summary_shows_task_statuses() -> None:
    """Задачи по статусам отображаются в ответе."""
    msg = _MessageStub("!swarm summary")
    bot = _BotStub("summary")

    with (
        patch(
            "src.core.swarm_task_board.swarm_task_board.get_board_summary",
            return_value=_board_with_tasks(),
        ),
        patch("src.core.swarm_artifact_store.swarm_artifact_store.list_artifacts", return_value=[]),
    ):
        await command_handlers.handle_swarm(bot, msg)

    reply = msg.reply_calls[0]
    assert "done: 3" in reply
    assert "pending: 1" in reply
    assert "failed: 1" in reply
    assert "Итого задач: 5" in reply


@pytest.mark.asyncio
async def test_summary_shows_artifacts() -> None:
    """Артефакты — количество раундов и команды отображаются."""
    msg = _MessageStub("!swarm summary")
    bot = _BotStub("summary")
    arts = _make_artifacts(4)

    with (
        patch(
            "src.core.swarm_task_board.swarm_task_board.get_board_summary",
            return_value=_empty_board(),
        ),
        patch(
            "src.core.swarm_artifact_store.swarm_artifact_store.list_artifacts", return_value=arts
        ),
    ):
        await command_handlers.handle_swarm(bot, msg)

    reply = msg.reply_calls[0]
    assert "Раундов сохранено: 4" in reply
    assert "traders" in reply
    # суммарное время: 4 * 10 = 40с
    assert "40с" in reply


@pytest.mark.asyncio
async def test_summary_russian_alias() -> None:
    """Псевдоним `сводка` тоже работает."""
    msg = _MessageStub("!swarm сводка")
    bot = _BotStub("сводка")

    with (
        patch(
            "src.core.swarm_task_board.swarm_task_board.get_board_summary",
            return_value=_empty_board(),
        ),
        patch("src.core.swarm_artifact_store.swarm_artifact_store.list_artifacts", return_value=[]),
    ):
        await command_handlers.handle_swarm(bot, msg)

    assert msg.reply_calls
    assert "Summary" in msg.reply_calls[0]
