# -*- coding: utf-8 -*-
"""
Model router — Phase 2 Wave FF extraction (Session 25).

Endpoints управления моделями через RouterContext. Endpoints используют
``ctx.deps["router"]`` (ModelRouter) и `model_manager` напрямую без
зависимости от WebApp class.

Endpoints (Wave FF, READY):
- GET  /api/model/status         — текущий статус модели и маршрутизации.
- POST /api/model/switch         — переключить модель/провайдера (write).
- GET  /api/model/recommend      — рекомендация модели для профиля.
- POST /api/model/preflight      — preflight task plan (write? нет — read).
- GET  /api/model/explain        — explainability route/policy/preflight.
- GET  /api/model/feedback       — сводка feedback по моделям.
- POST /api/model/feedback       — приём пользовательского feedback (write,
                                    с idempotency key через ctx.deps).
- GET  /api/model/local/status   — статус локального LM рантайма (через
                                    ``resolve_local_runtime_truth_helper``).

SKIP (HARD, требуют helper promote — не в этом waveе):
- /api/model/local/load-default — `_invalidate_lmstudio_snapshot_cache`
- /api/model/local/unload       — `_invalidate_lmstudio_snapshot_cache`
- /api/model/catalog            — `_get_model_catalog_cache` + `_build_model_catalog`
- /api/model/provider-action    — много helper'ов
- /api/model/apply              — много helper'ов + cache invalidation
- /api/thinking/status, /api/thinking/set, /api/depth/status — runtime controls
                                    helpers (`_build_openclaw_runtime_controls`,
                                    `_apply_openclaw_runtime_controls`).

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Header, HTTPException, Query

from ._context import RouterContext


def build_model_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с model-management endpoints (Wave FF)."""
    router = APIRouter(tags=["model"])

    # ---------- GET /api/model/status -------------------------------------
    @router.get("/api/model/status")
    async def model_status() -> dict:
        """Текущий статус модели и маршрутизации."""
        from src.model_manager import model_manager as _mm
        from src.openclaw_client import openclaw_client as _oc

        route = _oc.get_last_runtime_route()
        return {
            "ok": True,
            "route": route,
            "provider": _mm.format_status() if hasattr(_mm, "format_status") else str(_mm),
            "active_model": str(getattr(_mm, "active_model_id", None) or route.get("model", "")),
        }

    # ---------- POST /api/model/switch ------------------------------------
    @router.post("/api/model/switch")
    async def model_switch(
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Переключить модель через API."""
        ctx.assert_write_access(x_krab_web_key, token)
        from src.model_manager import model_manager as _mm

        model = str(payload.get("model") or "").strip()
        if not model:
            return {
                "ok": False,
                "error": "model required (e.g. 'auto', 'local', 'cloud', model_id)",
            }
        try:
            if model in {"auto", "local", "cloud"}:
                _mm.set_provider(model)
            else:
                _mm.set_model(model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "ok": True,
            "model": model,
            "active": str(getattr(_mm, "active_model_id", model)),
        }

    # ---------- GET /api/model/recommend ----------------------------------
    @router.get("/api/model/recommend")
    async def model_recommend(
        profile: str = Query(default="chat", description="Профиль задачи"),
    ) -> dict:
        router_obj = ctx.deps["router"]
        return router_obj.get_profile_recommendation(profile)

    # ---------- POST /api/model/preflight ---------------------------------
    @router.post("/api/model/preflight")
    async def model_preflight(payload: dict = Body(...)) -> dict:
        """Preflight-план задачи: профиль, канал/модель, риски, cost hint."""
        router_obj = ctx.deps["router"]
        if not hasattr(router_obj, "get_task_preflight"):
            return {"ok": False, "error": "task_preflight_not_supported"}

        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="prompt_required")

        task_type = str(payload.get("task_type", "chat")).strip().lower() or "chat"
        preferred_model = payload.get("preferred_model")
        preferred_model_str = str(preferred_model).strip() if preferred_model else None
        confirm_expensive = bool(payload.get("confirm_expensive", False))

        preflight = router_obj.get_task_preflight(
            prompt=prompt,
            task_type=task_type,
            preferred_model=preferred_model_str,
            confirm_expensive=confirm_expensive,
        )
        return {"ok": True, "preflight": preflight}

    # ---------- GET /api/model/explain ------------------------------------
    @router.get("/api/model/explain")
    async def model_explain(
        task_type: str = Query(default="chat", description="Тип задачи для preflight"),
        prompt: str = Query(default="", description="Опциональный prompt для preflight explain"),
        preferred_model: str = Query(
            default="", description="Опциональная предпочтительная модель"
        ),
        confirm_expensive: bool = Query(
            default=False, description="Флаг подтверждения дорогого cloud пути"
        ),
    ) -> dict:
        """Explainability endpoint: почему выбран канал/модель."""
        router_obj = ctx.deps["router"]
        normalized_prompt = str(prompt or "").strip()
        normalized_task_type = str(task_type or "chat").strip().lower() or "chat"
        preferred_model_str = str(preferred_model or "").strip() or None

        if hasattr(router_obj, "get_route_explain"):
            explain = router_obj.get_route_explain(
                prompt=normalized_prompt,
                task_type=normalized_task_type,
                preferred_model=preferred_model_str,
                confirm_expensive=bool(confirm_expensive),
            )
            return {"ok": True, "explain": explain}

        # Fallback для старого роутера без get_route_explain.
        last_route = router_obj.get_last_route() if hasattr(router_obj, "get_last_route") else {}
        preflight = None
        if normalized_prompt and hasattr(router_obj, "get_task_preflight"):
            preflight = router_obj.get_task_preflight(
                prompt=normalized_prompt,
                task_type=normalized_task_type,
                preferred_model=preferred_model_str,
                confirm_expensive=bool(confirm_expensive),
            )
        return {
            "ok": True,
            "explain": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "last_route": last_route if isinstance(last_route, dict) else {},
                "reason": {
                    "code": str(last_route.get("route_reason", "")).strip() or "unknown",
                    "detail": str(last_route.get("route_detail", "")).strip(),
                    "human": "Роутер не поддерживает расширенный explain; показан базовый срез.",
                },
                "policy": {
                    "force_mode": str(getattr(router_obj, "force_mode", "auto")),
                    "routing_policy": str(getattr(router_obj, "routing_policy", "unknown")),
                    "cloud_soft_cap_reached": bool(
                        getattr(router_obj, "cloud_soft_cap_reached", False)
                    ),
                    "local_available": bool(getattr(router_obj, "is_local_available", False)),
                },
                "preflight": preflight,
                "explainability_score": 40 if last_route else 0,
                "transparency_level": "low" if not last_route else "medium",
            },
        }

    # ---------- GET /api/model/feedback -----------------------------------
    @router.get("/api/model/feedback")
    async def model_feedback_summary(
        profile: str | None = Query(default=None),
        top: int = Query(default=5, ge=1, le=20),
    ) -> dict:
        """Сводка оценок качества роутинга моделей."""
        router_obj = ctx.deps["router"]
        if not hasattr(router_obj, "get_feedback_summary"):
            return {"ok": False, "error": "feedback_summary_not_supported"}
        normalized_profile = str(profile).strip().lower() if profile is not None else None
        return {
            "ok": True,
            "feedback": router_obj.get_feedback_summary(profile=normalized_profile, top=top),
        }

    # ---------- POST /api/model/feedback ----------------------------------
    @router.post("/api/model/feedback")
    async def model_feedback_submit(
        payload: dict = Body(...),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        x_idempotency_key: str = Header(default="", alias="X-Idempotency-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Принимает оценку качества ответа (1-5) для самообучающегося роутинга."""
        ctx.assert_write_access(x_krab_web_key, token)
        router_obj = ctx.deps["router"]
        if not hasattr(router_obj, "submit_feedback"):
            return {"ok": False, "error": "feedback_submit_not_supported"}

        idem_get = ctx.deps.get("idempotency_get")
        idem_set = ctx.deps.get("idempotency_set")
        idem_key = (x_idempotency_key or "").strip()
        if idem_get is not None:
            cached = idem_get("model_feedback_submit", idem_key)
            if cached:
                return cached

        score = payload.get("score")
        profile = payload.get("profile")
        model_name = payload.get("model")
        channel = payload.get("channel")
        note = payload.get("note", "")

        try:
            result = router_obj.submit_feedback(
                score=int(score),
                profile=str(profile).strip().lower() if profile is not None else None,
                model_name=str(model_name).strip() if model_name is not None else None,
                channel=str(channel).strip().lower() if channel is not None else None,
                note=str(note).strip(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"feedback_submit_failed: {exc}") from exc

        response_payload = {"ok": True, "result": result}
        if idem_set is not None:
            idem_set("model_feedback_submit", idem_key, response_payload)
        return response_payload

    # ---------- GET /api/model/local/status -------------------------------
    @router.get("/api/model/local/status")
    async def model_local_status() -> dict:
        """Возвращает статус локального рантайма LLM."""
        router_obj = ctx.deps["router"]
        helper = ctx.deps.get("resolve_local_runtime_truth_helper")
        if helper is None:
            return {"ok": False, "error": "resolve_local_runtime_truth_helper_missing"}
        truth = helper(router_obj)
        if asyncio.iscoroutine(truth):
            truth = await truth
        if not isinstance(truth, dict):
            truth = {}
        active_model = str(truth.get("active_model") or "").strip()
        engine_raw = str(truth.get("engine") or "unknown").strip()
        runtime_url = str(truth.get("runtime_url") or "n/a").strip()
        lifecycle_status = "loaded" if bool(truth.get("is_loaded")) else "not_loaded"

        return {
            "ok": True,
            "status": lifecycle_status,
            "model_name": active_model or "",
            "engine": engine_raw,
            "url": runtime_url or "n/a",
            "details": {
                "available": bool(truth.get("runtime_reachable")),
                "engine": engine_raw,
                "active_model": active_model,
                "is_loaded": lifecycle_status == "loaded",
                "url": runtime_url or "n/a",
                "loaded_models": truth.get("loaded_models", []),
                "probe_state": truth.get("probe_state", "down"),
                "error": truth.get("error", ""),
            },
            "status_legacy": {
                "available": bool(truth.get("runtime_reachable")),
                "engine": engine_raw,
                "active_model": active_model,
                "is_loaded": lifecycle_status == "loaded",
                "url": runtime_url or "n/a",
                "loaded_models": truth.get("loaded_models", []),
                "probe_state": truth.get("probe_state", "down"),
                "error": truth.get("error", ""),
            },
        }

    return router
