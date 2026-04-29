# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.misc_router`` — Phase 2 Wave Z + Wave AA
(Session 25).

Покрывают factory-pattern: ``build_misc_router(ctx)`` работает stand-alone
с mocked RouterContext. Контракт endpoint'ов сохранён 1:1 с inline
definitions из web_app.py. Wave AA добавляет POST endpoints
``/api/chat_windows/{evict_idle,clear}`` через ``ctx.assert_write_access``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.misc_router import build_misc_router


def _build_ctx(deps: dict | None = None) -> RouterContext:
    return RouterContext(
        deps=deps or {},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )


def _client(ctx: RouterContext) -> TestClient:
    app = FastAPI()
    app.include_router(build_misc_router(ctx))
    return TestClient(app)


# ── /api/transcriber/status ────────────────────────────────────────────────


def test_transcriber_status_no_deps() -> None:
    """Все voice/openclaw deps отсутствуют → readiness=down + рекомендации."""
    resp = _client(_build_ctx()).get("/api/transcriber/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    status = data["status"]
    assert status["readiness"] == "down"
    assert status["openclaw_ok"] is False
    assert status["voice_gateway_ok"] is False
    assert status["krab_ear_ok"] is False
    assert status["perceptor_ready"] is False
    assert any("Perceptor/STT" in r for r in status["recommendations"])


def test_transcriber_status_with_perceptor_only() -> None:
    """perceptor.transcribe есть → readiness=degraded (нет voice stack)."""

    class _Perceptor:
        whisper_model = "base"
        stt_isolated_worker = True

        def transcribe(self):
            return None

    deps = {"perceptor": _Perceptor()}
    resp = _client(_build_ctx(deps)).get("/api/transcriber/status")
    data = resp.json()
    assert data["status"]["readiness"] == "degraded"
    assert data["status"]["perceptor_ready"] is True
    assert data["status"]["whisper_model"] == "base"


def test_transcriber_status_full_ready() -> None:
    """openclaw+voice_gateway+krab_ear+perceptor → readiness=ready."""

    class _Perceptor:
        whisper_model = "small"
        stt_isolated_worker = True

        def transcribe(self):
            return None

    class _Health:
        async def health_check(self) -> bool:
            return True

    deps = {
        "openclaw_client": _Health(),
        "voice_gateway_client": _Health(),
        "krab_ear_client": _Health(),
        "perceptor": _Perceptor(),
    }
    resp = _client(_build_ctx(deps)).get("/api/transcriber/status")
    data = resp.json()
    assert data["status"]["readiness"] == "ready"
    assert data["status"]["openclaw_ok"] is True
    assert data["status"]["voice_gateway_ok"] is True
    assert data["status"]["krab_ear_ok"] is True


# ── /api/reactions/stats ────────────────────────────────────────────────────


def test_reactions_stats_no_engine() -> None:
    """reaction_engine отсутствует → ok=False, понятный error."""
    resp = _client(_build_ctx()).get("/api/reactions/stats")
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "error": "reaction_engine_not_configured"}


def test_reactions_stats_with_engine_and_chat_id() -> None:
    """reaction_engine.get_reaction_stats вызывается с chat_id."""
    seen: dict[str, Any] = {}

    class _Engine:
        def get_reaction_stats(self, chat_id=None):
            seen["chat_id"] = chat_id
            return {"total": 42, "chat_id": chat_id}

    resp = _client(_build_ctx({"reaction_engine": _Engine()})).get(
        "/api/reactions/stats?chat_id=12345"
    )
    data = resp.json()
    assert data["ok"] is True
    assert data["stats"] == {"total": 42, "chat_id": 12345}
    assert seen["chat_id"] == 12345


# ── /api/mood/{chat_id} ─────────────────────────────────────────────────────


def test_mood_no_engine() -> None:
    """reaction_engine отсутствует → ok=False, понятный error."""
    resp = _client(_build_ctx()).get("/api/mood/777")
    assert resp.json() == {"ok": False, "error": "reaction_engine_not_configured"}


