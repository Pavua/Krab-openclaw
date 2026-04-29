# -*- coding: utf-8 -*-
"""
Write router — Phase 2 Wave J extraction (Session 25).

Простые POST endpoints, использующие ``ctx.assert_write_access(header, token)``
для auth-проверки. Endpoints сохраняют исходный контракт 1:1 с inline-versions
из ``web_app.py``.

Endpoints:
- POST /api/notify/toggle  — toggle TOOL_NARRATION_ENABLED.
- POST /api/silence/toggle — silence mode (global/per-chat) on/off.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Header, Query

from ._context import RouterContext


def build_write_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с simple write-endpoints."""
    router = APIRouter(tags=["write"])

    @router.post("/api/notify/toggle")
    async def notify_toggle(
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Toggle tool narration через API."""
        ctx.assert_write_access(x_krab_web_key, token)
        from src.config import config

        enabled = bool(payload.get("enabled", not getattr(config, "TOOL_NARRATION_ENABLED", True)))
        config.update_setting("TOOL_NARRATION_ENABLED", "1" if enabled else "0")
        return {"ok": True, "enabled": enabled}

    @router.post("/api/silence/toggle")
    async def silence_toggle(
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Toggle silence mode через API."""
        ctx.assert_write_access(x_krab_web_key, token)
        from src.core.silence_mode import silence_manager

        chat_id = str(payload.get("chat_id") or "").strip()
        minutes = int(payload.get("minutes") or 30)
        global_mode = bool(payload.get("global", False))
        if global_mode:
            if silence_manager.is_global_muted():
                silence_manager.unmute_global()
                return {"ok": True, "action": "unmuted_global"}
            silence_manager.mute_global(minutes=minutes)
            return {"ok": True, "action": "muted_global", "minutes": minutes}
        if not chat_id:
            return {"ok": False, "error": "chat_id required for per-chat silence"}
        if silence_manager.is_silenced(chat_id):
            silence_manager.unmute(chat_id)
            return {"ok": True, "action": "unmuted", "chat_id": chat_id}
        silence_manager.mute(chat_id, minutes=minutes)
        return {"ok": True, "action": "muted", "chat_id": chat_id, "minutes": minutes}

    return router
