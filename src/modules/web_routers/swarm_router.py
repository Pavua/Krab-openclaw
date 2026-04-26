# -*- coding: utf-8 -*-
"""
Swarm router — Phase 2 Wave C extraction (Session 25).

8 read-only swarm endpoints, не требующих _assert_write_access:
- GET /api/swarm/teams                 — список команд + ролей (TEAM_REGISTRY)
- GET /api/swarm/task-board            — сводка board (get_board_summary)
- GET /api/swarm/tasks                 — list tasks (с фильтром по team)
- GET /api/swarm/artifacts             — list artifacts (с фильтром по team)
- GET /api/swarm/task/{task_id}        — детальная инфо о задаче
- GET /api/swarm/team/{team_name}      — детальная инфо о команде
- GET /api/swarm/stats                 — board + artifacts + listeners
- GET /api/swarm/listeners             — статус team listeners

Singleton-функции/объекты импортируются из core напрямую (как в commands_router).
DELETE /api/swarm/task/{id} остаётся в web_app.py — требует _assert_write_access
и будет вынесен после введения RouterContext (см. ROADMAP).

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

router = APIRouter(tags=["swarm"])


@router.get("/api/swarm/teams")
async def swarm_teams_list() -> dict:
    """Список swarm команд с ролями."""
    from ...core.swarm_bus import TEAM_REGISTRY

    return {
        "ok": True,
        "teams": {
            team: [
                {
                    "name": r["name"],
                    "title": r.get("title", ""),
                    "emoji": r.get("emoji", ""),
                }
                for r in roles
            ]
            for team, roles in TEAM_REGISTRY.items()
        },
    }


@router.get("/api/swarm/task-board")
async def swarm_task_board_status() -> dict:
    """Сводка task board."""
    from ...core.swarm_task_board import swarm_task_board

    return {"ok": True, "summary": swarm_task_board.get_board_summary()}


@router.get("/api/swarm/tasks")
async def swarm_tasks_list(
    team: str = Query(default=""),
    limit: int = Query(default=20),
) -> dict:
    """Список задач task board."""
    from ...core.swarm_task_board import swarm_task_board

    tasks = swarm_task_board.list_tasks(team=team or None, limit=limit)
    return {
        "ok": True,
        "tasks": [
            {
                "task_id": t.task_id,
                "team": t.team,
                "title": t.title,
                "status": t.status,
                "priority": t.priority,
                "created_at": t.created_at,
            }
            for t in tasks
        ],
    }


@router.get("/api/swarm/artifacts")
async def swarm_artifacts_list(
    team: str = Query(default=""),
    limit: int = Query(default=10),
) -> dict:
    """Список swarm artifacts."""
    from ...core.swarm_artifact_store import swarm_artifact_store

    arts = swarm_artifact_store.list_artifacts(team=team or None, limit=limit)
    return {
        "ok": True,
        "artifacts": [
            {
                "team": a.get("team"),
                "topic": a.get("topic"),
                "timestamp_iso": a.get("timestamp_iso"),
                "duration_sec": a.get("duration_sec"),
                "result_preview": (a.get("result") or "")[:200],
            }
            for a in arts
        ],
    }


@router.get("/api/swarm/task/{task_id}")
async def swarm_task_detail(task_id: str) -> dict:
    """Детальная инфо о задаче."""
    from ...core.swarm_task_board import swarm_task_board

    all_tasks = swarm_task_board.list_tasks(limit=500)
    match = next((t for t in all_tasks if t.task_id.startswith(task_id)), None)
    if not match:
        return {"ok": False, "error": f"task '{task_id}' not found"}
    return {
        "ok": True,
        "task": {
            "task_id": match.task_id,
            "team": match.team,
            "title": match.title,
            "description": match.description,
            "status": match.status,
            "priority": match.priority,
            "created_by": match.created_by,
            "assigned_to": match.assigned_to,
            "created_at": match.created_at,
            "updated_at": match.updated_at,
            "result": match.result,
            "artifacts": match.artifacts,
            "parent_task_id": match.parent_task_id,
        },
    }


@router.get("/api/swarm/team/{team_name}")
async def swarm_team_info(team_name: str) -> dict:
    """Детальная инфо о команде."""
    from ...core.swarm_artifact_store import swarm_artifact_store
    from ...core.swarm_bus import TEAM_REGISTRY, resolve_team_name
    from ...core.swarm_task_board import swarm_task_board

    resolved = resolve_team_name(team_name)
    if not resolved:
        return {"ok": False, "error": f"team '{team_name}' not found"}
    roles = TEAM_REGISTRY.get(resolved, [])
    tasks = swarm_task_board.list_tasks(team=resolved, limit=10)
    arts = swarm_artifact_store.list_artifacts(team=resolved, limit=5)
    return {
        "ok": True,
        "team": resolved,
        "roles": [
            {"name": r["name"], "title": r.get("title", ""), "emoji": r.get("emoji", "")}
            for r in roles
        ],
        "tasks": [{"task_id": t.task_id, "title": t.title, "status": t.status} for t in tasks],
        "artifacts": [
            {"topic": a.get("topic"), "timestamp_iso": a.get("timestamp_iso")} for a in arts
        ],
    }


@router.get("/api/swarm/stats")
async def swarm_stats() -> dict:
    """Сводная статистика по всем командам."""
    from ...core.swarm_artifact_store import swarm_artifact_store
    from ...core.swarm_task_board import swarm_task_board
    from ...core.swarm_team_listener import is_listeners_enabled

    board = swarm_task_board.get_board_summary()
    arts = swarm_artifact_store.list_artifacts(limit=100)
    return {
        "ok": True,
        "board": board,
        "artifacts_count": len(arts),
        "listeners_enabled": is_listeners_enabled(),
    }


@router.get("/api/swarm/listeners")
async def swarm_listeners_status() -> dict:
    """Статус team listeners."""
    from ...core.swarm_team_listener import is_listeners_enabled

    return {"ok": True, "listeners_enabled": is_listeners_enabled()}
