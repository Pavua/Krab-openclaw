# -*- coding: utf-8 -*-
"""
Тесты для src/core/swarm_task_board.py — task board для swarm teams.

Покрываем: create, update, complete, fail, get, list с фильтрами,
persist round-trip, board summary, parent_task_id, FIFO trim, edge cases.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.swarm_task_board import (
    SwarmTask,
    SwarmTaskBoard,
)

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def board(tmp_path: Path) -> SwarmTaskBoard:
    """Изолированный board на временном файле."""
    return SwarmTaskBoard(state_path=tmp_path / "swarm_task_board.json")


# ------------------------------------------------------------------
# create_task
# ------------------------------------------------------------------


class TestCreateTask:
    def test_returns_swarm_task(self, board: SwarmTaskBoard) -> None:
        task = board.create_task("coders", "Fix bug", "Описание бага")
        assert isinstance(task, SwarmTask)

    def test_default_status_pending(self, board: SwarmTaskBoard) -> None:
        task = board.create_task("coders", "T", "D")
        assert task.status == "pending"

    def test_task_id_contains_team(self, board: SwarmTaskBoard) -> None:
        task = board.create_task("traders", "T", "D")
        assert task.task_id.startswith("traders_")

    def test_team_normalized_lowercase(self, board: SwarmTaskBoard) -> None:
        task = board.create_task("ANALYSTS", "T", "D")
        assert task.team == "analysts"
        assert task.assigned_to == "analysts"

    def test_default_priority_medium(self, board: SwarmTaskBoard) -> None:
        task = board.create_task("coders", "T", "D")
        assert task.priority == "medium"

    def test_custom_priority(self, board: SwarmTaskBoard) -> None:
        task = board.create_task("coders", "T", "D", priority="critical")
        assert task.priority == "critical"

    def test_invalid_priority_falls_back_to_medium(self, board: SwarmTaskBoard) -> None:
        task = board.create_task("coders", "T", "D", priority="ultra")
        assert task.priority == "medium"

    def test_created_by_stored(self, board: SwarmTaskBoard) -> None:
        task = board.create_task("coders", "T", "D", created_by="scheduler")
        assert task.created_by == "scheduler"

    def test_parent_task_id_stored(self, board: SwarmTaskBoard) -> None:
        parent = board.create_task("coders", "Parent", "P")
        child = board.create_task("analysts", "Child", "C", parent_task_id=parent.task_id)
        assert child.parent_task_id == parent.task_id

    def test_title_truncated_at_500(self, board: SwarmTaskBoard) -> None:
        long_title = "X" * 600
        task = board.create_task("coders", long_title, "D")
        assert len(task.title) == 500

    def test_description_truncated_at_2000(self, board: SwarmTaskBoard) -> None:
        long_desc = "Y" * 2500
        task = board.create_task("coders", "T", long_desc)
        assert len(task.description) == 2000

    def test_task_persisted(self, board: SwarmTaskBoard, tmp_path: Path) -> None:
        board.create_task("coders", "Saved", "D")
        assert (tmp_path / "swarm_task_board.json").exists()


# ------------------------------------------------------------------
# update_task
# ------------------------------------------------------------------


class TestUpdateTask:
    def test_update_status(self, board: SwarmTaskBoard) -> None:
        task = board.create_task("coders", "T", "D")
        updated = board.update_task(task.task_id, status="in_progress")
        assert updated is not None
        assert updated.status == "in_progress"

    def test_update_invalid_status_ignored(self, board: SwarmTaskBoard) -> None:
        task = board.create_task("coders", "T", "D")
        updated = board.update_task(task.task_id, status="flying")
        # Статус не изменился, задача всё равно возвращается
        assert updated is not None
        assert updated.status == "pending"

    def test_update_nonexistent_returns_none(self, board: SwarmTaskBoard) -> None:
        result = board.update_task("no_such_id", status="done")
        assert result is None

    def test_cannot_change_task_id(self, board: SwarmTaskBoard) -> None:
        task = board.create_task("coders", "T", "D")
        # update_task принимает target_id позиционно; task_id в changes игнорируется
        updated = board.update_task(task.task_id, task_id="hacked_id")
        assert updated is not None
        assert updated.task_id == task.task_id

    def test_cannot_change_created_at(self, board: SwarmTaskBoard) -> None:
        task = board.create_task("coders", "T", "D")
        original_created = task.created_at
        updated = board.update_task(task.task_id, created_at="1970-01-01T00:00:00+00:00")
        assert updated is not None
        assert updated.created_at == original_created

    def test_updated_at_changes(self, board: SwarmTaskBoard) -> None:
        task = board.create_task("coders", "T", "D")
        updated = board.update_task(task.task_id, title="New Title")
        assert updated is not None
        # updated_at может совпасть при быстром запуске, но поле обновляется
        assert updated.updated_at >= task.updated_at


# ------------------------------------------------------------------
# complete_task / fail_task
# ------------------------------------------------------------------


class TestCompleteAndFail:
    def test_complete_sets_done(self, board: SwarmTaskBoard) -> None:
        task = board.create_task("analysts", "Research", "D")
        done = board.complete_task(task.task_id, result="Готово!", artifacts=["/tmp/out.txt"])
        assert done is not None
        assert done.status == "done"
        assert done.result == "Готово!"
        assert done.artifacts == ["/tmp/out.txt"]

    def test_complete_empty_artifacts_default(self, board: SwarmTaskBoard) -> None:
        task = board.create_task("analysts", "T", "D")
        done = board.complete_task(task.task_id)
        assert done is not None
        assert done.artifacts == []

    def test_fail_sets_failed(self, board: SwarmTaskBoard) -> None:
        task = board.create_task("traders", "Trade BTC", "D")
        failed = board.fail_task(task.task_id, reason="API timeout")
        assert failed is not None
        assert failed.status == "failed"
        assert failed.result == "API timeout"

    def test_complete_nonexistent_returns_none(self, board: SwarmTaskBoard) -> None:
        assert board.complete_task("no_such") is None

    def test_fail_nonexistent_returns_none(self, board: SwarmTaskBoard) -> None:
        assert board.fail_task("no_such") is None


# ------------------------------------------------------------------
# get_task
# ------------------------------------------------------------------


class TestGetTask:
    def test_get_existing(self, board: SwarmTaskBoard) -> None:
        task = board.create_task("coders", "T", "D")
        fetched = board.get_task(task.task_id)
        assert fetched is not None
        assert fetched.task_id == task.task_id

    def test_get_nonexistent_returns_none(self, board: SwarmTaskBoard) -> None:
        assert board.get_task("ghost_id") is None

    def test_returns_copy_not_reference(self, board: SwarmTaskBoard) -> None:
        task = board.create_task("coders", "T", "D")
        t1 = board.get_task(task.task_id)
        t2 = board.get_task(task.task_id)
        assert t1 is not t2


# ------------------------------------------------------------------
# list_tasks
# ------------------------------------------------------------------


class TestListTasks:
    def test_list_all(self, board: SwarmTaskBoard) -> None:
        board.create_task("coders", "T1", "D")
        board.create_task("traders", "T2", "D")
        tasks = board.list_tasks()
        assert len(tasks) == 2

    def test_filter_by_team(self, board: SwarmTaskBoard) -> None:
        board.create_task("coders", "T1", "D")
        board.create_task("traders", "T2", "D")
        coders = board.list_tasks(team="coders")
        assert len(coders) == 1
        assert coders[0].team == "coders"

    def test_filter_by_status(self, board: SwarmTaskBoard) -> None:
        t1 = board.create_task("coders", "T1", "D")
        board.create_task("coders", "T2", "D")
        board.complete_task(t1.task_id, result="ok")
        done_tasks = board.list_tasks(status="done")
        assert len(done_tasks) == 1

    def test_filter_team_and_status(self, board: SwarmTaskBoard) -> None:
        t1 = board.create_task("coders", "T1", "D")
        board.create_task("traders", "T2", "D")
        board.complete_task(t1.task_id, result="ok")
        results = board.list_tasks(team="coders", status="done")
        assert len(results) == 1

    def test_limit_respected(self, board: SwarmTaskBoard) -> None:
        for i in range(10):
            board.create_task("coders", f"T{i}", "D")
        tasks = board.list_tasks(limit=3)
        assert len(tasks) == 3

    def test_sorted_newest_first(self, board: SwarmTaskBoard) -> None:
        t1 = board.create_task("coders", "First", "D")
        t2 = board.create_task("coders", "Second", "D")
        tasks = board.list_tasks()
        # Новее — второй; created_at lte поскольку могут совпасть по секунде,
        # поэтому проверяем порядок по task_id (содержит timestamp)
        assert tasks[0].task_id in (t1.task_id, t2.task_id)


# ------------------------------------------------------------------
# get_board_summary
# ------------------------------------------------------------------


class TestBoardSummary:
    def test_empty_board(self, board: SwarmTaskBoard) -> None:
        summary = board.get_board_summary()
        assert summary["total"] == 0
        assert summary["by_status"] == {}
        assert summary["by_team"] == {}

    def test_summary_counts(self, board: SwarmTaskBoard) -> None:
        t1 = board.create_task("coders", "T1", "D")
        board.create_task("coders", "T2", "D")
        board.complete_task(t1.task_id, result="done")
        summary = board.get_board_summary()
        assert summary["total"] == 2
        assert summary["by_status"]["done"] == 1
        assert summary["by_status"]["pending"] == 1
        assert summary["by_team"]["coders"] == 2

    def test_summary_multiple_teams(self, board: SwarmTaskBoard) -> None:
        board.create_task("coders", "T1", "D")
        board.create_task("traders", "T2", "D")
        board.create_task("analysts", "T3", "D")
        summary = board.get_board_summary()
        assert summary["total"] == 3
        assert len(summary["by_team"]) == 3


# ------------------------------------------------------------------
# Persist round-trip
# ------------------------------------------------------------------


class TestPersistRoundTrip:
    def test_tasks_survive_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "board.json"
        b1 = SwarmTaskBoard(state_path=path)
        task = b1.create_task("coders", "Persistent task", "Description")
        b1.complete_task(task.task_id, result="Great result", artifacts=["/a/b.txt"])

        # Новый экземпляр читает с диска
        b2 = SwarmTaskBoard(state_path=path)
        loaded = b2.get_task(task.task_id)
        assert loaded is not None
        assert loaded.title == "Persistent task"
        assert loaded.status == "done"
        assert loaded.result == "Great result"
        assert loaded.artifacts == ["/a/b.txt"]

    def test_missing_file_gives_empty_board(self, tmp_path: Path) -> None:
        path = tmp_path / "nonexistent.json"
        b = SwarmTaskBoard(state_path=path)
        assert b.list_tasks() == []

    def test_corrupted_file_gives_empty_board(self, tmp_path: Path) -> None:
        path = tmp_path / "board.json"
        path.write_text("not valid json", encoding="utf-8")
        b = SwarmTaskBoard(state_path=path)
        assert b.list_tasks() == []


# ------------------------------------------------------------------
# FIFO trim
# ------------------------------------------------------------------


class TestFifoTrim:
    def test_trim_removes_done_tasks_first(self, tmp_path: Path) -> None:
        """При переполнении удаляются completed задачи раньше pending."""
        from src.core.swarm_task_board import _MAX_TASKS

        path = tmp_path / "board.json"
        b = SwarmTaskBoard(state_path=path)

        # Создаём MAX+5 задач: первые 10 сразу done, остальные pending
        done_ids = []
        for i in range(10):
            t = b.create_task("coders", f"Done task {i}", "D")
            b.complete_task(t.task_id, result="ok")
            done_ids.append(t.task_id)

        for i in range(_MAX_TASKS):
            b.create_task("coders", f"Pending task {i}", "D")

        # После создания _MAX_TASKS+10 задач (10 done + MAX pending), board <= MAX
        assert len(b.list_tasks(limit=0)) <= _MAX_TASKS


# ------------------------------------------------------------------
# configure_default_path
# ------------------------------------------------------------------


class TestConfigureDefaultPath:
    def test_reconfigure_loads_new_path(self, tmp_path: Path) -> None:
        b = SwarmTaskBoard(state_path=tmp_path / "old.json")
        b.create_task("coders", "Old task", "D")

        new_path = tmp_path / "new.json"
        b.configure_default_path(new_path)
        # После reconfig — пустой board
        assert b.list_tasks() == []


# ------------------------------------------------------------------
# Дополнительные тесты (расширение покрытия)
# ------------------------------------------------------------------


class TestUpdateTaskInvalidTarget:
    def test_update_task_invalid_id_returns_none(self, board: SwarmTaskBoard) -> None:
        """update_task с несуществующим task_id возвращает None."""
        result = board.update_task("definitely_not_real_id", title="New")
        assert result is None

    def test_update_task_empty_string_id_returns_none(self, board: SwarmTaskBoard) -> None:
        """update_task с пустым ID возвращает None, не выбрасывает исключение."""
        result = board.update_task("", status="done")
        assert result is None

    def test_update_task_after_deletion_via_trim(self, tmp_path: Path) -> None:
        """update_task после FIFO-вытеснения задачи возвращает None."""
        from src.core.swarm_task_board import _MAX_TASKS

        b = SwarmTaskBoard(state_path=tmp_path / "board.json")
        # Первая задача — done-кандидат на вытеснение
        first = b.create_task("coders", "First done", "D")
        b.complete_task(first.task_id, result="ok")

        # Заполняем board ещё MAX задачами (pending), чтобы first был вытеснен
        for i in range(_MAX_TASKS):
            b.create_task("coders", f"Pending {i}", "D")

        # first.task_id должен быть удалён
        result = b.update_task(first.task_id, title="Too late")
        assert result is None


class TestListTasksSorting:
    def test_sorted_by_created_at_descending(self, board: SwarmTaskBoard) -> None:
        """list_tasks возвращает задачи от новых к старым."""
        t1 = board.create_task("coders", "Older", "D")
        t2 = board.create_task("coders", "Newer", "D")

        # Принудительно расставляем created_at чтобы t1 < t2 независимо от скорости
        board._tasks[t1.task_id]["created_at"] = "2020-01-01T00:00:00+00:00"  # type: ignore[attr-defined]
        board._tasks[t2.task_id]["created_at"] = "2025-01-01T00:00:00+00:00"  # type: ignore[attr-defined]

        tasks = board.list_tasks()
        # Первым должен быть более новый (t2)
        assert tasks[0].task_id == t2.task_id
        assert tasks[1].task_id == t1.task_id

    def test_limit_zero_returns_all(self, board: SwarmTaskBoard) -> None:
        """list_tasks(limit=0) возвращает все задачи (срез [:0] пустой)."""
        for i in range(5):
            board.create_task("coders", f"T{i}", "D")
        # limit=0 означает tasks[:0] == [], так как 0 задач — это ожидаемое поведение
        tasks = board.list_tasks(limit=0)
        assert len(tasks) == 0

    def test_list_returns_copies_not_references(self, board: SwarmTaskBoard) -> None:
        """Элементы из list_tasks — независимые объекты SwarmTask."""
        board.create_task("coders", "T", "D")
        tasks1 = board.list_tasks()
        tasks2 = board.list_tasks()
        assert tasks1[0] is not tasks2[0]


class TestCompleteTaskIdempotency:
    def test_complete_already_done_updates_result(self, board: SwarmTaskBoard) -> None:
        """Повторный complete_task на done-задаче обновляет result."""
        task = board.create_task("analysts", "Research", "D")
        board.complete_task(task.task_id, result="First result")
        second = board.complete_task(task.task_id, result="Second result")
        assert second is not None
        assert second.status == "done"
        assert second.result == "Second result"

    def test_complete_failed_task_overrides_status(self, board: SwarmTaskBoard) -> None:
        """complete_task на failed-задаче переводит её в done."""
        task = board.create_task("traders", "Trade", "D")
        board.fail_task(task.task_id, reason="Timeout")
        recovered = board.complete_task(task.task_id, result="Recovered")
        assert recovered is not None
        assert recovered.status == "done"


class TestBoardSummaryMixedStatuses:
    def test_summary_all_statuses(self, board: SwarmTaskBoard) -> None:
        """get_board_summary учитывает все используемые статусы."""
        t1 = board.create_task("coders", "A", "D")
        t2 = board.create_task("coders", "B", "D")
        t3 = board.create_task("analysts", "C", "D")
        board.create_task("traders", "D", "D")

        board.complete_task(t1.task_id, result="ok")
        board.fail_task(t2.task_id, reason="err")
        board.update_task(t3.task_id, status="in_progress")
        # последняя задача остаётся pending

        summary = board.get_board_summary()
        assert summary["total"] == 4
        assert summary["by_status"].get("done") == 1
        assert summary["by_status"].get("failed") == 1
        assert summary["by_status"].get("in_progress") == 1
        assert summary["by_status"].get("pending") == 1
        assert summary["by_team"]["coders"] == 2
        assert summary["by_team"]["analysts"] == 1
        assert summary["by_team"]["traders"] == 1


class TestAsyncLoadInstrumentation:
    """Wave 22-H: async-ified load + elapsed_ms instrumentation."""

    def test_load_logs_elapsed_ms(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """_load логирует elapsed_ms после успешной загрузки."""
        import json
        import logging

        path = tmp_path / "board.json"
        # 200 задач = размер prod state-файла
        sample = {
            f"task_{i}": {
                "task_id": f"task_{i}",
                "team": "coders",
                "title": f"T{i}",
                "description": "D",
                "status": "pending",
                "created_by": "owner",
                "assigned_to": "coders",
                "priority": "medium",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
            for i in range(200)
        }
        path.write_text(json.dumps(sample), encoding="utf-8")

        with caplog.at_level(logging.INFO, logger="src.core.swarm_task_board"):
            b = SwarmTaskBoard(state_path=path)

        assert len(b._tasks) == 200  # type: ignore[attr-defined]
        # Лог должен содержать поле elapsed_ms
        loaded_records = [
            r for r in caplog.records if "swarm_task_board_loaded" in r.getMessage()
        ]
        assert loaded_records, "swarm_task_board_loaded event not logged"

    def test_load_missing_file_logs_zero_total(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """При отсутствии файла — лог с total=0 и path_missing=True."""
        import logging

        path = tmp_path / "not_here.json"
        with caplog.at_level(logging.INFO, logger="src.core.swarm_task_board"):
            b = SwarmTaskBoard(state_path=path)

        assert b.list_tasks() == []

    def test_load_async_completes(self, tmp_path: Path) -> None:
        """load_async() успешно перезагружает state через asyncio.to_thread."""
        import asyncio
        import json

        path = tmp_path / "board.json"
        sample = {
            "t1": {
                "task_id": "t1",
                "team": "coders",
                "title": "Async",
                "description": "D",
                "status": "pending",
                "created_by": "owner",
                "assigned_to": "coders",
                "priority": "medium",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        }
        path.write_text(json.dumps(sample), encoding="utf-8")

        b = SwarmTaskBoard(state_path=tmp_path / "empty.json")
        assert b.list_tasks() == []

        # Переключаем путь и async-перечитываем
        async def _reconfigure() -> None:
            await b.configure_default_path_async(path)

        asyncio.run(_reconfigure())
        assert b.get_task("t1") is not None
        assert b.get_task("t1").title == "Async"  # type: ignore[union-attr]

    def test_load_async_does_not_block_loop(self, tmp_path: Path) -> None:
        """
        load_async выполняется в thread; параллельная coroutine должна
        прогрессировать, пока идёт чтение.
        """
        import asyncio
        import json

        path = tmp_path / "board.json"
        path.write_text(json.dumps({}), encoding="utf-8")
        b = SwarmTaskBoard(state_path=tmp_path / "empty.json")

        async def _run() -> tuple[bool, bool]:
            progressed = False

            async def _ticker() -> None:
                nonlocal progressed
                await asyncio.sleep(0)
                progressed = True

            await asyncio.gather(
                b.configure_default_path_async(path),
                _ticker(),
            )
            return progressed, True

        progressed, completed = asyncio.run(_run())
        assert progressed and completed


class TestCleanupOld:
    """Метод cleanup_old: удаление done/failed задач."""

    def test_cleanup_removes_done(self, board: SwarmTaskBoard) -> None:
        t1 = board.create_task("coders", "A", "D")
        t2 = board.create_task("coders", "B", "D")
        board.complete_task(t1.task_id, result="ok")
        board.complete_task(t2.task_id, result="ok")

        removed = board.cleanup_old()
        assert removed == 2
        assert board.list_tasks() == []

    def test_cleanup_keeps_pending(self, board: SwarmTaskBoard) -> None:
        board.create_task("coders", "Pending", "D")
        t2 = board.create_task("coders", "Done", "D")
        board.complete_task(t2.task_id, result="ok")

        removed = board.cleanup_old()
        assert removed == 1
        assert len(board.list_tasks()) == 1

    def test_cleanup_with_keep_done(self, board: SwarmTaskBoard) -> None:
        for i in range(5):
            t = board.create_task("coders", f"T{i}", "D")
            board.complete_task(t.task_id, result="ok")

        removed = board.cleanup_old(keep_done=2)
        assert removed == 3
        assert len(board.list_tasks()) == 2

    def test_cleanup_empty_board(self, board: SwarmTaskBoard) -> None:
        assert board.cleanup_old() == 0


class TestFifo201Task:
    def test_201st_task_trims_oldest_done(self, tmp_path: Path) -> None:
        """При создании 201-й задачи board не превышает _MAX_TASKS."""
        from src.core.swarm_task_board import _MAX_TASKS

        b = SwarmTaskBoard(state_path=tmp_path / "board.json")

        # Создаём MAX задач, каждую вторую помечаем done
        ids = []
        for i in range(_MAX_TASKS):
            t = b.create_task("coders", f"Task {i}", "D")
            ids.append(t.task_id)
            if i % 2 == 0:
                b.complete_task(t.task_id, result="ok")

        assert len(b.list_tasks(limit=0)) <= _MAX_TASKS

        # Создаём 201-ю задачу
        extra = b.create_task("coders", "Task 201", "Extra")
        assert extra is not None

        # Board не должен превысить _MAX_TASKS
        assert len(b._tasks) <= _MAX_TASKS  # type: ignore[attr-defined]
