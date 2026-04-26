# -*- coding: utf-8 -*-
"""
Extras router — Phase 2 Wave F extraction (Session 25).

Первая RouterContext-based extraction в Krab проекте. Демонстрирует
factory-pattern (``build_extras_router(ctx)``) вместо module-level router.

Endpoints:
- GET /api/links   — статические ссылки экосистемы (через ctx.public_base_url())
- GET /api/uptime  — uptime + boot_ts (через ctx.get_boot_ts() — shared holder)

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.
"""

from __future__ import annotations

import os
import time

from fastapi import APIRouter

from ._context import RouterContext


def build_extras_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с /api/links и /api/uptime."""
    router = APIRouter(tags=["extras"])

    @router.get("/api/links")
    async def get_links() -> dict:
        """Ссылки по экосистеме в одном месте."""
        base = ctx.public_base_url()
        return {
            "dashboard": base,
            "stats_api": f"{base}/api/stats",
            "health_api": f"{base}/api/health",
            "health_lite_api": f"{base}/api/health/lite",
            "ecosystem_health_api": f"{base}/api/ecosystem/health",
            "links_api": f"{base}/api/links",
            "openclaw_cloud_api": f"{base}/api/openclaw/cloud",
            "runtime_handoff_api": f"{base}/api/runtime/handoff",
            "runtime_recover_api": f"{base}/api/runtime/recover",
            "context_checkpoint_api": f"{base}/api/context/checkpoint",
            "context_transition_pack_api": f"{base}/api/context/transition-pack",
            "context_latest_api": f"{base}/api/context/latest",
            "voice_gateway": os.getenv("VOICE_GATEWAY_URL", "http://127.0.0.1:8090"),
            "openclaw": os.getenv("OPENCLAW_BASE_URL", "http://127.0.0.1:18789"),
        }

    @router.get("/api/uptime")
    async def get_uptime() -> dict:
        """Uptime Краба в секундах."""
        boot = ctx.get_boot_ts()
        return {"ok": True, "uptime_sec": round(time.time() - boot), "boot_ts": boot}

    return router
