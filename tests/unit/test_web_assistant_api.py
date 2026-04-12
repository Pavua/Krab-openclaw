# -*- coding: utf-8 -*-
"""
Тесты assistant/chat API endpoints web-панели Krab.

Покрываем три endpoint'а:
  GET  /api/assistant/capabilities
  POST /api/assistant/query
  POST /api/assistant/attachment
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp


# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------


class _FakeRouter:
    """Минимальный роутер — не делает внешних вызовов."""

    def __init__(self) -> None:
        self.force_mode = None
        self.models = {"chat": "google/gemini-test"}

    def set_force_mode(self, mode: str):
        self.force_mode = f"force_{mode}" if mode != "auto" else None
        return {"ok": True, "mode": self.force_mode or "auto"}

    async def route_query(self, **kwargs):
        _ = kwargs
        return "Ответ от заглушки"

    def classify_task_profile(self, prompt: str, task_type: str = "chat"):
        _ = prompt
        return task_type

    def get_profile_recommendation(self, profile: str):
        return {"profile": profile, "recommended_model": "google/gemini-test"}

    def get_task_preflight(self, **kwargs):
        preferred_model = str(kwargs.get("preferred_model") or "").strip()
        model = preferred_model or "google/gemini-test"
        channel = "cloud" if "/" in model else "local"
        return {
            "profile": str(kwargs.get("task_type") or "chat"),
            "execution": {"model": model, "channel": channel, "force_mode": "auto"},
            "reasons": ["Тест"],
            "local_available": True,
        }

    def get_last_route(self):
        return {
            "route_reason": "cloud_primary",
            "channel": "cloud",
            "provider": "google",
            "model": "google/gemini-test",
            "status": "ok",
        }


def _make_client() -> TestClient:
    """Создаёт TestClient с минимальными зависимостями."""
    app = WebApp(
        deps={"router": _FakeRouter(), "openclaw_client": None, "black_box": None},
        host="127.0.0.1",
        port=18080,
    )
    return TestClient(app.app)


# ---------------------------------------------------------------------------
# GET /api/assistant/capabilities
# ---------------------------------------------------------------------------


def test_capabilities_returns_ok():
    """Endpoint возвращает ok=True и обязательные поля."""
    client = _make_client()
    resp = client.get("/api/assistant/capabilities")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["mode"] == "web_native"


def test_capabilities_includes_endpoint_fields():
    """Capabilities содержит ссылки на query и attachment endpoint'ы."""
    client = _make_client()
    data = client.get("/api/assistant/capabilities").json()
    assert "/api/assistant/query" in data["endpoint"]
    assert "/api/assistant/attachment" in data["attachment_endpoint"]


def test_capabilities_lists_task_types():
    """Capabilities перечисляет поддерживаемые типы задач."""
    client = _make_client()
    data = client.get("/api/assistant/capabilities").json()
    task_types = data.get("task_types", [])
    assert "chat" in task_types
    assert "coding" in task_types
    assert len(task_types) >= 4


def test_capabilities_no_auth_required():
    """GET /api/assistant/capabilities доступен без ключа."""
    client = _make_client()
    resp = client.get("/api/assistant/capabilities")
    # Не должно быть 401/403
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/assistant/query
# ---------------------------------------------------------------------------


def test_query_basic_success():
    """Простой запрос возвращает ok=True и reply."""
    client = _make_client()
    resp = client.post("/api/assistant/query", json={"prompt": "Привет"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "reply" in data
    assert len(data["reply"]) > 0


def test_query_empty_prompt_returns_400():
    """Пустой prompt должен возвращать 400."""
    client = _make_client()
    resp = client.post("/api/assistant/query", json={"prompt": ""})
    assert resp.status_code == 400


def test_query_missing_prompt_returns_400():
    """Отсутствие prompt в теле должно возвращать 400."""
    client = _make_client()
    resp = client.post("/api/assistant/query", json={})
    assert resp.status_code == 400


def test_query_force_mode_local():
    """force_mode=local сохраняется в effective_force_mode ответа."""
    client = _make_client()
    resp = client.post(
        "/api/assistant/query",
        json={"prompt": "тест", "force_mode": "local"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["effective_force_mode"] == "local"


def test_query_default_force_mode_is_auto():
    """Если force_mode не задан — effective_force_mode == 'auto'."""
    client = _make_client()
    resp = client.post("/api/assistant/query", json={"prompt": "тест"})
    assert resp.status_code == 200
    assert resp.json()["effective_force_mode"] == "auto"


def test_query_returns_last_route():
    """Ответ содержит last_route из роутера."""
    client = _make_client()
    resp = client.post("/api/assistant/query", json={"prompt": "маршрут?"})
    data = resp.json()
    assert "last_route" in data
    assert data["last_route"]["channel"] == "cloud"
    assert data["last_route"]["model"] == "google/gemini-test"


def test_query_model_command_presets():
    """!model presets возвращает command_mode=True."""
    client = _make_client()
    resp = client.post("/api/assistant/query", json={"prompt": "!model presets"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data.get("command_mode") is True


def test_query_model_command_force_cloud():
    """!model cloud переключает режим через роутер."""
    client = _make_client()
    resp = client.post("/api/assistant/query", json={"prompt": "!model cloud"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data.get("command_mode") is True


def test_query_idempotency_key_dedup():
    """Два запроса с одним X-Idempotency-Key возвращают одинаковый ответ."""
    client = _make_client()
    headers = {"X-Idempotency-Key": "test-idem-42"}
    payload = {"prompt": "идемпотентность"}
    resp1 = client.post("/api/assistant/query", json=payload, headers=headers)
    resp2 = client.post("/api/assistant/query", json=payload, headers=headers)
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    # Оба ответа должны быть идентичны
    assert resp1.json()["reply"] == resp2.json()["reply"]


def test_query_without_router_returns_503():
    """Если роутер не задан — возвращать 503."""
    app = WebApp(
        deps={"router": None, "openclaw_client": None, "black_box": None},
        host="127.0.0.1",
        port=18080,
    )
    client = TestClient(app.app)
    resp = client.post("/api/assistant/query", json={"prompt": "тест"})
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/assistant/attachment
# ---------------------------------------------------------------------------


def test_attachment_text_file_upload():
    """Загрузка текстового файла возвращает ok=True и attachment."""
    client = _make_client()
    content = b"Hello, Krab!"
    resp = client.post(
        "/api/assistant/attachment",
        files={"file": ("test.txt", io.BytesIO(content), "text/plain")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "attachment" in data


def test_attachment_empty_file_returns_400():
    """Пустой файл должен возвращать 400."""
    client = _make_client()
    resp = client.post(
        "/api/assistant/attachment",
        files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
    )
    assert resp.status_code == 400
