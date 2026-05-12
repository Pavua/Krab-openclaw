# -*- coding: utf-8 -*-
"""
Unit tests for ``src.modules.web_routers.swarm_admin_router`` — Wave 152.

Покрывает:
- GET  /api/admin/swarm/dashboard — shape (active/stats/recent/board),
  empty state, активный swarm runs (status='started'), board column splitting.
- GET  /admin/swarm — HTML render + nav tabs.
- Graceful degradation если activity_log / task_board бросают.

Используется чистый FastAPI + TestClient, без полного WebApp. Singletons
patched через ``unittest.mock.patch`` для изоляции от реального состояния.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.swarm_admin_router import build_swarm_admin_router

# ── Fakes ──────────────────────────────────────────────────────────────────


@dataclass
class _FakeTask:
    """Минимальный stub SwarmTask — поддерживает asdict()."""

    task_id: str = ""
    team: str = ""
    title: str = ""
    description: str = ""
    status: str = "pending"
    created_by: str = "owner"
    assigned_to: str = ""
    priority: str = "medium"
    created_at: str = ""
    updated_at: str = ""
    result: str = ""
    artifacts: list[str] = field(default_factory=list)
    parent_task_id: str = ""
    auto_execute: bool = False


class _FakeActivityLog:
    """Stub ``swarm_activity_log`` singleton — feeds rows and stats."""

    def __init__(
        self,
        *,
        rows: list[dict[str, Any]] | None = None,
        stats: dict[str, dict[str, Any]] | None = None,
        recent_raises: Exception | None = None,
        stats_raises: Exception | None = None,
    ) -> None:
        self._rows = rows or []
        self._stats = stats or {}
        self._recent_raises = recent_raises
        self._stats_raises = stats_raises
        self.query_calls: list[int] = []

    def query_recent(self, limit: int = 20, team: str | None = None) -> list[dict[str, Any]]:
        self.query_calls.append(limit)
        if self._recent_raises is not None:
            raise self._recent_raises
        return [dict(r) for r in self._rows[:limit]]

    def stats_by_team(self) -> dict[str, dict[str, Any]]:
        if self._stats_raises is not None:
            raise self._stats_raises
        return dict(self._stats)


class _FakeTaskBoard:
    """Stub ``swarm_task_board`` singleton — поддерживает list_tasks/summary."""

    def __init__(
        self,
        *,
        tasks_by_status: dict[str, list[_FakeTask]] | None = None,
        summary: dict[str, Any] | None = None,
        list_raises: Exception | None = None,
        summary_raises: Exception | None = None,
    ) -> None:
        self._tasks_by_status = tasks_by_status or {}
        self._summary = summary or {"total": 0, "by_status": {}, "by_team": {}}
        self._list_raises = list_raises
        self._summary_raises = summary_raises

    def list_tasks(
        self,
        team: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[_FakeTask]:
        if self._list_raises is not None:
            raise self._list_raises
        return list(self._tasks_by_status.get(status or "", []))[:limit]

    def get_board_summary(self) -> dict[str, Any]:
        if self._summary_raises is not None:
            raise self._summary_raises
        return dict(self._summary)


# ── Fixture builders ───────────────────────────────────────────────────────


def _build_ctx() -> RouterContext:
    """Минимальный RouterContext — для swarm_admin_router он почти не нужен,
    но _make_router_context требует все обязательные поля."""
    return RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda *_a, **_kw: None,
    )


def _client(
    *,
    activity_log: _FakeActivityLog | None = None,
    task_board: _FakeTaskBoard | None = None,
) -> tuple[TestClient, _FakeActivityLog, _FakeTaskBoard]:
    """Builds TestClient + patches singletons.

    Возвращает (client, activity_log, task_board) для inspections.
    Caller использует as-is — patch активен в течение теста через
    monkeypatch-like подход (контекст-менеджер scope = функция теста).
    """
    al = activity_log or _FakeActivityLog()
    tb = task_board or _FakeTaskBoard()

    app = FastAPI()
    app.include_router(build_swarm_admin_router(_build_ctx()))
    client = TestClient(app)
    return client, al, tb


def _apply_patches(al: _FakeActivityLog, tb: _FakeTaskBoard):
    """Контекстный менеджер с активными patch-ами для singletons."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch("src.core.swarm_activity_log.swarm_activity_log", al))
    stack.enter_context(patch("src.core.swarm_task_board.swarm_task_board", tb))
    return stack


