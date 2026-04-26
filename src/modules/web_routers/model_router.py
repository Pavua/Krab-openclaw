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

Endpoints (Wave OO, READY — Session 25):
- GET  /api/model/catalog       — каталог моделей/режимов для UI с кнопочным управлением.
- POST /api/model/apply         — применяет изменения модели/режима из web UI.

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException, Query

from src.core.model_aliases import normalize_model_alias

from ._context import RouterContext


def _normalize_force_mode_local(force_mode: str) -> str:
    """Pure нормализация force_* режимов в auto/local/cloud для UI.

    Локальная копия из ``WebApp._normalize_force_mode_static`` (Wave OO).
    """
    normalized = str(force_mode or "").strip().lower()
    if normalized in {"force_local", "local"}:
        return "local"
    if normalized in {"force_cloud", "cloud"}:
        return "cloud"
    return "auto"


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

    # ============== Wave OO (Session 25) ===================================

    # ---------- GET /api/model/catalog ------------------------------------
    @router.get("/api/model/catalog")
    async def model_catalog(force_refresh: bool = Query(default=False)) -> dict:
        """Каталог моделей/режимов для web-панели с кнопочным управлением."""
        router_obj = ctx.deps["router"]
        get_cache = ctx.deps.get("model_catalog_get_cache_helper")
        build_catalog = ctx.deps.get("model_catalog_build_helper")
        if build_catalog is None:
            raise HTTPException(status_code=500, detail="model_catalog_build_helper_missing")

        if not force_refresh and get_cache is not None:
            cached_catalog = get_cache()
            if cached_catalog is not None:
                return {"ok": True, "catalog": cached_catalog, "cached": True}

        catalog = await build_catalog(router_obj)
        return {"ok": True, "catalog": catalog}

    # ---------- POST /api/model/apply -------------------------------------
    @router.post("/api/model/apply")
    async def model_apply(
        payload: dict = Body(...),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Применяет изменения модели/режима из web UI без ручных команд."""
        ctx.assert_write_access(x_krab_web_key, token)
        router_obj = ctx.deps["router"]
        black_box = ctx.deps.get("black_box")

        build_catalog = ctx.deps.get("model_catalog_build_helper")
        build_fallback = ctx.deps.get("model_catalog_build_fallback_helper")
        store_cache = ctx.deps.get("model_catalog_store_cache_helper")
        apply_timeout_helper = ctx.deps.get("model_apply_catalog_timeout_helper")
        build_quick_presets = ctx.deps.get("runtime_quick_presets_build_helper")
        apply_runtime_controls = ctx.deps.get("openclaw_runtime_controls_apply_helper")
        build_runtime_controls = ctx.deps.get("openclaw_runtime_controls_build_helper")
        build_routing_status = ctx.deps.get("openclaw_model_routing_helper")
        runtime_lite_invalidate = ctx.deps.get("runtime_lite_cache_invalidator_helper")

        if (
            build_catalog is None
            or build_fallback is None
            or store_cache is None
            or apply_timeout_helper is None
        ):
            raise HTTPException(status_code=500, detail="model_apply_helpers_missing")

        action = str(payload.get("action", "")).strip().lower()
        if not action:
            raise HTTPException(status_code=400, detail="model_apply_action_required")

        result_payload: dict[str, object] = {}
        message_text = "✅ Изменения применены."
        post_apply_runtime_controls: dict[str, Any] | None = None
        post_apply_routing_status: dict[str, Any] | None = None

        if action == "set_mode":
            mode = str(payload.get("mode", "auto")).strip().lower() or "auto"
            if mode not in {"auto", "local", "cloud"}:
                raise HTTPException(status_code=400, detail="model_apply_invalid_mode")
            if not hasattr(router_obj, "set_force_mode"):
                raise HTTPException(status_code=400, detail="model_apply_set_mode_not_supported")
            update_result = router_obj.set_force_mode(mode)
            result_payload = {
                "mode": _normalize_force_mode_local(getattr(router_obj, "force_mode", "auto")),
                "router_response": str(update_result),
            }
            message_text = f"✅ Режим обновлен: {result_payload['mode']}"

        elif action == "set_slot_model":
            slot = str(payload.get("slot", "")).strip().lower()
            raw_model = str(payload.get("model", "")).strip()
            if not slot or not raw_model:
                raise HTTPException(status_code=400, detail="model_apply_slot_and_model_required")
            if not hasattr(router_obj, "models") or not isinstance(
                getattr(router_obj, "models"), dict
            ):
                raise HTTPException(status_code=400, detail="model_apply_slots_not_supported")
            if slot not in router_obj.models:
                available = ", ".join(sorted(router_obj.models.keys()))
                raise HTTPException(
                    status_code=400,
                    detail=f"model_apply_unknown_slot: {slot}; available={available}",
                )
            resolved_model, alias_note = normalize_model_alias(raw_model)
            old_model = str(router_obj.models.get(slot, ""))
            router_obj.models[slot] = resolved_model
            result_payload = {
                "slot": slot,
                "old_model": old_model,
                "new_model": resolved_model,
                "alias_note": alias_note,
            }
            message_text = f"✅ Слот `{slot}`: `{old_model}` → `{resolved_model}`"

        elif action == "apply_preset":
            if build_quick_presets is None:
                raise HTTPException(
                    status_code=500, detail="runtime_quick_presets_build_helper_missing"
                )
            preset_id = str(payload.get("preset", "")).strip().lower()
            if not preset_id:
                raise HTTPException(status_code=400, detail="model_apply_preset_required")
            if not hasattr(router_obj, "models") or not isinstance(
                getattr(router_obj, "models"), dict
            ):
                raise HTTPException(status_code=400, detail="model_apply_slots_not_supported")

            import os as _os

            local_override = str(payload.get("local_model", "")).strip() or str(
                getattr(router_obj, "active_local_model", "") or ""
            )
            if not local_override:
                local_override = (
                    _os.getenv("LOCAL_PREFERRED_MODEL", "nvidia/nemotron-3-nano").strip()
                    or "nvidia/nemotron-3-nano"
                )

            presets = build_quick_presets(
                current_slots={str(k): str(v) for k, v in router_obj.models.items()},
                local_override=local_override,
            )
            chosen = presets.get(preset_id)
            if not chosen:
                raise HTTPException(
                    status_code=400, detail=f"model_apply_unknown_preset: {preset_id}"
                )

            applied_changes: list[dict[str, str]] = []
            for slot, model_id in dict(chosen.get("slots", {})).items():
                if slot not in router_obj.models:
                    continue
                resolved_model, _ = normalize_model_alias(str(model_id))
                previous = str(router_obj.models.get(slot, ""))
                router_obj.models[slot] = resolved_model
                applied_changes.append(
                    {
                        "slot": str(slot),
                        "old_model": previous,
                        "new_model": resolved_model,
                    }
                )

            target_mode = (
                str(payload.get("mode_override", "") or chosen.get("mode", "auto")).strip().lower()
                or "auto"
            )
            if hasattr(router_obj, "set_force_mode"):
                router_obj.set_force_mode(target_mode)

            result_payload = {
                "preset": preset_id,
                "mode": _normalize_force_mode_local(getattr(router_obj, "force_mode", "auto")),
                "changes": applied_changes,
            }
            message_text = f"✅ Пресет `{preset_id}` применён ({len(applied_changes)} слотов)."

        elif action == "set_runtime_chain":
            if apply_runtime_controls is None:
                raise HTTPException(
                    status_code=500, detail="openclaw_runtime_controls_apply_helper_missing"
                )
            primary_raw = payload.get("primary")
            fallbacks_raw = (
                payload.get("fallbacks") if isinstance(payload.get("fallbacks"), list) else []
            )
            context_tokens_raw = payload.get("context_tokens")
            thinking_default_raw = payload.get("thinking_default", "off")
            execution_preset_raw = payload.get("execution_preset", "")
            main_max_concurrent_raw = payload.get("main_max_concurrent")
            subagent_max_concurrent_raw = payload.get("subagent_max_concurrent")
            slot_thinking_raw = payload.get("slot_thinking")
            try:
                applied = apply_runtime_controls(
                    primary_raw=primary_raw,
                    fallbacks_raw=list(fallbacks_raw),
                    context_tokens_raw=context_tokens_raw,
                    thinking_default_raw=thinking_default_raw,
                    execution_preset_raw=execution_preset_raw,
                    main_max_concurrent_raw=main_max_concurrent_raw,
                    subagent_max_concurrent_raw=subagent_max_concurrent_raw,
                    slot_thinking_raw=slot_thinking_raw
                    if isinstance(slot_thinking_raw, dict)
                    else {},
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            if runtime_lite_invalidate is not None:
                runtime_lite_invalidate()
            if build_routing_status is not None:
                post_apply_routing_status = build_routing_status()
            if build_runtime_controls is not None:
                post_apply_runtime_controls = build_runtime_controls()
            result_payload = {
                "runtime": applied,
                "routing_status": post_apply_routing_status,
                "runtime_controls": post_apply_runtime_controls,
            }
            backup_hint = ""
            if isinstance(applied, dict) and applied.get("backup_openclaw_json"):
                backup_hint = " backup создан."
            primary_id = str(applied.get("primary", "")) if isinstance(applied, dict) else ""
            fallback_count = (
                len(applied.get("fallbacks", []) or []) if isinstance(applied, dict) else 0
            )
            message_text = (
                f"✅ Глобальная цепочка OpenClaw обновлена: `{primary_id}` + "
                f"{fallback_count} fallback(s).{backup_hint}"
            )

        else:
            raise HTTPException(status_code=400, detail=f"model_apply_unknown_action: {action}")

        if black_box and hasattr(black_box, "log_event"):
            black_box.log_event("web_model_apply", f"action={action} result={message_text}")

        catalog_refresh = {
            "degraded": False,
            "reason": "",
            "detail": "",
        }
        try:
            catalog_payload = await asyncio.wait_for(
                build_catalog(router_obj),
                timeout=apply_timeout_helper(),
            )
        except asyncio.TimeoutError:
            catalog_payload = build_fallback(
                runtime_controls=post_apply_runtime_controls,
                routing_status=post_apply_routing_status,
                degraded_reason="catalog_refresh_timeout",
            )
            store_cache(catalog_payload)
            catalog_refresh = {
                "degraded": True,
                "reason": "catalog_refresh_timeout",
                "detail": (
                    "Runtime уже записан, но полный refresh каталога занял слишком "
                    "много времени; UI временно использует cache."
                ),
            }
        except Exception as exc:  # noqa: BLE001
            catalog_payload = build_fallback(
                runtime_controls=post_apply_runtime_controls,
                routing_status=post_apply_routing_status,
                degraded_reason="catalog_refresh_failed",
            )
            store_cache(catalog_payload)
            catalog_refresh = {
                "degraded": True,
                "reason": "catalog_refresh_failed",
                "detail": (
                    f"Runtime уже записан, но post-apply refresh каталога завершился ошибкой: {exc}"
                ),
            }

        return {
            "ok": True,
            "action": action,
            "message": message_text,
            "result": result_payload,
            "catalog": catalog_payload,
            "catalog_refresh": catalog_refresh,
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
