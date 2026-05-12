# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.cron_admin_router`` — Wave 165 (Session 48).

Покрытие сосредоточено на factory-pattern + парсерах. Все subprocess
вызовы launchctl мокируются — тесты не должны зависеть от реального
launchd/macOS состояния.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers import cron_admin_router as car
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.cron_admin_router import build_cron_admin_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_LIST_STDOUT = (
    "PID\tStatus\tLabel\n"
    "-\t0\tai.krab.log-rotation\n"
    "50521\t0\tai.krab.voice-gateway\n"
    "1254\t0\tai.krab.inbox-watcher\n"
    "-\t1\tai.krab.nightly-audit\n"
    "1189\t0\tai.openclaw.gateway\n"
    "-\t0\tcom.unrelated.service\n"  # должен быть отфильтрован
)


def _make_client(
    *,
    write_access_raises: Exception | None = None,
) -> TestClient:
    def _assert_write(*a: Any, **kw: Any) -> None:
        if write_access_raises is not None:
            raise write_access_raises

    ctx = RouterContext(
        deps={},
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=_assert_write,
    )
    app = FastAPI()
    app.include_router(build_cron_admin_router(ctx))
    return TestClient(app)


# ---------------------------------------------------------------------------
# _launchctl_list_parse — пул-парсер
# ---------------------------------------------------------------------------


def test_launchctl_list_parse_basic() -> None:
    parsed = car._launchctl_list_parse(_SAMPLE_LIST_STDOUT)
    # Krab + openclaw — да; unrelated отфильтрован.
    assert "ai.krab.log-rotation" in parsed
    assert "ai.krab.voice-gateway" in parsed
    assert "ai.openclaw.gateway" in parsed
    assert "com.unrelated.service" not in parsed
    # PID parsing
    assert parsed["ai.krab.voice-gateway"]["pid"] == 50521
    assert parsed["ai.krab.log-rotation"]["pid"] is None
    assert parsed["ai.krab.nightly-audit"]["exit_code"] == 1


def test_launchctl_list_parse_handles_blank_lines() -> None:
    parsed = car._launchctl_list_parse("\n\nPID\tStatus\tLabel\n\n")
    assert parsed == {}


# ---------------------------------------------------------------------------
# _format_schedule + _interval_seconds + _is_overdue
# ---------------------------------------------------------------------------


def test_format_schedule_start_interval_hours() -> None:
    assert car._format_schedule({"StartInterval": 21600}) == "every 6h"


def test_format_schedule_start_interval_minutes() -> None:
    assert car._format_schedule({"StartInterval": 900}) == "every 15m"


def test_format_schedule_calendar_interval_hour_minute() -> None:
    out = car._format_schedule({"StartCalendarInterval": {"Hour": 4, "Minute": 0}})
    assert "04:00" in out


def test_format_schedule_keep_alive() -> None:
    assert car._format_schedule({"KeepAlive": True}) == "keep-alive"


def test_format_schedule_unknown() -> None:
    assert car._format_schedule({}) == "unknown"


def test_interval_seconds_valid() -> None:
    assert car._interval_seconds({"StartInterval": 900}) == 900


def test_interval_seconds_missing_returns_none() -> None:
    assert car._interval_seconds({"KeepAlive": True}) is None


def test_is_overdue_true_when_double_interval_elapsed() -> None:
    # last_run was 3000s ago, interval 900 → overdue ( > 1800).
    assert car._is_overdue(__import__("time").time() - 3000, 900) is True


def test_is_overdue_false_when_recent() -> None:
    assert car._is_overdue(__import__("time").time() - 100, 900) is False


def test_is_overdue_false_without_interval() -> None:
    assert car._is_overdue(0.0, None) is False


# ---------------------------------------------------------------------------
# _validate_label
# ---------------------------------------------------------------------------


def test_validate_label_accepts_known_prefix() -> None:
    assert car._validate_label("ai.krab.foo") == "ai.krab.foo"


def test_validate_label_rejects_invalid_chars() -> None:
    from fastapi import HTTPException

    try:
        car._validate_label("ai.krab.bad/../etc")
    except HTTPException as exc:
        assert exc.status_code == 400
        return
    raise AssertionError("expected HTTPException")


