# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.misc_router`` — Phase 2 Wave Z (Session 25).

Покрывают factory-pattern: ``build_misc_router(ctx)`` работает stand-alone
с mocked RouterContext. Контракт endpoint'ов сохранён 1:1 с inline
definitions из web_app.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

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
