# -*- coding: utf-8 -*-
"""
Health router — Phase 2 Wave X extraction (Session 25).

Объединяет health и ecosystem read-only endpoints:
- GET /api/health                 — единый health для web-панели (uses EcosystemHealthService)
- GET /api/health/lite            — fast liveness (runtime_lite snapshot)
- GET /api/health/deep            — расширенная диагностика (12 секций, Session 24)
- GET /api/v1/health              — versioned health для внешних мониторов
- GET /api/ecosystem/health       — расширенный health 3-проектной экосистемы
- GET /api/ecosystem/health/debug — raw collector output для diagnose
- GET /api/ecosystem/health/export — экспорт ecosystem health в JSON file

Wave CC (Session 25): /api/health/deep extracted. Existing tests
(``test_api_health_deep.py``, ``test_health_deep_session24.py``,
``test_health_deep_orphans.py``) патчат
``src.core.health_deep_collector.collect_health_deep`` напрямую,
поэтому extraction safe — функция импортируется внутри handler'а.

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.

Все endpoints — read-only (GET без write_access checks).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ._context import RouterContext


def build_health_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с health + ecosystem endpoints."""
    router = APIRouter(tags=["health"])

    # ── /api/health ─────────────────────────────────────────────────────────

    @router.get("/api/health")
    async def get_health() -> dict:
        """Единый health статусов для web-панели."""
        from ...core.ecosystem_health import EcosystemHealthService

        router_dep = ctx.deps["router"]
        openclaw = ctx.get_dep("openclaw_client")
        voice_gateway = ctx.get_dep("voice_gateway_client")
        krab_ear = ctx.get_dep("krab_ear_client")
        lite_snapshot = await ctx.collect_runtime_lite()
        lm_state = str(lite_snapshot.get("lmstudio_model_state") or "unknown").strip().lower()
        local_ok = lm_state in {"loaded", "idle"}
        ecosystem = EcosystemHealthService(
            router=router_dep,
            openclaw_client=openclaw,
            voice_gateway_client=voice_gateway,
            krab_ear_client=krab_ear,
            local_health_override={
                "ok": local_ok,
                "status": "ok" if local_ok else (lm_state or "down"),
                "degraded": not local_ok,
                "latency_ms": 0,
                "source": "web_app.lite_snapshot",
            },
        )
        report = await ecosystem.collect()
        return {
            "status": "ok",
            "checks": {
                "openclaw": bool(report["checks"]["openclaw"]["ok"]),
                "local_lm": local_ok,
                "voice_gateway": bool(report["checks"]["voice_gateway"]["ok"]),
                "krab_ear": bool(report["checks"]["krab_ear"]["ok"]),
            },
            "degradation": str(report["degradation"]),
            "risk_level": str(report["risk_level"]),
            "chain": report["chain"],
        }

    # ── /api/health/lite ────────────────────────────────────────────────────

    @router.get("/api/health/lite")
    async def get_health_lite() -> dict:
        """
        Быстрый liveness-check web-панели.

        Важно:
        - не тянет deep ecosystem probes;
        - используется daemon-скриптами и uptime-watch для проверки
          «жив ли HTTP-процесс», а не «все ли внешние зависимости сейчас быстрые».
        """
        # Импорт через web_app namespace — чтобы существующие тесты,
        # патчащие `_resolve_memory_indexer_state` через WebApp module,
        # продолжали работать. Dual-patch стратегия (Wave W).
        from .. import web_app as _wam

        runtime = await ctx.collect_runtime_lite()
        # B.7 (session 4): telegram_rate_limiter stats для /stats dashboard.
        try:
            from ...core.telegram_rate_limiter import telegram_rate_limiter as _trl

            _rate_limiter_stats = _trl.stats()
        except Exception:
            _rate_limiter_stats = None
        result = {
            "ok": True,
            "status": "up",
            "telegram_session_state": runtime.get("telegram_session_state"),
            "telegram_userbot_state": (
                (runtime.get("telegram_userbot") or {}).get("startup_state")
            ),
            "telegram_userbot_client_connected": (
                (runtime.get("telegram_userbot") or {}).get("client_connected")
            ),
            "telegram_userbot_error_code": (
                (runtime.get("telegram_userbot") or {}).get("startup_error_code")
            ),
            "lmstudio_model_state": runtime.get("lmstudio_model_state"),
            "openclaw_auth_state": runtime.get("openclaw_auth_state"),
            "last_runtime_route": runtime.get("last_runtime_route"),
            "scheduler_enabled": runtime.get("scheduler_enabled"),
            "inbox_summary": runtime.get("inbox_summary"),
            "voice_gateway_configured": runtime.get("voice_gateway_configured"),
            "memory_indexer_state": _wam._resolve_memory_indexer_state(),
            "memory_indexer_queue_size": _wam._resolve_memory_indexer_queue_size(),
        }
        if _rate_limiter_stats is not None:
            result["telegram_rate_limiter"] = _rate_limiter_stats
        return result

    # ── /api/health/deep ────────────────────────────────────────────────────

    @router.get("/api/health/deep")
    async def get_health_deep() -> dict:
        """Расширенная диагностика Краба — структурированный JSON для Dashboard V4.

        Зеркало !health deep (Wave 29-EE), но возвращает dict вместо markdown.
        Включает: krab process, openclaw, lm_studio, archive_db,
        reminders, memory_validator, sigterm_recent_count, system,
        + Session 24 (8f0da60): sentry, mcp_servers, cf_tunnel, error_rate_5m.
        """
        from ...core.health_deep_collector import collect_health_deep

        userbot = ctx.get_dep("userbot")
        session_start = getattr(userbot, "_session_start_time", None) if userbot else None
        return await collect_health_deep(session_start_time=session_start)

    # ── /api/v1/health ──────────────────────────────────────────────────────

    @router.get("/api/v1/health")
    async def health_v1() -> dict:
        """Versioned health endpoint для внешних мониторов."""
        try:
            health = await ctx.collect_runtime_lite()
            return {
                "ok": True,
                "version": "1",
                "status": health.get("status", "unknown"),
                "telegram": health.get("telegram_userbot_state", "unknown"),
                "gateway": health.get("openclaw_auth_state", "unknown"),
                "uptime_probe": "pass",
            }
        except Exception as exc:
            return {"ok": False, "version": "1", "error": str(exc)}

    # ── /api/ecosystem/health ───────────────────────────────────────────────

    @router.get("/api/ecosystem/health")
    async def ecosystem_health() -> dict:
        """[R11] Расширенный health-отчет 3-проектной экосистемы с метриками ресурсов."""
        from ...core.ecosystem_health import EcosystemHealthService

        health_service = ctx.get_dep("health_service")
        if not health_service:
            # Fallback для совместимости, если сервис не в депсах
            router_dep = ctx.deps["router"]
            openclaw = ctx.get_dep("openclaw_client")
            voice_gateway = ctx.get_dep("voice_gateway_client")
            krab_ear = ctx.get_dep("krab_ear_client")
            health_service = EcosystemHealthService(
                router=router_dep,
                openclaw_client=openclaw,
                voice_gateway_client=voice_gateway,
                krab_ear_client=krab_ear,
            )
        report = await health_service.collect()
        return {"ok": True, "report": report}

    # ── /api/ecosystem/health/debug ─────────────────────────────────────────

    @router.get("/api/ecosystem/health/debug")
    async def ecosystem_health_debug(section: str = "") -> dict:
        """Raw health collector output + full dict для diagnose.

        Query params:
        - section: filter one section (session_10, session_12, runtime_route, etc.)
        """
        try:
            health_svc = ctx.get_dep("health_service")
            if health_svc is None:
                router_dep = ctx.get_dep("router")
                if router_dep is None:
                    return {"error": "router_not_found_in_deps"}
                from ...core.ecosystem_health import EcosystemHealthService

                health_svc = EcosystemHealthService(router=router_dep)
            direct = health_svc._collect_session_12_stats()
            full = await health_svc.collect()

            response: dict = {
                "direct": direct,
                "full_has_session_12": "session_12" in full,
                "full_keys": list(full.keys()),
            }

            if section:
                response["section_filter"] = section
                response["full_section"] = full.get(section)
            else:
                response["full_session_12"] = full.get("session_12")

            return response
        except Exception as exc:
            import traceback

            return {"error": str(exc), "trace": traceback.format_exc()[:500]}

    # ── /api/ecosystem/health/export ────────────────────────────────────────

    @router.get("/api/ecosystem/health/export")
    async def ecosystem_health_export() -> FileResponse:
        """Экспортирует расширенный ecosystem health report в JSON-файл."""
        from ...core.ecosystem_health import EcosystemHealthService

        router_dep = ctx.deps["router"]
        openclaw = ctx.get_dep("openclaw_client")
        voice_gateway = ctx.get_dep("voice_gateway_client")
        krab_ear = ctx.get_dep("krab_ear_client")
        payload = await EcosystemHealthService(
            router=router_dep,
            openclaw_client=openclaw,
            voice_gateway_client=voice_gateway,
            krab_ear_client=krab_ear,
        ).collect()
        ops_dir = Path("artifacts/ops")
        ops_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
        out_path = ops_dir / f"ecosystem_health_web_{stamp}.json"
        with out_path.open("w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
        return FileResponse(
            str(out_path),
            media_type="application/json",
            filename=out_path.name,
        )

    return router
