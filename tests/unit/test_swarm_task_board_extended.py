# -*- coding: utf-8 -*-
"""
tests/unit/test_swarm_task_board_extended.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Расширенные тесты для src/core/swarm_task_board.py.
Покрывает: create/update/delete, приоритеты, фильтрацию, сводку, персистентность.
"""

import json
import pytest
from pathlib import Path

from src.core.swarm_task_board import SwarmTaskBoard, SwarmTask, VALID_STATUSES, VALID_PRIORITIES


@pytest.fixture
def board(tmp_path):
    """Изолированный board с временным файлом."""
    return SwarmTaskBoard(state_path=tmp_path / "board.json")


# ---------------------------------------------------------------------------
# 1. Создание задач
# ---------------------------------------------------------------------------


def test_create_task_basic(board):
    """Задача создаётся с правильными полями по умолчанию."""
    task = board.create_task(team="coders", title="Сделать фичу", description="Детали")
    assert isinstance(task, SwarmTask)
    assert task.team == "coders"
    assert task.title == "Сделать фичу"
    assert task.status == "pending"
    assert task.priority == "medium"
    assert task.created_by == "owner"


def test_create_task_custom_priority(board):
    """Поддерживаются все допустимые приоритеты."""
    for prio in ("low", "medium", "high", "critical"):
        t = board.create_task(team="analysts", title="t", description="d", priority=prio)
        assert t.priority == prio


def test_create_task_invalid_priority_defaults_to_medium(board):
    """Неверный приоритет заменяется на medium."""
    task = board.create_task(team="traders", title="t", description="d", priority="ultra")
    assert task.priority == "medium"


def test_create_task_title_truncated(board):
    """Заголовок обрезается до 500 символов."""
    long_title = "А" * 600
    task = board.create_task(team="coders", title=long_title, description="d")
    assert len(task.title) == 500


def test_create_task_description_truncated(board):
    """Описание обрезается до 2000 символов."""
    long_desc = "Б" * 2500
    task = board.create_task(team="coders", title="t", description=long_desc)
    assert len(task.description) == 2000


def test_create_task_team_lowercased(board):
    """Название команды приводится к нижнему регистру."""
    task = board.create_task(team="CODERS", title="t", description="d")
    assert task.team == "coders"
    assert task.assigned_to == "coders"


def test_create_task_with_parent(board):
    """Задача с parent_task_id сохраняет связь."""
    parent = board.create_task(team="coders", title="Родительская", description="d")
    child = board.create_task(
        team="coders", title="Дочерняя", description="d", parent_task_id=parent.task_id
    )
    assert child.parent_task_id == parent.task_id


# ---------------------------------------------------------------------------
# 2. Обновление задач
# ---------------------------------------------------------------------------


def test_update_task_status(board):
    """Статус задачи обновляется корректно."""
    task = board.create_task(team="analysts", title="t", description="d")
    updated = board.update_task(task.task_id, status="in_progress")
    assert updated is not None
    assert updated.status == "in_progress"


def test_update_task_invalid_status_ignored(board):
    """Неверный статус при обновлении игнорируется, старый сохраняется."""
    task = board.create_task(team="analysts", title="t", description="d")
    updated = board.update_task(task.task_id, status="flying")
    # статус не изменился
    assert updated.status == "pending"


def test_update_task_not_found_returns_none(board):
    """Обновление несуществующей задачи возвращает None."""
    result = board.update_task("nonexistent_id", status="done")
    assert result is None


def test_update_task_cannot_change_task_id(board):
    """task_id нельзя изменить через update_task."""
    task = board.create_task(team="coders", title="t", description="d")
    original_id = task.task_id
    board.update_task(task.task_id, task_id="hacked_id")
    fetched = board.get_task(original_id)
    assert fetched is not None
    assert fetched.task_id == original_id


# ---------------------------------------------------------------------------
# 3. Завершение и провал задач
# ---------------------------------------------------------------------------


def test_complete_task(board):
    """complete_task переводит задачу в done с результатом."""
    task = board.create_task(team="traders", title="Анализ", description="d")
    done = board.complete_task(task.task_id, result="Отчёт готов", artifacts=["/tmp/report.txt"])
    assert done.status == "done"
    assert done.result == "Отчёт готов"
    assert "/tmp/report.txt" in done.artifacts


def test_fail_task(board):
    """fail_task переводит задачу в failed с причиной."""
    task = board.create_task(team="creative", title="Рендер", description="d")
    failed = board.fail_task(task.task_id, reason="Нет ресурсов")
    assert failed.status == "failed"
    assert failed.result == "Нет ресурсов"


# ---------------------------------------------------------------------------
# 4. Получение и фильтрация задач
# ---------------------------------------------------------------------------


def test_get_task_by_id(board):
    """get_task возвращает нужную задачу по ID."""
    task = board.create_task(team="coders", title="Найди меня", description="d")
    found = board.get_task(task.task_id)
    assert found is not None
    assert found.task_id == task.task_id


def test_get_task_nonexistent(board):
    """get_task для несуществующего ID возвращает None."""
    assert board.get_task("no_such_id") is None


