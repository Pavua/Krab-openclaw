# -*- coding: utf-8 -*-
"""
Voice router — Phase 2 Wave L extraction (Session 25).

Простые voice-домен endpoints через RouterContext. Не extract'ятся endpoints
требующие ``self._collect_runtime_lite_snapshot()`` или большой объём env-логики
(``/api/transcriber/status`` — отложен).

Endpoints:
- GET  /api/voice/profile         — voice runtime profile snapshot
- GET  /api/voice/runtime         — voice runtime сводка userbot
- POST /api/voice/runtime/update  — обновление voice runtime profile (write)
- POST /api/voice/toggle          — toggle voice mode (write)
- GET  /api/krab_ear/status       — KrabEar STT diarization status

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request

from ._context import RouterContext


def build_voice_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с voice/krab_ear endpoints."""
    router = APIRouter(tags=["voice"])

    # ---------- GET /api/voice/profile ------------------------------------
    @router.get("/api/voice/profile")
    async def voice_profile() -> dict:
        """Голосовой профиль runtime."""
        kraab = ctx.get_dep("kraab_userbot")
        return {"ok": True, "profile": kraab.get_voice_runtime_profile()}

    # ---------- GET /api/voice/runtime ------------------------------------
    @router.get("/api/voice/runtime")
    async def voice_runtime_status() -> dict:
        """Возвращает сводку по voice-runtime userbot."""
        kraab_userbot = ctx.get_dep("kraab_userbot")
        if not kraab_userbot or not hasattr(kraab_userbot, "get_voice_runtime_profile"):
            return {"ok": False, "error": "voice_runtime_not_available"}
        profile = dict(kraab_userbot.get_voice_runtime_profile() or {})
        return {"ok": True, "voice": profile}

    # ---------- POST /api/voice/runtime/update ----------------------------
    @router.post("/api/voice/runtime/update")
    async def voice_runtime_update(
        request: Request,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Обновляет voice-runtime profile userbot через owner web-key."""
        ctx.assert_write_access(x_krab_web_key, token)
        kraab_userbot = ctx.get_dep("kraab_userbot")
        if not kraab_userbot or not hasattr(kraab_userbot, "update_voice_runtime_profile"):
            raise HTTPException(status_code=503, detail="voice_runtime_not_available")
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="voice_update_body_required")
        profile = dict(
            kraab_userbot.update_voice_runtime_profile(
                enabled=body.get("enabled") if "enabled" in body else None,
                speed=body.get("speed") if "speed" in body else None,
                voice=body.get("voice") if "voice" in body else None,
                delivery=body.get("delivery") if "delivery" in body else None,
                persist=True,
            )
            or {}
        )
        return {"ok": True, "voice": profile}

    # ---------- POST /api/voice/toggle ------------------------------------
    @router.post("/api/voice/toggle")
    async def voice_toggle(
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Toggle voice mode через API."""
        ctx.assert_write_access(x_krab_web_key, token)
        kraab = ctx.get_dep("kraab_userbot")
        current = bool(getattr(kraab, "voice_mode", False))
        new_state = bool(payload.get("enabled", not current))
        kraab.voice_mode = new_state
        return {"ok": True, "voice_enabled": new_state}

    # ---------- GET /api/krab_ear/status ----------------------------------
    @router.get("/api/krab_ear/status")
    async def krab_ear_status() -> dict:
        """KrabEar STT diarization status and readiness."""
        import structlog

        _logger = structlog.get_logger("WebApp.voice_router")
        krab_ear = ctx.get_dep("krab_ear_client")
        if not krab_ear:
            return {
                "ok": False,
                "status": "unavailable",
                "error": "krab_ear_client_not_available",
            }
        try:
            report = await krab_ear.health_report()
            return {
                "ok": report.get("ok", False),
                "status": report.get("status", "unknown"),
                "latency_ms": report.get("latency_ms"),
                "source": report.get("source"),
                "detail": report.get("detail"),
            }
        except Exception as exc:  # noqa: BLE001
            _logger.warning("krab_ear_status_failed", error=str(exc))
            return {"ok": False, "status": "error", "error": str(exc)}

    return router