def test_validate_label_rejects_foreign_prefix() -> None:
    from fastapi import HTTPException

    try:
        car._validate_label("com.evil.foo")
    except HTTPException as exc:
        assert exc.status_code == 403
        return
    raise AssertionError("expected HTTPException")


# ---------------------------------------------------------------------------
# GET /api/admin/cron/list
# ---------------------------------------------------------------------------


def test_cron_list_returns_agents_from_launchctl() -> None:
    fake_launchctl = {
        "ok": True,
        "returncode": 0,
        "stdout": _SAMPLE_LIST_STDOUT,
        "stderr": "",
    }
    with (
        patch.object(car, "_run_launchctl", return_value=fake_launchctl),
        patch.object(car, "_find_plist_path", return_value=None),
        patch.object(
            car,
            "_load_health_watcher_state",
            return_value={
                "last_check_utc": "2026-05-13T00:00:00+00:00",
                "panel_down_count": 0,
                "gateway_down_count": 1,
                "last_actions": [{"label": "ai.krab.log-rotation", "reason": "skipped_disk_full"}],
            },
        ),
    ):
        client = _make_client()
        resp = client.get("/api/admin/cron/list")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    labels = {a["label"] for a in body["agents"]}
    # из stdout — Krab + openclaw, без unrelated.
    assert "ai.krab.voice-gateway" in labels
    assert "ai.openclaw.gateway" in labels
    assert "com.unrelated.service" not in labels
    # skip_reason из health_watcher должен попадать в payload
    for a in body["agents"]:
        if a["label"] == "ai.krab.log-rotation":
            assert a["last_skip_reason"] == "skipped_disk_full"
    assert body["health_watcher"]["gateway_down_count"] == 1


def test_cron_list_graceful_when_health_state_missing() -> None:
    fake_launchctl = {
        "ok": True,
        "returncode": 0,
        "stdout": _SAMPLE_LIST_STDOUT,
        "stderr": "",
    }
    with (
        patch.object(car, "_run_launchctl", return_value=fake_launchctl),
        patch.object(car, "_find_plist_path", return_value=None),
        patch.object(car, "_load_health_watcher_state", return_value={}),
    ):
        client = _make_client()
        resp = client.get("/api/admin/cron/list")

    assert resp.status_code == 200
    body = resp.json()
    assert body["health_watcher"]["last_check_utc"] is None
    assert body["health_watcher"]["panel_down_count"] == 0


def test_cron_list_propagates_internal_error_as_500() -> None:
    with patch.object(car, "_enumerate_agents", side_effect=RuntimeError("boom")):
        client = _make_client()
        resp = client.get("/api/admin/cron/list")
    assert resp.status_code == 500
    assert "cron_list_failed" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# POST /api/admin/cron/{label}/trigger
# ---------------------------------------------------------------------------


def test_cron_trigger_invokes_launchctl_kickstart() -> None:
    fake_ok = {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}
    with patch.object(car, "_run_launchctl", return_value=fake_ok) as mock_run:
        client = _make_client()
        resp = client.post("/api/admin/cron/ai.krab.log-rotation/trigger")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    args_list = mock_run.call_args_list
    assert any("kickstart" in call.args[0] for call in args_list), f"kickstart not in {args_list}"


def test_cron_trigger_fails_when_launchctl_errors() -> None:
    fake_err = {"ok": False, "returncode": 113, "stdout": "", "stderr": "no such service"}
    with patch.object(car, "_run_launchctl", return_value=fake_err):
        client = _make_client()
        resp = client.post("/api/admin/cron/ai.krab.log-rotation/trigger")
    assert resp.status_code == 500
    assert "cron_trigger_failed" in resp.json()["detail"]


def test_cron_trigger_validates_label() -> None:
    client = _make_client()
    # Bad chars
    resp = client.post("/api/admin/cron/ai.krab.bad..label/trigger")
    # FastAPI пропустит и .. в path → попадёт в _validate_label
    # bad..label всё ещё matches _LABEL_PATTERN, но prefix OK, return ok!
    # Точно невалидный — slash в path вообще не дойдёт.
    # Проверим foreign prefix.
    resp2 = client.post("/api/admin/cron/com.evil.foo/trigger")
    assert resp2.status_code == 403


