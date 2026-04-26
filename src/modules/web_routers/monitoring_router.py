# -*- coding: utf-8 -*-
"""
Monitoring router — Phase 2 Wave E + Wave T + Wave AA extraction (Session 25).

Wave E: 5 stateless GET endpoints (sla, ops/metrics, ops/timeline +
alias /api/timeline, archive/growth, reactions/incoming) использующих
module-level singletons (`metrics`, `timeline`).

Wave T: factory-pattern conversion + 7 router-backed ops GET endpoints
(usage, cost-report, runway, executive-summary, report, alerts, history)
через ``ctx.deps["router"]``. Эти endpoints возвращают данные модельного
роутера (ModelRouter) без зависимости от self.

Wave AA: добавлены write-protected POST/DELETE endpoints для управления
ops alerts/history через ``ctx.assert_write_access``:
- POST   /api/ops/maintenance/prune — prune ops history (retention)
- POST   /api/ops/ack/{code}        — acknowledge alert
- DELETE /api/ops/ack/{code}        — снять подтверждение alert

Endpoints:
- GET    /api/sla                     — SLA метрики (latency p50/p95, success rate)
- GET    /api/ops/metrics             — flat metrics для V4 ops dashboard sparklines
- GET    /api/ops/timeline            — recent event timeline (с alias /api/timeline)
- GET    /api/timeline                — alias для /api/ops/timeline
- GET    /api/archive/growth          — archive.db рост (snapshot + summary)
- GET    /api/reactions/incoming      — входящие реакции (по сообщению или recent)
- GET    /api/ops/usage               — Wave T: aggregated usage summary
- GET    /api/ops/cost-report         — Wave T: estimated cost report
- GET    /api/ops/runway              — Wave T: credit runway plan
- GET    /api/ops/executive-summary   — Wave T: ops executive summary
- GET    /api/ops/report              — Wave T: unified ops report
- GET    /api/ops/alerts              — Wave T: ops alerts
- GET    /api/ops/history             — Wave T: ops history snapshots
- POST   /api/ops/maintenance/prune   — Wave AA: prune ops history
- POST   /api/ops/ack/{code}          — Wave AA: acknowledge alert
- DELETE /api/ops/ack/{code}          — Wave AA: clear alert ack

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query

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

    # ---------------------------------------------------------------------
    # Wave AA: write-protected POST/DELETE endpoints
    # ---------------------------------------------------------------------

    @router.post("/api/ops/maintenance/prune")
    async def ops_prune(
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Очищает ops history по retention-параметрам."""
        ctx.assert_write_access(x_krab_web_key, token)
        model_router = ctx.deps["router"]
        if not hasattr(model_router, "prune_ops_history"):
            return {"ok": False, "error": "ops_prune_not_supported"}
        max_age_days = int(payload.get("max_age_days", 30))
        keep_last = int(payload.get("keep_last", 100))
        try:
            result = model_router.prune_ops_history(max_age_days=max_age_days, keep_last=keep_last)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "result": result}

    @router.post("/api/ops/ack/{code}")
    async def ops_ack(
        code: str,
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Подтверждает alert код оператором."""
        ctx.assert_write_access(x_krab_web_key, token)
        model_router = ctx.deps["router"]
        if not hasattr(model_router, "acknowledge_ops_alert"):
            return {"ok": False, "error": "ops_ack_not_supported"}
        actor = str(payload.get("actor", "web_api")).strip() or "web_api"
        note = str(payload.get("note", "")).strip()
        try:
            result = model_router.acknowledge_ops_alert(code=code, actor=actor, note=note)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "result": result}

    @router.delete("/api/ops/ack/{code}")
    async def ops_unack(
        code: str,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Снимает подтверждение alert кода."""
        ctx.assert_write_access(x_krab_web_key, token)
        model_router = ctx.deps["router"]
        if not hasattr(model_router, "clear_ops_alert_ack"):
            return {"ok": False, "error": "ops_unack_not_supported"}
        try:
            result = model_router.clear_ops_alert_ack(code=code)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "result": result}

    # ---------------------------------------------------------------------
    # Phase 2 Part 2B (Session 27): /api/ops/{diagnostics, runtime_snapshot,
    # models, report/export, bundle, bundle/export, openclaw-procs}
    # ---------------------------------------------------------------------

    @router.get("/api/ops/diagnostics")
    async def ops_diagnostics() -> dict:
        """[R12] Унифицированный операционный отчет (alias system/diagnostics)."""
        from datetime import datetime, timezone

        from ...core.ecosystem_health import EcosystemHealthService

        model_router = ctx.deps.get("router")
        if not model_router:
            return {"ok": False, "error": "router_not_found"}
        health_service = ctx.deps.get("health_service")
        if not health_service:
            health_service = EcosystemHealthService(router=model_router)
        health_data = await health_service.collect()

        resolve_helper = ctx.get_dep("resolve_local_runtime_truth_helper")
        if resolve_helper is None:
            local_truth = {}
        else:
            local_truth = await resolve_helper(model_router)

        watchdog = ctx.deps.get("watchdog")
        status = "ok"
        if not bool(local_truth.get("runtime_reachable")):
            status = "degraded"
            if getattr(model_router, "active_tier", "") == "default":
                status = "failed"
        elif getattr(model_router, "active_tier", "") == "paid":
            status = "degraded"
        return {
            "ok": True,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "resources": health_data.get("resources", {}),
            "budget": health_data.get("budget", {}),
            "local_ai": {
                "engine": local_truth.get(
                    "engine", getattr(model_router, "local_engine", "unknown")
                ),
                "model": local_truth.get("active_model", ""),
                "available": bool(local_truth.get("runtime_reachable")),
                "loaded_models": local_truth.get("loaded_models", []),
            },
            "watchdog": {
                "last_recoveries": getattr(watchdog, "last_recovery_attempt", {}),
            },
        }

    @router.get("/api/ops/runtime_snapshot")
    async def ops_runtime_snapshot() -> dict:
        """Deep observability snapshot linking all states."""
        # Late-bound inbox_service для совместимости с monkeypatch via web_app.
        import sys as _sys
        import time as _time
        from datetime import datetime, timezone

        from ...core.observability import get_observability_snapshot
        from ...core.openclaw_workspace import build_workspace_state_snapshot

        _wam = _sys.modules.get("src.modules.web_app")
        if _wam is not None and hasattr(_wam, "inbox_service"):
            _inbox_service = getattr(_wam, "inbox_service")
        else:
            from ...core.inbox_service import inbox_service as _inbox_service

        model_router = ctx.deps.get("router")
        if not model_router:
            return {"ok": False, "error": "router_not_found"}

        resolve_helper = ctx.get_dep("resolve_local_runtime_truth_helper")
        if resolve_helper is None:
            local_truth = {}
        else:
            local_truth = await resolve_helper(model_router)

        task_queue = ctx.deps.get("queue")
        queue_stats = task_queue.get_metrics() if getattr(task_queue, "get_metrics", None) else {}

        openclaw = getattr(model_router, "openclaw_client", None)
        tier_state = (
            openclaw.get_tier_state_export()
            if openclaw is not None and getattr(openclaw, "get_tier_state_export", None)
            else {}
        )
        operator_workflow = _inbox_service.get_workflow_snapshot()
        workspace_state = build_workspace_state_snapshot()

        return {
            "ok": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "router_state": {
                "is_local_available": bool(local_truth.get("runtime_reachable")),
                "active_local_model": local_truth.get("active_model", ""),
                "loaded_local_models": local_truth.get("loaded_models", []),
                "active_tier": getattr(model_router, "active_tier", "default"),
                "local_failures": model_router._stats.get("local_failures", 0),
                "cloud_failures": model_router._stats.get("cloud_failures", 0),
            },
            "tier_state": tier_state,
            "breaker_state": {
                "preflight_cache": {
                    k: {"expires_in": v[0] - _time.time(), "error": v[1]}
                    for k, v in getattr(model_router, "_preflight_cache", {}).items()
                    if v[0] > _time.time()
                }
            },
            "operator_workflow": operator_workflow,
            "workspace_state": workspace_state,
            "queue_depth": queue_stats.get("active_tasks", 0),
            "queue_stats": queue_stats,
            "observability": get_observability_snapshot(),
        }

    @router.post("/api/ops/models")
    async def ops_models_control(
        payload: dict = Body(...),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """[R12] Управление жизненным циклом локальных моделей.

        Payload: {"action": "load"|"unload"|"unload_all", "model": "model_name"}.
        """
        ctx.assert_write_access(x_krab_web_key, token)
        model_router = ctx.deps.get("router")
        if not model_router:
            return {"ok": False, "error": "router_not_found"}

        action = payload.get("action")
        model_name = payload.get("model")
        try:
            if action == "load":
                if not model_name:
                    return {"ok": False, "error": "model_name_required"}
                success = await model_router.load_local_model(model_name)
                return {"ok": success, "action": action, "model": model_name}
            elif action == "unload":
                if not model_name:
                    return {"ok": False, "error": "model_name_required"}
                success = await model_router.unload_model_manual(model_name)
                return {"ok": success, "action": action, "model": model_name}
            elif action == "unload_all":
                await model_router.unload_models_manual()
                return {"ok": True, "action": action}
            else:
                return {
                    "ok": False,
                    "error": "invalid_action",
                    "supported": ["load", "unload", "unload_all"],
                }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    @router.get("/api/ops/report/export")
    async def ops_report_export(
        history_limit: int = Query(default=50, ge=1, le=200),
        monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000),
    ):
        """Экспортирует полный ops report в JSON-файл."""
        import json as _json
        from datetime import datetime, timezone
        from pathlib import Path as _Path

        from fastapi.responses import FileResponse

        model_router = ctx.deps["router"]
        if not hasattr(model_router, "get_ops_report"):
            return {"ok": False, "error": "ops_report_not_supported"}
        report = model_router.get_ops_report(
            history_limit=history_limit,
            monthly_calls_forecast=monthly_calls_forecast,
        )
        ops_dir = _Path("artifacts/ops")
        ops_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
        out_path = ops_dir / f"ops_report_web_{stamp}.json"
        with out_path.open("w", encoding="utf-8") as fp:
            _json.dump(report, fp, ensure_ascii=False, indent=2)
        return FileResponse(
            str(out_path),
            media_type="application/json",
            filename=out_path.name,
        )

    async def _build_ops_bundle_payload(history_limit: int, monthly_calls_forecast: int) -> dict:
        from datetime import datetime, timezone

        model_router = ctx.deps["router"]
        if not hasattr(model_router, "get_ops_report"):
            return {"_error": "ops_report_not_supported"}
        openclaw = ctx.deps.get("openclaw_client")
        voice_gateway = ctx.deps.get("voice_gateway_client")
        local_ok = await model_router.check_local_health()
        openclaw_ok = await openclaw.health_check() if openclaw else False
        voice_ok = await voice_gateway.health_check() if voice_gateway else False
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ops_report": model_router.get_ops_report(
                history_limit=history_limit,
                monthly_calls_forecast=monthly_calls_forecast,
            ),
            "health": {
                "openclaw": openclaw_ok,
                "local_lm": local_ok,
                "voice_gateway": voice_ok,
            },
        }

    @router.get("/api/ops/bundle")
    async def ops_bundle(
        history_limit: int = Query(default=50, ge=1, le=200),
        monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000),
    ) -> dict:
        """Единый bundle: ops report + health snapshot."""
        payload = await _build_ops_bundle_payload(history_limit, monthly_calls_forecast)
        if "_error" in payload:
            return {"ok": False, "error": payload["_error"]}
        return {"ok": True, "bundle": payload}

    @router.get("/api/ops/bundle/export")
    async def ops_bundle_export(
        history_limit: int = Query(default=50, ge=1, le=200),
        monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000),
    ):
        """Экспортирует единый ops bundle в JSON-файл."""
        import json as _json
        from datetime import datetime, timezone
        from pathlib import Path as _Path

        from fastapi.responses import FileResponse

        payload = await _build_ops_bundle_payload(history_limit, monthly_calls_forecast)
        if "_error" in payload:
            return {"ok": False, "error": payload["_error"]}
        ops_dir = _Path("artifacts/ops")
        ops_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
        out_path = ops_dir / f"ops_bundle_web_{stamp}.json"
        with out_path.open("w", encoding="utf-8") as fp:
            _json.dump(payload, fp, ensure_ascii=False, indent=2)
        return FileResponse(
            str(out_path),
            media_type="application/json",
            filename=out_path.name,
        )

    @router.get("/api/ops/openclaw-procs")
    async def ops_openclaw_procs() -> dict:
        """Список текущих openclaw-процессов с командой, возрастом и RSS MB."""
        from ...core.openclaw_cli_budget import (
            OPENCLAW_CLI_BUDGET,
            budget_available,
            list_openclaw_procs,
        )

        procs = list_openclaw_procs()
        gateways = [p for p in procs if p.get("is_gateway")]
        transient = [p for p in procs if not p.get("is_gateway")]
        total = len(procs)
        leak_suspected = total > (1 + OPENCLAW_CLI_BUDGET)
        return {
            "ok": True,
            "total": total,
            "expected_steady_state": 1,
            "transient_count": len(transient),
            "gateway_count": len(gateways),
            "budget_slots_free": budget_available(),
            "budget_total": OPENCLAW_CLI_BUDGET,
            "leak_suspected": leak_suspected,
            "processes": procs,
        }

    return router
