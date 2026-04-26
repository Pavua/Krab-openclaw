# -*- coding: utf-8 -*-
"""
OpenClaw router — Phase 2 Wave M extraction (Session 25).

Простые OpenClaw GET endpoints через RouterContext. Только endpoints, которые
работают исключительно через ``ctx.get_dep("openclaw_client")`` без вызовов
WebApp helper-методов (``_collect_openclaw_*_snapshot``,
``_load_openclaw_runtime_config`` и пр.) и без мутации ``_runtime_lite_cache``.

Endpoints:
- GET /api/openclaw/report             — health-report OpenClaw
- GET /api/openclaw/deep-check         — расширенная проверка
- GET /api/openclaw/remediation-plan   — план исправлений
- GET /api/openclaw/cloud/tier/state   — диагностика Cloud Tier State

SKIP (HARD, требуют helper promote):
- /api/openclaw/cron/status, /api/openclaw/cron/jobs — `_collect_openclaw_cron_*`
- /api/openclaw/channels/status        — `_collect_openclaw_channels_snapshot`
- /api/openclaw/runtime-config         — `_load_openclaw_runtime_config`
- /api/openclaw/cloud, /api/openclaw/cloud/diagnostics — `_openclaw_cloud_diagnostics_impl`
- /api/openclaw/cloud/runtime-check    — мутирует `self._runtime_lite_cache`

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.
"""

from __future__ import annotations

from fastapi import APIRouter

from src.core.observability import build_ops_response

from ._context import RouterContext


def build_openclaw_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с openclaw GET endpoints."""
    router = APIRouter(tags=["openclaw"])

    # ---------- GET /api/openclaw/report ----------------------------------
    @router.get("/api/openclaw/report")
    async def openclaw_report() -> dict:
        """Агрегированный health-report OpenClaw."""
        openclaw = ctx.get_dep("openclaw_client")
        if not openclaw:
            return {"available": False, "error": "openclaw_client_not_configured"}
        if not hasattr(openclaw, "get_health_report"):
            return {"available": False, "error": "openclaw_report_not_supported"}
        try:
            report = await openclaw.get_health_report()
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "error": "openclaw_report_failed", "detail": str(exc)}
        return {"available": True, "report": report}

    # ---------- GET /api/openclaw/deep-check ------------------------------
    @router.get("/api/openclaw/deep-check")
    async def openclaw_deep_check() -> dict:
        """Расширенная проверка OpenClaw (включая tool smoke и remediation)."""
        openclaw = ctx.get_dep("openclaw_client")
        if not openclaw:
            return {"available": False, "error": "openclaw_client_not_configured"}
        if not hasattr(openclaw, "get_deep_health_report"):
            return {"available": False, "error": "openclaw_deep_check_not_supported"}
        try:
            report = await openclaw.get_deep_health_report()
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "error": "openclaw_deep_check_failed",
                "detail": str(exc),
            }
        return {"available": True, "report": report}

    # ---------- GET /api/openclaw/remediation-plan ------------------------
    @router.get("/api/openclaw/remediation-plan")
    async def openclaw_remediation_plan() -> dict:
        """Пошаговый план исправления OpenClaw контуров."""
        openclaw = ctx.get_dep("openclaw_client")
        if not openclaw:
            return {"available": False, "error": "openclaw_client_not_configured"}
        if not hasattr(openclaw, "get_remediation_plan"):
            return {"available": False, "error": "openclaw_remediation_not_supported"}
        try:
            report = await openclaw.get_remediation_plan()
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "error": "openclaw_remediation_failed",
                "detail": str(exc),
            }
        return {"available": True, "report": report}

    # ---------- GET /api/openclaw/cloud/tier/state ------------------------
    @router.get("/api/openclaw/cloud/tier/state")
    async def openclaw_cloud_tier_state() -> dict:
        """[R23/R25] Диагностика Cloud Tier State.

        Возвращает текущий активный tier (free/paid/default), статистику
        переключений, метрики (cloud_attempts_total и др.) и конфигурацию.
        Не содержит секретов — только счётчики событий.
        """
        try:
            openclaw = ctx.get_dep("openclaw_client")
            if not openclaw:
                return build_ops_response(
                    status="failed",
                    error_code="openclaw_client_not_configured",
                    summary="Openclaw client not configured",
                )
            if not hasattr(openclaw, "get_tier_state_export"):
                return build_ops_response(
                    status="failed",
                    error_code="tier_state_not_supported",
                    summary="Tier state not supported",
                )
            tier_state = openclaw.get_tier_state_export()
            return build_ops_response(status="ok", data={"tier_state": tier_state})
        except Exception as exc:  # noqa: BLE001
            return build_ops_response(status="failed", error_code="system_error", summary=str(exc))

    return router
