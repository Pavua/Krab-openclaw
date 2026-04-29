# -*- coding: utf-8 -*-
"""
Phase 2 proof-of-concept — extracted version router (Session 25).

Verify что extraction в src/modules/web_routers/version_router.py
сохраняет существующий контракт endpoint /api/version.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers.version_router import router as version_router


def _client() -> TestClient:
    """Минимальное FastAPI приложение с подключённым version_router."""
    app = FastAPI()
    app.include_router(version_router)
    return TestClient(app)


def test_version_endpoint_returns_200() -> None:
    """GET /api/version → 200."""
    resp = _client().get("/api/version")
    assert resp.status_code == 200


def test_version_response_shape() -> None:
    """Ответ содержит ok=True, version, features список."""
    data = _client().get("/api/version").json()
    assert data["ok"] is True
    assert "version" in data
    assert "features" in data
    assert isinstance(data["features"], list)
    assert len(data["features"]) > 0


def test_version_response_unchanged_after_extraction() -> None:
    """Контракт endpoint до и после extraction идентичен.

    Это regression test — обеспечивает что Phase 2 extraction
    не изменил shape ответа /api/version.
    """
    data = _client().get("/api/version").json()
    # Все ключи которые были в inline-версии (web_app.py до extraction)
    required_keys = {"ok", "version", "commits", "tests", "api_endpoints", "features"}
    assert required_keys.issubset(data.keys())
    # Точные значения зафиксированы
    assert data["version"] == "session5"
    assert data["commits"] == 113
    assert data["tests"] == 2043
    assert data["api_endpoints"] == 184


def test_version_features_contains_known_items() -> None:
    """Features список включает known items."""
    data = _client().get("/api/version").json()
    features = set(data["features"])
    expected = {
        "translator_mvp",
        "swarm_execution",
        "channel_parity",
        "finops",
        "hammerspoon_mcp",
    }
    assert expected.issubset(features)
