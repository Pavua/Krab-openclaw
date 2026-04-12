# -*- coding: utf-8 -*-
"""
Тесты Telegram/chat-связанных API endpoint'ов web-панели Krab.

Покрываем:
  POST /api/notify           — отправка Telegram-уведомления
  GET  /api/reactions/stats  — сводка по реакциям
  GET  /api/mood/{chat_id}   — mood-профиль чата
  GET  /api/links            — экосистемные ссылки
  GET  /api/swarm/listeners  — статус team listeners
  POST /api/swarm/listeners/toggle — toggle listeners (с auth)
  GET  /api/silence/status   — статус тишины
  POST /api/silence/toggle   — toggle тишины (per-chat)
  GET  /api/inbox/status     — статус inbox
  GET  /api/inbox/items      — список inbox items
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------


class _FakeOpenClaw:
    """Минимальный OpenClaw без внешних вызовов."""

    def get_last_runtime_route(self) -> dict:
        return {"channel": "cloud", "provider": "google", "model": "test", "status": "ok"}

    def get_tier_state_export(self) -> dict:
        return {"active_tier": "free", "last_error_code": None}

    async def health_check(self) -> bool:
        return True


class _FakeKraab:
    """Минимальный userbot-stub."""

    # client без send_message — тесты /api/notify используют отдельный mock
    client = None

    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": True}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": [], "stats": {}}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": False}


class _FakeReactionEngine:
    """Фейковый движок реакций."""

    def get_reaction_stats(self, chat_id=None) -> dict:
        return {"total": 42, "top": ["👍", "❤️"]}

    def get_chat_mood(self, chat_id: int) -> dict:
        return {"mood": "positive", "score": 0.8, "chat_id": chat_id}


class _FakeDummyRouter:
    def get_model_info(self) -> dict:
        return {}


def _make_app(*, with_reaction_engine: bool = False) -> WebApp:
    """Создаёт WebApp с минимальным набором заглушек."""
    deps = {
        "router": _FakeDummyRouter(),
        "openclaw_client": _FakeOpenClaw(),
        "black_box": None,
        "health_service": None,
        "provisioning_service": None,
        "ai_runtime": None,
        "reaction_engine": _FakeReactionEngine() if with_reaction_engine else None,
        "voice_gateway_client": MagicMock(**{"health_check": AsyncMock(return_value=True)}),
        "krab_ear_client": MagicMock(**{"health_check": AsyncMock(return_value=True)}),
        "perceptor": None,
        "watchdog": None,
        "queue": None,
        "kraab_userbot": _FakeKraab(),
    }
    return WebApp(deps, port=18092, host="127.0.0.1")


def _client(**kw) -> TestClient:
    return TestClient(_make_app(**kw).app)


# ---------------------------------------------------------------------------
# POST /api/notify
# ---------------------------------------------------------------------------


def test_notify_missing_text_returns_400() -> None:
    """Запрос без поля text должен вернуть 400."""
    resp = _client().post("/api/notify", json={})
    assert resp.status_code == 400


def test_notify_missing_chat_id_returns_400(monkeypatch) -> None:
    """Запрос без chat_id и без env-переменной должен вернуть 400."""
    monkeypatch.delenv("OPENCLAW_ALERT_TARGET", raising=False)
    resp = _client().post("/api/notify", json={"text": "hello"})
    assert resp.status_code == 400


def test_notify_userbot_not_ready_returns_503(monkeypatch) -> None:
    """Если userbot не подключён — 503."""
    monkeypatch.setenv("OPENCLAW_ALERT_TARGET", "123456")
    # client = None у _FakeKraab, userbot.client is None → 503
    resp = _client().post("/api/notify", json={"text": "hello"})
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/reactions/stats
# ---------------------------------------------------------------------------


def test_reactions_stats_without_engine() -> None:
    """Без reaction_engine — ok=False и описание ошибки."""
    resp = _client(with_reaction_engine=False).get("/api/reactions/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "reaction_engine_not_configured" in data.get("error", "")


def test_reactions_stats_with_engine() -> None:
    """С reaction_engine — ok=True и поле stats."""
    resp = _client(with_reaction_engine=True).get("/api/reactions/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "stats" in data
    assert data["stats"]["total"] == 42


# ---------------------------------------------------------------------------
# GET /api/mood/{chat_id}
# ---------------------------------------------------------------------------


def test_mood_without_engine() -> None:
    """Без reaction_engine — ok=False."""
    resp = _client(with_reaction_engine=False).get("/api/mood/100500")
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_mood_with_engine_returns_profile() -> None:
    """С reaction_engine — ok=True и поле mood с правильным chat_id."""
    resp = _client(with_reaction_engine=True).get("/api/mood/100500")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["mood"]["chat_id"] == 100500


# ---------------------------------------------------------------------------
# GET /api/links
# ---------------------------------------------------------------------------


def test_links_returns_all_keys() -> None:
    """GET /api/links должен содержать ключевые URL из экосистемы."""
    resp = _client().get("/api/links")
    assert resp.status_code == 200
    data = resp.json()
    for key in ("dashboard", "health_api", "stats_api", "openclaw", "voice_gateway"):
        assert key in data, f"отсутствует ключ: {key}"


# ---------------------------------------------------------------------------
# GET /api/swarm/listeners
# ---------------------------------------------------------------------------


def test_swarm_listeners_status_structure() -> None:
    """GET /api/swarm/listeners возвращает ok=True и listeners_enabled."""
    with patch("src.core.swarm_team_listener.is_listeners_enabled", return_value=False):
        resp = _client().get("/api/swarm/listeners")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "listeners_enabled" in data
    assert data["listeners_enabled"] is False


# ---------------------------------------------------------------------------
# POST /api/swarm/listeners/toggle
# ---------------------------------------------------------------------------


def test_swarm_listeners_toggle_requires_auth(monkeypatch) -> None:
    """POST /api/swarm/listeners/toggle без ключа при установленном WEB_API_KEY — 403."""
    monkeypatch.setenv("WEB_API_KEY", "secret-test-key")
    with (
        patch("src.core.swarm_team_listener.is_listeners_enabled", return_value=False),
        patch("src.core.swarm_team_listener.set_listeners_enabled"),
    ):
        resp = _client().post("/api/swarm/listeners/toggle", json={"enabled": True})
    assert resp.status_code == 403


def test_swarm_listeners_toggle_with_auth(monkeypatch) -> None:
    """POST /api/swarm/listeners/toggle с правильным ключом — 200."""
    monkeypatch.setenv("WEB_API_KEY", "secret-test-key")
    with (
        patch("src.core.swarm_team_listener.is_listeners_enabled", return_value=False),
        patch("src.core.swarm_team_listener.set_listeners_enabled"),
    ):
        resp = _client().post(
            "/api/swarm/listeners/toggle",
            json={"enabled": True},
            headers={"X-Krab-Web-Key": "secret-test-key"},
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# GET /api/silence/status
# ---------------------------------------------------------------------------


def test_silence_status_ok() -> None:
    """GET /api/silence/status возвращает ok=True и поля от silence_manager."""
    fake_status = {
        "global_muted": False,
        "global_remaining_min": 0,
        "muted_chats": {},
        "total_muted": 0,
    }
    fake_manager = MagicMock()
    fake_manager.status.return_value = fake_status

    with patch("src.core.silence_mode.silence_manager", fake_manager):
        resp = _client().get("/api/silence/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["global_muted"] is False
    assert data["total_muted"] == 0


# ---------------------------------------------------------------------------
# POST /api/silence/toggle  (per-chat)
# ---------------------------------------------------------------------------


def test_silence_toggle_requires_auth(monkeypatch) -> None:
    """POST /api/silence/toggle без ключа при установленном WEB_API_KEY — 403."""
    monkeypatch.setenv("WEB_API_KEY", "secret-test-key")
    resp = _client().post("/api/silence/toggle", json={"chat_id": "123", "minutes": 15})
    assert resp.status_code == 403


def test_silence_toggle_missing_chat_id_no_auth(monkeypatch) -> None:
    """POST /api/silence/toggle без chat_id и без auth (нет WEB_API_KEY) — ok=False."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake_manager = MagicMock()
    fake_manager.is_global_muted.return_value = False
    with patch("src.core.silence_mode.silence_manager", fake_manager):
        resp = _client().post("/api/silence/toggle", json={})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


# ---------------------------------------------------------------------------
# GET /api/inbox/status
# ---------------------------------------------------------------------------


def test_inbox_status_structure() -> None:
    """GET /api/inbox/status возвращает ok=True и поля summary/workflow."""
    fake_workflow = {"summary": {"open": 0, "acked": 0}, "items": []}
    fake_inbox = MagicMock()
    fake_inbox.get_workflow_snapshot.return_value = fake_workflow
    with patch("src.core.inbox_service.inbox_service", fake_inbox):
        resp = _client().get("/api/inbox/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "summary" in data
    assert "workflow" in data


# ---------------------------------------------------------------------------
# GET /api/inbox/items
# ---------------------------------------------------------------------------


def test_inbox_items_returns_list() -> None:
    """GET /api/inbox/items возвращает ok=True и список items."""
    fake_inbox = MagicMock()
    fake_inbox.list_items.return_value = []
    with patch("src.core.inbox_service.inbox_service", fake_inbox):
        resp = _client().get("/api/inbox/items")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["items"], list)
