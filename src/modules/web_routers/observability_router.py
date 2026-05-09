# -*- coding: utf-8 -*-
"""Wave 44-U: observability endpoints.

GET /api/observability/runs?since=...&limit=...&status=...&chat_id=...&model=...
GET /api/observability/run/<request_id>

Читает `~/.openclaw/krab_runtime_state/runs_history.jsonl` и возвращает
JSON для Owner panel /observability dashboard.
"""

from __future__ import annotations

from typing import Annotated

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

    return router
