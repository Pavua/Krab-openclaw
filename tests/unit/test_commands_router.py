# -*- coding: utf-8 -*-
"""
Phase 2 Wave A — commands_router (Session 25).

4 stateless endpoints для command_registry:
- /api/commands              → registry.to_api_response()
- /api/commands/usage        → get_usage()
- /api/commands/usage/top    → get_usage() + ranking
- /api/commands/{name}       → registry.get(name) (404 при unknown)

Все тесты используют изолированный FastAPI() + include_router (без WebApp).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers.commands_router import router as commands_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(commands_router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# /api/commands
# ---------------------------------------------------------------------------


def test_list_commands_returns_registry_payload() -> None:
    """GET /api/commands → registry.to_api_response()."""
    fake_payload = {
        "ok": True,
        "total": 2,
        "commands": [
            {"name": "ask", "category": "ai", "description": "x"},
            {"name": "help", "category": "basic", "description": "y"},
        ],
        "categories": ["basic", "ai"],
    }
    fake_registry = SimpleNamespace(to_api_response=lambda: fake_payload)
    with patch("src.core.command_registry.registry", fake_registry):
        resp = _client().get("/api/commands")
    assert resp.status_code == 200
    assert resp.json() == fake_payload


# ---------------------------------------------------------------------------
# /api/commands/usage
# ---------------------------------------------------------------------------


def test_usage_returns_aggregate_shape() -> None:
    """GET /api/commands/usage → ok+total_calls+unique_commands+usage."""
    fake_usage = {"!ping": 5, "!ask": 3}
    with patch("src.core.command_registry.get_usage", return_value=dict(fake_usage)):
        resp = _client().get("/api/commands/usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["total_calls"] == 8
    assert data["unique_commands"] == 2
    assert data["usage"] == fake_usage


def test_usage_empty_returns_zeros() -> None:
    """GET /api/commands/usage при пустом счётчике → нули."""
    with patch("src.core.command_registry.get_usage", return_value={}):
        resp = _client().get("/api/commands/usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["total_calls"] == 0
    assert data["unique_commands"] == 0
    assert data["usage"] == {}


# ---------------------------------------------------------------------------
# /api/commands/usage/top
# ---------------------------------------------------------------------------


def test_usage_top_default_limit_sorted_desc() -> None:
    """GET /api/commands/usage/top без limit → top-10 в порядке count DESC."""
    fake_usage = {f"cmd{i}": i for i in range(15)}
    with patch("src.core.command_registry.get_usage", return_value=fake_usage):
        resp = _client().get("/api/commands/usage/top")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert len(data["top"]) == 10
    counts = [item["count"] for item in data["top"]]
    assert counts == sorted(counts, reverse=True)
    assert data["total_commands"] == 15


def test_usage_top_custom_limit_clamped_to_100() -> None:
    """GET /api/commands/usage/top?limit=999 → max 100 элементов."""
    fake_usage = {f"cmd{i}": 200 - i for i in range(150)}
    with patch("src.core.command_registry.get_usage", return_value=fake_usage):
        resp = _client().get("/api/commands/usage/top?limit=999")
    data = resp.json()
    assert resp.status_code == 200
    assert len(data["top"]) == 100
    assert data["total_commands"] == 150


def test_usage_top_ties_sorted_by_name_asc() -> None:
    """При одинаковом count → сортировка по name ASC."""
    fake_usage = {"zzz": 7, "aaa": 7, "mmm": 7}
    with patch("src.core.command_registry.get_usage", return_value=fake_usage):
        resp = _client().get("/api/commands/usage/top?limit=5")
    data = resp.json()
    names = [item["command"] for item in data["top"]]
    assert names == ["aaa", "mmm", "zzz"]


# ---------------------------------------------------------------------------
# /api/commands/{name}
# ---------------------------------------------------------------------------


def test_get_command_known_returns_payload() -> None:
    """GET /api/commands/{name} → ok+command для известной команды."""
    fake_cmd = SimpleNamespace(to_dict=lambda: {"name": "ask", "category": "ai"})
    fake_registry = SimpleNamespace(get=lambda _name: fake_cmd)
    with patch("src.core.command_registry.registry", fake_registry):
        resp = _client().get("/api/commands/ask")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["command"] == {"name": "ask", "category": "ai"}


def test_get_command_unknown_returns_404() -> None:
    """GET /api/commands/{name} → 404 для unknown команды."""
    fake_registry = SimpleNamespace(get=lambda _name: None)
    with patch("src.core.command_registry.registry", fake_registry):
        resp = _client().get("/api/commands/nonexistent_xyz")
    assert resp.status_code == 404
    data = resp.json()
    assert "detail" in data
    assert "nonexistent_xyz" in data["detail"]
