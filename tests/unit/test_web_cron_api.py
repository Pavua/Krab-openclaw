# -*- coding: utf-8 -*-
"""
Тесты cron/scheduler API endpoints owner-панели Krab.

Покрываемые маршруты:
  GET  /api/openclaw/cron/status
  GET  /api/openclaw/cron/jobs
  POST /api/openclaw/cron/jobs/create
  POST /api/openclaw/cron/jobs/toggle
  POST /api/openclaw/cron/jobs/remove
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Константы и вспомогательные данные
# ---------------------------------------------------------------------------

_VALID_TOKEN = "test-secret"

_FAKE_SNAPSHOT_OK: dict[str, Any] = {
    "ok": True,
    "status": {
        "enabled": True,
        "store_path": "/tmp/cron.db",
        "jobs_total_runtime": 2,
        "next_wake_at_ms": 1712925000000,
    },
    "summary": {
        "total": 2,
        "enabled": 1,
        "disabled": 1,
        "include_all": True,
    },
    "jobs": [
        {
            "id": "job-aaa",
            "name": "daily-report",
            "enabled": True,
            "schedule_kind": "every",
            "schedule_label": "Каждые 24ч",
            "task_kind": "agent",
            "session_target": "main",
        },
        {
            "id": "job-bbb",
            "name": "hourly-check",
            "enabled": False,
            "schedule_kind": "cron",
            "schedule_label": "Cron: 0 * * * *",
            "task_kind": "system",
            "session_target": "isolated",
        },
    ],
}

_FAKE_SNAPSHOT_ERROR: dict[str, Any] = {
    "ok": False,
    "error": "openclaw_exec_failed",
    "detail": "gateway not responding",
}

_CLI_OK: dict[str, Any] = {
    "ok": True,
    "data": {"id": "job-new", "name": "test-job"},
    "raw": "created",
}

_CLI_ERROR: dict[str, Any] = {
    "ok": False,
    "error": "openclaw_exec_failed",
    "detail": "something went wrong",
    "raw": "",
}


# ---------------------------------------------------------------------------
# Фабрика приложения
# ---------------------------------------------------------------------------


def _make_app() -> WebApp:
    """WebApp с минимальными заглушками; OpenClaw CLI мокируется в тестах."""
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
    app = WebApp(deps, port=18092, host="127.0.0.1")
    return app


def _client() -> TestClient:
    return TestClient(_make_app().app)


# ---------------------------------------------------------------------------
# GET /api/openclaw/cron/status
# ---------------------------------------------------------------------------


def test_cron_status_ok() -> None:
    """GET /api/openclaw/cron/status возвращает snapshot при успешном вызове CLI."""
    app = _make_app()
    app._collect_openclaw_cron_snapshot = AsyncMock(return_value=_FAKE_SNAPSHOT_OK)  # type: ignore[attr-defined]
    client = TestClient(app.app)

    resp = client.get("/api/openclaw/cron/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["status"]["enabled"] is True
    assert data["summary"]["total"] == 2


def test_cron_status_cli_error_propagated() -> None:
    """GET /api/openclaw/cron/status пробрасывает ok=False при ошибке CLI."""
    app = _make_app()
    app._collect_openclaw_cron_snapshot = AsyncMock(return_value=_FAKE_SNAPSHOT_ERROR)  # type: ignore[attr-defined]
    client = TestClient(app.app)

    resp = client.get("/api/openclaw/cron/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "error" in data


def test_cron_status_timeout_returns_error() -> None:
    """GET /api/openclaw/cron/status возвращает ok=False при timeout."""
    import asyncio

    app = _make_app()

    async def _timeout(*_a: Any, **_kw: Any) -> dict:
        raise asyncio.TimeoutError

    app._collect_openclaw_cron_snapshot = _timeout  # type: ignore[attr-defined]
    client = TestClient(app.app)

    resp = client.get("/api/openclaw/cron/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "timeout" in data.get("error", "").lower() or "timeout" in data.get("detail", "").lower()


# ---------------------------------------------------------------------------
# GET /api/openclaw/cron/jobs
# ---------------------------------------------------------------------------


def test_cron_jobs_returns_list() -> None:
    """GET /api/openclaw/cron/jobs возвращает список jobs и summary."""
    app = _make_app()
    app._collect_openclaw_cron_snapshot = AsyncMock(return_value=_FAKE_SNAPSHOT_OK)  # type: ignore[attr-defined]
    client = TestClient(app.app)

    resp = client.get("/api/openclaw/cron/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["jobs"], list)
    assert len(data["jobs"]) == 2
    assert "summary" in data


def test_cron_jobs_include_all_false() -> None:
    """GET /api/openclaw/cron/jobs?include_all=false передаёт параметр в snapshot."""
    app = _make_app()
    snapshot_mock = AsyncMock(return_value=_FAKE_SNAPSHOT_OK)
    app._collect_openclaw_cron_snapshot = snapshot_mock  # type: ignore[attr-defined]
    client = TestClient(app.app)

    client.get("/api/openclaw/cron/jobs?include_all=false")
    # Убеждаемся, что snapshot вызван с include_all=False
    snapshot_mock.assert_awaited_once()
    _, kwargs = snapshot_mock.call_args
    assert kwargs.get("include_all") is False


# ---------------------------------------------------------------------------
# POST /api/openclaw/cron/jobs/create
# ---------------------------------------------------------------------------


def test_cron_create_success() -> None:
    """POST /api/openclaw/cron/jobs/create создаёт job и возвращает snapshot."""
    app = _make_app()
    app._run_openclaw_cli = AsyncMock(return_value=_CLI_OK)  # type: ignore[attr-defined]
    app._collect_openclaw_cron_snapshot = AsyncMock(return_value=_FAKE_SNAPSHOT_OK)  # type: ignore[attr-defined]
    client = TestClient(app.app)

    payload = {
        "name": "test-job",
        "every": "1h",
        "task_kind": "system",
        "payload_text": "health-check",
    }
    resp = client.post(
        "/api/openclaw/cron/jobs/create",
        json=payload,
        headers={"X-Krab-Web-Key": _VALID_TOKEN},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "jobs" in data


def test_cron_create_missing_name_returns_400() -> None:
    """POST /api/openclaw/cron/jobs/create без name → 400 cron_name_required."""
    client = _client()
    resp = client.post(
        "/api/openclaw/cron/jobs/create",
        json={"every": "1h", "task_kind": "system", "payload_text": "ping"},
        headers={"X-Krab-Web-Key": _VALID_TOKEN},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "cron_name_required"


def test_cron_create_invalid_task_kind_returns_400() -> None:
    """POST /api/openclaw/cron/jobs/create с неверным task_kind → 400."""
    client = _client()
    resp = client.post(
        "/api/openclaw/cron/jobs/create",
        json={"name": "x", "every": "1h", "task_kind": "INVALID", "payload_text": "ping"},
        headers={"X-Krab-Web-Key": _VALID_TOKEN},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "cron_task_kind_invalid"


def test_cron_create_no_auth_returns_403(monkeypatch) -> None:
    """POST /api/openclaw/cron/jobs/create без ключа → 403 (WEB_API_KEY задан)."""
    monkeypatch.setenv("WEB_API_KEY", _VALID_TOKEN)
    client = _client()
    resp = client.post(
        "/api/openclaw/cron/jobs/create",
        json={"name": "x", "every": "1h", "task_kind": "system", "payload_text": "ping"},
        # заголовок X-Krab-Web-Key намеренно не передан
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/openclaw/cron/jobs/toggle
# ---------------------------------------------------------------------------


def test_cron_toggle_success() -> None:
    """POST /api/openclaw/cron/jobs/toggle включает job и возвращает snapshot."""
    app = _make_app()
    app._run_openclaw_cli = AsyncMock(return_value={"ok": True, "raw": "enabled"})  # type: ignore[attr-defined]
    app._collect_openclaw_cron_snapshot = AsyncMock(return_value=_FAKE_SNAPSHOT_OK)  # type: ignore[attr-defined]
    client = TestClient(app.app)

    resp = client.post(
        "/api/openclaw/cron/jobs/toggle",
        json={"id": "job-aaa", "enabled": True},
        headers={"X-Krab-Web-Key": _VALID_TOKEN},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "jobs" in data


def test_cron_toggle_missing_id_returns_400() -> None:
    """POST /api/openclaw/cron/jobs/toggle без id → 400 cron_id_required."""
    client = _client()
    resp = client.post(
        "/api/openclaw/cron/jobs/toggle",
        json={"enabled": True},
        headers={"X-Krab-Web-Key": _VALID_TOKEN},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "cron_id_required"


def test_cron_toggle_non_bool_enabled_returns_400() -> None:
    """POST /api/openclaw/cron/jobs/toggle с enabled не-bool → 400."""
    client = _client()
    resp = client.post(
        "/api/openclaw/cron/jobs/toggle",
        json={"id": "job-aaa", "enabled": "yes"},
        headers={"X-Krab-Web-Key": _VALID_TOKEN},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "cron_enabled_bool_required"


# ---------------------------------------------------------------------------
# POST /api/openclaw/cron/jobs/remove
# ---------------------------------------------------------------------------


def test_cron_remove_success() -> None:
    """POST /api/openclaw/cron/jobs/remove удаляет job и возвращает snapshot."""
    app = _make_app()
    app._run_openclaw_cli = AsyncMock(return_value={"ok": True, "data": {}, "raw": "removed"})  # type: ignore[attr-defined]
    app._collect_openclaw_cron_snapshot = AsyncMock(return_value=_FAKE_SNAPSHOT_OK)  # type: ignore[attr-defined]
    client = TestClient(app.app)

    resp = client.post(
        "/api/openclaw/cron/jobs/remove",
        json={"id": "job-bbb"},
        headers={"X-Krab-Web-Key": _VALID_TOKEN},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "removed" in data


def test_cron_remove_missing_id_returns_400() -> None:
    """POST /api/openclaw/cron/jobs/remove без id → 400 cron_id_required."""
    client = _client()
    resp = client.post(
        "/api/openclaw/cron/jobs/remove",
        json={},
        headers={"X-Krab-Web-Key": _VALID_TOKEN},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "cron_id_required"
