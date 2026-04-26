# -*- coding: utf-8 -*-
"""
Runtime status router — Phase 2 Wave D extraction (Session 25).

4 stateless GET endpoints для diagnostics runtime-singleton'ов:
- GET /api/silence/status            — silence_manager.status()
- GET /api/notify/status             — config.TOOL_NARRATION_ENABLED
- GET /api/message_batcher/stats     — message_batcher.stats()
- GET /api/chat_windows/stats        — chat_window_manager.stats() (graceful)

POST endpoints (silence/toggle, notify/toggle) НЕ выносим — требуют
``_assert_write_access`` и будут вынесены после введения RouterContext.

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["runtime-status"])


@router.get("/api/silence/status")
async def silence_status() -> dict:
    """Текущий статус тишины."""
    from ...core.silence_mode import silence_manager

    return {"ok": True, **silence_manager.status()}


@router.get("/api/notify/status")
async def notify_status() -> dict:
    """Статус tool narration toggle."""
    from ...config import config

    return {"ok": True, "enabled": bool(getattr(config, "TOOL_NARRATION_ENABLED", True))}


@router.get("/api/message_batcher/stats")
async def batcher_stats() -> dict:
    """Статистика per-chat message batcher (backpressure буфер)."""
    from ...core.message_batcher import message_batcher

    return {"ok": True, **message_batcher.stats()}


@router.get("/api/chat_windows/stats")
async def chat_windows_stats() -> dict:
    """Статистика per-chat ChatWindow LRU manager (Chado blueprint)."""
    try:
        from ...core.chat_window_manager import chat_window_manager

        return {"ok": True, **chat_window_manager.stats()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
