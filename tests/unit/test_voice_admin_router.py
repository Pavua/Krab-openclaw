# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.voice_admin_router`` — Wave 183 (Session 48).

Покрытие: helper-функции (TTS state, STT metrics, KE state, health roll-up),
endpoints `/api/admin/voice/*` + HTML page. Все subprocess/httpx вызовы
мокаются — тесты не зависят от реального Voice Gateway/launchd/KrabEar.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.modules.web_routers import voice_admin_router as var
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.voice_admin_router import build_voice_admin_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(*, write_access_raises: Exception | None = None) -> TestClient:
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
    app.include_router(build_voice_admin_router(ctx))
    return TestClient(app)


# ---------------------------------------------------------------------------
# _validate_voice_id
# ---------------------------------------------------------------------------


def test_validate_voice_id_accepts_edge_tts_format() -> None:
    assert var._validate_voice_id("ru-RU-DmitryNeural") == "ru-RU-DmitryNeural"


def test_validate_voice_id_accepts_english() -> None:
    assert var._validate_voice_id("en-US-JennyNeural") == "en-US-JennyNeural"


def test_validate_voice_id_rejects_empty() -> None:
    try:
        var._validate_voice_id("")
    except HTTPException as exc:
        assert exc.status_code == 400
        return
    raise AssertionError("expected HTTPException")


def test_validate_voice_id_rejects_path_traversal() -> None:
    try:
        var._validate_voice_id("../../etc/passwd")
    except HTTPException as exc:
        assert exc.status_code == 400
        return
    raise AssertionError("expected HTTPException")


def test_validate_voice_id_rejects_arbitrary_garbage() -> None:
    try:
        var._validate_voice_id("not a voice id")
    except HTTPException as exc:
        assert exc.status_code == 400
        return
    raise AssertionError("expected HTTPException")


# ---------------------------------------------------------------------------
# _iso_or_none / _age_sec
# ---------------------------------------------------------------------------


def test_iso_or_none_handles_zero() -> None:
    assert var._iso_or_none(0.0) is None
    assert var._iso_or_none(None) is None


def test_iso_or_none_formats_unix_ts() -> None:
    out = var._iso_or_none(1715000000.0)
    assert out is not None and out.startswith("2024-")


def test_age_sec_returns_positive_age() -> None:
    import time

    age = var._age_sec(time.time() - 30)
    assert age is not None and 29 <= age <= 31


def test_age_sec_clamps_future_to_zero() -> None:
    import time

    assert var._age_sec(time.time() + 100) == 0.0


def test_age_sec_handles_none() -> None:
    assert var._age_sec(None) is None
    assert var._age_sec(0.0) is None


# ---------------------------------------------------------------------------
# _collect_tts_state
# ---------------------------------------------------------------------------


def test_collect_tts_state_reads_config() -> None:
    state = var._collect_tts_state()
    # Просто sanity — функция должна вернуть полный набор полей даже на
    # реальном live-config, без crashes.
    assert "voice" in state
    assert "speed" in state
    assert "delivery" in state
    assert "blocked_chats_count" in state
    assert "blocked_chats_preview" in state
    assert "voice_cache_files" in state
    assert "voice_cache_size_bytes" in state
    assert state["tts_max_chars"] is not None
    assert isinstance(state["blocked_chats_count"], int)


def test_collect_tts_state_handles_missing_voice_cache(tmp_path: Path) -> None:
    with patch.object(var, "_VOICE_CACHE_DIR", tmp_path / "nonexistent"):
        state = var._collect_tts_state()
    assert state["voice_cache_files"] == 0
    assert state["voice_cache_size_bytes"] == 0


def test_collect_tts_state_counts_real_files(tmp_path: Path) -> None:
    (tmp_path / "a.ogg").write_bytes(b"x" * 100)
    (tmp_path / "b.mp3").write_bytes(b"y" * 250)
    (tmp_path / "c.txt").write_bytes(b"ignored")
    with patch.object(var, "_VOICE_CACHE_DIR", tmp_path):
        state = var._collect_tts_state()
    assert state["voice_cache_files"] == 2
    assert state["voice_cache_size_bytes"] == 350


# ---------------------------------------------------------------------------
# _probe_voice_gateway
# ---------------------------------------------------------------------------


def test_probe_voice_gateway_alive(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"status": "ok"}

    class _FakeClient:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *a: Any) -> None:
            pass

        async def get(self, _url: str) -> Any:
            return fake_resp

    monkeypatch.setattr(var.httpx, "AsyncClient", _FakeClient)
    info = asyncio.run(var._probe_voice_gateway())
    assert info["alive"] is True
    assert info["status_code"] == 200
    assert info["payload"] == {"status": "ok"}


def test_probe_voice_gateway_connection_refused(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    class _FakeClient:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *a: Any) -> None:
            pass

        async def get(self, _url: str) -> Any:
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(var.httpx, "AsyncClient", _FakeClient)
    info = asyncio.run(var._probe_voice_gateway())
    assert info["alive"] is False
    assert info["error"] == "connection_refused"


