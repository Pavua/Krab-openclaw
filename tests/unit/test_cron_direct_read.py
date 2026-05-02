# -*- coding: utf-8 -*-
"""
Тесты для _collect_openclaw_cron_snapshot — новая реализация
прямого чтения JSON-файлов вместо CLI/WebSocket.

Покрываемые сценарии:
  - test_reads_jobs_json          — jobs.json прочитан, jobs возвращены
  - test_reads_state_json         — jobs-state.json смержен в jobs
  - test_includes_native_jobs     — нативные Krab jobs включены в результат
  - test_missing_files_graceful   — отсутствие файлов не вызывает исключений
  - test_response_format          — структура ответа ok/status/summary/jobs
  - test_include_all_false        — include_all=False фильтрует disabled jobs
  - test_state_merge_populates_last_run  — lastRunAtMs из state попадает в job
  - test_corrupted_json_graceful  — невалидный JSON не вызывает исключений
  - test_jobs_sorted              — jobs отсортированы (enabled first, then name)
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Фабрика минимального WebApp
# ---------------------------------------------------------------------------


def _make_app():
    """Создаёт WebApp с минимальными заглушками."""
    from src.modules.web_app import WebApp

    deps: dict[str, Any] = {
        "router": MagicMock(),
        "openclaw_client": MagicMock(),
        "black_box": None,
        "health_service": None,
        "provisioning_service": None,
        "ai_runtime": None,
        "reaction_engine": None,
        "voice_gateway_client": None,
        "krab_ear_client": None,
        "perceptor": None,
        "watchdog": None,
        "queue": None,
        "kraab_userbot": None,
    }
    return WebApp(deps, port=19090, host="127.0.0.1")


# ---------------------------------------------------------------------------
# Вспомогательные данные
# ---------------------------------------------------------------------------

_JOBS_JSON_DATA: dict[str, Any] = {
    "jobs": [
        {
            "id": "job-001",
            "name": "daily-digest",
            "enabled": True,
            "agentId": "main",
            "sessionTarget": "main",
            "wakeMode": "wake",
            "schedule": {"kind": "every", "everyMs": 86400000},
            "payload": {"kind": "prompt", "text": "Send digest"},
            "description": "Ежедневный дайджест",
            "updatedAtMs": 1712000000000,
            "createdAtMs": 1710000000000,
        },
        {
            "id": "job-002",
            "name": "hourly-check",
            "enabled": False,
            "agentId": "secondary",
            "sessionTarget": "isolated",
            "wakeMode": "wake",
            "schedule": {"kind": "cron", "expr": "0 * * * *", "tz": "Europe/Moscow"},
            "payload": {"kind": "system", "text": "health-check"},
            "description": "Почасовая проверка",
            "updatedAtMs": 1712000001000,
            "createdAtMs": 1710000001000,
        },
    ]
}

_STATE_JSON_DATA: dict[str, Any] = {
    "version": 1,
    "jobs": {
        "job-001": {
            "state": {
                "lastRunAtMs": 1712900000000,
                "lastStatus": "ok",
                "lastError": "",
                "consecutiveErrors": 0,
            }
        },
        "job-002": {
            "state": {
                "lastRunAtMs": 1712800000000,
                "lastStatus": "error",
                "lastError": "timeout",
                "consecutiveErrors": 3,
            }
        },
    },
}

_NATIVE_JOBS: list[dict[str, Any]] = [
    {
        "id": "native-abc",
        "cron_spec": "0 9 * * *",
        "prompt": "Morning briefing",
        "enabled": True,
        "last_run_at": "2026-04-29T09:00:00+00:00",
        "run_count": 5,
    }
]


# ---------------------------------------------------------------------------
# Утилита запуска корутины синхронно
# ---------------------------------------------------------------------------


def _run(coro):
    # Python 3.13: get_event_loop() выкидывает RuntimeError если loop'а нет.
    # asyncio.run() создаёт фresh loop сам — это правильный pattern для
    # синхронной обёртки в тестах.
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


def test_reads_jobs_json(tmp_path):
    """jobs.json читается, jobs.json->jobs возвращаются в результате."""
    jobs_path = tmp_path / ".openclaw" / "cron" / "jobs.json"
    state_path = tmp_path / ".openclaw" / "cron" / "jobs-state.json"
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    jobs_path.write_text(json.dumps(_JOBS_JSON_DATA), encoding="utf-8")
    state_path.write_text(json.dumps({"version": 1, "jobs": {}}), encoding="utf-8")

    app = _make_app()

    import pathlib

    with (
        patch.object(pathlib.Path, "home", staticmethod(lambda: tmp_path)),
        patch("src.core.cron_native_store.list_jobs", return_value=[]),
    ):
        result = _run(app._collect_openclaw_cron_snapshot())

    assert result["ok"] is True
    jobs = result["jobs"]
    # Должны быть оба OpenClaw jobs
    job_ids = {j["id"] for j in jobs}
    assert "job-001" in job_ids
    assert "job-002" in job_ids


def test_reads_state_json(tmp_path):
    """jobs-state.json мержится: last_run_at_ms и last_status попадают в job."""
    jobs_path = tmp_path / ".openclaw" / "cron" / "jobs.json"
    state_path = tmp_path / ".openclaw" / "cron" / "jobs-state.json"
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    jobs_path.write_text(json.dumps(_JOBS_JSON_DATA), encoding="utf-8")
    state_path.write_text(json.dumps(_STATE_JSON_DATA), encoding="utf-8")

    app = _make_app()

    import pathlib

    with (
        patch.object(pathlib.Path, "home", staticmethod(lambda: tmp_path)),
        patch("src.core.cron_native_store.list_jobs", return_value=[]),
    ):
        result = _run(app._collect_openclaw_cron_snapshot())

    jobs_by_id = {j["id"]: j for j in result["jobs"]}
    job1 = jobs_by_id["job-001"]
    assert job1["last_run_at_ms"] == 1712900000000
    assert job1["last_status"] == "ok"

    job2 = jobs_by_id["job-002"]
    assert job2["last_run_at_ms"] == 1712800000000
    assert job2["last_status"] == "error"
    assert job2["last_error"] == "timeout"
    assert job2["consecutive_errors"] == 3


def test_includes_native_jobs(tmp_path):
    """Нативные Krab jobs включаются в результат и имеют _source='native'."""
    jobs_path = tmp_path / ".openclaw" / "cron" / "jobs.json"
    state_path = tmp_path / ".openclaw" / "cron" / "jobs-state.json"
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    jobs_path.write_text(json.dumps({"jobs": []}), encoding="utf-8")
    state_path.write_text(json.dumps({"version": 1, "jobs": {}}), encoding="utf-8")

    app = _make_app()

    import pathlib

    with (
        patch.object(pathlib.Path, "home", staticmethod(lambda: tmp_path)),
        patch("src.core.cron_native_store.list_jobs", return_value=_NATIVE_JOBS),
    ):
        result = _run(app._collect_openclaw_cron_snapshot())

    assert result["ok"] is True
    jobs = result["jobs"]
    native_jobs = [j for j in jobs if j.get("_source") == "native"]
    assert len(native_jobs) == 1
    nj = native_jobs[0]
    assert nj["id"] == "native-abc"
    assert nj["agent_id"] == "krab-native"
    assert nj["session_target"] == "native"
    assert "Morning briefing" in nj["name"]
    # last_run_at_ms должен быть из ISO timestamp
    assert nj["last_run_at_ms"] > 0


def test_missing_files_graceful(tmp_path):
    """Отсутствие jobs.json и jobs-state.json не вызывает исключений — ok=True, jobs=[]."""
    # tmp_path не содержит .openclaw/cron/
    app = _make_app()

    import pathlib

    with (
        patch.object(pathlib.Path, "home", staticmethod(lambda: tmp_path)),
        patch("src.core.cron_native_store.list_jobs", return_value=[]),
    ):
        result = _run(app._collect_openclaw_cron_snapshot())

    assert result["ok"] is True
    assert isinstance(result["jobs"], list)
    # Без файлов и нативных jobs — пустой список
    assert len(result["jobs"]) == 0


def test_response_format(tmp_path):
    """Результат содержит ключи ok / status / summary / jobs с правильными типами."""
    jobs_path = tmp_path / ".openclaw" / "cron" / "jobs.json"
    state_path = tmp_path / ".openclaw" / "cron" / "jobs-state.json"
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    jobs_path.write_text(json.dumps(_JOBS_JSON_DATA), encoding="utf-8")
    state_path.write_text(json.dumps(_STATE_JSON_DATA), encoding="utf-8")

    app = _make_app()

    import pathlib

    with (
        patch.object(pathlib.Path, "home", staticmethod(lambda: tmp_path)),
        patch("src.core.cron_native_store.list_jobs", return_value=_NATIVE_JOBS),
    ):
        result = _run(app._collect_openclaw_cron_snapshot())

    # Верхний уровень
    assert "ok" in result
    assert "status" in result
    assert "summary" in result
    assert "jobs" in result
    assert result["ok"] is True

    # status
    status = result["status"]
    assert isinstance(status.get("enabled"), bool)
    assert "_source" in status
    assert status["_source"] == "direct_file_read"

    # summary
    summary = result["summary"]
    assert isinstance(summary["total"], int)
    assert isinstance(summary["enabled"], int)
    assert isinstance(summary["disabled"], int)
    assert isinstance(summary["openclaw_count"], int)
    assert isinstance(summary["native_count"], int)
    assert summary["openclaw_count"] == 2   # из _JOBS_JSON_DATA
    assert summary["native_count"] == 1     # из _NATIVE_JOBS
    assert summary["total"] == 3

    # каждый job имеет обязательные поля
    required_fields = {
        "id", "name", "enabled", "schedule_kind", "schedule_label",
        "last_run_at_ms", "last_status",
    }
    for job in result["jobs"]:
        for field in required_fields:
            assert field in job, f"Поле '{field}' отсутствует в job {job.get('id')}"


def test_include_all_false(tmp_path):
    """include_all=False возвращает только enabled jobs."""
    jobs_path = tmp_path / ".openclaw" / "cron" / "jobs.json"
    state_path = tmp_path / ".openclaw" / "cron" / "jobs-state.json"
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    jobs_path.write_text(json.dumps(_JOBS_JSON_DATA), encoding="utf-8")
    state_path.write_text(json.dumps({"version": 1, "jobs": {}}), encoding="utf-8")

    app = _make_app()

    import pathlib

    with (
        patch.object(pathlib.Path, "home", staticmethod(lambda: tmp_path)),
        patch("src.core.cron_native_store.list_jobs", return_value=[]),
    ):
        result = _run(app._collect_openclaw_cron_snapshot(include_all=False))

    # job-002 отключён — не должен попасть в результат
    assert result["ok"] is True
    job_ids = {j["id"] for j in result["jobs"]}
    assert "job-001" in job_ids
    assert "job-002" not in job_ids
    # summary.include_all должен отражать параметр
    assert result["summary"]["include_all"] is False


def test_state_merge_populates_last_run(tmp_path):
    """Проверяем детально, что state из jobs-state.json попадает в нужный job."""
    single_job_data = {
        "jobs": [
            {
                "id": "solo-job",
                "name": "solo",
                "enabled": True,
                "schedule": {"kind": "cron", "expr": "* * * * *"},
                "payload": {"kind": "prompt", "text": "do thing"},
            }
        ]
    }
    single_state_data = {
        "version": 1,
        "jobs": {
            "solo-job": {
                "state": {
                    "lastRunAtMs": 9876543210000,
                    "lastStatus": "ok",
                    "lastError": "",
                    "consecutiveErrors": 0,
                }
            }
        },
    }

    jobs_path = tmp_path / ".openclaw" / "cron" / "jobs.json"
    state_path = tmp_path / ".openclaw" / "cron" / "jobs-state.json"
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    jobs_path.write_text(json.dumps(single_job_data), encoding="utf-8")
    state_path.write_text(json.dumps(single_state_data), encoding="utf-8")

    app = _make_app()

    import pathlib

    with (
        patch.object(pathlib.Path, "home", staticmethod(lambda: tmp_path)),
        patch("src.core.cron_native_store.list_jobs", return_value=[]),
    ):
        result = _run(app._collect_openclaw_cron_snapshot())

    assert len(result["jobs"]) == 1
    job = result["jobs"][0]
    assert job["id"] == "solo-job"
    assert job["last_run_at_ms"] == 9876543210000
    assert job["last_status"] == "ok"


def test_corrupted_json_graceful(tmp_path):
    """Невалидный JSON в обоих файлах не вызывает исключений."""
    jobs_path = tmp_path / ".openclaw" / "cron" / "jobs.json"
    state_path = tmp_path / ".openclaw" / "cron" / "jobs-state.json"
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    jobs_path.write_text("NOT_VALID_JSON !!!", encoding="utf-8")
    state_path.write_text("{broken:", encoding="utf-8")

    app = _make_app()

    import pathlib

    with (
        patch.object(pathlib.Path, "home", staticmethod(lambda: tmp_path)),
        patch("src.core.cron_native_store.list_jobs", return_value=[]),
    ):
        result = _run(app._collect_openclaw_cron_snapshot())

    # Не крашится, возвращает ok=True с пустым списком
    assert result["ok"] is True
    assert result["jobs"] == []


def test_jobs_sorted(tmp_path):
    """Jobs отсортированы: сначала enabled, потом по имени (case-insensitive)."""
    jobs_data = {
        "jobs": [
            {
                "id": "z-job",
                "name": "zebra",
                "enabled": True,
                "schedule": {"kind": "cron", "expr": "0 * * * *"},
                "payload": {"kind": "prompt", "text": "z"},
            },
            {
                "id": "a-disabled",
                "name": "alpha",
                "enabled": False,
                "schedule": {"kind": "cron", "expr": "0 * * * *"},
                "payload": {"kind": "prompt", "text": "a"},
            },
            {
                "id": "a-job",
                "name": "apple",
                "enabled": True,
                "schedule": {"kind": "cron", "expr": "0 * * * *"},
                "payload": {"kind": "prompt", "text": "a2"},
            },
        ]
    }

    jobs_path = tmp_path / ".openclaw" / "cron" / "jobs.json"
    state_path = tmp_path / ".openclaw" / "cron" / "jobs-state.json"
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    jobs_path.write_text(json.dumps(jobs_data), encoding="utf-8")
    state_path.write_text(json.dumps({"version": 1, "jobs": {}}), encoding="utf-8")

    app = _make_app()

    import pathlib

    with (
        patch.object(pathlib.Path, "home", staticmethod(lambda: tmp_path)),
        patch("src.core.cron_native_store.list_jobs", return_value=[]),
    ):
        result = _run(app._collect_openclaw_cron_snapshot())

    jobs = result["jobs"]
    assert len(jobs) == 3
    # Enabled jobs идут первыми
    enabled_jobs = [j for j in jobs if j["enabled"]]
    disabled_jobs = [j for j in jobs if not j["enabled"]]
    assert len(enabled_jobs) == 2
    assert len(disabled_jobs) == 1
    # Disabled jobs должны идти после enabled
    enabled_indices = [jobs.index(j) for j in enabled_jobs]
    disabled_indices = [jobs.index(j) for j in disabled_jobs]
    assert max(enabled_indices) < min(disabled_indices)


def test_native_jobs_exception_graceful(tmp_path):
    """Если cron_native_store.list_jobs() бросает исключение — ok=True, native_count=0."""
    jobs_path = tmp_path / ".openclaw" / "cron" / "jobs.json"
    state_path = tmp_path / ".openclaw" / "cron" / "jobs-state.json"
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    jobs_path.write_text(json.dumps({"jobs": []}), encoding="utf-8")
    state_path.write_text(json.dumps({"version": 1, "jobs": {}}), encoding="utf-8")

    app = _make_app()

    import pathlib

    def _boom():
        raise RuntimeError("store is broken")

    with (
        patch.object(pathlib.Path, "home", staticmethod(lambda: tmp_path)),
        patch("src.core.cron_native_store.list_jobs", side_effect=_boom),
    ):
        result = _run(app._collect_openclaw_cron_snapshot())

    assert result["ok"] is True
    assert result["summary"]["native_count"] == 0
