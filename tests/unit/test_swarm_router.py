# -*- coding: utf-8 -*-
"""
Phase 2 Wave C — swarm_router (Session 25).

8 read-only swarm endpoints, вынесенные из web_app.py:
- /api/swarm/teams                 → TEAM_REGISTRY
- /api/swarm/task-board            → swarm_task_board.get_board_summary()
- /api/swarm/tasks                 → swarm_task_board.list_tasks()
- /api/swarm/artifacts             → swarm_artifact_store.list_artifacts()
- /api/swarm/task/{task_id}        → swarm_task_board.list_tasks()
- /api/swarm/team/{team_name}      → TEAM_REGISTRY + resolve_team_name + tasks + artifacts
- /api/swarm/stats                 → board + artifacts + listeners
- /api/swarm/listeners             → is_listeners_enabled()

Все тесты используют изолированный FastAPI() + include_router (без WebApp).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers.swarm_router import router as swarm_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(swarm_router)
    return TestClient(app)


def _make_task(
    task_id: str = "abc123",
    team: str = "coders",
    title: str = "Test task",
    status: str = "open",
    priority: str = "medium",
) -> SimpleNamespace:
    """Build a SimpleNamespace mimicking a SwarmTask record."""
    return SimpleNamespace(
        task_id=task_id,
        team=team,
        title=title,
        description="desc",
        status=status,
        priority=priority,
        created_by="api",
        assigned_to="",
        created_at="2026-04-26T00:00:00Z",
        updated_at="2026-04-26T01:00:00Z",
        result="",
        artifacts=[],
        parent_task_id=None,
    )


# ---------------------------------------------------------------------------
# /api/swarm/teams
# ---------------------------------------------------------------------------


def test_swarm_teams_returns_registry_payload() -> None:
    """GET /api/swarm/teams → ok+teams (mapping team -> roles)."""
    fake_registry = {
        "coders": [{"name": "alpha", "title": "Lead", "emoji": "💻"}],
        "traders": [{"name": "beta", "title": "Quant", "emoji": "📈"}],
    }
    with patch("src.core.swarm_bus.TEAM_REGISTRY", fake_registry):
        resp = _client().get("/api/swarm/teams")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "coders" in data["teams"]
    assert data["teams"]["coders"][0]["name"] == "alpha"
    assert data["teams"]["coders"][0]["emoji"] == "💻"


# ---------------------------------------------------------------------------
# /api/swarm/task-board
# ---------------------------------------------------------------------------


def test_swarm_task_board_returns_summary() -> None:
    """GET /api/swarm/task-board → ok+summary."""
    fake_board = SimpleNamespace(get_board_summary=lambda: {"open": 3, "done": 5})
    with patch("src.core.swarm_task_board.swarm_task_board", fake_board):
        resp = _client().get("/api/swarm/task-board")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["summary"] == {"open": 3, "done": 5}


# ---------------------------------------------------------------------------
# /api/swarm/tasks
# ---------------------------------------------------------------------------


def test_swarm_tasks_default_listing() -> None:
    """GET /api/swarm/tasks → list_tasks(team=None, limit=20)."""
    captured: dict = {}

    def fake_list(team=None, limit=20):
        captured["team"] = team
        captured["limit"] = limit
        return [_make_task("t1"), _make_task("t2", team="traders")]

    fake_board = SimpleNamespace(list_tasks=fake_list)
    with patch("src.core.swarm_task_board.swarm_task_board", fake_board):
        resp = _client().get("/api/swarm/tasks")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert len(data["tasks"]) == 2
    assert captured == {"team": None, "limit": 20}
    assert {"task_id", "team", "title", "status", "priority", "created_at"} <= set(
        data["tasks"][0].keys()
    )


def test_swarm_tasks_with_team_and_limit() -> None:
    """GET /api/swarm/tasks?team=coders&limit=5 → пробрасывает фильтры."""
    captured: dict = {}

    def fake_list(team=None, limit=20):
        captured["team"] = team
        captured["limit"] = limit
        return []

    fake_board = SimpleNamespace(list_tasks=fake_list)
    with patch("src.core.swarm_task_board.swarm_task_board", fake_board):
        resp = _client().get("/api/swarm/tasks?team=coders&limit=5")
    assert resp.status_code == 200
    assert captured == {"team": "coders", "limit": 5}


# ---------------------------------------------------------------------------
# /api/swarm/artifacts
# ---------------------------------------------------------------------------


def test_swarm_artifacts_default_listing() -> None:
    """GET /api/swarm/artifacts → result_preview обрезается до 200 символов."""
    long_result = "x" * 500
    fake_arts = [
        {
            "team": "coders",
            "topic": "Refactor",
            "timestamp_iso": "2026-04-26T00:00:00Z",
            "duration_sec": 12.3,
            "result": long_result,
        }
    ]
    fake_store = SimpleNamespace(list_artifacts=lambda team=None, limit=10: fake_arts)
    with patch("src.core.swarm_artifact_store.swarm_artifact_store", fake_store):
        resp = _client().get("/api/swarm/artifacts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert len(data["artifacts"]) == 1
    assert len(data["artifacts"][0]["result_preview"]) == 200


# ---------------------------------------------------------------------------
# /api/swarm/task/{task_id}
# ---------------------------------------------------------------------------


def test_swarm_task_detail_match_prefix() -> None:
    """GET /api/swarm/task/abc → находит task по prefix."""
    fake_board = SimpleNamespace(
        list_tasks=lambda limit=500: [_make_task("abc12345"), _make_task("xyz999")],
    )
    with patch("src.core.swarm_task_board.swarm_task_board", fake_board):
        resp = _client().get("/api/swarm/task/abc")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["task"]["task_id"] == "abc12345"
    expected_keys = {
        "task_id",
        "team",
        "title",
        "description",
        "status",
        "priority",
        "created_by",
        "assigned_to",
        "created_at",
        "updated_at",
        "result",
        "artifacts",
        "parent_task_id",
    }
    assert expected_keys <= set(data["task"].keys())


def test_swarm_task_detail_not_found() -> None:
    """GET /api/swarm/task/missing → ok=False."""
    fake_board = SimpleNamespace(list_tasks=lambda limit=500: [_make_task("abc12345")])
    with patch("src.core.swarm_task_board.swarm_task_board", fake_board):
        resp = _client().get("/api/swarm/task/missing")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "missing" in data["error"]


# ---------------------------------------------------------------------------
# /api/swarm/team/{team_name}
# ---------------------------------------------------------------------------


def test_swarm_team_info_resolved() -> None:
    """GET /api/swarm/team/coders → ok+roles+tasks+artifacts."""
    fake_registry = {
        "coders": [{"name": "alpha", "title": "Lead", "emoji": "💻"}],
    }
    fake_board = SimpleNamespace(
        list_tasks=lambda team=None, limit=10: [_make_task("t1", team="coders")],
    )
    fake_store = SimpleNamespace(
        list_artifacts=lambda team=None, limit=5: [
            {"topic": "Refactor", "timestamp_iso": "2026-04-26T00:00:00Z"},
        ],
    )
    with (
        patch("src.core.swarm_bus.TEAM_REGISTRY", fake_registry),
        patch("src.core.swarm_bus.resolve_team_name", lambda name: "coders" if name else None),
        patch("src.core.swarm_task_board.swarm_task_board", fake_board),
        patch("src.core.swarm_artifact_store.swarm_artifact_store", fake_store),
    ):
        resp = _client().get("/api/swarm/team/coders")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["team"] == "coders"
    assert len(data["roles"]) == 1
    assert len(data["tasks"]) == 1
    assert len(data["artifacts"]) == 1


def test_swarm_team_info_unknown() -> None:
    """GET /api/swarm/team/foo → ok=False при unresolved."""
    with patch("src.core.swarm_bus.resolve_team_name", lambda name: None):
        resp = _client().get("/api/swarm/team/foo")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "foo" in data["error"]


# ---------------------------------------------------------------------------
# /api/swarm/stats
# ---------------------------------------------------------------------------


def test_swarm_stats_aggregate() -> None:
    """GET /api/swarm/stats → board+artifacts_count+listeners_enabled."""
    fake_board = SimpleNamespace(get_board_summary=lambda: {"open": 1, "done": 4})
    fake_store = SimpleNamespace(
        list_artifacts=lambda limit=100: [{"team": "coders"}, {"team": "traders"}, {"team": "creative"}],
    )
    with (
        patch("src.core.swarm_task_board.swarm_task_board", fake_board),
        patch("src.core.swarm_artifact_store.swarm_artifact_store", fake_store),
        patch("src.core.swarm_team_listener.is_listeners_enabled", return_value=True),
    ):
        resp = _client().get("/api/swarm/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["board"] == {"open": 1, "done": 4}
    assert data["artifacts_count"] == 3
    assert data["listeners_enabled"] is True


# ---------------------------------------------------------------------------
# /api/swarm/listeners
# ---------------------------------------------------------------------------


def test_swarm_listeners_status_enabled() -> None:
    """GET /api/swarm/listeners → ok+listeners_enabled=True."""
    with patch("src.core.swarm_team_listener.is_listeners_enabled", return_value=True):
        resp = _client().get("/api/swarm/listeners")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "listeners_enabled": True}


def test_swarm_listeners_status_disabled() -> None:
    """GET /api/swarm/listeners → ok+listeners_enabled=False."""
    with patch("src.core.swarm_team_listener.is_listeners_enabled", return_value=False):
        resp = _client().get("/api/swarm/listeners")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "listeners_enabled": False}