# ── GET /api/admin/swarm/dashboard tests ───────────────────────────────────


def test_dashboard_returns_expected_shape() -> None:
    """Базовая форма: ok=true + active/stats/recent/board keys."""
    client, al, tb = _client()
    with _apply_patches(al, tb):
        resp = client.get("/api/admin/swarm/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["active"], list)
    assert isinstance(data["stats"], dict)
    assert isinstance(data["recent"], list)
    assert isinstance(data["board"], dict)
    # Board columns — 5 фиксированных.
    cols = data["board"]["columns"]
    for st in ("pending", "in_progress", "done", "failed", "blocked"):
        assert st in cols, f"missing column {st}"
    # Summary тоже на месте.
    assert "summary" in data["board"]


def test_dashboard_empty_state() -> None:
    """Пустой activity_log + пустой board → ok с пустыми списками."""
    client, al, tb = _client()
    with _apply_patches(al, tb):
        resp = client.get("/api/admin/swarm/dashboard")
    data = resp.json()
    assert data["active"] == []
    assert data["recent"] == []
    assert data["stats"] == {}
    # Все колонки — пустые.
    for col in data["board"]["columns"].values():
        assert col == []


def test_dashboard_active_runs_filter_started_status() -> None:
    """Только status='started' попадают в active; done/failed → recent."""
    now_ts = int(time.time())
    rows = [
        {
            "id": 1,
            "ts": now_ts - 60,
            "team": "coders",
            "topic": "build feature X",
            "status": "started",
            "latency_ms": None,
            "artifact_ref": None,
            "errors": [],
        },
        {
            "id": 2,
            "ts": now_ts - 200,
            "team": "traders",
            "topic": "analyze BTC",
            "status": "done",
            "latency_ms": 5400,
            "artifact_ref": "art_abc",
            "errors": [],
        },
        {
            "id": 3,
            "ts": now_ts - 300,
            "team": "analysts",
            "topic": "old run",
            "status": "started",
            "latency_ms": None,
            "artifact_ref": None,
            "errors": [],
        },
    ]
    al = _FakeActivityLog(rows=rows)
    client, _, tb = _client(activity_log=al)
    with _apply_patches(al, tb):
        resp = client.get("/api/admin/swarm/dashboard")
    data = resp.json()
    # 2 running runs.
    assert len(data["active"]) == 2
    active_ids = {r["id"] for r in data["active"]}
    assert active_ids == {1, 3}
    # Каждый running имеет started_ago_sec.
    for r in data["active"]:
        assert isinstance(r["started_ago_sec"], int)
        assert r["started_ago_sec"] >= 0
    # Recent содержит все 3.
    assert len(data["recent"]) == 3


def test_dashboard_stats_passthrough() -> None:
    """stats_by_team возвращается one-to-one в dashboard."""
    stats = {
        "coders": {
            "count": 10,
            "started": 1,
            "done": 8,
            "failed": 1,
            "avg_latency_ms": 4500.0,
            "success_rate": 0.89,
        },
        "traders": {
            "count": 3,
            "started": 0,
            "done": 3,
            "failed": 0,
            "avg_latency_ms": 2000.0,
            "success_rate": 1.0,
        },
    }
    al = _FakeActivityLog(stats=stats)
    client, _, tb = _client(activity_log=al)
    with _apply_patches(al, tb):
        resp = client.get("/api/admin/swarm/dashboard")
    data = resp.json()
    assert data["stats"]["coders"]["count"] == 10
    assert data["stats"]["coders"]["done"] == 8
    assert data["stats"]["traders"]["success_rate"] == 1.0


def test_dashboard_board_columns_populated() -> None:
    """list_tasks разбит по 5 колонкам Kanban."""
    pending = [
        _FakeTask(
            task_id="p1",
            team="coders",
            title="implement X",
            status="pending",
            priority="high",
        ),
        _FakeTask(
            task_id="p2", team="traders", title="research Y", status="pending", priority="low"
        ),
    ]
    in_progress = [
        _FakeTask(
            task_id="ip1",
            team="analysts",
            title="processing",
            status="in_progress",
            priority="critical",
        ),
    ]
    done = [
        _FakeTask(task_id="d1", team="creative", title="done task", status="done"),
    ]
    tb = _FakeTaskBoard(
        tasks_by_status={
            "pending": pending,
            "in_progress": in_progress,
            "done": done,
            "failed": [],
            "blocked": [],
        },
        summary={
            "total": 4,
            "by_status": {"pending": 2, "in_progress": 1, "done": 1},
            "by_team": {"coders": 1, "traders": 1, "analysts": 1, "creative": 1},
        },
    )
    client, al, _ = _client(task_board=tb)
    with _apply_patches(al, tb):
        resp = client.get("/api/admin/swarm/dashboard")
    data = resp.json()
    board = data["board"]
    assert board["summary"]["total"] == 4
    assert len(board["columns"]["pending"]) == 2
    assert len(board["columns"]["in_progress"]) == 1
    assert len(board["columns"]["done"]) == 1
    assert board["columns"]["failed"] == []
    assert board["columns"]["blocked"] == []
    # Каждая task — dict с ключевыми полями (asdict вернул их).
    p1 = board["columns"]["pending"][0]
    assert p1["task_id"] == "p1"
    assert p1["title"] == "implement X"
    assert p1["priority"] == "high"


def test_dashboard_graceful_when_activity_log_raises() -> None:
    """query_recent/stats_by_team бросают → dashboard всё равно 200 + пустые."""
    al = _FakeActivityLog(
        recent_raises=RuntimeError("db locked"),
        stats_raises=RuntimeError("stats fail"),
    )
    client, _, tb = _client(activity_log=al)
    with _apply_patches(al, tb):
        resp = client.get("/api/admin/swarm/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["active"] == []
    assert data["recent"] == []
    assert data["stats"] == {}


def test_dashboard_graceful_when_task_board_raises() -> None:
    """list_tasks бросает → все колонки пустые, summary fallback."""
    tb = _FakeTaskBoard(
        list_raises=RuntimeError("io fail"),
        summary_raises=RuntimeError("summary fail"),
    )
    client, al, _ = _client(task_board=tb)
    with _apply_patches(al, tb):
        resp = client.get("/api/admin/swarm/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    board = data["board"]
    # Summary fallback shape.
    assert board["summary"]["total"] == 0
    # Все колонки пустые.
    for col in board["columns"].values():
        assert col == []


# ── GET /admin/swarm tests ─────────────────────────────────────────────────


def test_admin_swarm_page_renders_html() -> None:
    """HTML страница рендерится с no-store + правильным content-type."""
    client, al, tb = _client()
    with _apply_patches(al, tb):
        resp = client.get("/admin/swarm")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "no-store" in resp.headers.get("cache-control", "")
    body = resp.text
    # Ключевые UI элементы.
    assert "Swarm Dashboard" in body
    assert "/api/admin/swarm/dashboard" in body  # endpoint referenced by JS
    # Nav tabs включают Models / Routing / Swarm.
    assert 'href="/admin/models"' in body
    assert 'href="/admin/routing"' in body
    assert 'href="/admin/swarm"' in body
    # Kanban колонки в шаблоне (через class names — UI JS использует).
    assert "col-pending" in body
    assert "col-in_progress" in body
    assert "col-done" in body
    assert "col-failed" in body
    assert "col-blocked" in body


def test_admin_swarm_page_has_swarm_tab_active() -> None:
    """В nav tabs Swarm имеет class 'active' на этой странице."""
    client, al, tb = _client()
    with _apply_patches(al, tb):
        resp = client.get("/admin/swarm")
    body = resp.text
    # Ищем закрытый тег a с class="active" и href /admin/swarm.
    # Допустимы оба порядка атрибутов, но в нашем шаблоне зафиксирован один.
    assert '<a href="/admin/swarm" class="active">Swarm</a>' in body
