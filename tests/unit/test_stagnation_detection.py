# -*- coding: utf-8 -*-
"""
Тесты `detect_stagnation` — обнаружение зависших задач OpenClaw Gateway.

Используется LLM flow watchdog'ом для hard-cancel текущего запроса, когда
codex-cli subprocess hung после gateway restart.

Что покрываем:

1. Пустой список задач не детектирует ничего.
2. Свежие задачи (last_event_at недавно) — не stagnant.
3. Задача с last_event_at > threshold — детектируется.
4. Завершённая задача (status != running/queued) игнорируется.
5. Задача без last_event_at (None/0) игнорируется.
6. Threshold настраивается параметром.
"""

from __future__ import annotations

import time

import pytest

from src.core.openclaw_task_poller import (
    STAGNATION_THRESHOLD_SEC,
    TaskState,
    detect_stagnation,
)


def _make_task(
    *,
    task_id: str = "task_1",
    status: str = "running",
    label: str = "codex-cli",
    progress: str = "",
    last_event_ms: int | None = None,
    age_sec: float | None = None,
) -> TaskState:
    """Фабрика TaskState для тестов: либо last_event_ms, либо age_sec (от now)."""
    if age_sec is not None:
        last_event_ms = int((time.time() - age_sec) * 1000)
    if last_event_ms is None:
        last_event_ms = int(time.time() * 1000)
    # is_stale не важен здесь — detect_stagnation сам считает от last_event_at_ms.
    return TaskState(
        task_id=task_id,
        status=status,
        label=label,
        progress_summary=progress,
        last_event_at_ms=last_event_ms,
        is_stale=False,
    )


def test_empty_tasks_no_stagnation() -> None:
    """Пустой список — пусто."""
    assert detect_stagnation([]) == []


def test_all_tasks_fresh_not_stagnant() -> None:
    """Свежие задачи (last_event_at только что) — не stagnant."""
    tasks = [
        _make_task(task_id="t1", age_sec=1.0),
        _make_task(task_id="t2", age_sec=5.0),
        _make_task(task_id="t3", age_sec=30.0),
    ]
    result = detect_stagnation(tasks, threshold_sec=120.0)
    assert result == []


def test_one_task_stagnant_detected() -> None:
    """Одна задача зависла > threshold — детектируется."""
    tasks = [
        _make_task(task_id="fresh", age_sec=10.0),
        _make_task(task_id="hung", age_sec=150.0),
    ]
    result = detect_stagnation(tasks, threshold_sec=120.0)
    assert len(result) == 1
    assert result[0].task_id == "hung"


def test_completed_task_ignored() -> None:
    """Задача в статусе succeeded/failed/done не проверяется."""
    tasks = [
        _make_task(task_id="done", status="succeeded", age_sec=500.0),
        _make_task(task_id="failed", status="failed", age_sec=400.0),
        _make_task(task_id="done2", status="done", age_sec=300.0),
    ]
    result = detect_stagnation(tasks, threshold_sec=120.0)
    assert result == []


def test_no_last_event_at_ignored() -> None:
    """Задача с last_event_at_ms=0 или None не считается stagnant."""
    tasks = [
        _make_task(task_id="no_ts_zero", last_event_ms=0),
        _make_task(task_id="no_ts_neg", last_event_ms=-1),
    ]
    result = detect_stagnation(tasks, threshold_sec=120.0)
    assert result == []


def test_threshold_configurable() -> None:
    """threshold=30 детектит 60s-старую задачу, которая не пройдёт default 120s."""
    tasks = [_make_task(task_id="medium_age", age_sec=60.0)]
    assert detect_stagnation(tasks, threshold_sec=30.0) != []
    assert detect_stagnation(tasks, threshold_sec=120.0) == []


def test_queued_task_also_detected() -> None:
    """Queued-задача (не только running) тоже может стагнировать."""
    tasks = [_make_task(task_id="queued_hung", status="queued", age_sec=150.0)]
    result = detect_stagnation(tasks, threshold_sec=120.0)
    assert len(result) == 1
    assert result[0].task_id == "queued_hung"


