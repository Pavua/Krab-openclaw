"""
Тесты web assistant endpoint.

Проверяем, что `/api/assistant/query` возвращает `last_route`,
который приходит из роутера (а не пустой объект).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp


class _FakeRouter:
    """Минимальный роутер для unit-теста web_app."""

    def __init__(self) -> None:
        self.force_mode = None
        self.models = {"chat": "nvidia/nemotron-3-nano"}

    def set_force_mode(self, mode: str):
        if mode == "local":
            self.force_mode = "force_local"
        elif mode == "cloud":
            self.force_mode = "force_cloud"
        else:
            self.force_mode = None
        return {"ok": True, "mode": self.force_mode or "auto"}

    async def route_query(self, **kwargs):
        _ = kwargs
        return "Тестовый ответ"

    def classify_task_profile(self, prompt: str, task_type: str = "chat"):
        _ = prompt
        return task_type

    def get_profile_recommendation(self, profile: str):
        return {"profile": profile, "recommended_model": "nvidia/nemotron-3-nano"}

    def get_task_preflight(self, **kwargs):
        preferred_model = str(kwargs.get("preferred_model") or "").strip()
        model = preferred_model or "nvidia/nemotron-3-nano"
        channel = "cloud" if model.startswith(("google/", "google-gemini-cli/", "openai/", "openai-codex/")) else "local"
        return {
            "profile": str(kwargs.get("task_type") or "chat"),
            "execution": {
                "model": model,
                "channel": channel,
                "force_mode": "auto",
            },
            "reasons": ["Использована явно запрошенная модель." if preferred_model else "Использована модель по умолчанию."],
            "local_available": True,
        }

    def get_last_route(self):
        return {
            "route_reason": "local_direct_primary",
            "route_detail": "Ответ получен напрямую из LM Studio",
            "channel": "local_direct",
            "provider": "nvidia",
            "model": "nvidia/nemotron-3-nano",
            "status": "ok",
        }


def test_assistant_query_returns_router_last_route():
    """
    API должен возвращать `last_route` из router.get_last_route(),
    чтобы UI показывал фактическую трассировку маршрута.
    """
    app = WebApp(
        deps={
            "router": _FakeRouter(),
            "openclaw_client": None,
            "black_box": None,
        },
        host="127.0.0.1",
        port=18080,
    )
    client = TestClient(app.app)

    resp = client.post(
        "/api/assistant/query",
        json={"prompt": "проверка", "force_mode": "local"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["effective_force_mode"] == "local"
    assert data["last_route"]["channel"] == "local_direct"
    assert data["last_route"]["model"] == "nvidia/nemotron-3-nano"


def test_assistant_query_model_status_uses_authoritative_route():
    """
    Если пользователь спрашивает про модель, API должен вернуть факт по last_route,
    даже если модель могла сгенерировать иной текст.
    """
    app = WebApp(
        deps={
            "router": _FakeRouter(),
            "openclaw_client": None,
            "black_box": None,
        },
        host="127.0.0.1",
        port=18080,
    )
    client = TestClient(app.app)

    resp = client.post(
        "/api/assistant/query",
        json={"prompt": "На какой модели ты работаешь сейчас?", "force_mode": "local"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "nvidia/nemotron-3-nano" in data["reply"]
    assert "local_direct" in data["reply"]


def test_assistant_query_returns_auto_force_mode_when_router_has_none():
    """
    Если force-mode не задан, API должен отдавать `auto`, а не строку `None`.
    """
    app = WebApp(
        deps={
            "router": _FakeRouter(),
            "openclaw_client": None,
            "black_box": None,
        },
        host="127.0.0.1",
        port=18080,
    )
    client = TestClient(app.app)

    resp = client.post(
        "/api/assistant/query",
        json={"prompt": "обычный запрос"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["effective_force_mode"] == "auto"


def test_assistant_query_recommendation_respects_preferred_model():
    """
    Если web-клиент явно передал preferred_model, recommendation в ответе тоже
    должен отражать этот выбор, а не старый default-profile.
    """
    app = WebApp(
        deps={
            "router": _FakeRouter(),
            "openclaw_client": None,
            "black_box": None,
        },
        host="127.0.0.1",
        port=18080,
    )
    client = TestClient(app.app)

    resp = client.post(
        "/api/assistant/query",
        json={
            "prompt": "проверка preferred",
            "preferred_model": "google-gemini-cli/gemini-3.1-pro-preview",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["recommendation"]["model"] == "google-gemini-cli/gemini-3.1-pro-preview"
    assert data["recommendation"]["recommended_model"] == "google-gemini-cli/gemini-3.1-pro-preview"
    assert data["recommendation"]["channel"] == "cloud"
