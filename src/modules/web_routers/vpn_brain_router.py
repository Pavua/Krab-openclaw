# -*- coding: utf-8 -*-
"""
VPN Brain router — Phase B (VPN Phase B).

POST /api/vpn/help — endpoint для VPN-бота (@pablito_vpn_bot). VPN-бот
проксирует freeform друзей сюда; Krab отвечает persona drift + memory + AI.

Контракт запроса::

    {
        "friend_id": "<telegram_user_id_or_slug>",
        "friend_name": "<display_name>",
        "question": "<freeform>",
        "context": {...}  // опционально
    }

Контракт ответа::

    {
        "ok": true,
        "text": "...",
        "confidence": 0.85,
        "suggested_action": "reissue_key" | null,
        "latency_ms": 412
    }
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Header, HTTPException, Query

from ._context import RouterContext


def build_vpn_brain_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с /api/vpn/help."""
    router = APIRouter(tags=["vpn"])

    @router.post("/api/vpn/help")
    async def vpn_help(
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Принимает freeform вопрос друга от VPN-бота, отвечает текстом."""
        ctx.assert_write_access(x_krab_web_key, token)

        friend_id = str(payload.get("friend_id") or "").strip()
        friend_name = str(payload.get("friend_name") or "").strip()
        question = str(payload.get("question") or "").strip()
        context = payload.get("context")

        if not friend_id:
            raise HTTPException(status_code=400, detail="friend_id required")
        if not friend_name:
            raise HTTPException(status_code=400, detail="friend_name required")
        if not question:
            raise HTTPException(status_code=400, detail="question required")
        if context is not None and not isinstance(context, dict):
            raise HTTPException(status_code=400, detail="context must be an object")

        from src.core.vpn_brain import vpn_brain

        answer = await vpn_brain.answer_friend_question(
            friend_id=friend_id,
            friend_name=friend_name,
            question=question,
            context=context,
        )

        return {
            "ok": True,
            "text": answer.text,
            "confidence": answer.confidence,
            "suggested_action": answer.suggested_action,
            "latency_ms": answer.latency_ms,
        }

    return router
