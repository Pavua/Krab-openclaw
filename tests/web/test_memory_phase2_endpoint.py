# -*- coding: utf-8 -*-
"""
Тесты GET /api/memory/phase2/status.

Покрываем три режима:
- flag=disabled (нет env)
- flag=enabled (KRAB_RAG_PHASE2_ENABLED=1)
- flag=shadow (KRAB_RAG_PHASE2_SHADOW=1)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.modules.web_app import WebApp


class _FakeHealthClient:
    async def health_check(self) -> bool:
        return True

    async def health_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake"}

    async def capabilities_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake", "detail": {}}


class _FakeKraab:
    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": True}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}


def _make_client() -> TestClient:
    deps = {
        "router": None,
        "openclaw_client": None,
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
    app = WebApp(deps, port=18093, host="127.0.0.1")
    return TestClient(app.app)


def test_phase2_status_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ни одной env-переменной → flag=disabled, endpoint всё равно 200."""
    monkeypatch.delenv("KRAB_RAG_PHASE2_ENABLED", raising=False)
    monkeypatch.delenv("KRAB_RAG_PHASE2_SHADOW", raising=False)
    # Избегаем чтения реального файла логов
    monkeypatch.setenv("KRAB_LOG_PATH", "/tmp/krab_phase2_nonexistent.log")

    with patch(
        "src.core.memory_stats.collect_memory_stats",
        return_value={"archive": {"chunks": 0, "vec": 0}},
    ):
        resp = _make_client().get("/api/memory/phase2/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["flag"] == "disabled"
    assert data["vec_chunks_count"] == 0
    assert "retrieval_mode_hour" in data
    assert "latency_avg" in data


def test_phase2_status_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """KRAB_RAG_PHASE2_ENABLED=1 → flag=enabled, vec_chunks_count отражает archive."""
    monkeypatch.setenv("KRAB_RAG_PHASE2_ENABLED", "1")
    monkeypatch.delenv("KRAB_RAG_PHASE2_SHADOW", raising=False)
    monkeypatch.setenv("KRAB_LOG_PATH", "/tmp/krab_phase2_nonexistent.log")

    with patch(
        "src.core.memory_stats.collect_memory_stats",
        return_value={"archive": {"chunks": 72328, "vec": 72328}},
    ):
        resp = _make_client().get("/api/memory/phase2/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["flag"] == "enabled"
    assert data["vec_chunks_count"] == 72328
    assert data["vec_join_pct"] == 100.0
    assert data["model_dim"] == 256


def test_phase2_status_shadow(monkeypatch: pytest.MonkeyPatch) -> None:
    """KRAB_RAG_PHASE2_SHADOW=1 без ENABLED → flag=shadow, shadow_delta_pct present."""
    monkeypatch.delenv("KRAB_RAG_PHASE2_ENABLED", raising=False)
    monkeypatch.setenv("KRAB_RAG_PHASE2_SHADOW", "1")
    monkeypatch.setenv("KRAB_LOG_PATH", "/tmp/krab_phase2_nonexistent.log")

    with patch(
        "src.core.memory_stats.collect_memory_stats",
        return_value={"archive": {"chunks": 100, "vec": 50}},
    ):
        resp = _make_client().get("/api/memory/phase2/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["flag"] == "shadow"
    assert data["vec_chunks_count"] == 50
    assert data["vec_join_pct"] == 50.0
    # shadow_delta_pct is None when no log data — ok
    assert "shadow_delta_pct" in data
