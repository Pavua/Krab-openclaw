# -*- coding: utf-8 -*-
"""
Monitoring router — Phase 2 Wave E + Wave T extraction (Session 25).

Wave E: 5 stateless GET endpoints (sla, ops/metrics, ops/timeline +
alias /api/timeline, archive/growth, reactions/incoming) использующих
module-level singletons (`metrics`, `timeline`).

Wave T: factory-pattern conversion + 7 router-backed ops GET endpoints
(usage, cost-report, runway, executive-summary, report, alerts, history)
через ``ctx.deps["router"]``. Эти endpoints возвращают данные модельного
роутера (ModelRouter) без зависимости от self.

Endpoints:
- GET /api/sla                     — SLA метрики (latency p50/p95, success rate)
- GET /api/ops/metrics             — flat metrics для V4 ops dashboard sparklines
- GET /api/ops/timeline            — recent event timeline (с alias /api/timeline)
- GET /api/timeline                — alias для /api/ops/timeline
- GET /api/archive/growth          — archive.db рост (snapshot + summary)
- GET /api/reactions/incoming      — входящие реакции (по сообщению или recent)
- GET /api/ops/usage               — Wave T: aggregated usage summary
- GET /api/ops/cost-report         — Wave T: estimated cost report
- GET /api/ops/runway              — Wave T: credit runway plan
- GET /api/ops/executive-summary   — Wave T: ops executive summary
- GET /api/ops/report              — Wave T: unified ops report
- GET /api/ops/alerts              — Wave T: ops alerts
- GET /api/ops/history             — Wave T: ops history snapshots

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from ._context import RouterContext


def build_monitoring_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с monitoring + ops endpoints."""
    router = APIRouter(tags=["monitoring"])

    # ---------------------------------------------------------------------
    # Wave E: stateless endpoints (singletons / sub-modules)
    # ---------------------------------------------------------------------

    @router.get("/api/sla")
    async def get_sla_metrics() -> dict:
        """Returns dynamic SLA metrics for the NOC-lite UI (Latency p50/p95, Success Rate)."""
        from ...core.observability import metrics

        snap = metrics.get_snapshot()
        counters = snap.get("counters", {})
        latencies = snap.get("latencies", {"p50_ms": 0.0, "p95_ms": 0.0})

        total_success = counters.get("local_success", 0) + counters.get("cloud_success", 0)
        total_fail = counters.get("local_failures", 0) + counters.get("cloud_failures", 0)
        total = total_success + total_fail
        success_rate = (total_success / total * 100.0) if total > 0 else 100.0

        fail_fast_count = counters.get("force_cloud_failfast_total", 0)

        return {
            "ok": True,
            "latency_p50_ms": latencies.get("p50_ms", 0.0),
            "latency_p95_ms": latencies.get("p95_ms", 0.0),
            "success_rate_pct": round(success_rate, 2),
            "fail_fast_count": fail_fast_count,
        }

    @router.get("/api/ops/metrics")
    async def ops_metrics() -> dict:
        """Export internal metrics — flat fields для V4 ops dashboard sparklines."""
        from ...core.observability import metrics

        snap = metrics.get_snapshot()
        counters = snap.get("counters", {})
        latencies = snap.get("latencies", {})

        success = counters.get("llm_success", 0)
        errors = counters.get("llm_error", 0)
        total = success + errors
        error_rate = (errors / total * 100) if total > 0 else 0.0

        return {
            "ok": True,
            "metrics": snap,
            "latency_p50": latencies.get("p50_ms", 0),
            "latency_p95": latencies.get("p95_ms", 0),
            "latency_p99": 0,  # LatencyTracker считает p50/p95; p99 зарезервирован
            "error_rate": round(error_rate, 2),
            "throughput": total,
        }

    @router.get("/api/ops/timeline")
    @router.get("/api/timeline")
    async def ops_timeline(
        limit: int = 200,
        min_severity: Optional[str] = None,
        channel: Optional[str] = None,
    ) -> dict:
        """Export recent event timeline."""
        from ...core.observability import timeline

        return {
            "ok": True,
            "events": timeline.get_events(limit=limit, min_severity=min_severity, channel=channel),
        }

    @router.get("/api/archive/growth")
    async def archive_growth() -> dict:
        """Archive.db рост: текущий snapshot + статистика по истории."""
        from ...core.archive_growth_monitor import growth_summary, take_snapshot

        current = take_snapshot()
        return {
            "ok": True,
            "current": current.__dict__ if current else None,
            **growth_summary(),
        }

    @router.get("/api/reactions/incoming")
    async def get_reactions_incoming(
        chat_id: Optional[int] = Query(default=None),
        message_id: Optional[int] = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict:
        """Входящие реакции от пользователей (кто что поставил).

        Query params:
          - chat_id + message_id: реакции на конкретное сообщение
          - limit: ограничение на количество последних событий (default 50)
        """
        try:
            from ...core.reaction_handler import (
                get_reactions_for_message,
                get_recent_reactions,
                get_stats,
            )

            if chat_id is not None and message_id is not None:
                events = get_reactions_for_message(chat_id, message_id)
                return {
                    "ok": True,
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reactions": events,
                    "count": len(events),
                }

            recent = get_recent_reactions(limit=limit)
            stats = get_stats()
            return {
                "ok": True,
                "recent": recent,
                "stats": stats,
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    # ---------------------------------------------------------------------
    # Wave T: ops endpoints через ctx.deps["router"]
    # ---------------------------------------------------------------------

    @router.get("/api/ops/usage")
    async def ops_usage() -> dict:
        """Агрегированный usage-срез роутера моделей."""
        model_router = ctx.deps["router"]
        if hasattr(model_router, "get_usage_summary"):
            return {"ok": True, "usage": model_router.get_usage_summary()}
        return {"ok": False, "error": "usage_summary_not_supported"}

    @router.get("/api/ops/cost-report")
    async def ops_cost_report(
        monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000),
    ) -> dict:
        """Оценочный отчет по затратам local/cloud маршрутизации."""
        model_router = ctx.deps["router"]
        if hasattr(model_router, "get_cost_report"):
            return {
                "ok": True,
                "report": model_router.get_cost_report(
                    monthly_calls_forecast=monthly_calls_forecast
                ),
            }
        return {"ok": False, "error": "cost_report_not_supported"}

    @router.get("/api/ops/runway")
    async def ops_runway(
        credits_usd: float = Query(default=300.0, ge=0.0, le=1000000.0),
        horizon_days: int = Query(default=80, ge=1, le=3650),
        reserve_ratio: float = Query(default=0.1, ge=0.0, le=0.95),
        monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000),
    ) -> dict:
        """План расхода кредитов: burn-rate, runway и safe calls/day."""
        model_router = ctx.deps["router"]
        if hasattr(model_router, "get_credit_runway_report"):
            return {
                "ok": True,
                "runway": model_router.get_credit_runway_report(
                    credits_usd=credits_usd,
                    horizon_days=horizon_days,
                    reserve_ratio=reserve_ratio,
                    monthly_calls_forecast=monthly_calls_forecast,
                ),
            }
        return {"ok": False, "error": "ops_runway_not_supported"}

    @router.get("/api/ops/executive-summary")
    async def ops_executive_summary(
        monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000),
    ) -> dict:
        """Компактный ops executive summary: KPI + риски + рекомендации."""
        model_router = ctx.deps["router"]
        if hasattr(model_router, "get_ops_executive_summary"):
            return {
                "ok": True,
                "summary": model_router.get_ops_executive_summary(
                    monthly_calls_forecast=monthly_calls_forecast
                ),
            }
        return {"ok": False, "error": "ops_executive_summary_not_supported"}

    @router.get("/api/ops/report")
    async def ops_report(
        history_limit: int = Query(default=20, ge=1, le=200),
        monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000),
    ) -> dict:
        """Единый ops отчет: usage + alerts + costs + history."""
        model_router = ctx.deps["router"]
        if hasattr(model_router, "get_ops_report"):
            return {
                "ok": True,
                "report": model_router.get_ops_report(
                    history_limit=history_limit,
                    monthly_calls_forecast=monthly_calls_forecast,
                ),
            }
        return {"ok": False, "error": "ops_report_not_supported"}

    @router.get("/api/ops/alerts")
    async def ops_alerts() -> dict:
        """Операционные алерты по расходам и маршрутизации."""
        model_router = ctx.deps["router"]
        if hasattr(model_router, "get_ops_alerts"):
            return {"ok": True, "alerts": model_router.get_ops_alerts()}
        return {"ok": False, "error": "ops_alerts_not_supported"}

    @router.get("/api/ops/history")
    async def ops_history(limit: int = Query(default=30, ge=1, le=200)) -> dict:
        """История ops snapshot-ов (alerts/status over time)."""
        model_router = ctx.deps["router"]
        if hasattr(model_router, "get_ops_history"):
            return {"ok": True, "history": model_router.get_ops_history(limit=limit)}
        return {"ok": False, "error": "ops_history_not_supported"}

    return router
