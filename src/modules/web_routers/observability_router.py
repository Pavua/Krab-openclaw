# -*- coding: utf-8 -*-
"""Wave 44-U + 51-D: observability endpoints.

GET /api/observability/runs?since=...&limit=...&status=...&chat_id=...&model=...
GET /api/observability/run/<request_id>
GET /api/observability/snapshots         (Wave 51-D — surfacing Wave 49-F)
GET /api/observability/route-switches    (Wave 51-D — surfacing Wave 48-B)

Читает:
- `~/.openclaw/krab_runtime_state/runs_history.jsonl`
- `~/.openclaw/krab_runtime_state/snapshots/`
- `~/.openclaw/krab_runtime_state/route_switches.jsonl`

Все endpoints — read-only. Возвращают JSON для Owner panel /observability dashboard.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from ._context import RouterContext


def build_observability_router(ctx: RouterContext) -> APIRouter:  # noqa: ARG001
    """Factory: возвращает APIRouter с observability endpoints."""
    router = APIRouter(prefix="/api/observability", tags=["observability"])

    @router.get("/runs")
    async def list_runs(
        since: Annotated[str, Query(description="Окно: '1h','24h','5m' или секунды")] = "24h",
        limit: Annotated[int, Query(ge=1, le=2000)] = 200,
        status: Annotated[str, Query(description="Filter by status: ok/error/timeout")] = "",
        chat_id: Annotated[str, Query(description="Filter by chat_id")] = "",
        model: Annotated[str, Query(description="Substring match по model")] = "",
    ) -> dict:
        """Список agent runs (most recent first)."""
        from src.integrations._bypass_perf import parse_duration
        from src.integrations._observability_log import read_runs

        since_sec = parse_duration(since) if since else None
        runs = read_runs(
            since_sec=since_sec,
            limit=limit,
            status_filter=(status or None),
            chat_id_filter=(chat_id or None),
            model_filter=(model or None),
        )
        return {"ok": True, "count": len(runs), "runs": runs}

    @router.get("/run/{request_id}")
    async def get_one_run(request_id: str) -> dict:
        """Полные данные одного run по request_id."""
        from src.integrations._observability_log import get_run

        rec = get_run(request_id)
        if not rec:
            raise HTTPException(status_code=404, detail="run_not_found")
        return {"ok": True, "run": rec}

    # ── Wave 51-D: snapshots tab ─────────────────────────────────────────────
    @router.get("/snapshots")
    async def list_snapshots(
        limit: Annotated[int, Query(ge=1, le=200)] = 24,
    ) -> dict:
        """Список последних snapshots (Wave 49-F).

        Сортировка — reverse chronological (новые первыми). Каждая запись
        содержит ``timestamp``, ``files_count``, ``total_size_kb``,
        ``created_at`` (mtime), ``files`` (список .bak файлов).

        Read-only: НЕ создаёт и НЕ удаляет snapshots — только показывает.
        """
        try:
            from src.core.state_snapshots import StateSnapshotManager

            manager = StateSnapshotManager()
            rows = manager.list_snapshots()
        except FileNotFoundError:
            rows = []
        except Exception:
            # Если directory нет или I/O ошибка — пустой список (graceful).
            rows = []

        result: list[dict[str, Any]] = []
        for row in rows[:limit]:
            total_bytes = int(row.get("total_bytes", 0) or 0)
            result.append(
                {
                    "timestamp": row.get("timestamp", ""),
                    "path": row.get("path", ""),
                    "files": row.get("files", []),
                    "files_count": int(row.get("file_count", 0) or 0),
                    "total_size_kb": round(total_bytes / 1024.0, 2),
                    "total_bytes": total_bytes,
                    "created_at": row.get("mtime", 0),
                }
            )
        return {"ok": True, "count": len(result), "snapshots": result}

    @router.get("/route-switches")
    async def list_route_switches(
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> dict:
        """Список последних route-switches (Wave 48-B).

        Tail последние ``limit`` строк JSONL ring-buffer'а, malformed
        строки тихо пропускаются.
        """
        from src.integrations.route_switch_log import LOG_FILE

        entries: list[dict[str, Any]] = []
        if LOG_FILE.exists():
            try:
                lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
            except OSError:
                lines = []
            # Берём хвост и парсим, malformed пропускаем (graceful).
            for raw in lines[-limit:]:
                line = raw.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, dict):
                        entries.append(parsed)
                except (ValueError, TypeError):
                    continue
        # Reverse chronological: новые первыми.
        entries.reverse()
        return {"ok": True, "count": len(entries), "switches": entries}

    # ── Wave 52-C: audit summary (bash + agent action logs aggregator) ──────
    @router.get("/audit-summary")
    async def get_audit_summary(
        window_minutes: Annotated[int, Query(ge=1, le=1440)] = 60,
    ) -> dict:
        """Агрегаты + suspicious-pattern alerts по audit-логам (Wave 52-C).

        Покрывает оба канала:

        - ``/tmp/krab_bash_audit.log`` (bash_guard verdicts)
        - ``~/.openclaw/krab_runtime_state/agent_audit.jsonl``
          (multi-channel agent actions)

        Read-only — анализатор не модифицирует логи.
        """
        try:
            from src.core.agent_audit_analyzer import AuditAnalyzer

            return AuditAnalyzer().analyze_recent(window_minutes=window_minutes)
        except Exception as exc:  # graceful: всегда возвращаем dict
            return {
                "ok": False,
                "window_minutes": window_minutes,
                "error": f"{type(exc).__name__}: {exc}",
                "bash_audit": {},
                "agent_audit": {},
                "alerts": [],
            }

    return router
