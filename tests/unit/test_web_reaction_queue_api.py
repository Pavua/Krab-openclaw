# -*- coding: utf-8 -*-
"""
Тесты для API endpoints реакций и очередей:
  GET /api/reactions/stats        — общая статистика реакций
  GET /api/reactions/stats?chat_id — статистика по конкретному чату
  GET /api/mood/{chat_id}         — mood-профиль чата
  GET /api/queue                  — состояние per-chat очередей
"""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------


class _FakeReactionEngine:
    """Минимальный stub reaction_engine."""

    def get_reaction_stats(self, chat_id: int | None = None) -> dict:
        if chat_id is not None:
            return {"chat_id": chat_id, "total": 5, "emojis": {"👍": 3, "❤️": 2}}
        return {"total": 42, "top_emoji": "👍", "chats": 3}

    def get_chat_mood(self, chat_id: int) -> dict:
        return {"chat_id": chat_id, "mood": "positive", "score": 0.8}


class _FakeQueueManager:
    """Минимальный stub queue_manager."""

    def get_stats(self) -> dict:
        return {"active": 2, "pending": 1, "chats": {"-100123": {"depth": 1}}}


class _FakeAiRuntime:
    """ai_runtime с queue_manager."""

    def __init__(self) -> None:
        self.queue_manager = _FakeQueueManager()


class _FakeDummyRouter:
    def get_model_info(self) -> dict:
        return {}


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


class _FakeKraab:
    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": True}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": [], "stats": {}}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}


class _FakeHealthClient:
    async def health_check(self) -> bool:
        return True

    async def health_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake"}

    async def capabilities_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake", "detail": {}}


# ---------------------------------------------------------------------------
# Фабрика клиентов
# ---------------------------------------------------------------------------


def _make_client(
    *,
    reaction_engine=None,
    ai_runtime=None,
) -> TestClient:
    """Создаёт TestClient с нужными зависимостями."""
    deps = {
        "router": _FakeDummyRouter(),
        "openclaw_client": _FakeOpenClaw(),
        "black_box": None,
        "health_service": None,
        "provisioning_service": None,
        "ai_runtime": ai_runtime,
        "reaction_engine": reaction_engine,
        "voice_gateway_client": _FakeHealthClient(),
        "krab_ear_client": _FakeHealthClient(),
        "perceptor": None,
        "watchdog": None,
        "queue": None,
        "kraab_userbot": _FakeKraab(),
    }
    app = WebApp(deps, port=18091, host="127.0.0.1")
    return TestClient(app.app)


# ---------------------------------------------------------------------------
# /api/reactions/stats — без reaction_engine
# ---------------------------------------------------------------------------


def test_reactions_stats_no_engine() -> None:
    """Без reaction_engine возвращает ok=False с описанием ошибки."""
    client = _make_client(reaction_engine=None)
    resp = client.get("/api/reactions/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "error" in data


# ---------------------------------------------------------------------------
# /api/reactions/stats — с reaction_engine
# ---------------------------------------------------------------------------


def test_reactions_stats_global() -> None:
    """GET /api/reactions/stats без chat_id возвращает общую статистику."""
    client = _make_client(reaction_engine=_FakeReactionEngine())
    resp = client.get("/api/reactions/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "stats" in data
    assert data["stats"]["total"] == 42


def test_reactions_stats_by_chat_id() -> None:
    """GET /api/reactions/stats?chat_id=999 возвращает статистику конкретного чата."""
    client = _make_client(reaction_engine=_FakeReactionEngine())
    resp = client.get("/api/reactions/stats?chat_id=999")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    stats = data["stats"]
    assert stats["chat_id"] == 999
    assert stats["total"] == 5


def test_reactions_stats_keys_structure() -> None:
    """Поле stats содержит ожидаемые ключи при глобальном запросе."""
    client = _make_client(reaction_engine=_FakeReactionEngine())
    resp = client.get("/api/reactions/stats")
    stats = resp.json()["stats"]
    assert "total" in stats
    assert "top_emoji" in stats


# ---------------------------------------------------------------------------
# /api/mood/{chat_id} — без reaction_engine
# ---------------------------------------------------------------------------


def test_mood_no_engine() -> None:
    """Без reaction_engine /api/mood возвращает ok=False."""
    client = _make_client(reaction_engine=None)
    resp = client.get("/api/mood/12345")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "error" in data


# ---------------------------------------------------------------------------
# /api/mood/{chat_id} — с reaction_engine
# ---------------------------------------------------------------------------


def test_mood_returns_profile() -> None:
    """GET /api/mood/42 возвращает mood-профиль для указанного чата."""
    client = _make_client(reaction_engine=_FakeReactionEngine())
    resp = client.get("/api/mood/42")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "mood" in data
    mood = data["mood"]
    assert mood["chat_id"] == 42
    assert mood["mood"] == "positive"


def test_mood_score_in_range() -> None:
    """Поле score в mood-профиле находится в диапазоне [0, 1]."""
    client = _make_client(reaction_engine=_FakeReactionEngine())
    resp = client.get("/api/mood/42")
    score = resp.json()["mood"]["score"]
    assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# /api/queue — без ai_runtime
# ---------------------------------------------------------------------------


def test_queue_no_runtime() -> None:
    """Без ai_runtime /api/queue возвращает ok=False."""
    client = _make_client(ai_runtime=None)
    resp = client.get("/api/queue")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "error" in data


# ---------------------------------------------------------------------------
# /api/queue — с ai_runtime
# ---------------------------------------------------------------------------


def test_queue_returns_stats() -> None:
    """GET /api/queue с активным queue_manager возвращает ok=True и queue stats."""
    client = _make_client(ai_runtime=_FakeAiRuntime())
    resp = client.get("/api/queue")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "queue" in data


def test_queue_stats_structure() -> None:
    """queue stats содержат поля active и pending."""
    client = _make_client(ai_runtime=_FakeAiRuntime())
    resp = client.get("/api/queue")
    queue = resp.json()["queue"]
    assert "active" in queue
    assert "pending" in queue


def test_queue_no_queue_manager() -> None:
    """ai_runtime без атрибута queue_manager возвращает ok=False."""
    fake_runtime = MagicMock(spec=[])  # без атрибута queue_manager
    client = _make_client(ai_runtime=fake_runtime)
    resp = client.get("/api/queue")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
