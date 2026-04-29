# -*- coding: utf-8 -*-
"""
Регрессионные тесты root cause: cron inbox items дублировались и не закрывались.

Root cause (исправлен в proactive_watch.py _check_and_trace_cron_executions):
1. dedupe_key включал last_run_at_ms → каждый запуск cron создавал НОВЫЙ item.
2. status всегда "open" → успешные cron jobs никогда не закрывались.

После фикса:
- dedupe_key = "proactive:cron_run:{job_id}" (без timestamp)
- status="done" если last_status in ok-группе
- повторный запуск того же job → upsert одного и того же item, а не создание нового
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.inbox_service import InboxService

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_job(job_id: str, job_name: str, last_run_at_ms: int, last_status: str) -> dict[str, Any]:
    return {
        "id": job_id,
        "name": job_name,
        "state": {
            "lastRunAtMs": last_run_at_ms,
            "lastStatus": last_status,
        },
    }


# ---------------------------------------------------------------------------
# Тесты: inbox_service dedupe через upsert_item
# ---------------------------------------------------------------------------


def test_upsert_same_dedupe_key_no_duplicates(tmp_path: Path) -> None:
    """Повторный upsert с тем же dedupe_key не создаёт второй item."""
    svc = InboxService(state_path=tmp_path / "inbox.json")

    r1 = svc.upsert_item(
        dedupe_key="proactive:cron_run:job-abc",
        kind="proactive_action",
        source="krab-internal",
        title="Cron job выполнен: test",
        body="первый запуск",
        status="done",
    )
    r2 = svc.upsert_item(
        dedupe_key="proactive:cron_run:job-abc",
        kind="proactive_action",
        source="krab-internal",
        title="Cron job выполнен: test",
        body="второй запуск",
        status="done",
    )

    assert r1["created"] is True
    assert r2["created"] is False
    # Ровно один item в inbox
    items = svc.list_items(limit=50)
    cron_items = [i for i in items if i["dedupe_key"] == "proactive:cron_run:job-abc"]
    assert len(cron_items) == 1, f"Ожидался 1 item, получено {len(cron_items)}"


def test_ok_cron_job_creates_done_item(tmp_path: Path) -> None:
    """Успешный cron job (status=ok) должен создавать item с status='done', не 'open'."""
    svc = InboxService(state_path=tmp_path / "inbox.json")

    svc.upsert_item(
        dedupe_key="proactive:cron_run:job-ok",
        kind="proactive_action",
        source="krab-internal",
        title="Cron job выполнен: ok-job",
        body="успешно",
        status="done",  # фикс: ok → done
    )

    items = svc.list_items(limit=50)
    cron_items = [i for i in items if i["dedupe_key"] == "proactive:cron_run:job-ok"]
    assert len(cron_items) == 1
    assert cron_items[0]["status"] == "done", (
        f"ok-job должен иметь status='done', получено '{cron_items[0]['status']}'"
    )


def test_failed_cron_job_remains_open(tmp_path: Path) -> None:
    """Упавший cron job (status=error) должен оставаться open для owner review."""
    svc = InboxService(state_path=tmp_path / "inbox.json")

    svc.upsert_item(
        dedupe_key="proactive:cron_run:job-fail",
        kind="proactive_action",
        source="krab-internal",
        title="Cron job выполнен: fail-job",
        body="ошибка",
        severity="warning",
        status="open",
    )

    items = svc.list_items(status="open", limit=50)
    cron_items = [i for i in items if i["dedupe_key"] == "proactive:cron_run:job-fail"]
    assert len(cron_items) == 1
    assert cron_items[0]["status"] == "open"


def test_ok_then_fail_then_ok_no_duplicates(tmp_path: Path) -> None:
    """Три запуска одного job: ok → fail → ok не создают три разных item."""
    svc = InboxService(state_path=tmp_path / "inbox.json")
    dedupe = "proactive:cron_run:job-multi"

    svc.upsert_item(dedupe_key=dedupe, kind="proactive_action", source="krab-internal",
                    title="job", body="run1", status="done")
    svc.upsert_item(dedupe_key=dedupe, kind="proactive_action", source="krab-internal",
                    title="job", body="run2", status="open", severity="warning")
    svc.upsert_item(dedupe_key=dedupe, kind="proactive_action", source="krab-internal",
                    title="job", body="run3", status="done")

    all_items = svc.list_items(limit=100)
    cron_items = [i for i in all_items if i["dedupe_key"] == dedupe]
    assert len(cron_items) == 1, f"Ожидался 1 item (upsert), получено {len(cron_items)}"
    # Последний upsert → status='done'
    assert cron_items[0]["status"] == "done"


# ---------------------------------------------------------------------------
# Тест: _check_and_trace_cron_executions реальная логика
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_and_trace_cron_executions_no_duplicates(tmp_path: Path) -> None:
    """
    _check_and_trace_cron_executions: второй вызов с тем же last_run_at_ms
    не создаёт второй open item.
    """
    from src.core import proactive_watch as pw_module
    from src.core.proactive_watch import ProactiveWatchService

    jobs_v1 = [_make_job("job1", "Daily digest", 1_000_000, "ok")]
    jobs_v2 = [_make_job("job1", "Daily digest", 1_000_000, "ok")]  # тот же timestamp

    svc = ProactiveWatchService(state_path=tmp_path / "watch.json")
    inbox_svc = InboxService(state_path=tmp_path / "inbox.json")

    with (
        patch.object(pw_module, "inbox_service", inbox_svc),
        patch(f"{pw_module.__name__}._fetch_openclaw_cron_jobs", new=AsyncMock(return_value=jobs_v1)),
    ):
        state1 = {}
        runs1 = await svc._check_and_trace_cron_executions(state1)

    with (
        patch.object(pw_module, "inbox_service", inbox_svc),
        patch(f"{pw_module.__name__}._fetch_openclaw_cron_jobs", new=AsyncMock(return_value=jobs_v2)),
    ):
        state2 = {"last_cron_runs": runs1}
        runs2 = await svc._check_and_trace_cron_executions(state2)

    all_items = inbox_svc.list_items(limit=100)
    cron_items = [i for i in all_items if "job1" in i.get("dedupe_key", "")]
    assert len(cron_items) == 1, (
        f"Повторный вызов с тем же timestamp не должен создавать дубль, получено {len(cron_items)}"
    )


@pytest.mark.asyncio
async def test_check_and_trace_ok_job_creates_done_not_open(tmp_path: Path) -> None:
    """
    _check_and_trace_cron_executions: ok-статус job создаёт item с status='done'.
    Это root cause фикс — до него было status='open' всегда.
    """
    from src.core import proactive_watch as pw_module
    from src.core.proactive_watch import ProactiveWatchService

    jobs = [_make_job("job-ok-test", "Weekly report", 2_000_000, "ok")]

    svc = ProactiveWatchService(state_path=tmp_path / "watch.json")
    inbox_svc = InboxService(state_path=tmp_path / "inbox.json")

    with (
        patch.object(pw_module, "inbox_service", inbox_svc),
        patch(f"{pw_module.__name__}._fetch_openclaw_cron_jobs", new=AsyncMock(return_value=jobs)),
    ):
        await svc._check_and_trace_cron_executions({})

    all_items = inbox_svc.list_items(limit=100)
    cron_items = [i for i in all_items if "job-ok-test" in i.get("dedupe_key", "")]
    assert len(cron_items) == 1
    assert cron_items[0]["status"] == "done", (
        f"ok-job должен иметь status='done' (а не 'open'), получено '{cron_items[0]['status']}'"
    )


@pytest.mark.asyncio
async def test_check_and_trace_new_run_upserts_same_item(tmp_path: Path) -> None:
    """
    Два последовательных запуска job с разными timestamp → один item (upsert),
    а не два разных open item.

    Это проверяет что dedupe_key больше НЕ включает timestamp.
    """
    from src.core import proactive_watch as pw_module
    from src.core.proactive_watch import ProactiveWatchService

    svc = ProactiveWatchService(state_path=tmp_path / "watch.json")
    inbox_svc = InboxService(state_path=tmp_path / "inbox.json")

    jobs_run1 = [_make_job("job-seq", "Seq job", 1_111_000, "ok")]
    jobs_run2 = [_make_job("job-seq", "Seq job", 2_222_000, "ok")]  # новый timestamp

    with (
        patch.object(pw_module, "inbox_service", inbox_svc),
        patch(f"{pw_module.__name__}._fetch_openclaw_cron_jobs", new=AsyncMock(return_value=jobs_run1)),
    ):
        runs = await svc._check_and_trace_cron_executions({})

    with (
        patch.object(pw_module, "inbox_service", inbox_svc),
        patch(f"{pw_module.__name__}._fetch_openclaw_cron_jobs", new=AsyncMock(return_value=jobs_run2)),
    ):
        await svc._check_and_trace_cron_executions({"last_cron_runs": runs})

    all_items = inbox_svc.list_items(limit=100)
    cron_items = [i for i in all_items if "job-seq" in i.get("dedupe_key", "")]
    assert len(cron_items) == 1, (
        f"Два запуска → один upserted item, получено {len(cron_items)}"
    )
