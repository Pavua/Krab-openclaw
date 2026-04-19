# -*- coding: utf-8 -*-
"""
Тесты для GET /api/health/deep — расширенная диагностика (Wave 29-FF).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------


class _FakeOpenClaw:
    def get_last_runtime_route(self) -> dict:
        return {"channel": "cloud", "provider": "google", "model": "gemini-3-pro"}

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
    _session_start_time: float = 0.0

    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": True}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": [], "stats": {}}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}


# Типичный ответ collect_health_deep()
_DEEP_DATA = {
    "krab": {"uptime_sec": 3600, "rss_mb": 256, "cpu_pct": 1.2},
    "openclaw": {"healthy": True, "last_route": {"model": "gemini-3-pro"}},
    "lm_studio": {"state": "offline", "active_model": None},
    "archive_db": {
        "integrity": "ok",
        "messages": 42000,
        "chunks": 9000,
        "size_mb": 42.0,
        "orphan_fts5": 0,
        "orphan_vec": 0,
    },
    "reminders": {"pending": 0},
    "memory_validator": {"pending_confirm": 0},
    "sigterm_recent_count": 0,
    "system": {
        "load_avg": [0.5, 0.7, 0.9],
        "free_mb": 20480,
        "total_mb": 32768,
        "used_pct": 37.5,
    },
}


# ---------------------------------------------------------------------------
# Фабрика TestClient
# ---------------------------------------------------------------------------


def _client() -> TestClient:
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
        "userbot": _FakeKraab(),
    }
    app = WebApp(deps, port=18092, host="127.0.0.1")
    return TestClient(app.app)


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


def test_health_deep_status_200() -> None:
    """GET /api/health/deep возвращает HTTP 200."""
    with patch(
        "src.core.health_deep_collector.collect_health_deep",
        new=AsyncMock(return_value=_DEEP_DATA),
    ):
        resp = _client().get("/api/health/deep")
    assert resp.status_code == 200


def test_health_deep_required_keys() -> None:
    """Ответ содержит все 8 ожидаемых секций."""
    with patch(
        "src.core.health_deep_collector.collect_health_deep",
        new=AsyncMock(return_value=_DEEP_DATA),
    ):
        data = _client().get("/api/health/deep").json()

    for key in ("krab", "openclaw", "lm_studio", "archive_db",
                "reminders", "memory_validator", "sigterm_recent_count", "system"):
        assert key in data, f"отсутствует ключ {key!r}"


def test_health_deep_krab_section_fields() -> None:
    """Секция krab содержит uptime_sec, rss_mb, cpu_pct."""
    with patch(
        "src.core.health_deep_collector.collect_health_deep",
        new=AsyncMock(return_value=_DEEP_DATA),
    ):
        data = _client().get("/api/health/deep").json()

    krab = data["krab"]
    assert "uptime_sec" in krab
    assert "rss_mb" in krab
    assert "cpu_pct" in krab
    assert krab["uptime_sec"] == 3600


def test_health_deep_openclaw_healthy() -> None:
    """Секция openclaw отражает статус healthy."""
    with patch(
        "src.core.health_deep_collector.collect_health_deep",
        new=AsyncMock(return_value=_DEEP_DATA),
    ):
        data = _client().get("/api/health/deep").json()

    assert data["openclaw"]["healthy"] is True


def test_health_deep_sigterm_count() -> None:
    """sigterm_recent_count возвращается как число."""
    with patch(
        "src.core.health_deep_collector.collect_health_deep",
        new=AsyncMock(return_value=_DEEP_DATA),
    ):
        data = _client().get("/api/health/deep").json()

    assert isinstance(data["sigterm_recent_count"], int)
    assert data["sigterm_recent_count"] == 0
