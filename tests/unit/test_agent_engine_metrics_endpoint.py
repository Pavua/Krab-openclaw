# -*- coding: utf-8 -*-
"""Tests for /api/agent-engine/* endpoints (Wave 17-B, Hermes Phase C).

Покрывает: /comparison, /runs, /status.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pathlib import Path

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.agent_engine_metrics_router import (
    build_agent_engine_metrics_router,
    _parse_window,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app() -> FastAPI:
    """Минимальный FastAPI app с agent engine metrics router."""
    ctx = RouterContext(
        deps={},
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
    )
    router = build_agent_engine_metrics_router(ctx)
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_comparison_endpoint_ok(client, tmp_path, monkeypatch):
    """GET /api/agent-engine/comparison возвращает 200 с корректной структурой."""
    db_path = tmp_path / "archive.db"
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.close()
    monkeypatch.setenv("KRAB_ARCHIVE_DB_PATH", str(db_path))

    resp = client.get("/api/agent-engine/comparison?window=7d")
    assert resp.status_code == 200
    data = resp.json()
    # Ключи должны присутствовать
    assert "window_days" in data
    assert data["window_days"] == 7


def test_runs_endpoint_ok(client, tmp_path, monkeypatch):
    """GET /api/agent-engine/runs возвращает 200 с ok=True."""
    db_path = tmp_path / "archive.db"
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.close()
    monkeypatch.setenv("KRAB_ARCHIVE_DB_PATH", str(db_path))

    resp = client.get("/api/agent-engine/runs?limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "items" in data
    assert isinstance(data["items"], list)


def test_status_endpoint(client, monkeypatch):
    """GET /api/agent-engine/status отражает ENV переменные."""
    monkeypatch.setenv("KRAB_AGENT_ENGINE_DISPATCH_ENABLED", "0")
    monkeypatch.setenv("KRAB_AGENT_ENGINE", "openclaw")

    resp = client.get("/api/agent-engine/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["dispatch_enabled"] is False
    assert data["default_engine"] == "openclaw"


# ---------------------------------------------------------------------------
# _parse_window unit tests
# ---------------------------------------------------------------------------


def test_parse_window_days():
    assert _parse_window("7d") == 7
    assert _parse_window("30d") == 30
    assert _parse_window("1d") == 1


def test_parse_window_hours():
    assert _parse_window("24h") == 1  # 24 часа = 1 день
    assert _parse_window("48h") == 2


def test_parse_window_invalid_defaults_to_7():
    assert _parse_window("foo") == 7
    assert _parse_window("") == 7
    assert _parse_window("abc123") == 7