def test_probe_voice_gateway_timeout(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    class _FakeClient:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *a: Any) -> None:
            pass

        async def get(self, _url: str) -> Any:
            raise httpx.TimeoutException("slow")

    monkeypatch.setattr(var.httpx, "AsyncClient", _FakeClient)
    info = asyncio.run(var._probe_voice_gateway())
    assert info["alive"] is False
    assert info["error"] == "timeout"


# ---------------------------------------------------------------------------
# _collect_krab_ear_state
# ---------------------------------------------------------------------------


def test_collect_krab_ear_state_with_snapshot(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import time

    fake_snap = {
        "installed": True,
        "last_probe_ts": time.time() - 30,
        "last_success_ts": time.time() - 60,
        "last_probe_ok": True,
        "consecutive_failures": 0,
        "total_failures": 5,
        "failures_by_reason": {"timeout": 3, "5xx": 2},
    }

    # Substitute module attribute
    fake_module = MagicMock()
    fake_module.get_snapshot.return_value = fake_snap
    monkeypatch.setitem(__import__("sys").modules, "src.core.krab_ear_health_probe", fake_module)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="2\n")
        state = var._collect_krab_ear_state()

    assert state["installed"] is True
    assert state["last_probe_ok"] is True
    assert state["consecutive_failures"] == 0
    assert state["total_failures"] == 5
    assert state["failures_by_reason"] == {"timeout": 3, "5xx": 2}
    assert state["backend_process_count"] == 2


