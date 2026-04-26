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
    # Session 24: 4 новые секции
    "sentry": {"initialized": True, "dsn_configured": True},
    "mcp_servers": {
        "yung-nagato": {"port": 8011, "ok": True},
        "p0lrd": {"port": 8012, "ok": True},
        "hammerspoon": {"port": 8013, "ok": True},
    },
    "cf_tunnel": {
        "label": "ai.krab.cloudflared-tunnel",
        "loaded": True,
        "last_url": "https://example.trycloudflare.com",
        "fail_count": 0,
    },
    "error_rate_5m": {"errors_5m": 0, "window_sec": 300},
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

    for key in (
        "krab",
        "openclaw",
        "lm_studio",
        "archive_db",
        "reminders",
        "memory_validator",
        "sigterm_recent_count",
        "system",
        # Session 24: 4 новые секции
        "sentry",
        "mcp_servers",
        "cf_tunnel",
        "error_rate_5m",
    ):
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


# ── Session 24: 4 новые секции ─────────────────────────────────────────────


def test_health_deep_sentry_section() -> None:
    """Секция sentry отдаёт initialized + dsn_configured booleans."""
    with patch(
        "src.core.health_deep_collector.collect_health_deep",
        new=AsyncMock(return_value=_DEEP_DATA),
    ):
        data = _client().get("/api/health/deep").json()

    sentry = data["sentry"]
    assert "initialized" in sentry
    assert "dsn_configured" in sentry
    assert isinstance(sentry["initialized"], bool)
    assert isinstance(sentry["dsn_configured"], bool)


def test_health_deep_mcp_servers_section() -> None:
    """Секция mcp_servers содержит 3 known servers с port + ok."""
    with patch(
        "src.core.health_deep_collector.collect_health_deep",
        new=AsyncMock(return_value=_DEEP_DATA),
    ):
        data = _client().get("/api/health/deep").json()

    mcp = data["mcp_servers"]
    for name in ("yung-nagato", "p0lrd", "hammerspoon"):
        assert name in mcp, f"отсутствует MCP {name!r}"
        assert "port" in mcp[name]
        assert "ok" in mcp[name]
    assert mcp["yung-nagato"]["port"] == 8011
    assert mcp["p0lrd"]["port"] == 8012
    assert mcp["hammerspoon"]["port"] == 8013


def test_health_deep_cf_tunnel_section() -> None:
    """Секция cf_tunnel содержит label, loaded, last_url, fail_count."""
    with patch(
        "src.core.health_deep_collector.collect_health_deep",
        new=AsyncMock(return_value=_DEEP_DATA),
    ):
        data = _client().get("/api/health/deep").json()

    tunnel = data["cf_tunnel"]
    assert tunnel["label"] == "ai.krab.cloudflared-tunnel"
    assert "loaded" in tunnel
    assert "last_url" in tunnel
    assert "fail_count" in tunnel


def test_health_deep_error_rate_section() -> None:
    """Секция error_rate_5m содержит errors_5m + window_sec=300."""
    with patch(
        "src.core.health_deep_collector.collect_health_deep",
        new=AsyncMock(return_value=_DEEP_DATA),
    ):
        data = _client().get("/api/health/deep").json()

    er = data["error_rate_5m"]
    assert isinstance(er["errors_5m"], int)
    assert er["window_sec"] == 300