def test_list_tasks_filter_by_team(board):
    """list_tasks фильтрует по команде."""
    board.create_task(team="coders", title="Coder task", description="d")
    board.create_task(team="traders", title="Trader task", description="d")
    board.create_task(team="coders", title="Coder task 2", description="d")

    coder_tasks = board.list_tasks(team="coders")
    assert all(t.team == "coders" for t in coder_tasks)
    assert len(coder_tasks) == 2


def test_list_tasks_filter_by_status(board):
    """list_tasks фильтрует по статусу."""
    t1 = board.create_task(team="analysts", title="Task 1", description="d")
    t2 = board.create_task(team="analysts", title="Task 2", description="d")
    board.complete_task(t1.task_id)

    done_tasks = board.list_tasks(status="done")
    pending_tasks = board.list_tasks(status="pending")
    assert len(done_tasks) == 1
    assert len(pending_tasks) == 1


def test_list_tasks_combined_filter(board):
    """list_tasks с командой и статусом одновременно."""
    board.create_task(team="coders", title="C pending", description="d")
    t = board.create_task(team="coders", title="C done", description="d")
    board.complete_task(t.task_id)
    board.create_task(team="traders", title="T done", description="d")

    results = board.list_tasks(team="coders", status="done")
    assert len(results) == 1
    assert results[0].title == "C done"


def test_list_tasks_limit(board):
    """list_tasks уважает параметр limit."""
    for i in range(10):
        board.create_task(team="coders", title=f"Task {i}", description="d")
    results = board.list_tasks(limit=3)
    assert len(results) == 3


# ---------------------------------------------------------------------------
# 5. Сводка board
# ---------------------------------------------------------------------------


def test_board_summary_empty(board):
    """Пустой board возвращает нули в сводке."""
    summary = board.get_board_summary()
    assert summary["total"] == 0
    assert summary["by_status"] == {}
    assert summary["by_team"] == {}


def test_board_summary_counts(board):
    """Сводка корректно считает задачи по статусу и команде."""
    t1 = board.create_task(team="coders", title="t1", description="d")
    t2 = board.create_task(team="coders", title="t2", description="d")
    t3 = board.create_task(team="analysts", title="t3", description="d")
    board.complete_task(t1.task_id)

    summary = board.get_board_summary()
    assert summary["total"] == 3
    assert summary["by_status"]["done"] == 1
    assert summary["by_status"]["pending"] == 2
    assert summary["by_team"]["coders"] == 2
    assert summary["by_team"]["analysts"] == 1


# ---------------------------------------------------------------------------
# 6. Персистентность
# ---------------------------------------------------------------------------


def test_persistence_reload(tmp_path):
    """Задачи сохраняются и загружаются при создании нового экземпляра."""
    path = tmp_path / "board.json"
    b1 = SwarmTaskBoard(state_path=path)
    task = b1.create_task(team="coders", title="Персистентная", description="d")

    b2 = SwarmTaskBoard(state_path=path)
    loaded = b2.get_task(task.task_id)
    assert loaded is not None
    assert loaded.title == "Персистентная"


def test_persistence_atomic_write(tmp_path):
    """После create_task JSON-файл валидный (атомарная запись через .tmp)."""
    path = tmp_path / "board.json"
    board = SwarmTaskBoard(state_path=path)
    board.create_task(team="traders", title="Test", description="d")

    data = json.loads(path.read_text())
    assert isinstance(data, dict)


def test_load_empty_file(tmp_path):
    """Пустой JSON-файл не вызывает ошибок — board инициализируется пустым."""
    path = tmp_path / "board.json"
    path.write_text("", encoding="utf-8")
    board = SwarmTaskBoard(state_path=path)
    assert board.get_board_summary()["total"] == 0


def test_load_list_format(tmp_path):
    """Поддерживается устаревший list-формат JSON (обратная совместимость)."""
    path = tmp_path / "board.json"
    tasks_list = [
        {
            "task_id": "coders_aabbccdd_1000000000",
            "team": "coders",
            "title": "Старый формат",
            "description": "d",
            "status": "pending",
            "created_by": "owner",
            "assigned_to": "coders",
            "priority": "medium",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    path.write_text(json.dumps(tasks_list), encoding="utf-8")

    board = SwarmTaskBoard(state_path=path)
    summary = board.get_board_summary()
    assert summary["total"] == 1
    assert board.get_task("coders_aabbccdd_1000000000") is not None


# ---------------------------------------------------------------------------
# 7. FIFO trim
# ---------------------------------------------------------------------------


def test_fifo_trim_removes_done_first(tmp_path):
    """При превышении лимита сначала удаляются задачи со статусом done."""
    from src.core import swarm_task_board as stb_module

    original_max = stb_module._MAX_TASKS
    stb_module._MAX_TASKS = 5
    try:
        board = SwarmTaskBoard(state_path=tmp_path / "fifo.json")
        # Создаём 4 задачи и 1 завершённую
        tasks = [board.create_task(team="coders", title=f"t{i}", description="d") for i in range(4)]
        done_task = board.create_task(team="coders", title="done_task", description="d")
        board.complete_task(done_task.task_id)

        # Добавляем 6-ю — должен сработать trim
        board.create_task(team="coders", title="trigger trim", description="d")

        summary = board.get_board_summary()
        assert summary["total"] <= 5
        # done_task должна быть удалена первой
        assert board.get_task(done_task.task_id) is None
    finally:
        stb_module._MAX_TASKS = original_max
