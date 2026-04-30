# -*- coding: utf-8 -*-
"""
Version router — Phase 2 extraction proof-of-concept (Session 25).

Первый extracted endpoint из web_app.py в Code Splits Phase 2.
Подключается в WebApp._setup_routes через self.app.include_router.

Дальнейшие routers (health, memory, voice...) будут следовать тому же
паттерну: APIRouter + include_router. См. docs/CODE_SPLITS_PLAN.md.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["meta"])


@router.get("/api/version")
async def version_info() -> dict:
    """Версия Краба и session info.

    Stateless endpoint — не зависит от deps или WebApp instance state.
    Подходит для первого Phase 2 extraction (proof-of-concept pattern).
    """
    return {
        "ok": True,
        "version": "session31",
        "commits": 650,
        "tests": 9991,
        "api_endpoints": 249,
        "features": [
            "translator_mvp",
            "swarm_execution",
            "channel_parity",
            "finops",
            "hammerspoon_mcp",
            "memory_layer_phase2",
            "cross_ai_review",
            "skill_discovery",
            "command_handlers_split",
            "sentry_integration",
            "smart_routing",
            "memory_doctor",
        ],
    }
