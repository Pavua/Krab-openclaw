# -*- coding: utf-8 -*-
"""
Phase 2 Wave B extraction — memory_router (Session 25).

Verify что extraction в src/modules/web_routers/memory_router.py
сохраняет существующий контракт endpoints /api/memory/stats и
/api/memory/indexer.
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers.memory_router import router as memory_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(memory_router)
    return TestClient(app)


# ── /api/memory/stats ─────────────────────────────────────────────────


def test_memory_stats_returns_200() -> None:
    """GET /api/memory/stats → 200, проксирует collect_memory_stats."""
    fake_stats = {"messages": 12345, "chunks": 6789, "vec_chunks": 6500}
    with patch(
        "src.core.memory_stats.collect_memory_stats",
        return_value=fake_stats,
    ):
        resp = _client().get("/api/memory/stats")

    assert resp.status_code == 200
    assert resp.json() == fake_stats


def test_memory_stats_empty_dict() -> None:
    """Пустой dict от collect_memory_stats не валит endpoint."""
    with patch("src.core.memory_stats.collect_memory_stats", return_value={}):
        resp = _client().get("/api/memory/stats")
    assert resp.status_code == 200
    assert resp.json() == {}


# ── /api/memory/indexer ───────────────────────────────────────────────


class _FakeStats:
    is_running = True
    started_at = None
    queue_size = 5
    queue_maxsize = 1000
    enqueued_total = 100
    processed_total = 95
    chunks_committed = 90
    embeddings_committed = 85
    skipped = {"empty": 2}
    dropped_queue_full = 0
    failed = {"oom": 1}
    last_flush_at = None
    last_flush_duration_sec = 0.42
    builders_active = 2
    restarts = 0
    embed_disabled = False


def test_memory_indexer_returns_200() -> None:
    """GET /api/memory/indexer → 200 + полный shape."""

    class _FakeIndexer:
        def get_stats(self):
            return _FakeStats()

    with patch(
        "src.core.memory_indexer_worker.get_indexer",
        return_value=_FakeIndexer(),
    ):
        resp = _client().get("/api/memory/indexer")

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_running"] is True
    assert data["queue_size"] == 5
    assert data["queue_maxsize"] == 1000
    assert data["enqueued_total"] == 100
    assert data["processed_total"] == 95
    assert data["chunks_committed"] == 90
    assert data["embeddings_committed"] == 85
    assert data["skipped"] == {"empty": 2}
    assert data["failed"] == {"oom": 1}
    assert data["dropped_queue_full"] == 0
    assert data["last_flush_duration_sec"] == 0.42
    assert data["builders_active"] == 2
    assert data["restarts"] == 0
    assert data["embed_disabled"] is False
    assert data["started_at"] is None
    assert data["last_flush_at"] is None


def test_memory_indexer_serializes_datetime() -> None:
    """started_at / last_flush_at вызывают .isoformat()."""
    from datetime import datetime, timezone

    ts = datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)

    class _StatsWithDt(_FakeStats):
        started_at = ts
        last_flush_at = ts

    class _FakeIndexer:
        def get_stats(self):
            return _StatsWithDt()

    with patch(
        "src.core.memory_indexer_worker.get_indexer",
        return_value=_FakeIndexer(),
    ):
        resp = _client().get("/api/memory/indexer")

    data = resp.json()
    assert data["started_at"] == ts.isoformat()
    assert data["last_flush_at"] == ts.isoformat()


def test_memory_indexer_graceful_on_import_error() -> None:
    """ImportError при импорте get_indexer → graceful {error: indexer_unavailable}."""
    import builtins

    real_import = builtins.__import__

    def _raising_import(name, *args, **kwargs):
        if name == "src.core.memory_indexer_worker" or name.endswith(
            "memory_indexer_worker"
        ):
            raise ImportError("simulated missing module")
        return real_import(name, *args, **kwargs)

    with patch.object(builtins, "__import__", side_effect=_raising_import):
        resp = _client().get("/api/memory/indexer")

    assert resp.status_code == 200
    assert resp.json() == {"error": "indexer_unavailable"}
