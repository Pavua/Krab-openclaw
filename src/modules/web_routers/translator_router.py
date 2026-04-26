# -*- coding: utf-8 -*-
"""
Translator router — Phase 2 Waves K + Q + S extraction (Session 25).

GET endpoints translator-домена через RouterContext. Wave Q добавляет
endpoints, требующие ``ctx.collect_runtime_lite()`` + translator snapshot
helpers (через deps-injection из WebApp).

Endpoints (Wave K):
- GET /api/translator/languages — список языковых пар + текущая
- GET /api/translator/status    — лёгкий профиль + session state
- GET /api/translator/history   — история переводов + статистика
- GET /api/translator/test      — быстрый тест перевода через GET-параметры

Endpoints (Wave Q):
- GET /api/translator/readiness        — readiness translator-контура
- GET /api/translator/control-plane    — session/policy truth
- GET /api/translator/session-inspector — why-report + timeline digest
- GET /api/translator/mobile-readiness — readiness iPhone companion
- GET /api/translator/delivery-matrix  — product truth ordinary/internet tracks

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.
"""

from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request

from ._context import RouterContext


async def _await_if_needed(value: Any) -> Any:
    """Helper: await result if helper-lambda returned a coroutine."""
    if inspect.iscoroutine(value):
        return await value
    return value