def test_mood_with_engine() -> None:
    """reaction_engine.get_chat_mood возвращает mood-профиль."""

    class _Engine:
        def get_chat_mood(self, chat_id):
            return {"chat_id": chat_id, "score": 0.42, "label": "calm"}

    resp = _client(_build_ctx({"reaction_engine": _Engine()})).get("/api/mood/-100777")
    data = resp.json()
    assert data["ok"] is True
    assert data["mood"]["chat_id"] == -100777
    assert data["mood"]["label"] == "calm"


# ── /api/chat_windows/config ────────────────────────────────────────────────


def test_chat_windows_config() -> None:
    """Возвращает env-конфигурацию ChatWindowManager (CAPACITY и пр.)."""
    resp = _client(_build_ctx()).get("/api/chat_windows/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["capacity"], int)
    assert isinstance(data["message_cap_per_window"], int)
    assert isinstance(data["idle_eviction_sec"], (int, float))


# ── /api/chat_windows/list ──────────────────────────────────────────────────


def test_chat_windows_list() -> None:
    """Возвращает список окон (через chat_window_manager singleton)."""
    resp = _client(_build_ctx()).get("/api/chat_windows/list")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["windows"], list)
    assert data["total"] == len(data["windows"])


# ── /api/inbox/events (SSE — smoke check shape) ─────────────────────────────


def test_inbox_events_route_registered() -> None:
    """SSE endpoint зарегистрирован в router (без stream consumption — endpoint
    бесконечный SSE generator с asyncio.sleep)."""
    ctx = _build_ctx()
    router = build_misc_router(ctx)
    paths = {route.path for route in router.routes}
    assert "/api/inbox/events" in paths


# ── Wave AA: /api/chat_windows/evict_idle (POST) ────────────────────────────


def test_chat_windows_evict_idle_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake_mgr = MagicMock()
    fake_mgr.evict_idle.return_value = 3
    with patch("src.core.chat_window_manager.chat_window_manager", fake_mgr):
        resp = _client(_build_ctx()).post("/api/chat_windows/evict_idle")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["evicted"] == 3
    assert data["timeout_sec"] == 3600
    fake_mgr.evict_idle.assert_called_once_with(timeout_sec=3600)


def test_chat_windows_evict_idle_custom_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake_mgr = MagicMock()
    fake_mgr.evict_idle.return_value = 1
    with patch("src.core.chat_window_manager.chat_window_manager", fake_mgr):
        resp = _client(_build_ctx()).post(
            "/api/chat_windows/evict_idle?max_age_sec=120"
        )
    assert resp.status_code == 200
    assert resp.json()["timeout_sec"] == 120
    fake_mgr.evict_idle.assert_called_once_with(timeout_sec=120)


def test_chat_windows_evict_idle_invalid_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    fake_mgr = MagicMock()
    with patch("src.core.chat_window_manager.chat_window_manager", fake_mgr):
        resp = _client(_build_ctx()).post("/api/chat_windows/evict_idle")
    assert resp.status_code == 403


# ── Wave AA: /api/chat_windows/clear (POST) ─────────────────────────────────


def test_chat_windows_clear_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake_mgr = MagicMock()
    fake_mgr.clear_all.return_value = 7
    with patch("src.core.chat_window_manager.chat_window_manager", fake_mgr):
        resp = _client(_build_ctx()).post("/api/chat_windows/clear")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["cleared"] == 7
    fake_mgr.clear_all.assert_called_once()


def test_chat_windows_clear_invalid_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    fake_mgr = MagicMock()
    with patch("src.core.chat_window_manager.chat_window_manager", fake_mgr):
        resp = _client(_build_ctx()).post("/api/chat_windows/clear")
    assert resp.status_code == 403


# ── Wave RR: /api/diagnostics/smoke (POST) ─────────────────────────────────


