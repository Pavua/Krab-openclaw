# -*- coding: utf-8 -*-
"""
Тесты error-handling путей web_app.py.

Покрываем:
- 404 на несуществующих маршрутах
- 400 при отсутствии обязательных полей (POST /api/notify)
- 403 при неверном WEB_API_KEY (write-эндпоинты)
- 503 когда userbot не готов
- Некорректные тела (невалидный JSON / пустые поля) для POST-эндпоинтов
- swarm task/team not found (ok=False)
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp


# ---------------------------------------------------------------------------
# Вспомогательные заглушки
# ---------------------------------------------------------------------------


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
# Фабрика
# ---------------------------------------------------------------------------


def _make_app(*, kraab=None) -> WebApp:
    """Создаёт WebApp с заглушками."""
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
        "kraab_userbot": kraab or _FakeKraab(),
    }
    app = WebApp(deps, port=18091, host="127.0.0.1")
    return app


def _client(*, kraab=None) -> TestClient:
    return TestClient(_make_app(kraab=kraab).app)


# ---------------------------------------------------------------------------
# 1. 404 — несуществующие маршруты
# ---------------------------------------------------------------------------


def test_404_unknown_get_route() -> None:
    """GET /api/totally_unknown_route должен возвращать 404."""
    resp = _client().get("/api/totally_unknown_route")
    assert resp.status_code == 404


def test_404_unknown_post_route() -> None:
    """POST /api/nonexistent должен возвращать 404."""
    resp = _client().post("/api/nonexistent", json={})
    assert resp.status_code == 404


def test_404_unknown_nested_route() -> None:
    """GET /api/swarm/unknown/deep/path должен возвращать 404."""
    resp = _client().get("/api/swarm/unknown/deep/path")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 2. 400 — отсутствующие обязательные поля (POST /api/notify)
# ---------------------------------------------------------------------------


def test_notify_missing_text_returns_400() -> None:
    """POST /api/notify без поля text должен возвращать 400."""
    resp = _client().post("/api/notify", json={"chat_id": "12345"})
    assert resp.status_code == 400
    assert "text_required" in resp.json().get("detail", "")


def test_notify_empty_text_returns_400() -> None:
    """POST /api/notify с пустым text должен возвращать 400."""
    resp = _client().post("/api/notify", json={"text": "   ", "chat_id": "12345"})
    assert resp.status_code == 400
    assert "text_required" in resp.json().get("detail", "")


def test_notify_missing_chat_id_returns_400(monkeypatch) -> None:
    """POST /api/notify без chat_id и без env OPENCLAW_ALERT_TARGET должен вернуть 400."""
    monkeypatch.delenv("OPENCLAW_ALERT_TARGET", raising=False)
    resp = _client().post("/api/notify", json={"text": "hello"})
    assert resp.status_code == 400
    assert "chat_id_required" in resp.json().get("detail", "")


# ---------------------------------------------------------------------------
# 3. 503 — userbot не готов
# ---------------------------------------------------------------------------


def test_notify_userbot_none_returns_503() -> None:
    """POST /api/notify когда userbot=None должен возвращать 503."""
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
        "kraab_userbot": None,  # нет userbot
    }
    app = WebApp(deps, port=18092, host="127.0.0.1")
    client = TestClient(app.app)
    resp = client.post("/api/notify", json={"text": "hello", "chat_id": "12345"})
    assert resp.status_code == 503
    assert "userbot_not_ready" in resp.json().get("detail", "")


def test_notify_userbot_no_client_attr_returns_503() -> None:
    """POST /api/notify когда у userbot нет атрибута .client должен возвращать 503."""

    class _UserbotNoClient:
        pass  # нет атрибута client

    resp = _client(kraab=_UserbotNoClient()).post(
        "/api/notify", json={"text": "hello", "chat_id": "12345"}
    )
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# 4. 403 — неверный WEB_API_KEY (write-эндпоинты)
# ---------------------------------------------------------------------------


def test_notify_toggle_wrong_key_returns_403(monkeypatch) -> None:
    """POST /api/notify/toggle с неверным ключом должен возвращать 403."""
    monkeypatch.setenv("WEB_API_KEY", "mykey")
    client = _client()
    resp = client.post(
        "/api/notify/toggle",
        json={"enabled": True},
        headers={"X-Krab-Web-Key": "badkey"},
    )
    assert resp.status_code == 403
    assert "forbidden" in resp.json().get("detail", "")


def test_swarm_task_create_wrong_key_returns_403(monkeypatch) -> None:
    """POST /api/swarm/tasks/create с неверным ключом должен возвращать 403."""
    monkeypatch.setenv("WEB_API_KEY", "mykey")
    client = _client()
    resp = client.post(
        "/api/swarm/tasks/create",
        json={"team": "coders", "title": "Test task"},
        headers={"X-Krab-Web-Key": "badkey"},
    )
    assert resp.status_code == 403


def test_swarm_task_delete_wrong_key_returns_403(monkeypatch) -> None:
    """DELETE /api/swarm/task/{id} с неверным ключом должен возвращать 403."""
    monkeypatch.setenv("WEB_API_KEY", "mykey")
    client = _client()
    resp = client.delete(
        "/api/swarm/task/some_task",
        headers={"X-Krab-Web-Key": "badkey"},
    )
    assert resp.status_code == 403


def test_notify_toggle_correct_key_passes(monkeypatch) -> None:
    """POST /api/notify/toggle с верным ключом должен пройти auth (не 403)."""
    monkeypatch.setenv("WEB_API_KEY", "correct_key")
    monkeypatch.setattr("src.config.config.update_setting", lambda *a: None, raising=False)
    client = _client()
    resp = client.post(
        "/api/notify/toggle",
        json={"enabled": True},
        headers={"X-Krab-Web-Key": "correct_key"},
    )
    assert resp.status_code != 403


# ---------------------------------------------------------------------------
# 5. Некорректные тела — missing required fields для swarm task create
# ---------------------------------------------------------------------------


def test_swarm_task_create_missing_team_returns_error(monkeypatch) -> None:
    """POST /api/swarm/tasks/create без team должен вернуть ok=False."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    client = _client()
    resp = client.post("/api/swarm/tasks/create", json={"title": "Some task"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "team" in data.get("error", "").lower()


def test_swarm_task_create_missing_title_returns_error(monkeypatch) -> None:
    """POST /api/swarm/tasks/create без title должен вернуть ok=False."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    client = _client()
    resp = client.post("/api/swarm/tasks/create", json={"team": "coders"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False


# ---------------------------------------------------------------------------
# 6. swarm task/team not found
# ---------------------------------------------------------------------------


def test_swarm_task_detail_not_found() -> None:
    """GET /api/swarm/task/nonexistent_id должен вернуть ok=False и error."""
    resp = _client().get("/api/swarm/task/nonexistent_id_xyz_999")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "not found" in data.get("error", "").lower()


def test_swarm_team_info_not_found() -> None:
    """GET /api/swarm/team/nonexistent_team должен вернуть ok=False и error."""
    resp = _client().get("/api/swarm/team/nonexistent_team_xyz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "not found" in data.get("error", "").lower()


# ---------------------------------------------------------------------------
# 7. Swarm task update — missing status
# ---------------------------------------------------------------------------


def test_swarm_task_update_missing_status_returns_error(monkeypatch) -> None:
    """POST /api/swarm/task/{id}/update без status должен вернуть ok=False."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    client = _client()
    resp = client.post("/api/swarm/task/some_task_id/update", json={"result": "done"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "status" in data.get("error", "").lower()
