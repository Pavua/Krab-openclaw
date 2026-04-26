# -*- coding: utf-8 -*-
"""
Memory router — Phase 2 Wave B extraction (Session 25).

Stateless memory endpoints:
  • /api/memory/stats     — Memory Layer статистика (Dashboard V4)
  • /api/memory/indexer   — IndexerStats snapshot для owner panel

Эти endpoints НЕ требуют RouterContext (deps). Lazy import core-функций
сохраняет graceful fallback (ImportError → indexer_unavailable).

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["memory"])


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