def build_translator_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с translator GET endpoints."""
    router = APIRouter(tags=["translator"])

    @router.get("/api/translator/languages")
    async def translator_languages() -> dict:
        """Доступные языковые пары."""
        from ...core.translator_runtime_profile import ALLOWED_LANGUAGE_PAIRS

        kraab = ctx.get_dep("kraab_userbot")
        profile = kraab.get_translator_runtime_profile() if kraab else {}
        return {
            "ok": True,
            "current": profile.get("language_pair", "es-ru"),
            "available": sorted(ALLOWED_LANGUAGE_PAIRS),
        }

    @router.get("/api/translator/status")
    async def translator_status() -> dict:
        """Лёгкий status endpoint для dashboard /translator page."""
        try:
            kraab = ctx.get_dep("kraab_userbot")
            profile = kraab.get_translator_runtime_profile()
            session = kraab.get_translator_session_state()
            return {"ok": True, "profile": profile, "session": session}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/api/translator/history")
    async def translator_history(n: int = 20) -> dict:
        """История переводов и статистика. ?n=N — последние N записей (default 20)."""
        try:
            kraab = ctx.get_dep("kraab_userbot")
            state = kraab.get_translator_session_state()
            stats = state.get("stats") or {}
            total = stats.get("total_translations", 0)
            history: list[dict] = list(state.get("history") or [])
            n_clamped = max(1, min(20, n))
            recent = history[-n_clamped:] if history else []
            return {
                "ok": True,
                "total_translations": total,
                "total_latency_ms": stats.get("total_latency_ms", 0),
                "avg_latency_ms": round(stats.get("total_latency_ms", 0) / max(1, total)),
                "last_pair": state.get("last_language_pair", ""),
                "last_original": state.get("last_translated_original", ""),
                "last_translation": state.get("last_translated_translation", ""),
                "history": list(reversed(recent)),  # новые первыми
                "history_count": len(history),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/api/translator/test")
    async def translator_test_api(
        text: str = Query(default=""),
        tgt: str = Query(default=""),
    ) -> dict:
        """Тестовый перевод через API (GET для простоты)."""
        if not text:
            return {"ok": False, "error": "?text=Buenos+dias+amigo required"}
        try:
            from ...core.language_detect import detect_language, resolve_translation_pair
            from ...core.translator_engine import translate_text
            from ...openclaw_client import openclaw_client as _oc

            detected = detect_language(text)
            if not detected:
                return {"ok": False, "error": "language not detected"}
            kraab = ctx.get_dep("kraab_userbot")
            profile = kraab.get_translator_runtime_profile() if kraab else {}
            src, tgt_lang = resolve_translation_pair(
                detected, profile.get("language_pair", "es-ru")
            )
            if tgt:
                tgt_lang = tgt
            result = await translate_text(text, src, tgt_lang, openclaw_client=_oc)
            return {
                "ok": True,
                "src": src,
                "tgt": tgt_lang,
                "original": result.original,
                "translated": result.translated,
                "latency_ms": result.latency_ms,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Wave Q — endpoints через runtime_lite + translator snapshot helpers.
    # ------------------------------------------------------------------

    @router.get("/api/translator/readiness")
    async def translator_readiness() -> dict:
        """Truthful readiness translator-контура внутри экосистемы Краба."""
        readiness_fn = ctx.get_dep("translator_readiness_snapshot")
        if readiness_fn is None:
            return {"ok": False, "error": "translator_readiness_snapshot not bound"}
        runtime_lite = await ctx.collect_runtime_lite()
        snapshot = await readiness_fn(runtime_lite=runtime_lite)
        snapshot["capability_registry_endpoint"] = "/api/capabilities/registry"
        snapshot["policy_matrix_endpoint"] = "/api/policy/matrix"
        return snapshot

    @router.get("/api/translator/control-plane")
    async def translator_control_plane() -> dict:
        """Session/policy truth translator-контура через control-plane Краба."""
        control_plane_fn = ctx.get_dep("translator_control_plane_snapshot")
        if control_plane_fn is None:
            return {"ok": False, "error": "translator_control_plane_snapshot not bound"}
        runtime_lite = await ctx.collect_runtime_lite()
        return await control_plane_fn(runtime_lite=runtime_lite)

    @router.get("/api/translator/session-inspector")
    async def translator_session_inspector() -> dict:
        """Why-report, timeline digest и escalation context для translator session."""
        control_plane_fn = ctx.get_dep("translator_control_plane_snapshot")
        inspector_fn = ctx.get_dep("translator_session_inspector_snapshot")
        if control_plane_fn is None or inspector_fn is None:
            return {"ok": False, "error": "translator snapshot helpers not bound"}
        runtime_lite = await ctx.collect_runtime_lite()
        control_plane = await control_plane_fn(runtime_lite=runtime_lite)
        return await inspector_fn(
            runtime_lite=runtime_lite,
            current_control_plane=control_plane,
        )

    @router.get("/api/translator/mobile-readiness")
    async def translator_mobile_readiness() -> dict:
        """Readiness iPhone companion/mobile device слоя переводчика."""
        control_plane_fn = ctx.get_dep("translator_control_plane_snapshot")
        mobile_fn = ctx.get_dep("translator_mobile_readiness_snapshot")
        if control_plane_fn is None or mobile_fn is None:
            return {"ok": False, "error": "translator snapshot helpers not bound"}
        runtime_lite = await ctx.collect_runtime_lite()
        control_plane = await control_plane_fn(runtime_lite=runtime_lite)
        return await mobile_fn(
            runtime_lite=runtime_lite,
            current_control_plane=control_plane,
        )

    @router.get("/api/translator/delivery-matrix")
    async def translator_delivery_matrix() -> dict:
        """Product truth по ordinary/internet call tracks переводчика."""
        readiness_fn = ctx.get_dep("translator_readiness_snapshot")
        control_plane_fn = ctx.get_dep("translator_control_plane_snapshot")
        mobile_fn = ctx.get_dep("translator_mobile_readiness_snapshot")
        delivery_fn = ctx.get_dep("translator_delivery_matrix_snapshot")
        if (
            readiness_fn is None
            or control_plane_fn is None
            or mobile_fn is None
            or delivery_fn is None
        ):
            return {"ok": False, "error": "translator snapshot helpers not bound"}
        runtime_lite = await ctx.collect_runtime_lite()
        readiness = await readiness_fn(runtime_lite=runtime_lite)
        control_plane = await control_plane_fn(runtime_lite=runtime_lite)
        mobile_readiness = await mobile_fn(
            runtime_lite=runtime_lite,
            current_control_plane=control_plane,
        )
        return await delivery_fn(
            runtime_lite=runtime_lite,
            current_readiness=readiness,
            current_control_plane=control_plane,
            current_mobile_readiness=mobile_readiness,
        )

    # ------------------------------------------------------------------
    # Wave S — POST endpoints через ctx.assert_write_access.
    # Низкоуровневые операции над runtime_profile / session_state;
    # не требуют voice_gateway или _translator_* helpers.
    # ------------------------------------------------------------------

    @router.post("/api/translator/session/toggle")
    async def translator_session_toggle(
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Start/stop translator session через API."""
        ctx.assert_write_access(x_krab_web_key, token)
        kraab = ctx.get_dep("kraab_userbot")
        state = kraab.get_translator_session_state()
        if state.get("session_status") == "active":
            kraab.update_translator_session_state(
                session_status="idle",
                active_chats=[],
                last_event="session_stopped_api",
                persist=True,
            )
            return {"ok": True, "action": "stopped", "status": "idle"}
        profile = kraab.get_translator_runtime_profile()
        chat_id = str(payload.get("chat_id") or "").strip()
        active_chats = [chat_id] if chat_id else []
        kraab.update_translator_session_state(
            session_status="active",
            active_chats=active_chats,
            last_language_pair=profile.get("language_pair"),
            last_event="session_started_api",
            persist=True,
        )
        return {
            "ok": True,
            "action": "started",
            "status": "active",
            "active_chats": active_chats,
        }

    @router.post("/api/translator/auto")
    async def translator_auto(
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Switch to auto-detect mode via API."""
        ctx.assert_write_access(x_krab_web_key, token)
        kraab = ctx.get_dep("kraab_userbot")
        kraab.update_translator_runtime_profile(language_pair="auto-detect", persist=True)
        return {"ok": True, "language_pair": "auto-detect"}

    @router.post("/api/translator/lang")
    async def translator_set_lang(
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Сменить языковую пару через API."""
        ctx.assert_write_access(x_krab_web_key, token)
        from ...core.translator_runtime_profile import ALLOWED_LANGUAGE_PAIRS

        pair = str(payload.get("language_pair") or "").strip().lower()
        if pair not in ALLOWED_LANGUAGE_PAIRS:
            return {
                "ok": False,
                "error": f"invalid pair, use: {sorted(ALLOWED_LANGUAGE_PAIRS)}",
            }
        kraab = ctx.get_dep("kraab_userbot")
        kraab.update_translator_runtime_profile(language_pair=pair, persist=True)
        return {"ok": True, "language_pair": pair}

    @router.post("/api/translator/translate")
    async def translator_translate(
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Прямой перевод текста через API (без voice note)."""
        ctx.assert_write_access(x_krab_web_key, token)
        text = str(payload.get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "text required"}
        src_lang = str(payload.get("src_lang") or "").strip()
        tgt_lang = str(payload.get("tgt_lang") or "ru").strip()
        try:
            from ...core.language_detect import detect_language, resolve_translation_pair
            from ...core.translator_engine import translate_text
            from ...openclaw_client import openclaw_client as _oc

            if not src_lang:
                src_lang = detect_language(text)
            if not src_lang:
                return {"ok": False, "error": "language not detected"}
            kraab = ctx.get_dep("kraab_userbot")
            profile = kraab.get_translator_runtime_profile() if kraab else {}
            if not tgt_lang or tgt_lang == "auto":
                src_lang, tgt_lang = resolve_translation_pair(
                    src_lang, profile.get("language_pair", "es-ru")
                )
            result = await translate_text(text, src_lang, tgt_lang, openclaw_client=_oc)
            return {
                "ok": True,
                "original": result.original,
                "translated": result.translated,
                "src_lang": result.src_lang,
                "tgt_lang": result.tgt_lang,
                "latency_ms": result.latency_ms,
                "model": result.model_id,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Wave HH — translator session POST endpoints через helper injection.
    # Использует late-bound lambdas из _make_router_context для доступа к
    # WebApp методам (gateway_client, resolve_session_context, action_response,
    # gateway_error_detail, vg_subscriber_*) без self-bind на WebApp class.
    # ------------------------------------------------------------------

    def _require_helpers(*names: str):
        for n in names:
            if ctx.get_dep(n) is None:
                raise HTTPException(
                    status_code=503,
                    detail=f"translator_helper_missing:{n}",
                )

    @router.post("/api/translator/session/start")
    async def translator_session_start(
        request: Request,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ):
        """Создаёт translator session из owner panel без прямого доступа UI к Voice Gateway."""
        ctx.assert_write_access(x_krab_web_key, token)
        _require_helpers(
            "translator_gateway_client_helper",
            "translator_action_response_helper",
            "translator_gateway_error_detail_helper",
            "vg_subscriber_start_helper",
        )
        from ...core.operator_identity import current_account_id, current_operator_id

        voice_gateway = ctx.get_dep("translator_gateway_client_helper")()
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="translator_session_start_body_required")

        source = str(body.get("source") or "mic").strip() or "mic"
        translation_mode = str(body.get("translation_mode") or "auto_to_ru").strip() or "auto_to_ru"
        notify_mode = str(body.get("notify_mode") or "auto_on").strip() or "auto_on"
        tts_mode = str(body.get("tts_mode") or "hybrid").strip() or "hybrid"
        src_lang = str(body.get("src_lang") or "auto").strip() or "auto"
        tgt_lang = str(body.get("tgt_lang") or "ru").strip() or "ru"
        label = str(body.get("label") or "").strip()
        meta = dict(body.get("meta") or {}) if isinstance(body.get("meta"), dict) else {}
        meta["initiated_by"] = "owner_panel"
        meta["operator_id"] = current_operator_id()
        meta["account_id"] = current_account_id()
        if label:
            meta["session_label"] = label

        result = await voice_gateway.start_session(
            source=source,
            translation_mode=translation_mode,
            notify_mode=notify_mode,
            tts_mode=tts_mode,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            meta=meta,
        )
        if not result.get("ok"):
            err_helper = ctx.get_dep("translator_gateway_error_detail_helper")
            status_code, detail = err_helper(result, fallback="translator_session_start_failed")
            raise HTTPException(status_code=status_code, detail=detail)

        new_session_id = str(result.get("session_id") or "").strip()
        if new_session_id:
            await _await_if_needed(
                ctx.get_dep("vg_subscriber_start_helper")(new_session_id, voice_gateway)
            )

        return await _await_if_needed(
            ctx.get_dep("translator_action_response_helper")(
                action="start_session", gateway_result=result
            )
        )

    @router.post("/api/translator/session/policy")
    async def translator_session_policy_update(
        request: Request,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ):
        """Обновляет policy текущей translator session через owner panel."""
        ctx.assert_write_access(x_krab_web_key, token)
        _require_helpers(
            "translator_gateway_client_helper",
            "translator_resolve_session_context_helper",
            "translator_action_response_helper",
            "translator_gateway_error_detail_helper",
        )
        voice_gateway = ctx.get_dep("translator_gateway_client_helper")()
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="translator_session_policy_body_required")

        (
            session_id,
            runtime_lite,
            _control_plane,
        ) = await _await_if_needed(
            ctx.get_dep("translator_resolve_session_context_helper")(
                requested_session_id=str(body.get("session_id") or "").strip()
            )
        )
        patch: dict[str, Any] = {}
        for key in ("translation_mode", "notify_mode", "tts_mode", "src_lang", "tgt_lang"):
            value = body.get(key)
            if value is not None:
                clean = str(value).strip()
                if clean:
                    patch[key] = clean
        if not patch:
            raise HTTPException(status_code=400, detail="translator_session_policy_patch_required")

        result = await voice_gateway.patch_session(session_id, **patch)
        if not result.get("ok"):
            err_helper = ctx.get_dep("translator_gateway_error_detail_helper")
            status_code, detail = err_helper(
                result, fallback="translator_session_policy_update_failed"
            )
            raise HTTPException(status_code=status_code, detail=detail)
        return await _await_if_needed(
            ctx.get_dep("translator_action_response_helper")(
                action="update_session_policy",
                gateway_result=result,
                runtime_lite=runtime_lite,
            )
        )

    @router.post("/api/translator/session/action")
    async def translator_session_action(
        request: Request,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ):
        """Выполняет lifecycle-действие над translator session: pause/resume/stop."""
        ctx.assert_write_access(x_krab_web_key, token)
        _require_helpers(
            "translator_gateway_client_helper",
            "translator_resolve_session_context_helper",
            "translator_action_response_helper",
            "translator_gateway_error_detail_helper",
            "vg_subscriber_stop_helper",
        )
        voice_gateway = ctx.get_dep("translator_gateway_client_helper")()
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="translator_session_action_body_required")

        action = str(body.get("action") or "").strip().lower()
        if action not in {"pause", "resume", "stop"}:
            raise HTTPException(status_code=400, detail="translator_session_action_invalid")

        (
            session_id,
            runtime_lite,
            _control_plane,
        ) = await _await_if_needed(
            ctx.get_dep("translator_resolve_session_context_helper")(
                requested_session_id=str(body.get("session_id") or "").strip()
            )
        )
        if action == "stop":
            await _await_if_needed(ctx.get_dep("vg_subscriber_stop_helper")())
            result = await voice_gateway.stop_session(session_id)
        else:
            target_status = "paused" if action == "pause" else "running"
            result = await voice_gateway.patch_session(session_id, status=target_status)

        if not result.get("ok"):
            err_helper = ctx.get_dep("translator_gateway_error_detail_helper")
            status_code, detail = err_helper(result, fallback=f"translator_session_{action}_failed")
            raise HTTPException(status_code=status_code, detail=detail)
        return await _await_if_needed(
            ctx.get_dep("translator_action_response_helper")(
                action=f"{action}_session",
                gateway_result=result,
                runtime_lite=runtime_lite,
            )
        )

    @router.post("/api/translator/session/runtime-tune")
    async def translator_session_runtime_tune(
        request: Request,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ):
        """Обновляет runtime tuning текущей translator session."""
        ctx.assert_write_access(x_krab_web_key, token)
        _require_helpers(
            "translator_gateway_client_helper",
            "translator_resolve_session_context_helper",
            "translator_action_response_helper",
            "translator_gateway_error_detail_helper",
        )
        voice_gateway = ctx.get_dep("translator_gateway_client_helper")()
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="translator_runtime_tune_body_required")

        (
            session_id,
            runtime_lite,
            _control_plane,
        ) = await _await_if_needed(
            ctx.get_dep("translator_resolve_session_context_helper")(
                requested_session_id=str(body.get("session_id") or "").strip()
            )
        )
        buffering_mode = str(body.get("buffering_mode") or "").strip() or None
        target_latency_raw = body.get("target_latency_ms")
        vad_raw = body.get("vad_sensitivity")

        target_latency_ms = None
        if target_latency_raw not in (None, ""):
            try:
                target_latency_ms = int(target_latency_raw)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="translator_target_latency_invalid"
                ) from exc

        vad_sensitivity = None
        if vad_raw not in (None, ""):
            try:
                vad_sensitivity = float(vad_raw)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="translator_vad_sensitivity_invalid"
                ) from exc

        if buffering_mode is None and target_latency_ms is None and vad_sensitivity is None:
            raise HTTPException(status_code=400, detail="translator_runtime_tune_patch_required")

        result = await voice_gateway.tune_runtime(
            session_id,
            buffering_mode=buffering_mode,
            target_latency_ms=target_latency_ms,
            vad_sensitivity=vad_sensitivity,
        )
        if not result.get("ok"):
            err_helper = ctx.get_dep("translator_gateway_error_detail_helper")
            status_code, detail = err_helper(result, fallback="translator_runtime_tune_failed")
            raise HTTPException(status_code=status_code, detail=detail)
        return await _await_if_needed(
            ctx.get_dep("translator_action_response_helper")(
                action="runtime_tune_session",
                gateway_result=result,
                runtime_lite=runtime_lite,
            )
        )

    @router.post("/api/translator/session/quick-phrase")
    async def translator_session_quick_phrase(
        request: Request,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ):
        """Публикует quick-phrase в текущую translator session через owner panel."""
        ctx.assert_write_access(x_krab_web_key, token)
        _require_helpers(
            "translator_gateway_client_helper",
            "translator_resolve_session_context_helper",
            "translator_action_response_helper",
            "translator_gateway_error_detail_helper",
        )
        voice_gateway = ctx.get_dep("translator_gateway_client_helper")()
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="translator_quick_phrase_body_required")

        (
            session_id,
            runtime_lite,
            control_plane,
        ) = await _await_if_needed(
            ctx.get_dep("translator_resolve_session_context_helper")(
                requested_session_id=str(body.get("session_id") or "").strip()
            )
        )
        text = str(body.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="translator_quick_phrase_text_required")

        defaults = (
            ((control_plane.get("operator_actions") or {}).get("draft_defaults") or {})
            if isinstance(control_plane.get("operator_actions"), dict)
            else {}
        )
        source_lang = (
            str(body.get("source_lang") or defaults.get("quick_phrase_source_lang") or "ru").strip()
            or "ru"
        )
        target_lang = (
            str(body.get("target_lang") or defaults.get("quick_phrase_target_lang") or "es").strip()
            or "es"
        )
        voice = (
            str(body.get("voice") or defaults.get("quick_phrase_voice") or "default").strip()
            or "default"
        )
        style = (
            str(body.get("style") or defaults.get("quick_phrase_style") or "neutral").strip()
            or "neutral"
        )

        result = await voice_gateway.send_quick_phrase(
            session_id,
            text=text,
            source_lang=source_lang,
            target_lang=target_lang,
            voice=voice,
            style=style,
        )
        if not result.get("ok"):
            err_helper = ctx.get_dep("translator_gateway_error_detail_helper")
            status_code, detail = err_helper(result, fallback="translator_quick_phrase_failed")
            raise HTTPException(status_code=status_code, detail=detail)
        return await _await_if_needed(
            ctx.get_dep("translator_action_response_helper")(
                action="quick_phrase_session",
                gateway_result=result,
                runtime_lite=runtime_lite,
            )
        )

    @router.post("/api/translator/session/summary")
    async def translator_session_summary(
        request: Request,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ):
        """Принудительно пересобирает session summary через Voice Gateway."""
        ctx.assert_write_access(x_krab_web_key, token)
        _require_helpers(
            "translator_gateway_client_helper",
            "translator_resolve_session_context_helper",
            "translator_action_response_helper",
            "translator_gateway_error_detail_helper",
        )
        voice_gateway = ctx.get_dep("translator_gateway_client_helper")()
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="translator_session_summary_body_required")
        (
            session_id,
            runtime_lite,
            _control_plane,
        ) = await _await_if_needed(
            ctx.get_dep("translator_resolve_session_context_helper")(
                requested_session_id=str(body.get("session_id") or "").strip()
            )
        )
        max_items_raw = body.get("max_items", 20)
        try:
            max_items = int(max_items_raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail="translator_summary_max_items_invalid"
            ) from exc
        result = await voice_gateway.build_summary(session_id, max_items=max_items)
        if not result.get("ok"):
            err_helper = ctx.get_dep("translator_gateway_error_detail_helper")
            status_code, detail = err_helper(result, fallback="translator_session_summary_failed")
            raise HTTPException(status_code=status_code, detail=detail)
        return await _await_if_needed(
            ctx.get_dep("translator_action_response_helper")(
                action="build_session_summary",
                gateway_result=result,
                runtime_lite=runtime_lite,
            )
        )

    return router
