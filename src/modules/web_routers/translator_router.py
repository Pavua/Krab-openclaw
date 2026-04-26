# -*- coding: utf-8 -*-
"""
Translator router — Phase 2 Wave K extraction (Session 25).

Простые GET endpoints translator-домена через RouterContext.
Не extract'ятся endpoints, требующие ``self._collect_runtime_lite_snapshot()``
(readiness/bootstrap/control-plane/session-inspector/mobile/* —
отложены до wave с runtime_lite refactor).

Endpoints:
- GET /api/translator/languages — список языковых пар + текущая
- GET /api/translator/status    — лёгкий профиль + session state
- GET /api/translator/history   — история переводов + статистика
- GET /api/translator/test      — быстрый тест перевода через GET-параметры

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

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

    return router