def test_collect_krab_ear_state_no_snapshot_module(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Force ImportError on snapshot import
    import sys

    monkeypatch.setitem(sys.modules, "src.core.krab_ear_health_probe", None)
    with patch("subprocess.run", side_effect=FileNotFoundError):
        state = var._collect_krab_ear_state()
    # Should not crash — graceful fallback.
    assert "ipc_socket_path" in state
    assert state["backend_process_count"] == 0


# ---------------------------------------------------------------------------
# _compute_overall_health
# ---------------------------------------------------------------------------


def test_compute_overall_health_all_ok() -> None:
    h = var._compute_overall_health(
        tts={"voice": "ru-RU-DmitryNeural"},
        voice_gateway={"alive": True},
        krab_ear={"installed": True, "last_probe_ok": True},
    )
    assert h == "ok"


def test_compute_overall_health_gateway_down() -> None:
    h = var._compute_overall_health(
        tts={"voice": "ru-RU-DmitryNeural"},
        voice_gateway={"alive": False},
        krab_ear={"installed": True, "last_probe_ok": True},
    )
    assert h == "degraded"


def test_compute_overall_health_ear_not_installed_ok() -> None:
    h = var._compute_overall_health(
        tts={"voice": "ru-RU-DmitryNeural"},
        voice_gateway={"alive": True},
        krab_ear={"installed": False, "last_probe_ok": False},
    )
    assert h == "ok"


def test_compute_overall_health_all_down() -> None:
    h = var._compute_overall_health(
        tts={"voice": None},
        voice_gateway={"alive": False},
        krab_ear={"installed": True, "last_probe_ok": False, "backend_process_count": 0},
    )
    assert h == "down"


# ---------------------------------------------------------------------------
# GET /api/admin/voice/status
# ---------------------------------------------------------------------------


def test_status_returns_complete_snapshot(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def _fake_probe() -> dict:
        return {"base_url": "http://127.0.0.1:8090", "alive": True, "latency_ms": 12.5}

    monkeypatch.setattr(var, "_probe_voice_gateway", _fake_probe)
    monkeypatch.setattr(
        var, "_collect_krab_ear_state", lambda: {"installed": True, "last_probe_ok": True}
    )
    monkeypatch.setattr(
        var, "_collect_stt_metrics", lambda: {"providers": {}, "duration_buckets": {}}
    )
    monkeypatch.setattr(var, "_collect_typing_indicator_metrics", lambda: {"started_total": 0})

    client = _make_client()
    resp = client.get("/api/admin/voice/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "health" in body
    assert "tts" in body
    assert body["voice_gateway"]["alive"] is True
    assert "krab_ear" in body
    assert "stt_metrics" in body
    assert "typing_indicator_metrics" in body


def test_status_resilient_to_probe_failure(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def _exploding() -> dict:
        raise RuntimeError("boom")

    monkeypatch.setattr(var, "_probe_voice_gateway", _exploding)
    client = _make_client()
    resp = client.get("/api/admin/voice/status")
    # 200 even with partial failures — fail-safe by design.
    assert resp.status_code == 200
    body = resp.json()
    assert body["voice_gateway"]["alive"] is False
    assert "error" in body["voice_gateway"]


# ---------------------------------------------------------------------------
# POST /api/admin/voice/restart_gateway
# ---------------------------------------------------------------------------


def test_restart_gateway_calls_launchctl_kickstart() -> None:
    fake_ok = {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}
    with patch.object(var, "_run_launchctl", return_value=fake_ok) as mock_run:
        client = _make_client()
        resp = client.post("/api/admin/voice/restart_gateway")
    assert resp.status_code == 200
    body = resp.json()
    assert body["label"] == "ai.krab.voice-gateway"
    args = mock_run.call_args.args[0]
    assert "kickstart" in args
    assert any("voice-gateway" in a for a in args)


def test_restart_gateway_fails_on_launchctl_error() -> None:
    fake_err = {"ok": False, "returncode": 113, "stdout": "", "stderr": "no such service"}
    with patch.object(var, "_run_launchctl", return_value=fake_err):
        client = _make_client()
        resp = client.post("/api/admin/voice/restart_gateway")
    assert resp.status_code == 500
    assert "voice_restart_gateway_failed" in resp.json()["detail"]


def test_restart_gateway_blocked_when_no_write_access() -> None:
    client = _make_client(write_access_raises=HTTPException(status_code=403, detail="forbidden"))
    resp = client.post("/api/admin/voice/restart_gateway")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/admin/voice/restart_ear
# ---------------------------------------------------------------------------


def test_restart_ear_calls_launchctl_kickstart() -> None:
    fake_ok = {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}
    with patch.object(var, "_run_launchctl", return_value=fake_ok) as mock_run:
        client = _make_client()
        resp = client.post("/api/admin/voice/restart_ear")
    assert resp.status_code == 200
    body = resp.json()
    assert body["label"] == "ai.krab.ear.rest"
    args = mock_run.call_args.args[0]
    assert "kickstart" in args
    assert any("ear.rest" in a for a in args)


def test_restart_ear_fails_on_launchctl_error() -> None:
    fake_err = {"ok": False, "returncode": 113, "stdout": "", "stderr": "no such service"}
    with patch.object(var, "_run_launchctl", return_value=fake_err):
        client = _make_client()
        resp = client.post("/api/admin/voice/restart_ear")
    assert resp.status_code == 500
    assert "voice_restart_ear_failed" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# POST /api/admin/voice/test_tts
# ---------------------------------------------------------------------------


def test_test_tts_requires_text() -> None:
    client = _make_client()
    resp = client.post("/api/admin/voice/test_tts", json={})
    assert resp.status_code == 400
    assert "voice_test_tts_text_required" in resp.json()["detail"]


def test_test_tts_rejects_too_long_text() -> None:
    client = _make_client()
    resp = client.post(
        "/api/admin/voice/test_tts",
        json={"text": "x" * 9999},
    )
    assert resp.status_code == 400
    assert "voice_test_tts_text_too_long" in resp.json()["detail"]


def test_test_tts_rejects_invalid_voice_id() -> None:
    client = _make_client()
    resp = client.post(
        "/api/admin/voice/test_tts",
        json={"text": "hi", "voice": "not a voice"},
    )
    assert resp.status_code == 400


def test_test_tts_calls_voice_engine(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    fake_output = tmp_path / "test.ogg"
    fake_output.write_bytes(b"\x00" * 1024)

    async def _fake_tts(
        text: str, filename: str = "voice.ogg", voice: Any = None, **kw: Any
    ) -> str:
        return str(fake_output)

    fake_module = MagicMock()
    fake_module.text_to_speech = _fake_tts
    monkeypatch.setitem(__import__("sys").modules, "src.voice_engine", fake_module)

    client = _make_client()
    resp = client.post(
        "/api/admin/voice/test_tts",
        json={"text": "Привет, тест", "voice": "ru-RU-DmitryNeural"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["size_bytes"] == 1024
    assert body["chars"] == len("Привет, тест")


def test_test_tts_blocked_when_no_write_access() -> None:
    client = _make_client(write_access_raises=HTTPException(status_code=403, detail="forbidden"))
    resp = client.post("/api/admin/voice/test_tts", json={"text": "hi"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /admin/voice — HTML page
# ---------------------------------------------------------------------------


def test_admin_voice_page_returns_html() -> None:
    client = _make_client()
    resp = client.get("/admin/voice")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "Voice Admin" in resp.text
    assert "/api/admin/voice/status" in resp.text


def test_admin_voice_page_has_action_buttons() -> None:
    client = _make_client()
    resp = client.get("/admin/voice")
    assert resp.status_code == 200
    assert "Test TTS" in resp.text
    assert "Restart Gateway" in resp.text
    assert "Restart Krab Ear" in resp.text


# ---------------------------------------------------------------------------
# _run_launchctl smoke
# ---------------------------------------------------------------------------


def test_run_launchctl_handles_missing_binary(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        var.subprocess,
        "run",
        MagicMock(side_effect=FileNotFoundError("no launchctl")),
    )
    result = var._run_launchctl(["list"])
    assert result["ok"] is False
    assert result["stderr"] == "launchctl_not_found"


def test_run_launchctl_handles_timeout(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import subprocess as _subproc

    monkeypatch.setattr(
        var.subprocess,
        "run",
        MagicMock(side_effect=_subproc.TimeoutExpired(cmd="launchctl", timeout=10)),
    )
    result = var._run_launchctl(["list"])
    assert result["ok"] is False
    assert result["stderr"] == "launchctl_timeout"