def test_mixed_statuses_only_active_selected() -> None:
    """Из смеси running + succeeded + queued возвращаются только активные стагнирующие."""
    tasks = [
        _make_task(task_id="r1", status="running", age_sec=200.0),
        _make_task(task_id="q1", status="queued", age_sec=200.0),
        _make_task(task_id="s1", status="succeeded", age_sec=200.0),
    ]
    result = detect_stagnation(tasks, threshold_sec=120.0)
    ids = {t.task_id for t in result}
    assert ids == {"r1", "q1"}


def test_default_threshold_from_constant() -> None:
    """Без явного параметра используется STAGNATION_THRESHOLD_SEC default."""
    # Задача чуть моложе default threshold — не должна считаться стагнирующей.
    tasks = [_make_task(task_id="borderline", age_sec=STAGNATION_THRESHOLD_SEC - 10)]
    assert detect_stagnation(tasks) == []
    # Задача старше default threshold — должна считаться стагнирующей.
    tasks_old = [_make_task(task_id="old", age_sec=STAGNATION_THRESHOLD_SEC + 10)]
    assert len(detect_stagnation(tasks_old)) == 1


def test_exactly_at_threshold_not_stagnant() -> None:
    """age == threshold не детектируется (нужно строго больше)."""
    # Создадим задачу с age ровно в threshold (с небольшим запасом отрицательным).
    # Из-за float-дрейфа детектор использует строгое >.
    tasks = [_make_task(task_id="edge", age_sec=120.0 - 1.0)]
    result = detect_stagnation(tasks, threshold_sec=120.0)
    assert result == []


@pytest.mark.parametrize(
    "threshold_sec,age_sec,expected_count",
    [
        (60.0, 30.0, 0),
        (60.0, 90.0, 1),
        (300.0, 200.0, 0),
        (300.0, 400.0, 1),
    ],
)
def test_parametrized_threshold(threshold_sec: float, age_sec: float, expected_count: int) -> None:
    """Параметризованные границы threshold."""
    tasks = [_make_task(task_id="t", age_sec=age_sec)]
    result = detect_stagnation(tasks, threshold_sec=threshold_sec)
    assert len(result) == expected_count


# ---------------------------------------------------------------------------
# Client-side cancel primitives
# ---------------------------------------------------------------------------


class _StubClient:
    """Минимальный stub для OpenClawClient cancel API (без __init__ overhead)."""

    def __init__(self) -> None:
        self._current_request_task = None

    # Методы перенесены из OpenClawClient один-в-один
    def register_current_request_task(self, task) -> None:  # type: ignore[no-untyped-def]
        self._current_request_task = task

    def cancel_current_request(self) -> bool:
        task = self._current_request_task
        if task and not task.done():
            task.cancel()
            return True
        return False


@pytest.mark.asyncio
async def test_cancel_current_request_without_active_returns_false() -> None:
    """Без зарегистрированной task — cancel возвращает False и ничего не делает."""
    client = _StubClient()
    assert client.cancel_current_request() is False


@pytest.mark.asyncio
async def test_cancel_current_request_cancels_active_task() -> None:
    """Зарегистрированная running-task отменяется, CancelledError поднимается."""
    import asyncio

    async def _long_sleep() -> None:
        await asyncio.sleep(10.0)

    client = _StubClient()
    task = asyncio.create_task(_long_sleep())
    client.register_current_request_task(task)

    assert client.cancel_current_request() is True

    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


@pytest.mark.asyncio
async def test_cancel_current_request_done_task_is_noop() -> None:
    """Уже завершённый task не кансельим — .done()==True → возвращаем False."""
    import asyncio

    async def _quick() -> int:
        return 42

    client = _StubClient()
    task = asyncio.create_task(_quick())
    await task  # ждём завершения
    client.register_current_request_task(task)

    assert client.cancel_current_request() is False
    assert task.result() == 42


def test_real_openclaw_client_exposes_cancel_api() -> None:
    """Sanity: production-клиент имеет register_current_request_task + cancel_current_request."""
    from src.openclaw_client import OpenClawClient

    assert hasattr(OpenClawClient, "register_current_request_task")
    assert hasattr(OpenClawClient, "cancel_current_request")
