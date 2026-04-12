# -*- coding: utf-8 -*-
"""
Тесты для /api/notify/* и /api/silence/* endpoints web-панели Krab.

Покрываем:
  GET  /api/notify/status
  POST /api/notify/toggle
  GET  /api/silence/status
  POST /api/silence/toggle  (global, per-chat, ошибка без chat_id)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------

VALID_TOKEN = "test-token-123"


class _FakeOpenClaw:
    def get_last_runtime_route(self) -> dict:
        return {
            "channel": "cloud",
            "provider": "google",
            "model": "gemini-test",
            "status": "ok",
            "error_code": None,
        }

    def get_tier_state_export(self) -> dict:
        return {"active_tier": "free", "last_error_code": None}

    async def health_check(self) -> bool:
        return True


class _FakeHealthClient:
    async def health_check(self) -> bool:
        return True

    async def health_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake"}

    async def capabilities_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake", "detail": {}}


class _DummyRouter:
    def get_model_info(self) -> dict:
        return {}


class _FakeKraab:
    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": True}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": [], "stats": {}}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}


# ---------------------------------------------------------------------------
# Фабрика WebApp
# ---------------------------------------------------------------------------


def _make_app() -> WebApp:
    """Создаёт WebApp с полным набором заглушек в deps."""
    deps = {
        "router": _DummyRouter(),
        "openclaw_client": _FakeOpenClaw(),
        "black_box": None,
        "health_service": None,
        "provisioning_service": None,
        "ai_runtime": None,
        "reaction_engine": None,
        "voice_gateway_client": _FakeHealthClient(),
        "krab_ear_client": _FakeHealthClient(),
        "perceptor": None,
        "watchdog": None,
        "queue": None,
        "kraab_userbot": _FakeKraab(),
    }
    app = WebApp(deps, port=18091, host="127.0.0.1")
    # Устанавливаем WEB_KEY для проверки write-access в тестах
    import src.config as cfg

    cfg.config.WEB_KEY = VALID_TOKEN
    return app


def _client() -> TestClient:
    return TestClient(_make_app().app)


def _auth_headers() -> dict:
    return {"X-Krab-Web-Key": VALID_TOKEN}


# ---------------------------------------------------------------------------
# GET /api/notify/status
# ---------------------------------------------------------------------------


def test_notify_status_returns_ok(monkeypatch) -> None:
    """GET /api/notify/status всегда возвращает ok=True."""
    monkeypatch.setattr("src.config.config.TOOL_NARRATION_ENABLED", True, raising=False)
    resp = _client().get("/api/notify/status")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_notify_status_has_enabled_field(monkeypatch) -> None:
    """GET /api/notify/status содержит поле enabled."""
    monkeypatch.setattr("src.config.config.TOOL_NARRATION_ENABLED", True, raising=False)
    data = _client().get("/api/notify/status").json()
    assert "enabled" in data


def test_notify_status_reflects_false(monkeypatch) -> None:
    """Если TOOL_NARRATION_ENABLED=False, enabled=False в ответе."""
    monkeypatch.setattr("src.config.config.TOOL_NARRATION_ENABLED", False, raising=False)
    data = _client().get("/api/notify/status").json()
    assert data["enabled"] is False


# ---------------------------------------------------------------------------
# POST /api/notify/toggle
# ---------------------------------------------------------------------------


def test_notify_toggle_enables(monkeypatch) -> None:
    """POST /api/notify/toggle с enabled=True возвращает enabled=True."""
    monkeypatch.setattr("src.config.config.TOOL_NARRATION_ENABLED", False, raising=False)
    monkeypatch.setattr("src.config.config.update_setting", lambda k, v: None, raising=False)
    resp = _client().post(
        "/api/notify/toggle",
        json={"enabled": True},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["enabled"] is True


def test_notify_toggle_disables(monkeypatch) -> None:
    """POST /api/notify/toggle с enabled=False возвращает enabled=False."""
    monkeypatch.setattr("src.config.config.TOOL_NARRATION_ENABLED", True, raising=False)
    monkeypatch.setattr("src.config.config.update_setting", lambda k, v: None, raising=False)
    resp = _client().post(
        "/api/notify/toggle",
        json={"enabled": False},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


def test_notify_toggle_requires_auth(monkeypatch) -> None:
    """POST /api/notify/toggle без токена должен вернуть 403 (WEB_API_KEY установлен)."""
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    monkeypatch.setattr("src.config.config.update_setting", lambda k, v: None, raising=False)
    resp = _client().post("/api/notify/toggle", json={"enabled": True})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/silence/status
# ---------------------------------------------------------------------------


def test_silence_status_ok() -> None:
    """GET /api/silence/status возвращает ok=True."""
    fake_mgr = MagicMock()
    fake_mgr.status.return_value = {
        "global_muted": False,
        "global_remaining_min": 0,
        "muted_chats": {},
        "total_muted": 0,
    }
    with patch("src.core.silence_mode.silence_manager", fake_mgr):
        resp = _client().get("/api/silence/status")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_silence_status_fields() -> None:
    """GET /api/silence/status содержит global_muted и muted_chats."""
    fake_mgr = MagicMock()
    fake_mgr.status.return_value = {
        "global_muted": True,
        "global_remaining_min": 15,
        "muted_chats": {"123": 10},
        "total_muted": 1,
    }
    with patch("src.core.silence_mode.silence_manager", fake_mgr):
        data = _client().get("/api/silence/status").json()
    assert data["global_muted"] is True
    assert "muted_chats" in data


# ---------------------------------------------------------------------------
# POST /api/silence/toggle
# ---------------------------------------------------------------------------


def test_silence_toggle_global_mute(monkeypatch) -> None:
    """POST /api/silence/toggle с global=True мьютит глобально."""
    fake_mgr = MagicMock()
    fake_mgr.is_global_muted.return_value = False  # ещё не мьют
    with patch("src.core.silence_mode.silence_manager", fake_mgr):
        resp = _client().post(
            "/api/silence/toggle",
            json={"global": True, "minutes": 30},
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["action"] == "muted_global"
    fake_mgr.mute_global.assert_called_once_with(minutes=30)


def test_silence_toggle_global_unmute(monkeypatch) -> None:
    """POST /api/silence/toggle с global=True, когда уже мьют — анмьютит."""
    fake_mgr = MagicMock()
    fake_mgr.is_global_muted.return_value = True  # уже мьют
    with patch("src.core.silence_mode.silence_manager", fake_mgr):
        resp = _client().post(
            "/api/silence/toggle",
            json={"global": True},
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "unmuted_global"
    fake_mgr.unmute_global.assert_called_once()


def test_silence_toggle_per_chat_mute() -> None:
    """POST /api/silence/toggle с chat_id мьютит конкретный чат."""
    fake_mgr = MagicMock()
    fake_mgr.is_silenced.return_value = False  # ещё не заглушен
    with patch("src.core.silence_mode.silence_manager", fake_mgr):
        resp = _client().post(
            "/api/silence/toggle",
            json={"chat_id": "999", "minutes": 60},
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["action"] == "muted"
    assert data["chat_id"] == "999"
    fake_mgr.mute.assert_called_once_with("999", minutes=60)


def test_silence_toggle_per_chat_unmute() -> None:
    """POST /api/silence/toggle с chat_id, когда уже заглушен — анмьютит."""
    fake_mgr = MagicMock()
    fake_mgr.is_silenced.return_value = True  # уже заглушен
    with patch("src.core.silence_mode.silence_manager", fake_mgr):
        resp = _client().post(
            "/api/silence/toggle",
            json={"chat_id": "999"},
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "unmuted"
    fake_mgr.unmute.assert_called_once_with("999")


def test_silence_toggle_no_chat_id_returns_error() -> None:
    """POST /api/silence/toggle без chat_id и без global=True — ошибка."""
    fake_mgr = MagicMock()
    fake_mgr.is_global_muted.return_value = False
    with patch("src.core.silence_mode.silence_manager", fake_mgr):
        resp = _client().post(
            "/api/silence/toggle",
            json={},
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "chat_id" in data["error"]


def test_silence_toggle_requires_auth(monkeypatch) -> None:
    """POST /api/silence/toggle без токена должен вернуть 403 (WEB_API_KEY установлен)."""
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    fake_mgr = MagicMock()
    fake_mgr.is_global_muted.return_value = False
    with patch("src.core.silence_mode.silence_manager", fake_mgr):
        resp = _client().post("/api/silence/toggle", json={"global": True})
    assert resp.status_code == 403
