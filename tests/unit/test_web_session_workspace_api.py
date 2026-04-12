# -*- coding: utf-8 -*-
"""
Тесты session- и workspace-related API endpoint'ов web-панели Krab.

Покрываем маршруты:
  GET  /api/ops/runtime_snapshot  — deep observability срез, включая workspace_state
  POST /api/runtime/chat-session/clear  — очистка chat-session через openclaw
  GET  /api/runtime/handoff  — Anti-413 handoff bundle с полями workspace_state и telegram_session
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

WEB_KEY = "session-ws-test-secret"

# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------


class _FakeOpenClaw:
    """Минимальный OpenClaw клиент без внешних вызовов."""

    def __init__(self) -> None:
        self.cleared_sessions: list[str] = []

    def get_last_runtime_route(self) -> dict:
        return {
            "channel": "cloud",
            "provider": "google",
            "model": "google/gemini-test",
            "status": "ok",
            "error_code": None,
        }

    def get_tier_state_export(self) -> dict:
        return {"active_tier": "free", "last_error_code": None}

    async def health_check(self) -> bool:
        return True

    async def get_cloud_runtime_check(self) -> dict:
        return {"ok": True, "provider": "google", "active_tier": "free"}

    def clear_session(self, chat_id: str) -> None:
        self.cleared_sessions.append(str(chat_id))


class _FakeOpenClawNoClear:
    """OpenClaw клиент без метода clear_session — имитирует old/limited version."""

    def get_last_runtime_route(self) -> dict:
        return {"channel": "cloud", "model": "gemini", "status": "ok"}

    def get_tier_state_export(self) -> dict:
        return {"active_tier": "free"}

    async def health_check(self) -> bool:
        return True


class _FakeRouter:
    """Минимальный роутер-заглушка с openclaw_client для ops endpoint'ов."""

    _stats: dict = {"local_failures": 0, "cloud_failures": 0}
    _preflight_cache: dict = {}
    active_tier: str = "default"

    def __init__(self) -> None:
        self.openclaw_client = _FakeOpenClaw()

    def get_model_info(self) -> dict:
        return {}


class _FakeHealthClient:
    async def health_check(self) -> bool:
        return True

    async def health_report(self) -> dict:
        return {"ok": True, "status": "ok"}

    async def capabilities_report(self) -> dict:
        return {"ok": True, "detail": {}}


class _FakeUserbot:
    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}

    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": True}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": []}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False}


# ---------------------------------------------------------------------------
# Фабрика WebApp
# ---------------------------------------------------------------------------


def _make_app(
    *,
    openclaw_client=None,
    router=None,
    kraab_userbot=None,
) -> WebApp:
    deps = {
        "router": router or _FakeRouter(),
        "openclaw_client": openclaw_client or _FakeOpenClaw(),
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
        "kraab_userbot": kraab_userbot or _FakeUserbot(),
    }
    return WebApp(deps, port=18080, host="127.0.0.1")


def _client(**kwargs) -> TestClient:
    return TestClient(_make_app(**kwargs).app)


# ---------------------------------------------------------------------------
# Фиксированный snapshot Telegram session (без реального SQLite в тестах)
# ---------------------------------------------------------------------------

_FAKE_SESSION_SNAPSHOT = {
    "state": "ready",
    "session_name": "kraab",
    "session_path": "/tmp/kraab.session",
    "session_exists": True,
    "session_size_bytes": 4096,
    "wal_exists": False,
    "shm_exists": False,
    "journal_exists": False,
    "lock_files": [],
    "sqlite_quick_check_ok": True,
    "sqlite_error": "",
}

_FAKE_WORKSPACE_SNAPSHOT = {
    "ok": True,
    "workspace_dir": "/tmp/fake_workspace",
    "exists": True,
    "shared_workspace_attached": True,
    "shared_memory_ready": True,
    "memory_dir": "/tmp/fake_workspace/memory",
    "memory_dir_exists": True,
    "prompt_files": {},
    "prompt_files_present": [],
    "prompt_files_present_count": 0,
    "memory_file_count": 0,
    "recent_entries": [],
}

# ---------------------------------------------------------------------------
# GET /api/ops/runtime_snapshot
# ---------------------------------------------------------------------------


def test_ops_runtime_snapshot_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /api/ops/runtime_snapshot должен вернуть ok=True и ключевые секции."""
    monkeypatch.setattr(
        "src.modules.web_app.build_workspace_state_snapshot",
        lambda: _FAKE_WORKSPACE_SNAPSHOT,
    )
    monkeypatch.setattr(
        "src.modules.web_app.get_observability_snapshot",
        lambda: {"events_total": 0},
    )

    resp = _client().get("/api/ops/runtime_snapshot")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True


def test_ops_runtime_snapshot_contains_workspace_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /api/ops/runtime_snapshot должен включать workspace_state с полем exists."""
    monkeypatch.setattr(
        "src.modules.web_app.build_workspace_state_snapshot",
        lambda: _FAKE_WORKSPACE_SNAPSHOT,
    )
    monkeypatch.setattr(
        "src.modules.web_app.get_observability_snapshot",
        lambda: {},
    )

    data = _client().get("/api/ops/runtime_snapshot").json()
    assert "workspace_state" in data
    ws = data["workspace_state"]
    # workspace_state должен содержать базовые поля
    assert "exists" in ws
    assert ws["ok"] is True


