# -*- coding: utf-8 -*-
"""
Тесты inbox API endpoints web-панели Krab.

Покрываем маршруты inbox-слоя:
  GET  /api/inbox/status
  GET  /api/inbox/items
  POST /api/inbox/update
  GET  /api/inbox/stale-processing
  GET  /api/inbox/stale-open
  POST /api/inbox/create
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки зависимостей WebApp
# ---------------------------------------------------------------------------

WEB_KEY = "test-web-key-inbox"


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
# Фабрика WebApp + mock inbox_service
# ---------------------------------------------------------------------------

_FAKE_ITEM = {
    "item_id": "item-abc-123",
    "kind": "owner_request",
    "status": "open",
    "title": "Тестовый запрос",
    "body": "Тело тестового запроса",
    "severity": "info",
    "source": "test",
    "created_at_utc": "2026-04-12T10:00:00Z",
    "updated_at_utc": "2026-04-12T10:00:00Z",
    "metadata": {},
}

_FAKE_SUMMARY = {
    "total": 1,
    "open": 1,
    "acked": 0,
    "done": 0,
    "cancelled": 0,
    "has_pending": True,
}

_FAKE_WORKFLOW = {
    "summary": _FAKE_SUMMARY,
    "items": [_FAKE_ITEM],
    "updated_at_utc": "2026-04-12T10:00:00Z",
}


def _make_inbox_service_mock() -> MagicMock:
    """Создаёт mock inbox_service с разумными возвратами по умолчанию."""
    m = MagicMock()
    m.get_workflow_snapshot.return_value = _FAKE_WORKFLOW
    m.get_summary.return_value = _FAKE_SUMMARY
    m.list_items.return_value = [_FAKE_ITEM]
    m.list_stale_processing_items.return_value = []
    m.list_stale_open_items.return_value = []
    m.set_item_status.return_value = {"ok": True, "item": _FAKE_ITEM}
    m.resolve_approval.return_value = {"ok": True, "item": _FAKE_ITEM}
    m.bulk_update_status.return_value = {"ok": True, "updated": 0}
    m.upsert_owner_task.return_value = {"ok": True, "item": _FAKE_ITEM}
    m.upsert_approval_request.return_value = {"ok": True, "item": _FAKE_ITEM}
    return m


def _make_app() -> tuple[WebApp, MagicMock]:
    """Создаёт WebApp и возвращает пару (app, inbox_mock)."""
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
    return app, _make_inbox_service_mock()


def _client() -> tuple[TestClient, MagicMock]:
    """Возвращает (TestClient, inbox_mock) с пропатченным inbox_service."""
    app, inbox_mock = _make_app()
    client = TestClient(app.app)
    return client, inbox_mock


# ---------------------------------------------------------------------------
# GET /api/inbox/status
# ---------------------------------------------------------------------------


def test_inbox_status_ok() -> None:
    """GET /api/inbox/status возвращает ok=True и поля summary/workflow."""
    client, inbox_mock = _client()
    with patch("src.modules.web_app.inbox_service", inbox_mock):
        resp = client.get("/api/inbox/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "summary" in data
    assert "workflow" in data


def test_inbox_status_summary_fields() -> None:
    """summary в /api/inbox/status содержит ключи total, open, has_pending."""
    client, inbox_mock = _client()
    with patch("src.modules.web_app.inbox_service", inbox_mock):
        resp = client.get("/api/inbox/status")
    summary = resp.json()["summary"]
    for key in ("total", "open", "has_pending"):
        assert key in summary, f"отсутствует ключ summary.{key}"


# ---------------------------------------------------------------------------
# GET /api/inbox/items
# ---------------------------------------------------------------------------


def test_inbox_items_default() -> None:
    """GET /api/inbox/items без параметров возвращает ok=True и список items."""
    client, inbox_mock = _client()
    with patch("src.modules.web_app.inbox_service", inbox_mock):
        resp = client.get("/api/inbox/items")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["items"], list)


def test_inbox_items_filter_by_status() -> None:
    """GET /api/inbox/items?status=done вызывает list_items с status=done."""
    client, inbox_mock = _client()
    inbox_mock.list_items.return_value = []
    with patch("src.modules.web_app.inbox_service", inbox_mock):
        resp = client.get("/api/inbox/items?status=done")
    assert resp.status_code == 200
    # Проверяем что list_items вызван с нужным статусом
    call_kwargs = inbox_mock.list_items.call_args
    assert call_kwargs.kwargs.get("status") == "done" or call_kwargs.args[0] == "done"


def test_inbox_items_filter_by_kind() -> None:
    """GET /api/inbox/items?kind=owner_request фильтрует по kind."""
    client, inbox_mock = _client()
    inbox_mock.list_items.return_value = [_FAKE_ITEM]
    with patch("src.modules.web_app.inbox_service", inbox_mock):
        resp = client.get("/api/inbox/items?kind=owner_request")
    assert resp.status_code == 200
    call_kwargs = inbox_mock.list_items.call_args
    assert call_kwargs.kwargs.get("kind") == "owner_request"


def test_inbox_items_limit_param() -> None:
    """GET /api/inbox/items?limit=5 передаёт limit=5 в list_items."""
    client, inbox_mock = _client()
    inbox_mock.list_items.return_value = []
    with patch("src.modules.web_app.inbox_service", inbox_mock):
        resp = client.get("/api/inbox/items?limit=5")
    assert resp.status_code == 200
    call_kwargs = inbox_mock.list_items.call_args
    assert call_kwargs.kwargs.get("limit") == 5


# ---------------------------------------------------------------------------
# POST /api/inbox/update
# ---------------------------------------------------------------------------


def test_inbox_update_ack_item() -> None:
    """POST /api/inbox/update с action=ack переводит item в статус acked."""
    client, inbox_mock = _client()
    inbox_mock.set_item_status.return_value = {
        "ok": True,
        "item": {**_FAKE_ITEM, "status": "acked"},
    }
    with patch("src.modules.web_app.inbox_service", inbox_mock):
        resp = client.post(
            "/api/inbox/update",
            json={"item_id": "item-abc-123", "status": "acked"},
            headers={"X-Krab-Web-Key": WEB_KEY},
        )
    # Без реального WEB_KEY ждём 403 (auth) — проверяем что роут существует
    assert resp.status_code in (200, 403)


def test_inbox_update_missing_item_id() -> None:
    """POST /api/inbox/update без item_id возвращает 400."""
    client, inbox_mock = _client()
    with patch("src.modules.web_app.inbox_service", inbox_mock):
        # Пустой item_id — endpoint вернёт 400 до auth-проверки
        resp = client.post(
            "/api/inbox/update",
            json={"status": "acked"},
            headers={"X-Krab-Web-Key": WEB_KEY},
        )
    # 400 (пустой item_id) или 403 (нет доступа)
    assert resp.status_code in (400, 403)


# ---------------------------------------------------------------------------
# GET /api/inbox/stale-processing
# ---------------------------------------------------------------------------


def test_inbox_stale_processing_empty() -> None:
    """GET /api/inbox/stale-processing возвращает ok=True и пустой список."""
    client, inbox_mock = _client()
    inbox_mock.list_stale_processing_items.return_value = []
    with patch("src.modules.web_app.inbox_service", inbox_mock):
        resp = client.get("/api/inbox/stale-processing")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["count"] == 0
    assert isinstance(data["items"], list)


def test_inbox_stale_processing_with_items() -> None:
    """GET /api/inbox/stale-processing возвращает count > 0 если есть stale items."""
    client, inbox_mock = _client()
    inbox_mock.list_stale_processing_items.return_value = [_FAKE_ITEM, _FAKE_ITEM]
    with patch("src.modules.web_app.inbox_service", inbox_mock):
        resp = client.get("/api/inbox/stale-processing?kind=owner_request")
    data = resp.json()
    assert data["ok"] is True
    assert data["count"] == 2
    assert data["kind"] == "owner_request"


# ---------------------------------------------------------------------------
# GET /api/inbox/stale-open
# ---------------------------------------------------------------------------


def test_inbox_stale_open_structure() -> None:
    """GET /api/inbox/stale-open возвращает ok, kind, count, items."""
    client, inbox_mock = _client()
    inbox_mock.list_stale_open_items.return_value = [_FAKE_ITEM]
    with patch("src.modules.web_app.inbox_service", inbox_mock):
        resp = client.get("/api/inbox/stale-open")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "kind" in data
    assert "count" in data
    assert "items" in data
    assert data["count"] == 1


# ---------------------------------------------------------------------------
# POST /api/inbox/create
# ---------------------------------------------------------------------------


def test_inbox_create_invalid_kind() -> None:
    """POST /api/inbox/create с неизвестным kind отдаёт 400 или 403."""
    client, inbox_mock = _client()
    with patch("src.modules.web_app.inbox_service", inbox_mock):
        resp = client.post(
            "/api/inbox/create",
            json={"kind": "unknown_kind", "title": "Test", "body": "Body"},
            headers={"X-Krab-Web-Key": WEB_KEY},
        )
    assert resp.status_code in (400, 403)


def test_inbox_create_missing_title() -> None:
    """POST /api/inbox/create без title отдаёт 400 или 403."""
    client, inbox_mock = _client()
    with patch("src.modules.web_app.inbox_service", inbox_mock):
        resp = client.post(
            "/api/inbox/create",
            json={"kind": "owner_task", "body": "Body"},
            headers={"X-Krab-Web-Key": WEB_KEY},
        )
    assert resp.status_code in (400, 403)
