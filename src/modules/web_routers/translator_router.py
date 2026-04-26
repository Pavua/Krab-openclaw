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

from fastapi import APIRouter, Body, Header, Query

from ._context import RouterContext


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

    return router
