# -*- coding: utf-8 -*-
"""
System router — Phase 2 Wave Y extraction (Session 25).

Объединяет runtime/stats/system read-only endpoints, агрегирующих состояние
Краба для Dashboard V4 и diagnostics:

- GET /api/runtime/operator-profile — machine-readable профиль учётки/runtime
- GET /api/runtime/summary          — единый summary (health/route/costs/swarm/...)
- GET /api/dashboard/summary        — Dashboard V4 агрегатор (15 источников в один)
- GET /api/stats                    — router/black_box/rag stats
- GET /api/stats/caches             — chat_ban/capability/voice cache counts
- GET /api/system/diagnostics       — RAM/CPU/budget/local LLM diagnostics

POST endpoints (`/api/runtime/recover`, `/api/runtime/chat-session/clear`,
`/api/runtime/repair-active-shared-permissions`) сохранены inline — Wave Y
ограничивается read-only extraction.

Helper-методы WebApp (``_runtime_operator_profile``,
``_build_stats_router_payload``, ``_resolve_local_runtime_truth``)
инжектируются через ``ctx.deps`` factory'ем ``_make_router_context``.
Контракт ответов сохранён 1:1 с inline definitions из web_app.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter

from ._context import RouterContext


def build_system_router(ctx: RouterContext) -> APIRouter:
    """Factory: APIRouter с runtime/stats/system endpoints."""
    router = APIRouter(tags=["system"])

    # ── /api/runtime/operator-profile ───────────────────────────────────────

    @router.get("/api/runtime/operator-profile")
    async def runtime_operator_profile() -> dict:
        """Profile текущей учётки/runtime для multi-account handoff."""
        helper = ctx.get_dep("runtime_operator_profile_helper")
        profile = helper() if callable(helper) else {}
        return {"ok": True, "profile": profile}

    # ── /api/runtime/summary ────────────────────────────────────────────────

    @router.get("/api/runtime/summary")
    async def runtime_summary() -> dict:
        """Единый summary endpoint — полное состояние Краба одним запросом."""
        from ...config import config
        from ...core.cost_analytics import cost_analytics as _ca
        from ...core.silence_mode import silence_manager
        from ...core.swarm_task_board import swarm_task_board
        from ...core.swarm_team_listener import is_listeners_enabled
        from ...openclaw_client import openclaw_client as _oc

        try:
            health = await ctx.collect_runtime_lite()
        except Exception:  # noqa: BLE001
            health = {}

        kraab = ctx.get_dep("kraab_userbot")
        translator_profile: Any = {}
        translator_session: Any = {}
        if kraab is not None:
            try:
                translator_profile = kraab.get_translator_runtime_profile()
            except Exception:  # noqa: BLE001
                translator_profile = {}
            try:
                translator_session = kraab.get_translator_session_state()
            except Exception:  # noqa: BLE001
                translator_session = {}

        return {
            "ok": True,
            "health": health,
            "route": _oc.get_last_runtime_route(),
            "costs": _ca.build_usage_report_dict(),
            "translator": {
                "profile": translator_profile,
                "session": translator_session,
            },
            "swarm": {
                "task_board": swarm_task_board.get_board_summary(),
                "listeners_enabled": is_listeners_enabled(),
            },
            "silence": silence_manager.status(),
            "notify_enabled": bool(getattr(config, "TOOL_NARRATION_ENABLED", True)),
        }

    # ── /api/dashboard/summary ──────────────────────────────────────────────

    @router.get("/api/dashboard/summary")
    async def dashboard_summary() -> dict:
        """Dashboard V4 aggregator — один запрос вместо 15."""
        from ...core.dashboard_summary import collect_dashboard_summary_async

        boot = ctx.get_boot_ts()
        router_dep = ctx.get_dep("router")
        return await collect_dashboard_summary_async(boot_ts=boot, router=router_dep)

    # ── /api/stats ──────────────────────────────────────────────────────────

    @router.get("/api/stats")
    async def get_stats() -> dict:
        """Главный stats endpoint (router/black_box/rag)."""
        router_dep = ctx.deps["router"]
        black_box = ctx.get_dep("black_box")
        rag = getattr(router_dep, "rag", None)

        builder = ctx.get_dep("build_stats_router_payload_helper")
        if callable(builder):
            router_payload = await builder(router_dep)
        else:
            router_payload = {}

        return {
            "router": router_payload,
            "black_box": black_box.get_stats()
            if black_box and hasattr(black_box, "get_stats")
            else {"enabled": False},
            "rag": rag.get_stats()
            if rag and hasattr(rag, "get_stats")
            else {"enabled": False, "count": 0},
        }

    # ── /api/stats/caches ───────────────────────────────────────────────────

    @router.get("/api/stats/caches")
    async def get_stats_caches() -> dict:
        """Aggregated cache-метрики для /stats dashboard."""
        try:
            from ...core.chat_ban_cache import chat_ban_cache as _cbc

            ban_entries = _cbc.list_entries()
            ban_count = len(ban_entries)
        except Exception:  # noqa: BLE001
            ban_count = 0

        try:
            from ...core.chat_capability_cache import chat_capability_cache as _ccc

            cap_entries = _ccc.list_entries()
            cap_count = len(cap_entries)
            voice_disallowed = sum(1 for e in cap_entries if e.get("voice_allowed") is False)
            slow_mode = sum(
                1
                for e in cap_entries
                if isinstance(e.get("slow_mode_seconds"), (int, float))
                and e["slow_mode_seconds"] > 0
            )
        except Exception:  # noqa: BLE001
            cap_count = 0
            voice_disallowed = 0
            slow_mode = 0

        try:
            userbot = ctx.get_dep("kraab_userbot")
            blocked = userbot.get_voice_blocked_chats() if userbot else []
            voice_blocked_count = len(blocked)
        except Exception:  # noqa: BLE001
            voice_blocked_count = 0

        return {
            "ban_cache_count": ban_count,
            "capability_cache_count": cap_count,
            "voice_blocked_count": voice_blocked_count,
            "capability_voice_disallowed": voice_disallowed,
            "capability_slow_mode": slow_mode,
        }

    # ── /api/system/diagnostics ─────────────────────────────────────────────

    @router.get("/api/system/diagnostics")
    async def system_diagnostics() -> dict:
        """[R11] Глубокая диагностика сервера (RAM/CPU/Бюджет/Local LLM)."""
        from ...core.ecosystem_health import EcosystemHealthService

        router_dep = ctx.get_dep("router")
        if not router_dep:
            return {"ok": False, "error": "router_not_found"}

        health_service = ctx.get_dep("health_service")
        if not health_service:
            health_service = EcosystemHealthService(router=router_dep)

        health_data = await health_service.collect()

        truth_helper = ctx.get_dep("resolve_local_runtime_truth_helper")
        if callable(truth_helper):
            local_truth = await truth_helper(router_dep)
        else:
            local_truth = {}

        status = "ok"
        if not bool(local_truth.get("runtime_reachable")):
            status = "degraded"
            if getattr(router_dep, "active_tier", "") == "default":
                status = "failed"
        elif getattr(router_dep, "active_tier", "") == "paid":
            status = "degraded"

        watchdog_dep = ctx.get_dep("watchdog")
        return {
            "ok": True,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "resources": health_data.get("resources", {}),
            "budget": health_data.get("budget", {}),
            "local_ai": {
                "engine": local_truth.get("engine", getattr(router_dep, "local_engine", "unknown")),
                "model": local_truth.get("active_model", ""),
                "available": bool(local_truth.get("runtime_reachable")),
                "loaded_models": local_truth.get("loaded_models", []),
            },
            "watchdog": {
                "last_recoveries": getattr(watchdog_dep, "last_recovery_attempt", {})
                if watchdog_dep
                else {}
            },
        }

    return router
