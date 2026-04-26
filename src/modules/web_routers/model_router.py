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

Endpoints (Wave GG, READY — Session 25):
- POST /api/model/local/load-default — загрузка предпочтительной локальной модели.
- POST /api/model/local/unload       — выгрузка локальных моделей (free RAM).
- POST /api/model/provider-action    — provider repair/migrate helper launch.
- GET  /api/thinking/status          — текущий thinking_default + список режимов.
- POST /api/thinking/set             — обновить глобальный thinking_default.
- GET  /api/depth/status             — алиас thinking/status (depth == thinking).

SKIP (HARD, требуют дополнительной экстракции — не в этом waveе):
- /api/model/catalog            — `_get_model_catalog_cache` + `_build_model_catalog`
- /api/model/apply              — много helper'ов + cache invalidation
                                  (включая `_build_runtime_quick_presets`,
                                  `_build_model_catalog`, `_build_model_catalog_fallback`,
                                  `_store_model_catalog_cache`, `_model_apply_catalog_timeout_sec`,
                                  `normalize_model_alias`).

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

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

    # ============== Wave GG (Session 25) ===================================

    # ---------- POST /api/model/local/load-default ------------------------
    @router.post("/api/model/local/load-default")
    async def model_local_load_default(
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Загружает предпочтительную локальную модель (write endpoint)."""
        ctx.assert_write_access(x_krab_web_key, token)
        router_obj = ctx.deps["router"]
        preferred = str(getattr(router_obj, "local_preferred_model", "") or "").strip()
        if not preferred:
            # Страховка для compat-роутеров/старых инстансов: fallback на config.
            from src.config import config as _config

            fallback_preferred = str(getattr(_config, "LOCAL_PREFERRED_MODEL", "") or "").strip()
            if fallback_preferred.lower() not in {"", "auto", "smallest"}:
                preferred = fallback_preferred
        if not preferred:
            return {"ok": False, "error": "no_preferred_model_configured"}

        success = await router_obj._smart_load(preferred, reason="web_forced")
        invalidate = ctx.deps.get("lmstudio_snapshot_invalidate_helper")
        if invalidate is not None:
            invalidate()
        return {"ok": success, "model": preferred}

    # ---------- POST /api/model/local/unload ------------------------------
    @router.post("/api/model/local/unload")
    async def model_local_unload(
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Выгружает локальные модели для освобождения памяти (write endpoint)."""
        ctx.assert_write_access(x_krab_web_key, token)
        router_obj = ctx.deps["router"]
        invalidate = ctx.deps.get("lmstudio_snapshot_invalidate_helper")

        freed_gb = 0.0
        if hasattr(router_obj, "_evict_idle_models"):
            active = getattr(router_obj, "active_local_model", None)
            if active:
                success = await router_obj.unload_local_model(active)
                if success:
                    router_obj.active_local_model = None
                    if invalidate is not None:
                        invalidate()
                    return {"ok": True, "unloaded": active}

            # Если активной нет, попытаемся выгрузить всё через _evict_idle_models
            freed_gb = await router_obj._evict_idle_models(needed_gb=100.0)
            if invalidate is not None:
                invalidate()

        return {"ok": True, "freed_gb_estimate": round(freed_gb, 1)}

    # ---------- POST /api/model/provider-action ---------------------------
    @router.post("/api/model/provider-action")
    async def model_provider_action(
        payload: dict = Body(...),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Запускает provider-specific repair/migration action из owner-панели."""
        ctx.assert_write_access(x_krab_web_key, token)

        provider = str(payload.get("provider", "") or "").strip().lower()
        action = str(payload.get("action", "") or "").strip().lower()
        if not provider:
            raise HTTPException(status_code=400, detail="provider_action_provider_required")
        if not action:
            raise HTTPException(status_code=400, detail="provider_action_action_required")

        provider_ui_helper = ctx.deps.get("provider_ui_metadata_helper")
        helper_path_helper = ctx.deps.get("provider_repair_helper_path_helper")
        launch_helper = ctx.deps.get("launch_local_app_helper")
        if provider_ui_helper is None or helper_path_helper is None or launch_helper is None:
            raise HTTPException(
                status_code=500,
                detail="provider_action_helpers_missing",
            )

        provider_ui = provider_ui_helper(provider)
        if not isinstance(provider_ui, dict):
            provider_ui = {}
        expected_action = str(provider_ui.get("repair_action", "") or "").strip().lower()
        if action not in {"repair_oauth", "migrate_to_gemini_cli"}:
            raise HTTPException(status_code=400, detail=f"provider_action_unsupported:{action}")
        if not expected_action or action != expected_action:
            raise HTTPException(
                status_code=400,
                detail=f"provider_action_not_available:{provider}:{action}",
            )

        helper_provider = "google-gemini-cli" if action == "migrate_to_gemini_cli" else provider
        helper_path = helper_path_helper(helper_provider)
        if not helper_path or not Path(helper_path).exists():
            raise HTTPException(
                status_code=404,
                detail=f"provider_action_helper_missing:{helper_provider}",
            )

        launch = launch_helper(helper_path)
        if not isinstance(launch, dict) or not launch.get("ok"):
            error_detail = (
                str(launch.get("error") or "provider_action_launch_failed")
                if isinstance(launch, dict)
                else "provider_action_launch_failed"
            )
            raise HTTPException(status_code=500, detail=error_detail)

        detail = str(provider_ui.get("repair_detail", "") or "").strip()
        if action == "migrate_to_gemini_cli":
            message = "✅ Открыт helper миграции на Gemini CLI OAuth."
        else:
            message = f"✅ Открыт helper для провайдера `{provider}`."

        return {
            "ok": True,
            "provider": provider,
            "action": action,
            "message": message,
            "detail": detail,
            "launch": launch,
        }

    # ---------- GET /api/thinking/status ----------------------------------
    @router.get("/api/thinking/status")
    async def thinking_status() -> dict:
        """Текущий thinking_default и список доступных режимов."""
        build_helper = ctx.deps.get("openclaw_runtime_controls_build_helper")
        if build_helper is None:
            raise HTTPException(
                status_code=500,
                detail="openclaw_runtime_controls_build_helper_missing",
            )
        controls = build_helper() or {}
        return {
            "ok": True,
            "thinking_default": controls.get("thinking_default", "off"),
            "thinking_modes": controls.get(
                "thinking_modes",
                ["off", "minimal", "low", "medium", "high", "xhigh", "adaptive"],
            ),
            "chain_items": controls.get("chain_items", []),
        }

    # ---------- POST /api/thinking/set ------------------------------------
    @router.post("/api/thinking/set")
    async def thinking_set(
        payload: dict = Body(...),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Устанавливает глобальный thinking_default без изменения моделей."""
        ctx.assert_write_access(x_krab_web_key, token)
        normalize_helper = ctx.deps.get("thinking_normalize_helper")
        build_helper = ctx.deps.get("openclaw_runtime_controls_build_helper")
        apply_helper = ctx.deps.get("openclaw_runtime_controls_apply_helper")
        if normalize_helper is None or build_helper is None or apply_helper is None:
            raise HTTPException(
                status_code=500,
                detail="thinking_set_helpers_missing",
            )

        raw_mode = str(payload.get("mode", "")).strip().lower()
        try:
            mode = normalize_helper(raw_mode)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"invalid_thinking_mode: {raw_mode!r}")

        current = build_helper() or {}
        try:
            applied = apply_helper(
                primary_raw=current.get("primary") or "",
                fallbacks_raw=list(current.get("fallbacks") or []),
                context_tokens_raw=current.get("context_tokens"),
                thinking_default_raw=mode,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Invalidate runtime-lite cache (через тот же helper, что выгрузка LMStudio).
        invalidate = ctx.deps.get("lmstudio_snapshot_invalidate_helper")
        if invalidate is not None:
            invalidate()

        if not isinstance(applied, dict):
            applied = {}
        return {
            "ok": True,
            "thinking_default": applied.get("thinking_default", mode),
            "changed": applied.get("changed", {}),
        }

    # ---------- GET /api/depth/status -------------------------------------
    @router.get("/api/depth/status")
    async def depth_status() -> dict:
        """Алиас /api/thinking/status — depth == thinking_default в OpenClaw."""
        build_helper = ctx.deps.get("openclaw_runtime_controls_build_helper")
        if build_helper is None:
            raise HTTPException(
                status_code=500,
                detail="openclaw_runtime_controls_build_helper_missing",
            )
        controls = build_helper() or {}
        thinking_default = controls.get("thinking_default", "off")
        modes = controls.get(
            "thinking_modes",
            ["off", "minimal", "low", "medium", "high", "xhigh", "adaptive"],
        )
        return {
            "ok": True,
            "depth": thinking_default,
            "thinking_default": thinking_default,
            "available_modes": modes,
        }

    return router