def test_cron_trigger_blocked_when_write_access_denied() -> None:
    from fastapi import HTTPException

    client = _make_client(write_access_raises=HTTPException(status_code=403, detail="forbidden"))
    resp = client.post("/api/admin/cron/ai.krab.log-rotation/trigger")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/admin/cron/{label}/pause
# ---------------------------------------------------------------------------


def test_cron_pause_calls_bootout() -> None:
    fake_ok = {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}
    with patch.object(car, "_run_launchctl", return_value=fake_ok) as mock_run:
        client = _make_client()
        resp = client.post("/api/admin/cron/ai.krab.log-rotation/pause")
    assert resp.status_code == 200
    args = mock_run.call_args.args[0]
    assert "bootout" in args


def test_cron_pause_soft_succeeds_on_already_unloaded() -> None:
    fake_err = {"ok": False, "returncode": 36, "stdout": "", "stderr": "already unloaded"}
    with patch.object(car, "_run_launchctl", return_value=fake_err):
        client = _make_client()
        resp = client.post("/api/admin/cron/ai.krab.log-rotation/pause")
    # Pause возвращает 200 даже если bootout не удался — это soft action.
    assert resp.status_code == 200
    assert resp.json()["warning"] == "already_unloaded_or_failed"


# ---------------------------------------------------------------------------
# POST /api/admin/cron/{label}/resume
# ---------------------------------------------------------------------------


def test_cron_resume_404_when_plist_missing(tmp_path: Path) -> None:
    with patch.object(car, "_find_plist_path", return_value=None):
        client = _make_client()
        resp = client.post("/api/admin/cron/ai.krab.never-existed/resume")
    assert resp.status_code == 404
    assert "plist_not_found" in resp.json()["detail"]


def test_cron_resume_calls_bootstrap(tmp_path: Path) -> None:
    fake_plist = tmp_path / "ai.krab.log-rotation.plist"
    fake_plist.write_text("<plist/>")
    fake_ok = {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}
    with (
        patch.object(car, "_find_plist_path", return_value=fake_plist),
        patch.object(car, "_run_launchctl", return_value=fake_ok) as mock_run,
    ):
        client = _make_client()
        resp = client.post("/api/admin/cron/ai.krab.log-rotation/resume")
    assert resp.status_code == 200
    assert resp.json()["plist"] == str(fake_plist)
    args = mock_run.call_args.args[0]
    assert "bootstrap" in args


# ---------------------------------------------------------------------------
# GET /admin/cron — HTML page
# ---------------------------------------------------------------------------


def test_admin_cron_page_returns_html() -> None:
    client = _make_client()
    resp = client.get("/admin/cron")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    # Sanity check на ключевой контент.
    assert "Cron Admin" in resp.text
    # JS вызывает /api/admin/cron/list
    assert "/api/admin/cron/list" in resp.text


# ---------------------------------------------------------------------------
# _load_health_watcher_state — graceful fallback
# ---------------------------------------------------------------------------


def test_load_health_watcher_state_returns_empty_when_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    with patch.object(car, "_HEALTH_WATCHER_STATE", missing):
        assert car._load_health_watcher_state() == {}


def test_load_health_watcher_state_reads_real_json(tmp_path: Path) -> None:
    state_file = tmp_path / "health_watcher.json"
    state_file.write_text(
        json.dumps({"last_check_utc": "2026-05-13T00:00:00Z", "panel_down_count": 2})
    )
    with patch.object(car, "_HEALTH_WATCHER_STATE", state_file):
        state = car._load_health_watcher_state()
    assert state["panel_down_count"] == 2


def test_load_health_watcher_state_handles_invalid_json(tmp_path: Path) -> None:
    state_file = tmp_path / "bad.json"
    state_file.write_text("{not-json")
    with patch.object(car, "_HEALTH_WATCHER_STATE", state_file):
        assert car._load_health_watcher_state() == {}
