# -*- coding: utf-8 -*-
"""
Chat Policy router — Smart Routing Phase 4 (Session 26).

Endpoints:
- GET    /api/chat/policy/{chat_id}  — read policy
- POST   /api/chat/policy/{chat_id}  — update (write-protected)
- GET    /api/chat/policies          — list all custom policies (filter ?mode=)
- DELETE /api/chat/policy/{chat_id}  — reset (write-protected)

Underlying store: ``src.core.chat_response_policy.ChatResponsePolicyStore``
(Smart Routing Phase 1).

Tests injectируют свой store через ``ctx.deps['chat_policy_store']``;
production использует module-level singleton ``get_store()``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException, Query

from ._context import RouterContext


def _get_store(ctx: RouterContext):  # noqa: ANN202
    """Resolve store: ctx.deps override → singleton fallback."""
    store = ctx.get_dep("chat_policy_store")
    if store is not None:
        return store
    try:
        from ...core.chat_response_policy import get_store as _singleton

        return _singleton()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503, detail=f"chat_policy_store_unavailable: {exc}"
        ) from exc


def _serialize_policy(policy) -> dict[str, Any]:  # noqa: ANN001
    data = policy.to_dict()
    data["effective_threshold"] = policy.effective_threshold()
    return data


def build_chat_policy_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с chat policy endpoints."""
    router = APIRouter(tags=["chat_policy"])

    @router.get("/api/chat/policy/{chat_id}")
    async def get_chat_policy(chat_id: str) -> dict:
        store = _get_store(ctx)
        policy = store.get_policy(chat_id)
        return {"ok": True, "policy": _serialize_policy(policy)}

    @router.post("/api/chat/policy/{chat_id}")
    async def update_chat_policy(
        chat_id: str,
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        ctx.assert_write_access_fn(x_krab_web_key, token)
        store = _get_store(ctx)
        # Whitelist fields
        allowed = {
            "mode",
            "threshold_override",
            "auto_adjust_enabled",
            "blocked_topics",
            "notes",
        }
        fields = {k: v for k, v in payload.items() if k in allowed}
        # Validations
        if "mode" in fields:
            if fields["mode"] not in {"silent", "cautious", "normal", "chatty"}:
                raise HTTPException(status_code=400, detail="invalid_mode")
        if "threshold_override" in fields and fields["threshold_override"] is not None:
            try:
                v = float(fields["threshold_override"])
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="invalid_threshold") from exc
            if not 0.0 <= v <= 1.0:
                raise HTTPException(status_code=400, detail="threshold_out_of_range")
            fields["threshold_override"] = v
        if "blocked_topics" in fields and not isinstance(fields["blocked_topics"], list):
            raise HTTPException(status_code=400, detail="blocked_topics_must_be_list")
        try:
            policy = store.update_policy(chat_id, **fields)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"update_failed: {exc}") from exc
        return {"ok": True, "policy": _serialize_policy(policy)}

    @router.get("/api/chat/policies")
    async def list_chat_policies(mode: str | None = Query(default=None)) -> dict:
        store = _get_store(ctx)
        policies = store.list_all()
        if mode:
            policies = [p for p in policies if p.mode.value == mode]
        return {
            "ok": True,
            "count": len(policies),
            "policies": [_serialize_policy(p) for p in policies],
        }

    @router.delete("/api/chat/policy/{chat_id}")
    async def reset_chat_policy(
        chat_id: str,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        ctx.assert_write_access_fn(x_krab_web_key, token)
        store = _get_store(ctx)
        existed = store.reset_policy(chat_id)
        return {"ok": True, "existed": existed, "chat_id": chat_id}

    return router


__all__ = ["build_chat_policy_router"]
