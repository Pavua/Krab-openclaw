# -*- coding: utf-8 -*-
"""
Misc router — Phase 2 Wave Z + Wave AA extraction (Session 25).

Объединяет miscellaneous endpoints, которые не вписываются доменно в уже
существующие routers, но self-contained через RouterContext (используют
только ``ctx.deps`` + module-level singletons).

Endpoints:
- GET  /api/transcriber/status      — readiness транскрибатора (perceptor + voice stack)
- GET  /api/reactions/stats         — сводка реакций reaction_engine (опц. chat_id)
- GET  /api/mood/{chat_id}          — mood-профиль конкретного чата
- GET  /api/inbox/events            — SSE stream обновлений inbox
- GET  /api/chat_windows/config     — env-конфигурация ChatWindowManager
- GET  /api/chat_windows/list       — активные окна с метаданными
- POST /api/chat_windows/evict_idle — Wave AA: выгнать окна старше max_age_sec
- POST /api/chat_windows/clear      — Wave AA: очистить все окна (owner-only)

Wave AA добавляет POST endpoints через ``ctx.assert_write_access``.
``/api/diagnostics/smoke`` остаётся inline — endpoint без write_access guard
и зависит от WebApp helpers (deferred). /api/session10/summary тоже inline.

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, Query
from fastapi.responses import StreamingResponse

from ._context import RouterContext


def build_misc_router(ctx: RouterContext) -> APIRouter:
    """Factory: APIRouter с misc read-only endpoints."""
    router = APIRouter(tags=["misc"])

    # ── /api/transcriber/status ─────────────────────────────────────────────

    @router.get("/api/transcriber/status")
    async def transcriber_status() -> dict:
        """Операционный статус транскрибатора (voice/STT readiness)."""
        openclaw = ctx.get_dep("openclaw_client")
        voice_gateway = ctx.get_dep("voice_gateway_client")
        krab_ear = ctx.get_dep("krab_ear_client")
        perceptor = ctx.get_dep("perceptor")
        kraab_userbot = ctx.get_dep("kraab_userbot")

        openclaw_ok = False
        voice_gateway_ok = False
        krab_ear_ok = False
        try:
            openclaw_ok = bool(await openclaw.health_check()) if openclaw else False
        except Exception:
            openclaw_ok = False
        try:
            voice_gateway_ok = bool(await voice_gateway.health_check()) if voice_gateway else False
        except Exception:
            voice_gateway_ok = False
        try:
            krab_ear_ok = bool(await krab_ear.health_check()) if krab_ear else False
        except Exception:
            krab_ear_ok = False

        def _env_on(key: str, default: str = "0") -> bool:
            return str(os.getenv(key, default)).strip().lower() in {"1", "true", "yes", "on"}

        stt_isolated_worker = _env_on("STT_ISOLATED_WORKER", "1")
        perceptor_ready = bool(perceptor) and hasattr(perceptor, "transcribe")
        perceptor_isolated_worker = bool(
            getattr(perceptor, "stt_isolated_worker", stt_isolated_worker)
        )
        stt_worker_timeout = int(
            str(os.getenv("STT_WORKER_TIMEOUT_SECONDS", "240")).strip() or "240"
        )
        voice_stack_ready = bool(voice_gateway_ok and krab_ear_ok)
        voice_profile: dict = {}
        if kraab_userbot and hasattr(kraab_userbot, "get_voice_runtime_profile"):
            try:
                voice_profile = dict(kraab_userbot.get_voice_runtime_profile() or {})
            except Exception:
                voice_profile = {}
        live_voice_ready = bool(
            perceptor_ready and voice_stack_ready and voice_profile.get("enabled")
        )

        if perceptor_ready and perceptor_isolated_worker and voice_stack_ready:
            readiness = "ready"
        elif perceptor_ready:
            readiness = "degraded"
        else:
            readiness = "down"
        recommendations: list[str] = []
        if not perceptor_ready:
            recommendations.append(
                "Perceptor/STT не подключён: voice notes не будут транскрибироваться"
            )
            recommendations.append("Запусти ./transcriber_doctor.command --heal")
        if perceptor_ready and not perceptor_isolated_worker:
            recommendations.append("Включи STT_ISOLATED_WORKER=1 и перезапусти Krab")
        if not voice_gateway_ok:
            recommendations.append(
                "Voice Gateway недоступен: звонки и live voice-stream будут ограничены"
            )
        if not krab_ear_ok:
            recommendations.append(
                "Krab Ear недоступен: wake/call-часть voice-контура деградировала"
            )
        if voice_profile:
            if not bool(voice_profile.get("enabled")):
                recommendations.append(
                    "Voice replies выключены: входящий voice ingress готов, но ответы голосом отключены"
                )
            elif live_voice_ready:
                recommendations.append("Voice replies включены: foundation для live voice готова")
        if not recommendations:
            recommendations.append("Система транскрибации в рабочем режиме")

        return {
            "ok": True,
            "status": {
                "readiness": readiness,
                "openclaw_ok": openclaw_ok,
                "voice_gateway_ok": voice_gateway_ok,
                "krab_ear_ok": krab_ear_ok,
                "perceptor_ready": perceptor_ready,
                "stt_isolated_worker": perceptor_isolated_worker,
                "stt_worker_timeout_seconds": stt_worker_timeout,
                "voice_gateway_url": os.getenv("VOICE_GATEWAY_URL", "http://127.0.0.1:8090"),
                "whisper_model": str(getattr(perceptor, "whisper_model", "")),
                "audio_warmup_enabled": _env_on("PERCEPTOR_AUDIO_WARMUP", "0"),
                "voice_profile": voice_profile,
                "live_voice_ready": live_voice_ready,
                "recommendations": recommendations,
            },
        }

    # ── /api/reactions/stats ────────────────────────────────────────────────

    @router.get("/api/reactions/stats")
    async def get_reactions_stats(chat_id: int | None = Query(default=None)) -> dict:
        """Сводка по реакциям (общая или по чату)."""
        reaction_engine = ctx.get_dep("reaction_engine")
        if not reaction_engine:
            return {"ok": False, "error": "reaction_engine_not_configured"}
        return {"ok": True, "stats": reaction_engine.get_reaction_stats(chat_id=chat_id)}

    # ── /api/mood/{chat_id} ─────────────────────────────────────────────────

    @router.get("/api/mood/{chat_id}")
    async def get_chat_mood(chat_id: int) -> dict:
        """Возвращает mood-профиль конкретного чата."""
        reaction_engine = ctx.get_dep("reaction_engine")
        if not reaction_engine:
            return {"ok": False, "error": "reaction_engine_not_configured"}
        return {"ok": True, "mood": reaction_engine.get_chat_mood(chat_id)}

    # ── /api/inbox/events ───────────────────────────────────────────────────

    @router.get("/api/inbox/events")
    async def inbox_events(token: str = Query(default="")) -> StreamingResponse:
        """SSE stream для inbox updates.

        Стримит summary (open/attention/escalations/stale) + items list.
        Эмитит event 'update' только при реальном изменении состояния.
        Heartbeat каждые 5 секунд.
        """
        # Local import — singleton может быть mock'нут в тестах через monkeypatch.
        import structlog

        from ...core.inbox_service import inbox_service

        _logger = structlog.get_logger("misc_router")

        async def event_stream():
            last_hash: Optional[str] = None
            while True:
                try:
                    workflow = inbox_service.get_workflow_snapshot()
                    summary = workflow.get("summary") or {}
                    items = inbox_service.list_items(status="all", kind="", limit=20)

                    payload = {
                        "summary": summary,
                        "workflow": workflow,
                        "items": items,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }

                    current_hash = hashlib.sha256(
                        json.dumps(
                            {"summary": summary, "items": items},
                            sort_keys=True,
                            default=str,
                        ).encode()
                    ).hexdigest()

                    if current_hash != last_hash:
                        last_hash = current_hash
                        yield f"event: update\ndata: {json.dumps(payload, default=str)}\n\n"
                    else:
                        yield ": heartbeat\n\n"

                    await asyncio.sleep(5)
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    _logger.error("inbox_events_error", error=str(exc))
                    yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
                    await asyncio.sleep(10)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── /api/chat_windows/config ────────────────────────────────────────────

    @router.get("/api/chat_windows/config")
    async def chat_windows_config() -> dict:
        """Возвращает env-конфигурацию ChatWindowManager."""
        from src.core.chat_window_manager import (
            CAPACITY,
            IDLE_EVICTION_SEC,
            MESSAGE_CAP_PER_WINDOW,
        )

        return {
            "ok": True,
            "capacity": CAPACITY,
            "message_cap_per_window": MESSAGE_CAP_PER_WINDOW,
            "idle_eviction_sec": IDLE_EVICTION_SEC,
        }

    # ── /api/chat_windows/list ──────────────────────────────────────────────

    @router.get("/api/chat_windows/list")
    async def chat_windows_list() -> dict:
        """Список всех активных окон с метаданными."""
        from src.core.chat_window_manager import chat_window_manager

        windows = chat_window_manager.list_windows()
        return {
            "ok": True,
            "total": len(windows),
            "windows": windows,
        }

    # ── /api/chat_windows/evict_idle (Wave AA) ──────────────────────────────

    @router.post("/api/chat_windows/evict_idle")
    async def chat_windows_evict_idle(
        max_age_sec: int = Query(default=3600),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Выгнать окна, неактивные дольше max_age_sec."""
        ctx.assert_write_access(x_krab_web_key, token)
        from src.core.chat_window_manager import IDLE_EVICTION_SEC, chat_window_manager

        timeout = max_age_sec if max_age_sec > 0 else IDLE_EVICTION_SEC
        count = chat_window_manager.evict_idle(timeout_sec=timeout)
        return {
            "ok": True,
            "evicted": count,
            "timeout_sec": timeout,
        }

    # ── /api/chat_windows/clear (Wave AA) ───────────────────────────────────

    @router.post("/api/chat_windows/clear")
    async def chat_windows_clear(
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Очистить все окна (owner-only)."""
        ctx.assert_write_access(x_krab_web_key, token)
        from src.core.chat_window_manager import chat_window_manager

        count = chat_window_manager.clear_all()
        return {
            "ok": True,
            "cleared": count,
        }

    return router
