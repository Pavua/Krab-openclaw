# -*- coding: utf-8 -*-
"""
Тесты для src/core/swarm_auto_executor.py
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.swarm_auto_executor import SwarmAutoExecutor
from src.core.swarm_task_board import SwarmTask

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------

def _make_task(
    task_id: str = "task-1",
    team: str = "coders",
    title: str = "Test task",
    priority: str = "medium",
    auto_execute: bool = True,
    status: str = "pending",
    description: str = "",
) -> SwarmTask:
    return SwarmTask(
        task_id=task_id,
        team=team,
        title=title,
        description=description,
        status=status,
        created_by="owner",
        assigned_to=team,
        priority=priority,
        created_at="2026-04-30T00:00:00+00:00",
        updated_at="2026-04-30T00:00:00+00:00",
        auto_execute=auto_execute,
    )


def _make_executor() -> SwarmAutoExecutor:
    """Создаёт экземпляр с привязанными зависимостями (не запущен)."""
    executor = SwarmAutoExecutor()
    executor.bind(
        sender=AsyncMock(),
        router_factory=MagicMock(),
        owner_chat_id="123456",
    )
    return executor


# ---------------------------------------------------------------------------
# test_disabled_by_default
# ---------------------------------------------------------------------------

class TestDisabledByDefault:
    def test_disabled_by_default(self):
        """Когда KRAB_SWARM_AUTO_EXECUTE_ENABLED=False, start() не создаёт задачу."""
        executor = SwarmAutoExecutor()
        with patch("src.core.swarm_auto_executor.config") as mock_cfg:
            mock_cfg.KRAB_SWARM_AUTO_EXECUTE_ENABLED = False
            mock_cfg.KRAB_SWARM_AUTO_EXECUTE_INTERVAL = 60
            mock_cfg.KRAB_SWARM_AUTO_EXECUTE_MAX_PER_HOUR = 5
            executor.start()

        assert executor._task is None
        assert executor._started is False

    def test_starts_when_enabled(self):
        """Когда флаг включён, start() создаёт asyncio задачу."""
        executor = SwarmAutoExecutor()

        async def _run():
            with patch("src.core.swarm_auto_executor.config") as mock_cfg:
                mock_cfg.KRAB_SWARM_AUTO_EXECUTE_ENABLED = True
                mock_cfg.KRAB_SWARM_AUTO_EXECUTE_INTERVAL = 60
                mock_cfg.KRAB_SWARM_AUTO_EXECUTE_MAX_PER_HOUR = 5
                executor.start()
                assert executor._started is True
                assert executor._task is not None
                executor.stop()

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# test_picks_highest_priority_task
# ---------------------------------------------------------------------------

class TestPicksHighestPriorityTask:
    def test_picks_highest_priority_task(self):
        """_pick_next_task возвращает critical раньше low."""
        executor = _make_executor()

        tasks = [
            _make_task(task_id="low-1", priority="low"),
            _make_task(task_id="critical-1", priority="critical"),
            _make_task(task_id="medium-1", priority="medium"),
        ]

        with patch("src.core.swarm_auto_executor.swarm_task_board") as mock_board:
            mock_board.list_tasks.return_value = tasks
            result = executor._pick_next_task()

        assert result is not None
        assert result.task_id == "critical-1"

    def test_picks_high_before_medium(self):
        """high приоритет раньше medium."""
        executor = _make_executor()

        tasks = [
            _make_task(task_id="med-1", priority="medium"),
            _make_task(task_id="high-1", priority="high"),
        ]

        with patch("src.core.swarm_auto_executor.swarm_task_board") as mock_board:
            mock_board.list_tasks.return_value = tasks
            result = executor._pick_next_task()

        assert result is not None
        assert result.task_id == "high-1"

    def test_returns_none_when_no_tasks(self):
        """Возвращает None если нет pending задач."""
        executor = _make_executor()

        with patch("src.core.swarm_auto_executor.swarm_task_board") as mock_board:
            mock_board.list_tasks.return_value = []
            result = executor._pick_next_task()

        assert result is None


# ---------------------------------------------------------------------------
# test_rate_limit_max_per_hour
# ---------------------------------------------------------------------------

class TestRateLimitMaxPerHour:
    def test_allows_up_to_limit(self):
        """Разрешает выполнение пока не достигнут лимит."""
        executor = _make_executor()
        now = time.time()
        # Заполним 4 из 5 слотов
        executor._executions_history = deque([now - 100, now - 200, now - 300, now - 400])

        with patch("src.core.swarm_auto_executor.config") as mock_cfg:
            mock_cfg.KRAB_SWARM_AUTO_EXECUTE_MAX_PER_HOUR = 5
            assert executor._check_rate_limit() is True

    def test_blocks_when_limit_reached(self):
        """Блокирует выполнение при достижении лимита (5/час)."""
        executor = _make_executor()
        now = time.time()
        executor._executions_history = deque([now - 100, now - 200, now - 300, now - 400, now - 500])

        with patch("src.core.swarm_auto_executor.config") as mock_cfg:
            mock_cfg.KRAB_SWARM_AUTO_EXECUTE_MAX_PER_HOUR = 5
            assert executor._check_rate_limit() is False

    def test_old_entries_are_pruned(self):
        """Записи старше 1 часа не учитываются в лимите."""
        executor = _make_executor()
        now = time.time()
        # 5 старых записей (> 1 часа назад)
        executor._executions_history = deque([now - 3700, now - 4000, now - 5000, now - 6000, now - 7000])

        with patch("src.core.swarm_auto_executor.config") as mock_cfg:
            mock_cfg.KRAB_SWARM_AUTO_EXECUTE_MAX_PER_HOUR = 5
            assert executor._check_rate_limit() is True


# ---------------------------------------------------------------------------
# test_only_auto_execute_tasks
# ---------------------------------------------------------------------------

class TestOnlyAutoExecuteTasks:
    def test_skips_tasks_without_auto_execute_flag(self):
        """Задачи без auto_execute=True не выбираются."""
        executor = _make_executor()

        tasks = [
            _make_task(task_id="manual-1", auto_execute=False, priority="critical"),
            _make_task(task_id="manual-2", auto_execute=False, priority="high"),
        ]

        with patch("src.core.swarm_auto_executor.swarm_task_board") as mock_board:
            mock_board.list_tasks.return_value = tasks
            result = executor._pick_next_task()

        assert result is None

    def test_picks_only_auto_execute_task_among_mixed(self):
        """Выбирает только задачу с auto_execute=True среди смешанных."""
        executor = _make_executor()

        tasks = [
            _make_task(task_id="manual-1", auto_execute=False, priority="critical"),
            _make_task(task_id="auto-1", auto_execute=True, priority="low"),
        ]

        with patch("src.core.swarm_auto_executor.swarm_task_board") as mock_board:
            mock_board.list_tasks.return_value = tasks
            result = executor._pick_next_task()

        assert result is not None
        assert result.task_id == "auto-1"


# ---------------------------------------------------------------------------
# test_task_status_updated_on_success
# ---------------------------------------------------------------------------

class TestTaskStatusUpdatedOnSuccess:
    @pytest.mark.asyncio
    async def test_task_status_updated_on_success(self):
        """После успешного выполнения статус меняется: pending → done."""
        executor = _make_executor()
        task = _make_task(task_id="task-ok", priority="medium")

        mock_artifact_store = MagicMock()
        mock_artifact_store.save_round_artifact = MagicMock()

        with (
            patch("src.core.swarm_auto_executor.config") as mock_cfg,
            patch("src.core.swarm_auto_executor.swarm_task_board") as mock_board,
            patch.object(executor, "_run_swarm_for_task", new=AsyncMock(return_value="Результат успешный")),
            patch.object(executor, "_notify_owner", new=AsyncMock()),
            patch.dict("sys.modules", {"src.core.swarm_artifact_store": MagicMock(swarm_artifact_store=mock_artifact_store)}),
        ):
            mock_cfg.KRAB_SWARM_AUTO_EXECUTE_MAX_PER_HOUR = 5
            mock_board.list_tasks.return_value = [task]
            mock_board.update_task.return_value = task
            mock_board.complete_task.return_value = task

            await executor._tick()

        # update_task вызван с in_progress
        mock_board.update_task.assert_called_once_with(task.task_id, status="in_progress")
        # complete_task вызван
        mock_board.complete_task.assert_called_once()
        complete_args = mock_board.complete_task.call_args
        assert complete_args[0][0] == task.task_id

    @pytest.mark.asyncio
    async def test_execution_recorded_in_history(self):
        """Успешное выполнение записывается в _executions_history."""
        executor = _make_executor()
        task = _make_task(task_id="task-hist", priority="medium")

        assert len(executor._executions_history) == 0

        mock_artifact_store = MagicMock()
        mock_artifact_store.save_round_artifact = MagicMock()

        with (
            patch("src.core.swarm_auto_executor.config") as mock_cfg,
            patch("src.core.swarm_auto_executor.swarm_task_board") as mock_board,
            patch.object(executor, "_run_swarm_for_task", new=AsyncMock(return_value="ok")),
            patch.object(executor, "_notify_owner", new=AsyncMock()),
            patch.dict("sys.modules", {"src.core.swarm_artifact_store": MagicMock(swarm_artifact_store=mock_artifact_store)}),
        ):
            mock_cfg.KRAB_SWARM_AUTO_EXECUTE_MAX_PER_HOUR = 5
            mock_board.list_tasks.return_value = [task]
            mock_board.update_task.return_value = task
            mock_board.complete_task.return_value = task

            await executor._tick()

        assert len(executor._executions_history) == 1


# ---------------------------------------------------------------------------
# test_task_status_updated_on_failure
# ---------------------------------------------------------------------------

class TestTaskStatusUpdatedOnFailure:
    @pytest.mark.asyncio
    async def test_task_status_updated_on_failure(self):
        """При исключении в run_swarm_for_task статус меняется на failed."""
        executor = _make_executor()
        task = _make_task(task_id="task-fail", priority="high")

        with (
            patch("src.core.swarm_auto_executor.config") as mock_cfg,
            patch("src.core.swarm_auto_executor.swarm_task_board") as mock_board,
            patch.object(executor, "_run_swarm_for_task", new=AsyncMock(side_effect=RuntimeError("boom"))),
            patch.object(executor, "_notify_owner", new=AsyncMock()),
        ):
            mock_cfg.KRAB_SWARM_AUTO_EXECUTE_MAX_PER_HOUR = 5
            mock_board.list_tasks.return_value = [task]
            mock_board.update_task.return_value = task
            mock_board.fail_task.return_value = task

            await executor._tick()

        # fail_task должен быть вызван с правильным task_id
        mock_board.fail_task.assert_called_once()
        fail_args = mock_board.fail_task.call_args
        assert fail_args[0][0] == task.task_id

        # complete_task не должен вызываться
        mock_board.complete_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_failure_not_recorded_in_history(self):
        """Провалившееся выполнение НЕ записывается в _executions_history."""
        executor = _make_executor()
        task = _make_task(task_id="task-fail2", priority="high")

        with (
            patch("src.core.swarm_auto_executor.config") as mock_cfg,
            patch("src.core.swarm_auto_executor.swarm_task_board") as mock_board,
            patch.object(executor, "_run_swarm_for_task", new=AsyncMock(side_effect=RuntimeError("fail"))),
            patch.object(executor, "_notify_owner", new=AsyncMock()),
        ):
            mock_cfg.KRAB_SWARM_AUTO_EXECUTE_MAX_PER_HOUR = 5
            mock_board.list_tasks.return_value = [task]
            mock_board.update_task.return_value = task
            mock_board.fail_task.return_value = task

            await executor._tick()

        assert len(executor._executions_history) == 0


# ---------------------------------------------------------------------------
# test_get_status_returns_dict
# ---------------------------------------------------------------------------

class TestGetStatus:
    def test_get_status_returns_dict(self):
        """get_status() возвращает словарь с ожидаемыми ключами."""
        executor = _make_executor()

        with patch("src.core.swarm_auto_executor.config") as mock_cfg:
            mock_cfg.KRAB_SWARM_AUTO_EXECUTE_ENABLED = True
            mock_cfg.KRAB_SWARM_AUTO_EXECUTE_INTERVAL = 120
            mock_cfg.KRAB_SWARM_AUTO_EXECUTE_MAX_PER_HOUR = 5
            status = executor.get_status()

        assert isinstance(status, dict)
        assert "enabled" in status
        assert "started" in status
        assert "interval_sec" in status
        assert "max_per_hour" in status
        assert "executions_last_hour" in status

    def test_get_status_counts_recent_executions(self):
        """executions_last_hour отражает только записи за последний час."""
        executor = _make_executor()
        now = time.time()
        # 2 свежих + 3 старых
        executor._executions_history = deque([
            now - 100,
            now - 200,
            now - 4000,  # > 1 часа
            now - 5000,
            now - 6000,
        ])

        with patch("src.core.swarm_auto_executor.config") as mock_cfg:
            mock_cfg.KRAB_SWARM_AUTO_EXECUTE_ENABLED = True
            mock_cfg.KRAB_SWARM_AUTO_EXECUTE_INTERVAL = 120
            mock_cfg.KRAB_SWARM_AUTO_EXECUTE_MAX_PER_HOUR = 5
            status = executor.get_status()

        assert status["executions_last_hour"] == 2

    def test_get_status_started_false_by_default(self):
        """Новый экзекутор не запущен — started=False."""
        executor = SwarmAutoExecutor()

        with patch("src.core.swarm_auto_executor.config") as mock_cfg:
            mock_cfg.KRAB_SWARM_AUTO_EXECUTE_ENABLED = False
            mock_cfg.KRAB_SWARM_AUTO_EXECUTE_INTERVAL = 60
            mock_cfg.KRAB_SWARM_AUTO_EXECUTE_MAX_PER_HOUR = 5
            status = executor.get_status()

        assert status["started"] is False

    def test_stop_resets_started(self):
        """После stop() started возвращается в False."""
        executor = _make_executor()

        async def _run():
            with patch("src.core.swarm_auto_executor.config") as mock_cfg:
                mock_cfg.KRAB_SWARM_AUTO_EXECUTE_ENABLED = True
                mock_cfg.KRAB_SWARM_AUTO_EXECUTE_INTERVAL = 60
                mock_cfg.KRAB_SWARM_AUTO_EXECUTE_MAX_PER_HOUR = 5
                executor.start()
                assert executor._started is True
                executor.stop()
                assert executor._started is False

        asyncio.run(_run())