def test_ops_runtime_snapshot_contains_operator_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /api/ops/runtime_snapshot должен включать operator_workflow."""
    monkeypatch.setattr(
        "src.modules.web_app.build_workspace_state_snapshot",
        lambda: _FAKE_WORKSPACE_SNAPSHOT,
    )
    monkeypatch.setattr(
        "src.modules.web_app.get_observability_snapshot",
        lambda: {},
    )

    data = _client().get("/api/ops/runtime_snapshot").json()
    assert "operator_workflow" in data


def test_ops_runtime_snapshot_no_router_returns_error() -> None:
    """GET /api/ops/runtime_snapshot без роутера должен вернуть ok=False."""
    # WebApp с пустым router=None
    deps = {
        "router": None,
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
    }
    app = WebApp(deps, port=18080, host="127.0.0.1")
    client = TestClient(app.app)

    resp = client.get("/api/ops/runtime_snapshot")
    assert resp.status_code == 200
    data = resp.json()
    # Без роутера endpoint возвращает {"ok": False, "error": "router_not_found"}
    assert data["ok"] is False
    assert data["error"] == "router_not_found"


# ---------------------------------------------------------------------------
# POST /api/runtime/chat-session/clear
# ---------------------------------------------------------------------------


def test_chat_session_clear_unsupported_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Endpoint должен вернуть 503, если openclaw не поддерживает clear_session."""
    monkeypatch.setenv("WEB_API_KEY", WEB_KEY)

    resp = _client(openclaw_client=_FakeOpenClawNoClear()).post(
        "/api/runtime/chat-session/clear",
        json={"chat_id": "99999999"},
        headers={"X-Krab-Web-Key": WEB_KEY},
    )

    assert resp.status_code == 503
    assert resp.json()["detail"] == "chat_session_clear_not_supported"


def test_chat_session_clear_success_returns_action_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """Успешный clear должен вернуть action='clear_chat_session' и переданный chat_id."""
    monkeypatch.setenv("WEB_API_KEY", WEB_KEY)
    monkeypatch.setattr(
        WebApp,
        "_telegram_session_snapshot",
        lambda self: _FAKE_SESSION_SNAPSHOT,
    )

    fake_oc = _FakeOpenClaw()
    resp = _client(openclaw_client=fake_oc).post(
        "/api/runtime/chat-session/clear",
        json={"chat_id": "123456789"},
        headers={"X-Krab-Web-Key": WEB_KEY},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["action"] == "clear_chat_session"
    assert data["chat_id"] == "123456789"
    # clear_session должен был быть вызван ровно один раз
    assert fake_oc.cleared_sessions == ["123456789"]


def test_chat_session_clear_runtime_after_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ответ clear должен содержать runtime_after со статусом после очистки."""
    monkeypatch.setenv("WEB_API_KEY", WEB_KEY)
    monkeypatch.setattr(
        WebApp,
        "_telegram_session_snapshot",
        lambda self: _FAKE_SESSION_SNAPSHOT,
    )

    data = (
        _client(openclaw_client=_FakeOpenClaw())
        .post(
            "/api/runtime/chat-session/clear",
            json={"chat_id": "777000"},
            headers={"X-Krab-Web-Key": WEB_KEY},
        )
        .json()
    )

    assert "runtime_after" in data
    # runtime_after должен содержать поле telegram_session_state
    assert "telegram_session_state" in data["runtime_after"]


# ---------------------------------------------------------------------------
# GET /api/runtime/handoff — workspace и session поля
# ---------------------------------------------------------------------------


def test_handoff_contains_workspace_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /api/runtime/handoff должен включать workspace_state внутри runtime-среза."""
    monkeypatch.setattr(
        "src.modules.web_app.build_workspace_state_snapshot",
        lambda: _FAKE_WORKSPACE_SNAPSHOT,
    )
    monkeypatch.setattr(
        WebApp,
        "_telegram_session_snapshot",
        lambda self: _FAKE_SESSION_SNAPSHOT,
    )

    resp = _client().get("/api/runtime/handoff", params={"probe_cloud_runtime": "0"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    # workspace_state хранится внутри runtime-секции handoff bundle
    runtime = data.get("runtime", {})
    assert "workspace_state" in runtime


def test_handoff_contains_telegram_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /api/runtime/handoff должен включать telegram_session внутри runtime."""
    monkeypatch.setattr(
        "src.modules.web_app.build_workspace_state_snapshot",
        lambda: _FAKE_WORKSPACE_SNAPSHOT,
    )
    monkeypatch.setattr(
        WebApp,
        "_telegram_session_snapshot",
        lambda self: _FAKE_SESSION_SNAPSHOT,
    )

    data = _client().get("/api/runtime/handoff", params={"probe_cloud_runtime": "0"}).json()
    runtime = data.get("runtime", {})
    # telegram_session хранится в runtime под ключом telegram_session
    assert "telegram_session" in runtime or "telegram_session_state" in runtime


def test_handoff_health_lite_workspace_attached(monkeypatch: pytest.MonkeyPatch) -> None:
    """handoff health_lite.workspace_attached должен быть True при наличии shared workspace."""
    monkeypatch.setattr(
        "src.modules.web_app.build_workspace_state_snapshot",
        lambda: {**_FAKE_WORKSPACE_SNAPSHOT, "shared_workspace_attached": True},
    )
    monkeypatch.setattr(
        WebApp,
        "_telegram_session_snapshot",
        lambda self: _FAKE_SESSION_SNAPSHOT,
    )

    data = _client().get("/api/runtime/handoff", params={"probe_cloud_runtime": "0"}).json()
    # workspace_attached — флаг верхнего уровня в health_lite
    health_lite = data.get("health_lite", {})
    assert health_lite.get("workspace_attached") is True
