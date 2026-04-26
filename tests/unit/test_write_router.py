# -*- coding: utf-8 -*-
"""
Unit tests для write_router (Phase 2 Wave J, Session 25).

Покрывают:
- POST /api/notify/toggle (200 + 403 при invalid auth)
- POST /api/silence/toggle (global + per-chat + missing chat_id + 403)

RouterContext создаётся напрямую — router self-contained, WebApp не нужен.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.write_router import build_write_router


def _build_ctx() -> RouterContext:
    return RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )


def _client(ctx: RouterContext | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(build_write_router(ctx or _build_ctx()))
    return TestClient(app)


# ---------------------------------------------------------------------------
# POST /api/notify/toggle
# ---------------------------------------------------------------------------


def test_notify_toggle_enables(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/notify/toggle с enabled=True возвращает enabled=True."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    monkeypatch.setattr("src.config.config.TOOL_NARRATION_ENABLED", False, raising=False)
    monkeypatch.setattr("src.config.config.update_setting", lambda k, v: None, raising=False)
    resp = _client().post("/api/notify/toggle", json={"enabled": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["enabled"] is True


def test_notify_toggle_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/notify/toggle с enabled=False возвращает enabled=False."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    monkeypatch.setattr("src.config.config.TOOL_NARRATION_ENABLED", True, raising=False)
    monkeypatch.setattr("src.config.config.update_setting", lambda k, v: None, raising=False)
    resp = _client().post("/api/notify/toggle", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


def test_notify_toggle_invalid_auth_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """WEB_API_KEY установлен, заголовок не передан → 403."""
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    monkeypatch.setattr("src.config.config.update_setting", lambda k, v: None, raising=False)
    resp = _client().post("/api/notify/toggle", json={"enabled": True})
    assert resp.status_code == 403


def test_notify_toggle_valid_auth_via_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """WEB_API_KEY установлен, передан корректный header → 200."""
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    monkeypatch.setattr("src.config.config.TOOL_NARRATION_ENABLED", False, raising=False)
    monkeypatch.setattr("src.config.config.update_setting", lambda k, v: None, raising=False)
    resp = _client().post(
        "/api/notify/toggle",
        json={"enabled": True},
        headers={"X-Krab-Web-Key": "secret-key"},
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


# ---------------------------------------------------------------------------
# POST /api/silence/toggle
# ---------------------------------------------------------------------------


def test_silence_toggle_global_mute(monkeypatch: pytest.MonkeyPatch) -> None:
    """global=True + не мьют → mute_global вызывается."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake_mgr = MagicMock()
    fake_mgr.is_global_muted.return_value = False
    with patch("src.core.silence_mode.silence_manager", fake_mgr):
        resp = _client().post(
            "/api/silence/toggle", json={"global": True, "minutes": 30}
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "muted_global"
    assert data["minutes"] == 30
    fake_mgr.mute_global.assert_called_once_with(minutes=30)


def test_silence_toggle_global_unmute(monkeypatch: pytest.MonkeyPatch) -> None:
    """global=True + уже мьют → unmute_global."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake_mgr = MagicMock()
    fake_mgr.is_global_muted.return_value = True
    with patch("src.core.silence_mode.silence_manager", fake_mgr):
        resp = _client().post("/api/silence/toggle", json={"global": True})
    assert resp.status_code == 200
    assert resp.json()["action"] == "unmuted_global"
    fake_mgr.unmute_global.assert_called_once()


def test_silence_toggle_per_chat_mute(monkeypatch: pytest.MonkeyPatch) -> None:
    """chat_id указан + не заглушен → mute(chat_id)."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake_mgr = MagicMock()
    fake_mgr.is_silenced.return_value = False
    with patch("src.core.silence_mode.silence_manager", fake_mgr):
        resp = _client().post(
            "/api/silence/toggle", json={"chat_id": "999", "minutes": 60}
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "muted"
    assert data["chat_id"] == "999"
    fake_mgr.mute.assert_called_once_with("999", minutes=60)


def test_silence_toggle_per_chat_unmute(monkeypatch: pytest.MonkeyPatch) -> None:
    """chat_id указан + уже заглушен → unmute(chat_id)."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake_mgr = MagicMock()
    fake_mgr.is_silenced.return_value = True
    with patch("src.core.silence_mode.silence_manager", fake_mgr):
        resp = _client().post("/api/silence/toggle", json={"chat_id": "999"})
    assert resp.status_code == 200
    assert resp.json()["action"] == "unmuted"
    fake_mgr.unmute.assert_called_once_with("999")


def test_silence_toggle_missing_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без chat_id и без global=True → ok=False + error."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake_mgr = MagicMock()
    with patch("src.core.silence_mode.silence_manager", fake_mgr):
        resp = _client().post("/api/silence/toggle", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "chat_id" in data["error"]


def test_silence_toggle_invalid_auth_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """WEB_API_KEY установлен, header не передан → 403."""
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    fake_mgr = MagicMock()
    with patch("src.core.silence_mode.silence_manager", fake_mgr):
        resp = _client().post("/api/silence/toggle", json={"global": True})
    assert resp.status_code == 403
