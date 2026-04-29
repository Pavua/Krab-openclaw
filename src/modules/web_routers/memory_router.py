# -*- coding: utf-8 -*-
"""
Memory router — Phase 2 Wave B + Wave S + Wave BB extraction (Session 25).

Wave B: stateless GET endpoints (/api/memory/stats, /api/memory/indexer).
Wave S: factory-pattern + POST /api/memory/indexer/flush через
``ctx.assert_write_access`` (write-protected owner debug tool).
Wave BB: advanced GET endpoints — /api/memory/search (HybridRetriever)
и /api/memory/heatmap (chat×time density через sqlite3).

Endpoints:
- GET  /api/memory/stats           — Memory Layer статистика (Dashboard V4)
- GET  /api/memory/indexer         — IndexerStats snapshot для owner panel
- POST /api/memory/indexer/flush   — owner-tool принудительный flush
- GET  /api/memory/search          — FTS5/semantic/hybrid поиск (Phase 2)
- GET  /api/memory/heatmap         — chat × time density (Dashboard V4)

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.

Backwards-compat: модуль также экспортирует ``router`` (готовый APIRouter
без ctx) — для сценариев, где POST не нужен. Новый код должен использовать
``build_memory_router(ctx)``.
"""

from __future__ import annotations

import asyncio
from typing import Optional

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

    @router.get("/api/memory/search")
    async def memory_search(
        q: str = "",
        mode: str = "hybrid",
        limit: int = 10,
        chat_id: Optional[str] = None,
    ):
        """
        Поиск в Memory Layer archive.db (FTS5 + semantic + hybrid).

        Phase 2 retrieval: использует ``HybridRetriever``, который внутри
        делает FTS5 BM25, опционально vector similarity через sqlite-vec,
        и Reciprocal Rank Fusion.

        Параметры:
          q: поисковый запрос (обязателен).
          mode: ``fts`` | ``semantic`` | ``hybrid`` (default ``hybrid``).
          limit: сколько результатов вернуть (default 10, max 50).
          chat_id: опциональный фильтр по чату.

        Returns: ``{ok, query, mode, count, results: [...]}``.
        """
        query = (q or "").strip()
        if not query:
            return {"ok": False, "error": "empty_query"}

        mode_normalized = (mode or "hybrid").strip().lower()
        if mode_normalized not in {"fts", "semantic", "hybrid"}:
            return {"ok": False, "error": "invalid_mode", "mode": mode}

        try:
            limit_val = max(1, min(50, int(limit)))
        except (TypeError, ValueError):
            limit_val = 10

        try:
            from ...core.memory_archive import ArchivePaths
            from ...core.memory_retrieval import HybridRetriever
        except ImportError:
            return {"ok": False, "error": "memory_layer_unavailable"}

        paths = ArchivePaths.default()
        if not paths.db.exists():
            return {"ok": False, "error": "archive_db_missing"}

        try:
            retriever = HybridRetriever(archive_paths=paths)
            raw_results = await asyncio.to_thread(
                retriever.search,
                query,
                chat_id=chat_id,
                top_k=limit_val,
            )
            effective_mode = mode_normalized
            if mode_normalized == "semantic" and not getattr(retriever, "_vec_available", False):
                effective_mode = "fts"
            retriever.close()
        except Exception as exc:  # noqa: BLE001 — endpoint не должен падать
            try:
                import structlog as _structlog

                _structlog.get_logger("WebApp").warning("memory_search_failed", error=str(exc))
            except Exception:  # noqa: BLE001
                pass
            return {"ok": False, "error": "search_failed", "detail": str(exc)}

        results = []
        for sr in raw_results:
            text = sr.text_redacted or ""
            preview = text if len(text) <= 300 else text[:300] + "..."
            results.append(
                {
                    "chunk_id": sr.message_id,
                    "chat_id": sr.chat_id,
                    "text": preview,
                    "score": float(sr.score),
                    "timestamp": sr.timestamp.isoformat() if sr.timestamp else None,
                    "mode": effective_mode,
                }
            )

        return {
            "ok": True,
            "query": query,
            "mode": effective_mode,
            "requested_mode": mode_normalized,
            "count": len(results),
            "results": results,
        }

    @router.get("/api/memory/heatmap")
    async def memory_heatmap(
        bucket_hours: int = 24,
        top_chats: int = 20,
    ):
        """
        Плотность сообщений по чатам и временным bucket'ам (heatmap, Dashboard V4).

        Params:
          bucket_hours: размер bucket в часах (clamped к [1, 8760]).
          top_chats: сколько топ-чатов включить (default 20).

        Returns:
          { bucket_hours, chats: [{chat_id, chat_title, buckets: [{ts, count}]}], generated_at }
        """
        import sqlite3 as _sqlite3
        from datetime import datetime, timezone
        from pathlib import Path

        from src.modules.web_app_heatmap import build_bucket_sql_expr

        # Clamp до разумного диапазона: [1, 8760] часов (год)
        bucket_hours = max(1, min(int(bucket_hours), 8760))

        db_path = Path("~/.openclaw/krab_memory/archive.db").expanduser()

        if not db_path.exists():
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=503,
                content={"error": f"archive.db not found: {db_path}"},
            )

        try:
            uri = f"file:{db_path}?mode=ro"
            conn = _sqlite3.connect(uri, uri=True)
        except _sqlite3.OperationalError as exc:
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=503,
                content={"error": f"archive.db open failed: {exc}"},
            )

        try:
            try:
                top_rows = conn.execute(
                    """
                    SELECT chat_id, COUNT(*) as cnt
                    FROM messages
                    GROUP BY chat_id
                    ORDER BY cnt DESC
                    LIMIT ?
                    """,
                    (max(1, top_chats),),
                ).fetchall()
            except _sqlite3.DatabaseError as exc:
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    status_code=503,
                    content={"error": f"archive.db malformed: {exc}"},
                )

            if not top_rows:
                return {
                    "bucket_hours": bucket_hours,
                    "chats": [],
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                }

            top_chat_ids = [r[0] for r in top_rows]

            chat_titles: dict[str, str] = {}
            try:
                placeholders = ",".join("?" * len(top_chat_ids))
                title_rows = conn.execute(
                    f"SELECT chat_id, title FROM chats WHERE chat_id IN ({placeholders})",
                    top_chat_ids,
                ).fetchall()
                chat_titles = {r[0]: r[1] for r in title_rows if r[1]}
            except _sqlite3.DatabaseError:
                pass

            bucket_expr = build_bucket_sql_expr(bucket_hours)
            try:
                placeholders = ",".join("?" * len(top_chat_ids))
                density_rows = conn.execute(
                    f"""
                    SELECT chat_id,
                           {bucket_expr} AS bucket_ts,
                           COUNT(*) AS cnt
                    FROM messages
                    WHERE chat_id IN ({placeholders})
                    GROUP BY chat_id, bucket_ts
                    ORDER BY chat_id, bucket_ts
                    """,
                    top_chat_ids,
                ).fetchall()
            except _sqlite3.DatabaseError as exc:
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    status_code=503,
                    content={"error": f"archive.db malformed on density query: {exc}"},
                )

            from collections import defaultdict

            buckets_by_chat: dict[str, list[dict]] = defaultdict(list)
            for chat_id, bucket_ts, cnt in density_rows:
                buckets_by_chat[chat_id].append({"ts": bucket_ts, "count": cnt})

            chats_out = []
            for chat_id in top_chat_ids:
                chats_out.append(
                    {
                        "chat_id": chat_id,
                        "chat_title": chat_titles.get(chat_id, chat_id),
                        "buckets": buckets_by_chat.get(chat_id, []),
                    }
                )

            return {
                "bucket_hours": bucket_hours,
                "chats": chats_out,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }

        finally:
            conn.close()


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
