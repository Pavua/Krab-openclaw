# -*- coding: utf-8 -*-
"""
Swarm router — Phase 2 Wave C extraction (Session 25) + Wave ZZ (Session 26).

Read-only endpoints:
- GET /api/swarm/teams                 — список команд + ролей (TEAM_REGISTRY)
- GET /api/swarm/task-board            — сводка board (get_board_summary)
- GET /api/swarm/tasks                 — list tasks (с фильтром по team)
- GET /api/swarm/artifacts             — list artifacts (с фильтром по team)
- GET /api/swarm/task/{task_id}        — детальная инфо о задаче
- GET /api/swarm/team/{team_name}      — детальная инфо о команде
- GET /api/swarm/stats                 — board + artifacts + listeners
- GET /api/swarm/listeners             — статус team listeners

Wave ZZ (Session 26) — добавлены 10 leaked endpoints:
- GET /api/swarm/status                — runtime status (channels/memory/scheduler)
- GET /api/swarm/memory                — последние записи памяти команды
- GET /api/swarm/reports               — список markdown reports
- GET /api/swarm/task-board/export     — CSV/JSON export
- GET /api/swarm/delegations/active    — активные цепочки делегирования
- GET /api/swarm/events                — SSE stream task board updates
- POST /api/swarm/tasks/create         — создать task (write)
- POST /api/swarm/task/{id}/update     — обновить status/result (write)
- POST /api/swarm/task/{id}/priority   — сменить приоритет (write)
- DELETE /api/swarm/task/{id}          — удалить task (write)
- POST /api/swarm/listeners/toggle     — toggle listeners (write)
- POST /api/swarm/artifacts/cleanup    — очистка старых артефактов (write)

Singleton-функции/объекты импортируются из core напрямую.
Write endpoints используют ctx.assert_write_access(header_key, token) для auth.

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.

NB: для backwards-compat сохранён module-level ``router`` (старый Wave C импорт)
с read-only endpoints; новый ``build_swarm_router(ctx)`` factory добавляет write+
дополнительные read endpoints поверх. WebApp использует factory.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, Header, Query

from ._context import RouterContext

logger = logging.getLogger(__name__)


def _register_readonly(router: APIRouter) -> None:
    """Регистрирует read-only endpoints на переданном router."""

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


# Backwards-compat: module-level router with read-only endpoints (Wave C).
router = APIRouter(tags=["swarm"])
_register_readonly(router)


def build_swarm_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter со ВСЕМИ swarm endpoints (Wave C + Wave ZZ).

    Включает read-only (8) + Wave ZZ leaked (12) — итого 20 endpoints.
    Write endpoints используют ctx.assert_write_access для auth.
    """
    new_router = APIRouter(tags=["swarm"])
    _register_readonly(new_router)

    # ── Wave ZZ: дополнительные read-only endpoints ───────────────────────

    @new_router.get("/api/swarm/status")
    async def get_swarm_status() -> dict:
        """Статус мультиагентного свёрма для /swarm dashboard."""
        try:
            from ...core.swarm_channels import swarm_channels as _sc
            from ...core.swarm_memory import swarm_memory as _sm
            from ...core.swarm_scheduler import swarm_scheduler as _ss

            teams_data = {}
            for team_name in ["traders", "coders", "analysts", "creative"]:
                is_active = (
                    _sc.is_round_active(team_name) if hasattr(_sc, "is_round_active") else False
                )
                teams_data[team_name] = {
                    "active": bool(is_active),
                    "rounds_total": 0,
                }

            memory_count = 0
            try:
                for team_name in teams_data:
                    entries = _sm.recall(team_name) if hasattr(_sm, "recall") else []
                    memory_count += len(entries) if entries else 0
            except Exception:
                pass

            scheduler_jobs = 0
            try:
                if hasattr(_ss, "list_jobs"):
                    scheduler_jobs = len(_ss.list_jobs() or [])
            except Exception:
                pass

            return {
                "ok": True,
                "teams": teams_data,
                "memory_entries": memory_count,
                "scheduler_jobs": scheduler_jobs,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @new_router.get("/api/swarm/memory")
    async def get_swarm_memory(team: str = "traders", limit: int = 5) -> dict:
        """Последние записи памяти свёрма для конкретной команды."""
        try:
            from ...core.swarm_memory import swarm_memory as _sm

            entries = _sm.recall(team, limit=limit) if hasattr(_sm, "recall") else []
            return {
                "ok": True,
                "entries": [
                    {
                        "topic": str(e.get("topic", "")),
                        "summary": str(e.get("summary", e.get("content", "")))[:300],
                        "timestamp": str(e.get("timestamp", "")),
                    }
                    for e in (entries or [])[:limit]
                ]
                if entries
                else [],
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @new_router.get("/api/swarm/reports")
    async def swarm_reports_list(limit: int = Query(default=10)) -> dict:
        """Список markdown reports."""
        from pathlib import Path as _P  # noqa: N814

        report_dir = _P.home() / ".openclaw" / "krab_runtime_state" / "reports"
        if not report_dir.exists():
            return {"ok": True, "reports": []}
        files = sorted(report_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[
            :limit
        ]
        return {
            "ok": True,
            "reports": [
                {
                    "name": f.stem,
                    "size_kb": round(f.stat().st_size / 1024, 1),
                    "modified": f.stat().st_mtime,
                }
                for f in files
            ],
        }

    @new_router.get("/api/swarm/task-board/export")
    async def swarm_task_board_export(format: str = Query(default="csv")):
        """Export full task board as CSV or JSON."""
        from ...core.swarm_task_board import swarm_task_board

        tasks = swarm_task_board.list_tasks(limit=500)

        if format == "json":
            from fastapi.responses import JSONResponse

            return JSONResponse(
                {
                    "ok": True,
                    "tasks": [
                        {
                            "task_id": t.task_id,
                            "team": t.team,
                            "title": t.title,
                            "description": t.description,
                            "status": t.status,
                            "priority": t.priority,
                            "created_by": t.created_by,
                            "assigned_to": t.assigned_to,
                            "created_at": t.created_at,
                            "updated_at": t.updated_at,
                            "result": t.result,
                            "artifacts": t.artifacts,
                            "parent_task_id": t.parent_task_id,
                        }
                        for t in tasks
                    ],
                }
            )

        # CSV export
        import csv
        import io

        from fastapi.responses import PlainTextResponse

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "task_id",
                "team",
                "title",
                "status",
                "priority",
                "created_by",
                "assigned_to",
                "created_at",
                "updated_at",
            ]
        )
        for t in tasks:
            writer.writerow(
                [
                    t.task_id,
                    t.team,
                    t.title,
                    t.status,
                    t.priority,
                    t.created_by,
                    t.assigned_to,
                    t.created_at,
                    t.updated_at,
                ]
            )

        return PlainTextResponse(
            buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=task_board.csv"},
        )

    @new_router.get("/api/swarm/delegations/active")
    async def swarm_delegations_active() -> dict:
        """Активные цепочки делегирования для visibility/dashboard."""
        from ...core.swarm_loop_guard import swarm_loop_guard

        chains = swarm_loop_guard.active_chains_snapshot()
        counters = swarm_loop_guard.blocked_counters()
        return {
            "ok": True,
            "active_chains": chains,
            "active_count": len(chains),
            "blocked_counters": counters,
            "max_hops": swarm_loop_guard._max_hops,
            "timeout_sec": swarm_loop_guard._timeout_sec,
        }

    @new_router.get("/api/swarm/events")
    async def swarm_events(token: str = Query(default="")):
        """SSE stream для обновлений Swarm task board."""
        from fastapi.responses import StreamingResponse as _StreamingResponse

        async def event_stream():
            last_hash: Optional[str] = None
            while True:
                try:
                    from ...core.swarm_artifact_store import swarm_artifact_store
                    from ...core.swarm_task_board import swarm_task_board
                    from ...core.swarm_team_listener import is_listeners_enabled

                    board = swarm_task_board.get_board_summary()
                    tasks = swarm_task_board.list_tasks(limit=30)
                    tasks_payload = [
                        {
                            "task_id": t.task_id,
                            "team": t.team,
                            "title": t.title,
                            "status": t.status,
                            "priority": t.priority,
                            "created_at": t.created_at,
                        }
                        for t in tasks
                    ]
                    arts = swarm_artifact_store.list_artifacts(limit=10)
                    arts_payload = [
                        {
                            "team": a.get("team"),
                            "topic": a.get("topic"),
                            "timestamp_iso": a.get("timestamp_iso"),
                            "duration_sec": a.get("duration_sec"),
                            "result_preview": (a.get("result") or "")[:200],
                        }
                        for a in arts
                    ]
                    listeners_enabled = is_listeners_enabled()

                    payload = {
                        "summary": board,
                        "tasks": tasks_payload,
                        "artifacts": arts_payload,
                        "listeners_enabled": listeners_enabled,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }

                    current_hash = hashlib.sha256(
                        json.dumps(
                            {
                                "summary": board,
                                "tasks": tasks_payload,
                                "artifacts": arts_payload,
                                "listeners_enabled": listeners_enabled,
                            },
                            sort_keys=True,
                            default=str,
                        ).encode()
                    ).hexdigest()

                    if current_hash != last_hash:
                        last_hash = current_hash
                        yield f"event: update\ndata: {json.dumps(payload, default=str)}\n\n"
                    else:
                        yield ": heartbeat\n\n"

                    await asyncio.sleep(5)
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.error("swarm_events_error: %s", exc)
                    yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
                    await asyncio.sleep(10)

        return _StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Wave ZZ: write endpoints (require ctx.assert_write_access) ────────

    @new_router.post("/api/swarm/tasks/create")
    async def swarm_task_create(
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Создать task в swarm board через API."""
        ctx.assert_write_access(x_krab_web_key, token)
        from ...core.swarm_task_board import swarm_task_board

        team = str(payload.get("team") or "").strip()
        title = str(payload.get("title") or "").strip()
        if not team or not title:
            return {"ok": False, "error": "team and title required"}
        task = swarm_task_board.create_task(
            team=team,
            title=title,
            description=str(payload.get("description") or ""),
            priority=str(payload.get("priority") or "medium"),
            created_by=str(payload.get("created_by") or "api"),
        )
        return {"ok": True, "task_id": task.task_id, "team": task.team, "title": task.title}

    @new_router.post("/api/swarm/task/{task_id}/update")
    async def swarm_task_update(
        task_id: str,
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Обновить task status/result через API."""
        ctx.assert_write_access(x_krab_web_key, token)
        from ...core.swarm_task_board import swarm_task_board

        status = str(payload.get("status") or "").strip()
        result = str(payload.get("result") or "").strip()
        if status == "done" and result:
            swarm_task_board.complete_task(task_id, result=result)
        elif status == "failed":
            swarm_task_board.fail_task(task_id, reason=result or "via API")
        elif status:
            swarm_task_board.update_task(task_id, status=status)
        else:
            return {"ok": False, "error": "status required"}
        return {"ok": True, "task_id": task_id, "new_status": status}

    @new_router.delete("/api/swarm/task/{task_id}")
    async def swarm_task_delete(
        task_id: str,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Удалить task из board."""
        ctx.assert_write_access(x_krab_web_key, token)
        from ...core.swarm_task_board import swarm_task_board

        try:
            swarm_task_board.fail_task(task_id, reason="deleted via API")
            return {"ok": True, "deleted": task_id}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @new_router.post("/api/swarm/task/{task_id}/priority")
    async def swarm_task_priority(
        task_id: str,
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Change task priority via API."""
        ctx.assert_write_access(x_krab_web_key, token)
        from ...core.swarm_task_board import swarm_task_board

        level = str(payload.get("priority") or "").strip().lower()
        if level not in {"low", "medium", "high", "critical"}:
            return {"ok": False, "error": "priority must be low/medium/high/critical"}
        swarm_task_board.update_task(task_id, priority=level)
        return {"ok": True, "task_id": task_id, "priority": level}

    @new_router.post("/api/swarm/listeners/toggle")
    async def swarm_listeners_toggle(
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Toggle team listeners через API."""
        ctx.assert_write_access(x_krab_web_key, token)
        from ...core.swarm_team_listener import is_listeners_enabled, set_listeners_enabled

        enabled = bool(payload.get("enabled", not is_listeners_enabled()))
        set_listeners_enabled(enabled)
        return {"ok": True, "listeners_enabled": enabled}

    @new_router.post("/api/swarm/artifacts/cleanup")
    async def swarm_artifacts_cleanup(
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Очистка старых артефактов."""
        ctx.assert_write_access(x_krab_web_key, token)
        from ...core.swarm_artifact_store import swarm_artifact_store

        removed = swarm_artifact_store.cleanup_old(max_files=50)
        return {"ok": True, "removed": removed}

    return new_router
