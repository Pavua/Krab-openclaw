# -*- coding: utf-8 -*-
"""
Policy router — Phase 2 Wave I extraction (Session 25).

RouterContext-based extraction для unified policy matrix endpoint.

Endpoints:
- GET /api/policy/matrix — unified policy matrix для owner/full/partial/guest

Контракт ответа сохранён 1:1 с inline definition из web_app.py.

Skipped (требует ai_runtime + полного снимка контракта):
- /api/policy — нативно нуждается в ai_runtime.get_policy_snapshot();
  оставлен в web_app.py до отдельной волны.
"""

from __future__ import annotations

from fastapi import APIRouter

from ._context import RouterContext


def build_policy_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с /api/policy/matrix."""
    router = APIRouter(tags=["policy"])

    @router.get("/api/policy/matrix")
    async def get_policy_matrix() -> dict:
        """Возвращает unified policy matrix для owner/full/partial/guest."""
        runtime_lite = await ctx.collect_runtime_lite()
        return {
            "ok": True,
            "policy_matrix": ctx.policy_matrix_snapshot(runtime_lite=runtime_lite),
        }

    return router
