# -*- coding: utf-8 -*-
"""
Runtime inspect router — Phase 2 Wave G extraction (Session 25).

RouterContext-based extraction для простых ai_runtime-зависимых GET endpoints.

Endpoints:
- GET /api/queue   — состояние per-chat queue_manager
- GET /api/ctx     — context snapshots (по chat_id или все чаты)

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.
Skipped:
- /api/capabilities/registry — требует WebApp._collect_runtime_lite_snapshot/
  _capability_registry_snapshot helpers (не promoted в _helpers).
- /api/channels/capabilities — то же самое (требует _channel_capabilities_snapshot).
- /api/policy / /api/policy/matrix — требует _policy_matrix_snapshot.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from ._context import RouterContext


def build_runtime_inspect_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с /api/queue + /api/ctx."""
    router = APIRouter(tags=["runtime-inspect"])

    @router.get("/api/queue")
    async def get_queue() -> dict:
        """Возвращает состояние per-chat очередей автообработки."""
        ai_runtime = ctx.get_dep("ai_runtime")
        if not ai_runtime or not hasattr(ai_runtime, "queue_manager"):
            return {"ok": False, "error": "queue_not_configured"}
        return {"ok": True, "queue": ai_runtime.queue_manager.get_stats()}

    @router.get("/api/ctx")
    async def get_ctx(chat_id: int | None = Query(default=None)) -> dict:
        """Snapshot контекста последнего запроса (по чату или все чаты)."""
        ai_runtime = ctx.get_dep("ai_runtime")
        if not ai_runtime:
            return {"ok": False, "error": "ai_runtime_not_configured"}
        if chat_id is None:
            if not hasattr(ai_runtime, "get_context_snapshots"):
                return {"ok": False, "error": "ctx_not_supported"}
            return {"ok": True, "contexts": ai_runtime.get_context_snapshots()}
        return {"ok": True, "context": ai_runtime.get_context_snapshot(int(chat_id))}

    return router