def test_diagnostics_smoke_helpers_missing_returns_503() -> None:
    """Без injected helper'ов → 503."""
    resp = _client(_build_ctx()).post("/api/diagnostics/smoke")
    assert resp.status_code == 503
    assert "diagnostics_smoke_helpers_missing" in resp.json()["detail"]


def test_diagnostics_smoke_all_ok() -> None:
    """browser_smoke + photo_smoke ok → ok=True, оба check-а проходят."""

    async def _browser_helper(url):
        return {"browser_smoke": {"ok": True, "detail": "browser ok"}}

    async def _photo_helper():
        return {
            "available": True,
            "report": {"photo_smoke": {"ok": True, "detail": "photo ok"}},
        }

    deps = {
        "openclaw_browser_smoke_helper": _browser_helper,
        "openclaw_photo_smoke_helper": _photo_helper,
    }
    resp = _client(_build_ctx(deps)).post("/api/diagnostics/smoke")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["available"] is True
    checks = {c["name"]: c for c in data["checks"]}
    assert checks["browser_smoke"]["ok"] is True
    assert checks["photo_smoke"]["ok"] is True
    assert data["report"]["browser"]["available"] is True


def test_diagnostics_smoke_photo_unavailable() -> None:
    """photo unavailable → ok=False; detail из payload.error."""

    async def _browser_helper(url):
        return {"browser_smoke": {"ok": True, "detail": "browser ok"}}

    async def _photo_helper():
        return {"available": False, "error": "photo skill missing"}

    deps = {
        "openclaw_browser_smoke_helper": _browser_helper,
        "openclaw_photo_smoke_helper": _photo_helper,
    }
    resp = _client(_build_ctx(deps)).post("/api/diagnostics/smoke")
    data = resp.json()
    assert data["ok"] is False
    photo_check = next(c for c in data["checks"] if c["name"] == "photo_smoke")
    assert photo_check["ok"] is False
    assert "photo skill missing" in photo_check["detail"]


# ── Wave RR: /api/notify (POST) ────────────────────────────────────────────


def test_notify_text_required() -> None:
    resp = _client(_build_ctx()).post("/api/notify", json={})
    assert resp.status_code == 400
    assert "text_required" in resp.json()["detail"]


def test_notify_chat_id_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCLAW_ALERT_TARGET", raising=False)
    resp = _client(_build_ctx()).post("/api/notify", json={"text": "hi"})
    assert resp.status_code == 400
    assert "chat_id_required" in resp.json()["detail"]


def test_notify_userbot_not_ready_returns_503() -> None:
    """userbot отсутствует → 503 без raise (Sentry-safe)."""
    resp = _client(_build_ctx()).post(
        "/api/notify", json={"text": "hi", "chat_id": "123"}
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] == "userbot_not_ready"
    assert resp.headers.get("retry-after") == "10"


def test_notify_sends_when_userbot_ready() -> None:
    """userbot.client.send_message вызывается с chat_id и text."""
    sent: dict[str, Any] = {}

    class _Client:
        async def send_message(self, chat_id, text):
            sent["chat_id"] = chat_id
            sent["text"] = text

    class _Userbot:
        def __init__(self):
            self.client = _Client()

    deps = {"kraab_userbot": _Userbot()}
    resp = _client(_build_ctx(deps)).post(
        "/api/notify", json={"text": "hello", "chat_id": "42"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["chat_id"] == "42"
    assert sent == {"chat_id": "42", "text": "hello"}


def test_notify_uses_env_default_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    """OPENCLAW_ALERT_TARGET используется как fallback."""
    monkeypatch.setenv("OPENCLAW_ALERT_TARGET", "777")
    sent: dict[str, Any] = {}

    class _Client:
        async def send_message(self, chat_id, text):
            sent["chat_id"] = chat_id

    class _Userbot:
        def __init__(self):
            self.client = _Client()

    deps = {"kraab_userbot": _Userbot()}
    resp = _client(_build_ctx(deps)).post("/api/notify", json={"text": "hi"})
    assert resp.status_code == 200
    assert sent["chat_id"] == "777"
