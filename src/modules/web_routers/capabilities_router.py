# -*- coding: utf-8 -*-
"""
Capabilities router — Phase 2 Wave R extraction (Session 25).

RouterContext-based extraction для capability/channels/policy endpoints.

Endpoints:
- GET /api/capabilities/registry  — единый capability registry
- GET /api/channels/capabilities  — channel capability parity snapshot
- GET /api/policy                 — runtime AI policy snapshot + matrix

Контракт ответа сохранён 1:1 с inline definition из web_app.py.

Dependencies (через RouterContext.deps, инжектируется в _make_router_context):
- ``capability_registry_snapshot_helper`` — async (runtime_lite=...) -> dict
- ``channel_capabilities_snapshot_helper`` — sync (runtime_lite=, policy_matrix=) -> dict
- ``ai_runtime`` — обладатель ``get_policy_snapshot()``
"""

from __future__ import annotations

from fastapi import APIRouter

from ._context import RouterContext


def build_capabilities_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с capability/channels/policy endpoints."""
    router = APIRouter(tags=["capabilities"])

    @router.get("/api/capabilities/registry")
    async def capability_registry() -> dict:
        """Возвращает единый capability registry поверх truthful runtime-срезов."""
        runtime_lite = await ctx.collect_runtime_lite()
        helper = ctx.get_dep("capability_registry_snapshot_helper")
        if helper is None:
            return {"ok": False, "error": "capability_registry_helper_unavailable"}
        return await helper(runtime_lite=runtime_lite)

    @router.get("/api/channels/capabilities")
    async def channel_capabilities() -> dict:
        """Возвращает unified channel capability parity snapshot."""
        runtime_lite = await ctx.collect_runtime_lite()
        policy_matrix = ctx.policy_matrix_snapshot(runtime_lite=runtime_lite)
        helper = ctx.get_dep("channel_capabilities_snapshot_helper")
        if helper is None:
            return {"ok": False, "error": "channel_capabilities_helper_unavailable"}
        return {
            "ok": True,
            "channel_capabilities": helper(
                runtime_lite=runtime_lite,
                policy_matrix=policy_matrix,
            ),
        }

    @router.get("/api/policy")
    async def get_policy() -> dict:
        """Возвращает runtime-политику AI (queue/guardrails/reactions)."""
        ai_runtime = ctx.get_dep("ai_runtime")
        runtime_lite = await ctx.collect_runtime_lite()
        policy_matrix = ctx.policy_matrix_snapshot(runtime_lite=runtime_lite)
        if not ai_runtime:
            return {
                "ok": False,
                "error": "ai_runtime_not_configured",
                "policy_matrix": policy_matrix,
            }
        return {
            "ok": True,
            "policy": ai_runtime.get_policy_snapshot(),
            "policy_matrix": policy_matrix,
        }

    return router
