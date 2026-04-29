"""Тесты для CronNativeScheduler — selection criteria в _tick().

Refactor: trigger только когда due_ts фактически due (due_ts ≤ now),
без early-pick на _POLL_INTERVAL вперёд.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import patch

import pytest

from src.core import cron_native_scheduler as scheduler_module
from src.core.cron_native_scheduler import CronNativeScheduler


def _make_job(job_id: str = "j1", enabled: bool = True) -> dict[str, Any]:
    return {
        "id": job_id,
        "enabled": enabled,
        "cron_spec": "* * * * *",
        "prompt": "test prompt",
    }


@pytest.mark.asyncio
async def test_tick_does_not_trigger_when_due_far_in_future() -> None:
    """due_ts = now + 100 → не должен trigger (раньше early-pick срабатывал)."""
    sched = CronNativeScheduler()
    job = _make_job()
    now = time.time()
    fired: list[str] = []

    async def fake_run(j: dict) -> None:
        fired.append(str(j.get("id")))

    with (
        patch.object(scheduler_module.cron_native_store, "list_jobs", return_value=[job]),
        patch.object(scheduler_module.cron_native_store, "next_due", return_value=now + 100),
        patch.object(sched, "_run_job", side_effect=fake_run),
    ):
        await sched._tick()
        # дать ensure_future шанс отработать
        await asyncio.sleep(0)

    assert fired == [], f"Job не должен fire когда due_ts на 100s впереди, got: {fired}"


@pytest.mark.asyncio
async def test_tick_triggers_when_due_now() -> None:
    """due_ts ≈ now → должен trigger immediately."""
    sched = CronNativeScheduler()
    job = _make_job()
    now = time.time()
    fired: list[str] = []

    async def fake_run(j: dict) -> None:
        fired.append(str(j.get("id")))

    with (
        patch.object(scheduler_module.cron_native_store, "list_jobs", return_value=[job]),
        patch.object(scheduler_module.cron_native_store, "next_due", return_value=now),
        patch.object(sched, "_run_job", side_effect=fake_run),
    ):
        await sched._tick()
        await asyncio.sleep(0)

    assert fired == ["j1"], f"Job должен fire когда due_ts == now, got: {fired}"


@pytest.mark.asyncio
async def test_tick_triggers_when_due_in_past() -> None:
    """due_ts в прошлом (overdue) → должен trigger."""
    sched = CronNativeScheduler()
    job = _make_job()
    now = time.time()
    fired: list[str] = []

    async def fake_run(j: dict) -> None:
        fired.append(str(j.get("id")))

    with (
        patch.object(scheduler_module.cron_native_store, "list_jobs", return_value=[job]),
        patch.object(scheduler_module.cron_native_store, "next_due", return_value=now - 5),
        patch.object(sched, "_run_job", side_effect=fake_run),
    ):
        await sched._tick()
        await asyncio.sleep(0)

    assert fired == ["j1"]


@pytest.mark.asyncio
async def test_tick_skips_disabled_jobs() -> None:
    sched = CronNativeScheduler()
    job = _make_job(enabled=False)
    now = time.time()
    fired: list[str] = []

    async def fake_run(j: dict) -> None:
        fired.append(str(j.get("id")))

    with (
        patch.object(scheduler_module.cron_native_store, "list_jobs", return_value=[job]),
        patch.object(scheduler_module.cron_native_store, "next_due", return_value=now),
        patch.object(sched, "_run_job", side_effect=fake_run),
    ):
        await sched._tick()
        await asyncio.sleep(0)

    assert fired == []


@pytest.mark.asyncio
async def test_tick_cooldown_prevents_double_fire() -> None:
    """Если _last_fired стоит в пределах poll-interval — не должен fire."""
    sched = CronNativeScheduler()
    job = _make_job()
    now = time.time()
    sched._last_fired["j1"] = now - 5  # fired 5s ago, < _POLL_INTERVAL=30
    fired: list[str] = []

    async def fake_run(j: dict) -> None:
        fired.append(str(j.get("id")))

    with (
        patch.object(scheduler_module.cron_native_store, "list_jobs", return_value=[job]),
        patch.object(scheduler_module.cron_native_store, "next_due", return_value=now),
        patch.object(sched, "_run_job", side_effect=fake_run),
    ):
        await sched._tick()
        await asyncio.sleep(0)

    assert fired == [], "Cooldown должен блокировать повторный fire"


@pytest.mark.asyncio
async def test_tick_calendar_boundary_no_early_pick() -> None:
    """Edge case: cron на Monday 00:00, текущее время Sunday 23:59:30.

    due_ts = now + 30s — НЕ должен trigger (раньше early-pick на _POLL_INTERVAL=30
    мог сработать преждевременно с пограничным значением).
    """
    sched = CronNativeScheduler()
    job = _make_job()
    now = time.time()
    fired: list[str] = []

    async def fake_run(j: dict) -> None:
        fired.append(str(j.get("id")))

    with (
        patch.object(scheduler_module.cron_native_store, "list_jobs", return_value=[job]),
        patch.object(scheduler_module.cron_native_store, "next_due", return_value=now + 30),
        patch.object(sched, "_run_job", side_effect=fake_run),
    ):
        await sched._tick()
        await asyncio.sleep(0)

    assert fired == [], "Не должен делать early-pick на calendar boundary"
