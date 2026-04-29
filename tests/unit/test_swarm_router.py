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

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.swarm_router import build_swarm_router, router as swarm_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(swarm_router)
    return TestClient(app)


def _full_client(write_ok: bool = True) -> TestClient:
    """Factory client со всеми endpoints (Wave C + Wave ZZ).

    write_ok=True — assert_write_access ничего не делает; иначе кидает 403.
    """
    from pathlib import Path as _P

    def _assert(header_key: str, token: str) -> None:
        if not write_ok:
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="forbidden")

    ctx = RouterContext(
        deps={},
        project_root=_P("/tmp"),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=_assert,
    )
    # Override the bound method to use our test version (bypass _helpers env check).
    ctx.assert_write_access = _assert  # type: ignore[method-assign]
    app = FastAPI()
    app.include_router(build_swarm_router(ctx))
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


# ===========================================================================
# Wave ZZ (Session 26) — extracted leaked endpoints
# ===========================================================================


# /api/swarm/status -----------------------------------------------------------


def test_swarm_status_payload() -> None:
    """GET /api/swarm/status → teams + memory_entries + scheduler_jobs."""
    fake_sc = SimpleNamespace(is_round_active=lambda team: team == "coders")
    fake_sm = SimpleNamespace(recall=lambda team, **kw: [{"x": 1}, {"x": 2}])
    fake_ss = SimpleNamespace(list_jobs=lambda: [{"id": 1}, {"id": 2}, {"id": 3}])
    with (
        patch("src.core.swarm_channels.swarm_channels", fake_sc),
        patch("src.core.swarm_memory.swarm_memory", fake_sm),
        patch("src.core.swarm_scheduler.swarm_scheduler", fake_ss),
    ):
        resp = _full_client().get("/api/swarm/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["teams"]["coders"]["active"] is True
    assert data["teams"]["traders"]["active"] is False
    assert data["memory_entries"] == 8  # 2 entries × 4 teams
    assert data["scheduler_jobs"] == 3


# /api/swarm/memory -----------------------------------------------------------


def test_swarm_memory_returns_entries() -> None:
    """GET /api/swarm/memory → topic+summary+timestamp."""
    fake_sm = SimpleNamespace(
        recall=lambda team, limit=5: [
            {"topic": "T1", "summary": "S1", "timestamp": "2026-04-26"},
        ],
    )
    with patch("src.core.swarm_memory.swarm_memory", fake_sm):
        resp = _full_client().get("/api/swarm/memory?team=coders&limit=3")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["entries"][0]["topic"] == "T1"


# /api/swarm/reports ----------------------------------------------------------


def test_swarm_reports_empty_when_no_dir(tmp_path) -> None:
    """GET /api/swarm/reports → ok+empty list если report_dir отсутствует."""
    # report_dir = ~/.openclaw/.../reports — мокируем Path.home()
    with patch("pathlib.Path.home", return_value=tmp_path):
        resp = _full_client().get("/api/swarm/reports")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["reports"] == []


# /api/swarm/task-board/export ------------------------------------------------


def test_swarm_task_board_export_csv() -> None:
    """GET /api/swarm/task-board/export → CSV с header."""
    fake_board = SimpleNamespace(
        list_tasks=lambda limit=500: [_make_task("abc12345", title="Task 1")]
    )
    with patch("src.core.swarm_task_board.swarm_task_board", fake_board):
        resp = _full_client().get("/api/swarm/task-board/export?format=csv")
    assert resp.status_code == 200
    assert "task_id,team,title" in resp.text
    assert "abc12345" in resp.text


def test_swarm_task_board_export_json() -> None:
    """GET /api/swarm/task-board/export?format=json → JSON списка."""
    fake_board = SimpleNamespace(
        list_tasks=lambda limit=500: [_make_task("abc12345", title="Task 1")]
    )
    with patch("src.core.swarm_task_board.swarm_task_board", fake_board):
        resp = _full_client().get("/api/swarm/task-board/export?format=json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["tasks"][0]["task_id"] == "abc12345"


# /api/swarm/tasks/create -----------------------------------------------------


def test_swarm_task_create_requires_team_title() -> None:
    """POST /api/swarm/tasks/create без team/title → ok=False."""
    resp = _full_client().post("/api/swarm/tasks/create", json={})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_swarm_task_create_calls_board() -> None:
    """POST /api/swarm/tasks/create → swarm_task_board.create_task()."""
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(task_id="new-id", team=kwargs["team"], title=kwargs["title"])

    fake_board = SimpleNamespace(create_task=fake_create)
    with patch("src.core.swarm_task_board.swarm_task_board", fake_board):
        resp = _full_client().post(
            "/api/swarm/tasks/create",
            json={"team": "coders", "title": "Refactor X", "priority": "high"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["task_id"] == "new-id"
    assert captured["priority"] == "high"


def test_swarm_task_create_unauthorized() -> None:
    """POST /api/swarm/tasks/create без write access → 403."""
    resp = _full_client(write_ok=False).post(
        "/api/swarm/tasks/create",
        json={"team": "coders", "title": "X"},
    )
    assert resp.status_code == 403


# /api/swarm/task/{id}/update -------------------------------------------------


def test_swarm_task_update_status_done() -> None:
    """POST /api/swarm/task/{id}/update status=done → complete_task."""
    captured: dict = {}

    def fake_complete(task_id, **kw):
        captured["complete"] = (task_id, kw)

    fake_board = SimpleNamespace(
        complete_task=fake_complete,
        fail_task=lambda *a, **k: None,
        update_task=lambda *a, **k: None,
    )
    with patch("src.core.swarm_task_board.swarm_task_board", fake_board):
        resp = _full_client().post(
            "/api/swarm/task/abc/update",
            json={"status": "done", "result": "✅"},
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert captured["complete"][0] == "abc"


def test_swarm_task_update_no_status() -> None:
    """POST /api/swarm/task/{id}/update без status → ok=False."""
    fake_board = SimpleNamespace(
        complete_task=lambda *a, **k: None,
        fail_task=lambda *a, **k: None,
        update_task=lambda *a, **k: None,
    )
    with patch("src.core.swarm_task_board.swarm_task_board", fake_board):
        resp = _full_client().post("/api/swarm/task/abc/update", json={})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


# /api/swarm/task/{id} (DELETE) -----------------------------------------------


def test_swarm_task_delete() -> None:
    """DELETE /api/swarm/task/{id} → fail_task('deleted via API')."""
    captured: dict = {}

    def fake_fail(task_id, reason=""):
        captured["task_id"] = task_id
        captured["reason"] = reason

    fake_board = SimpleNamespace(fail_task=fake_fail)
    with patch("src.core.swarm_task_board.swarm_task_board", fake_board):
        resp = _full_client().delete("/api/swarm/task/abc")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["deleted"] == "abc"
    assert "deleted" in captured["reason"]


# /api/swarm/task/{id}/priority -----------------------------------------------


def test_swarm_task_priority_valid() -> None:
    """POST /api/swarm/task/{id}/priority high → update_task."""
    captured: dict = {}

    def fake_update(task_id, **kw):
        captured.update({"task_id": task_id, **kw})

    fake_board = SimpleNamespace(update_task=fake_update)
    with patch("src.core.swarm_task_board.swarm_task_board", fake_board):
        resp = _full_client().post(
            "/api/swarm/task/abc/priority", json={"priority": "high"}
        )
    assert resp.status_code == 200
    assert resp.json()["priority"] == "high"
    assert captured["priority"] == "high"


def test_swarm_task_priority_invalid() -> None:
    """POST /api/swarm/task/{id}/priority bogus → ok=False."""
    resp = _full_client().post(
        "/api/swarm/task/abc/priority", json={"priority": "bogus"}
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


# /api/swarm/listeners/toggle -------------------------------------------------


def test_swarm_listeners_toggle_explicit() -> None:
    """POST /api/swarm/listeners/toggle enabled=true → set_listeners_enabled."""
    captured: dict = {}

    def fake_set(value):
        captured["value"] = value

    with (
        patch("src.core.swarm_team_listener.is_listeners_enabled", return_value=False),
        patch("src.core.swarm_team_listener.set_listeners_enabled", fake_set),
    ):
        resp = _full_client().post(
            "/api/swarm/listeners/toggle", json={"enabled": True}
        )
    assert resp.status_code == 200
    assert resp.json()["listeners_enabled"] is True
    assert captured["value"] is True


# /api/swarm/artifacts/cleanup ------------------------------------------------


def test_swarm_artifacts_cleanup() -> None:
    """POST /api/swarm/artifacts/cleanup → cleanup_old(max_files=50)."""
    captured: dict = {}

    def fake_cleanup(max_files=50):
        captured["max_files"] = max_files
        return 7

    fake_store = SimpleNamespace(cleanup_old=fake_cleanup)
    with patch("src.core.swarm_artifact_store.swarm_artifact_store", fake_store):
        resp = _full_client().post("/api/swarm/artifacts/cleanup")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["removed"] == 7
    assert captured["max_files"] == 50


def test_swarm_artifacts_cleanup_unauthorized() -> None:
    """POST /api/swarm/artifacts/cleanup без write access → 403."""
    resp = _full_client(write_ok=False).post("/api/swarm/artifacts/cleanup")
    assert resp.status_code == 403


# /api/swarm/delegations/active -----------------------------------------------


def test_swarm_delegations_active() -> None:
    """GET /api/swarm/delegations/active → active_chains+blocked_counters."""
    fake_guard = SimpleNamespace(
        active_chains_snapshot=lambda: [{"chain_id": "c1"}, {"chain_id": "c2"}],
        blocked_counters=lambda: {"loops": 3, "timeouts": 1},
        _max_hops=5,
        _timeout_sec=120,
    )
    # Insert a fake module since swarm_loop_guard.py не существует в src/core/.
    import sys
    import types

    fake_module = types.ModuleType("src.core.swarm_loop_guard")
    fake_module.swarm_loop_guard = fake_guard
    sys.modules["src.core.swarm_loop_guard"] = fake_module
    try:
        resp = _full_client().get("/api/swarm/delegations/active")
    finally:
        sys.modules.pop("src.core.swarm_loop_guard", None)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["active_count"] == 2
    assert data["max_hops"] == 5
    assert data["timeout_sec"] == 120
