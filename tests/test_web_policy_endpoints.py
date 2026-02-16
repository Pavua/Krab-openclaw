# -*- coding: utf-8 -*-
"""Тесты новых web-endpoints policy/queue/reactions/mood."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp


class _DummyRouter:
    """Минимальный роутер для инициализации WebApp."""

    rag = None

    def get_model_info(self):
        return {"ok": True}


class _DummyBlackBox:
    """Минимальный black-box для /api/stats."""

    def get_stats(self):
        return {"events": 0}


class _DummyQueue:
    """Заглушка очереди."""

    def get_stats(self):
        return {"queued_total": 2, "active_chats": 1, "max_per_chat": 50}


class _DummyAiRuntime:
    """Заглушка runtime-политики."""

    def __init__(self):
        self.queue_manager = _DummyQueue()

    def get_policy_snapshot(self):
        return {
            "queue_enabled": True,
            "reaction_learning_enabled": True,
            "chat_mood_enabled": True,
            "auto_reactions_enabled": True,
            "queue": self.queue_manager.get_stats(),
            "guardrails": {},
        }

    def get_context_snapshot(self, chat_id: int):
        return {
            "chat_id": int(chat_id),
            "context_messages": 12,
            "prompt_length_chars": 900,
        }

    def get_context_snapshots(self):
        return {
            "100": {"chat_id": 100, "context_messages": 7, "prompt_length_chars": 320},
            "200": {"chat_id": 200, "context_messages": 4, "prompt_length_chars": 150},
        }


class _DummyReactionEngine:
    """Заглушка движка реакций."""

    def get_reaction_stats(self, chat_id=None):
        return {"total": 3, "positive": 2, "negative": 1, "neutral": 0, "chat_id": chat_id}

    def get_chat_mood(self, chat_id: int):
        return {"chat_id": chat_id, "label": "positive", "avg": 0.4, "events": 7, "top_emojis": []}


def _build_client() -> TestClient:
    deps = {
        "router": _DummyRouter(),
        "black_box": _DummyBlackBox(),
        "ai_runtime": _DummyAiRuntime(),
        "reaction_engine": _DummyReactionEngine(),
    }
    web = WebApp(deps=deps, port=8999, host="127.0.0.1")
    return TestClient(web.app)


def test_policy_endpoint_returns_runtime_snapshot() -> None:
    client = _build_client()
    response = client.get("/api/policy")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["policy"]["queue_enabled"] is True


def test_queue_endpoint_returns_queue_stats() -> None:
    client = _build_client()
    response = client.get("/api/queue")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["queue"]["queued_total"] == 2


def test_reactions_stats_endpoint_returns_data() -> None:
    client = _build_client()
    response = client.get("/api/reactions/stats", params={"chat_id": 777})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["stats"]["chat_id"] == 777


def test_mood_endpoint_returns_chat_profile() -> None:
    client = _build_client()
    response = client.get("/api/mood/12345")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["mood"]["chat_id"] == 12345
    assert payload["mood"]["label"] == "positive"


def test_ctx_endpoint_returns_single_chat_snapshot() -> None:
    client = _build_client()
    response = client.get("/api/ctx", params={"chat_id": 77})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["context"]["chat_id"] == 77


def test_ctx_endpoint_returns_all_snapshots() -> None:
    client = _build_client()
    response = client.get("/api/ctx")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert "100" in payload["contexts"]
