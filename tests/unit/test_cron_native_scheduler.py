"""Тесты для src/core/cron_native_scheduler.py — bug_004: early-fire fix."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.core.cron_native_store as store
from src.core.cron_native_scheduler import CronNativeScheduler

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def tmp_store(tmp_path: Path):
    """Изолируем хранилище во временной директории."""
    path = tmp_path / "cron_jobs.json"
    store.configure_default_path(path)
    yield path
    store.configure_default_path(store._DEFAULT_PATH)


def _make_scheduler() -> tuple[CronNativeScheduler, list[str]]:
    """Возвращает scheduler и список вызовов sender."""
    fired: list[str] = []

    async def sender(chat_id: str, prompt: str) -> None:
        fired.append(prompt)

    sched = CronNativeScheduler()
    sched.bind_sender(sender)
    return sched, fired


# ---------------------------------------------------------------------------
# _run_job: sleep до due_ts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_job_sleeps_until_due_ts():
    """_run_job должен вызвать asyncio.sleep(due_delay) перед отправкой."""
    sched, fired = _make_scheduler()

    import time as time_mod

    fake_now = 1_000_000.0
    due_ts = fake_now + 15.0  # 15s в будущем

    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    job = {"id": "job1", "prompt": "Test prompt", "cron_spec": "0 0 * * MON"}

    with (
        patch("src.core.cron_native_scheduler.time") as mock_time,
        patch("src.core.cron_native_scheduler.asyncio.sleep", side_effect=fake_sleep),
    ):
        mock_time.time.return_value = fake_now
        await sched._run_job(job, due_ts=due_ts)

    assert len(sleep_calls) == 1
    assert abs(sleep_calls[0] - 15.0) < 0.01
    assert "Test prompt" in fired


@pytest.mark.asyncio
async def test_run_job_no_negative_sleep():
    """_run_job не спит отрицательное время (due_ts уже в прошлом)."""
    sched, fired = _make_scheduler()

    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    job = {"id": "job2", "prompt": "Past due", "cron_spec": "0 0 * * *"}
    due_ts = 999_900.0  # прошлое

    with (
        patch("src.core.cron_native_scheduler.time") as mock_time,
        patch("src.core.cron_native_scheduler.asyncio.sleep", side_effect=fake_sleep),
    ):
        mock_time.time.return_value = 1_000_000.0
        await sched._run_job(job, due_ts=due_ts)

    assert len(sleep_calls) == 1
    assert sleep_calls[0] == 0.0
    assert "Past due" in fired


@pytest.mark.asyncio
async def test_run_job_no_due_ts_no_sleep():
    """_run_job без due_ts — не спит, просто стреляет."""
    sched, fired = _make_scheduler()

    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    job = {"id": "job3", "prompt": "No due", "cron_spec": "0 0 * * *"}

    with patch("src.core.cron_native_scheduler.asyncio.sleep", side_effect=fake_sleep):
        await sched._run_job(job, due_ts=None)

    assert sleep_calls == []
    assert "No due" in fired


@pytest.mark.asyncio
async def test_run_job_empty_prompt_skips():
    """_run_job с пустым prompt не должен стрелять."""
    sched, fired = _make_scheduler()
    job = {"id": "job4", "prompt": "", "cron_spec": "0 0 * * *"}
    await sched._run_job(job, due_ts=None)
    assert fired == []


# ---------------------------------------------------------------------------
# _last_fired stamps due_ts, not peek-now
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_stamps_due_ts_in_last_fired():
    """_tick должен записывать due_ts (не текущий now) в _last_fired."""
    sched, fired = _make_scheduler()

    from datetime import datetime, timezone

    monday_midnight = datetime(2026, 4, 20, 0, 0, 0, tzinfo=timezone.utc)
    due_ts = monday_midnight.timestamp()

    # Peek происходит в Sunday 23:59:45 — за 15s до срабатывания
    peek_now = due_ts - 15.0

    store.add_job("0 0 * * MON", "Monday job", job_id="mon-job")

    # Перехватываем ensure_future через замену метода _run_job — без рекурсии
    run_job_args: list[tuple] = []
    original_run_job = sched._run_job

    async def capturing_run_job(job: dict, due_ts_arg: float | None = None) -> None:
        run_job_args.append((job.get("id"), due_ts_arg))
        # не выполняем реальный run_job — тест проверяет только _last_fired

    sched._run_job = capturing_run_job

    with (
        patch("src.core.cron_native_scheduler.time") as mock_time,
        patch("src.core.cron_native_store.next_due") as mock_next_due,
        patch("src.core.cron_native_scheduler.asyncio.ensure_future") as mock_ensure,
    ):
        mock_time.time.return_value = peek_now
        mock_next_due.return_value = due_ts
        # ensure_future: просто записываем, не выполняем корутину
        mock_ensure.side_effect = lambda coro: coro.close()

        await sched._tick()

    # _last_fired["mon-job"] должен быть due_ts, а не peek_now
    assert "mon-job" in sched._last_fired
    assert sched._last_fired["mon-job"] == due_ts
    assert sched._last_fired["mon-job"] != peek_now


# ---------------------------------------------------------------------------
# Calendar-boundary: Monday 00:00 — peek Sunday 23:59:45
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monday_midnight_fires_at_correct_time():
    """Monday 00:00 cron: peek Sunday 23:59:45 → sleep 15s → fire at Monday 00:00."""
    sched, fired = _make_scheduler()

    from datetime import datetime, timezone

    monday_midnight = datetime(2026, 4, 20, 0, 0, 0, tzinfo=timezone.utc)
    due_ts = monday_midnight.timestamp()
    peek_now = due_ts - 15.0

    store.add_job("0 0 * * MON", "Monday midnight", job_id="mon-midnight")

    sleep_calls: list[float] = []
    run_job_calls: list[tuple] = []

    original_run_job = sched._run_job

    async def tracking_run_job(job: dict, due_ts: float | None = None) -> None:
        run_job_calls.append((job.get("id"), due_ts))
        await original_run_job(job, due_ts=due_ts)

    sched._run_job = tracking_run_job

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    with (
        patch("src.core.cron_native_scheduler.time") as mock_time,
        patch("src.core.cron_native_store.next_due") as mock_next_due,
        patch("src.core.cron_native_scheduler.asyncio.sleep", side_effect=fake_sleep),
    ):
        mock_time.time.return_value = peek_now
        mock_next_due.return_value = due_ts

        # Запускаем tick — он создаёт ensure_future(_run_job)
        # Для теста вызываем _run_job напрямую с due_ts
        await sched._tick()

        # Имитируем вызов _run_job с due_ts как peek_now
        mock_time.time.return_value = peek_now
        await sched._run_job(
            {"id": "mon-midnight", "prompt": "Monday midnight", "cron_spec": "0 0 * * MON"},
            due_ts=due_ts,
        )

    # Проверяем: sleep вызывался примерно 15s
    assert any(abs(s - 15.0) < 0.5 for s in sleep_calls), f"sleep_calls: {sleep_calls}"
    assert "Monday midnight" in fired


# ---------------------------------------------------------------------------
# First-of-month cron
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_of_month_peek_on_last_day():
    """1-го числа 00:00 cron: peek на последний день прошлого месяца → sleep → fire."""
    sched, fired = _make_scheduler()

    from datetime import datetime, timezone

    # 1 мая 2026 00:00 UTC
    first_may = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
    due_ts = first_may.timestamp()
    peek_now = due_ts - 20.0  # 30 апреля 23:59:40

    job = {"id": "first-month", "prompt": "First of month", "cron_spec": "0 0 1 * *"}

    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    with (
        patch("src.core.cron_native_scheduler.time") as mock_time,
        patch("src.core.cron_native_scheduler.asyncio.sleep", side_effect=fake_sleep),
    ):
        mock_time.time.return_value = peek_now
        await sched._run_job(job, due_ts=due_ts)

    assert len(sleep_calls) == 1
    assert abs(sleep_calls[0] - 20.0) < 0.01
    assert "First of month" in fired


# ---------------------------------------------------------------------------
# Sub-minute / hourly — fires on time, not late
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hourly_fires_on_time():
    """Hourly job: peek в :30 → sleep 30s → fires в :00."""
    sched, fired = _make_scheduler()

    from datetime import datetime, timezone

    next_hour = datetime(2026, 4, 20, 11, 0, 0, tzinfo=timezone.utc)
    due_ts = next_hour.timestamp()
    peek_now = due_ts - 30.0  # 10:59:30

    job = {"id": "hourly", "prompt": "Hourly", "cron_spec": "0 * * * *"}

    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    with (
        patch("src.core.cron_native_scheduler.time") as mock_time,
        patch("src.core.cron_native_scheduler.asyncio.sleep", side_effect=fake_sleep),
    ):
        mock_time.time.return_value = peek_now
        await sched._run_job(job, due_ts=due_ts)

    assert abs(sleep_calls[0] - 30.0) < 0.01
    assert "Hourly" in fired


# ---------------------------------------------------------------------------
# Multiple independent jobs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_jobs_independent():
    """Несколько jobs срабатывают независимо с правильными sleep delays."""
    sched, fired = _make_scheduler()

    from datetime import datetime, timezone

    base_now = datetime(2026, 4, 20, 10, 0, 0, tzinfo=timezone.utc).timestamp()

    job_a = {"id": "ja", "prompt": "Job A", "cron_spec": "0 11 * * *"}
    job_b = {"id": "jb", "prompt": "Job B", "cron_spec": "0 12 * * *"}

    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    with (
        patch("src.core.cron_native_scheduler.time") as mock_time,
        patch("src.core.cron_native_scheduler.asyncio.sleep", side_effect=fake_sleep),
    ):
        mock_time.time.return_value = base_now
        await sched._run_job(job_a, due_ts=base_now + 3600.0)
        mock_time.time.return_value = base_now
        await sched._run_job(job_b, due_ts=base_now + 7200.0)

    assert len(sleep_calls) == 2
    assert abs(sleep_calls[0] - 3600.0) < 0.5
    assert abs(sleep_calls[1] - 7200.0) < 0.5
    assert "Job A" in fired
    assert "Job B" in fired


# ---------------------------------------------------------------------------
# Cooldown: 50s защита от двойного срабатывания
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cooldown_prevents_double_fire():
    """_tick не запускает job повторно в течение 50s после last_fired."""
    sched, fired = _make_scheduler()

    store.add_job("*/30 * * * *", "Half-hour", job_id="hh")

    from datetime import datetime, timezone

    now_ts = datetime(2026, 4, 20, 10, 0, 0, tzinfo=timezone.utc).timestamp()
    due_ts = now_ts + 10.0  # через 10s

    ensure_calls: list[Any] = []

    with (
        patch("src.core.cron_native_scheduler.time") as mock_time,
        patch("src.core.cron_native_store.next_due") as mock_next_due,
        patch("src.core.cron_native_scheduler.asyncio.ensure_future") as mock_ensure,
    ):
        mock_time.time.return_value = now_ts
        mock_next_due.return_value = due_ts
        mock_ensure.side_effect = lambda coro: ensure_calls.append(coro)

        # Первый tick — должен запустить
        await sched._tick()
        first_count = len(ensure_calls)

        # Второй tick сразу — cooldown 50s не прошёл
        await sched._tick()
        second_count = len(ensure_calls)

    assert first_count == 1, "Первый tick должен запустить job"
    assert second_count == 1, "Второй tick в течение cooldown не должен запускать"


@pytest.mark.asyncio
async def test_cooldown_allows_after_50s():
    """_tick позволяет повторный запуск после 50s cooldown."""
    sched, fired = _make_scheduler()

    store.add_job("*/30 * * * *", "Half-hour2", job_id="hh2")

    from datetime import datetime, timezone

    now_ts = datetime(2026, 4, 20, 10, 30, 0, tzinfo=timezone.utc).timestamp()
    due_ts1 = now_ts + 10.0
    due_ts2 = due_ts1 + 1800.0  # следующие 30 минут

    ensure_calls: list[Any] = []

    with (
        patch("src.core.cron_native_scheduler.time") as mock_time,
        patch("src.core.cron_native_store.next_due") as mock_next_due,
        patch("src.core.cron_native_scheduler.asyncio.ensure_future") as mock_ensure,
    ):
        mock_ensure.side_effect = lambda coro: ensure_calls.append(coro)

        # Первый tick
        mock_time.time.return_value = now_ts
        mock_next_due.return_value = due_ts1
        await sched._tick()

        # Второй tick: имитируем прошедшие 60s (> 50s cooldown) и новый due_ts
        mock_time.time.return_value = now_ts + 1800.0
        mock_next_due.return_value = due_ts2
        await sched._tick()

    assert len(ensure_calls) == 2, "После cooldown job должен сработать снова"
