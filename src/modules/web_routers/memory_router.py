# -*- coding: utf-8 -*-
"""
Memory router — Phase 2 Wave B + Wave S extraction (Session 25).

Wave B: stateless GET endpoints (/api/memory/stats, /api/memory/indexer).
Wave S: factory-pattern + POST /api/memory/indexer/flush через
``ctx.assert_write_access`` (write-protected owner debug tool).

Endpoints:
- GET  /api/memory/stats           — Memory Layer статистика (Dashboard V4)
- GET  /api/memory/indexer         — IndexerStats snapshot для owner panel
- POST /api/memory/indexer/flush   — owner-tool принудительный flush

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.

Backwards-compat: модуль также экспортирует ``router`` (готовый APIRouter
без ctx) — для сценариев, где POST не нужен. Новый код должен использовать
``build_memory_router(ctx)``.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, Query

from ._context import RouterContext


def _build_get_endpoints(router: APIRouter) -> None:
    """Регистрирует stateless GET endpoints (без ctx).

    Используется и factory ``build_memory_router``, и backwards-compat
    ``router`` экспорт.
    """

    @router.get("/api/memory/stats")
    async def memory_stats() -> dict:
        """Статистика Memory Layer для Dashboard V4."""
        from ...core.memory_stats import collect_memory_stats

        return collect_memory_stats()

    @router.get("/api/memory/indexer")
    async def memory_indexer_stats() -> dict:
        """Снимок IndexerStats для owner panel."""
        try:
            from ...core.memory_indexer_worker import get_indexer
        except ImportError:
            return {"error": "indexer_unavailable"}
        stats = get_indexer().get_stats()
        return {
            "is_running": stats.is_running,
            "started_at": stats.started_at.isoformat() if stats.started_at else None,
            "queue_size": stats.queue_size,
            "queue_maxsize": stats.queue_maxsize,
            "enqueued_total": stats.enqueued_total,
            "processed_total": stats.processed_total,
            "chunks_committed": stats.chunks_committed,
            "embeddings_committed": stats.embeddings_committed,
            "skipped": dict(stats.skipped),
            "dropped_queue_full": stats.dropped_queue_full,
            "failed": dict(stats.failed),
            "last_flush_at": stats.last_flush_at.isoformat() if stats.last_flush_at else None,
            "last_flush_duration_sec": stats.last_flush_duration_sec,
            "builders_active": stats.builders_active,
            "restarts": stats.restarts,
            "embed_disabled": stats.embed_disabled,
        }


def build_memory_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с memory GET + POST endpoints.

    Wave S добавляет POST /api/memory/indexer/flush через
    ``ctx.assert_write_access`` (write-protected owner-tool).
    """
    router = APIRouter(tags=["memory"])
    _build_get_endpoints(router)

    @router.post("/api/memory/indexer/flush")
    async def memory_indexer_flush(
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Принудительный flush (debug/owner tool)."""
        ctx.assert_write_access(x_krab_web_key, token)
        try:
            from ...core.memory_indexer_worker import get_indexer
        except ImportError:
            return {"error": "indexer_unavailable"}
        stats = get_indexer().get_stats()
        return {
            "ack": True,
            "queue_size": stats.queue_size,
            "note": "flush будет выполнен в течение batch_timeout_sec",
        }

    return router


# Backwards-compat: legacy GET-only router без ctx (Wave B контракт).
# Существующие тесты / импорты ``from ...memory_router import router``
# продолжают работать без изменений.
router = APIRouter(tags=["memory"])
_build_get_endpoints(router)
