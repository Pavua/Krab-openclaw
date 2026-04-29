# -*- coding: utf-8 -*-
"""
Phase 2 Wave B extraction — memory_router (Session 25).

Verify что extraction в src/modules/web_routers/memory_router.py
сохраняет существующий контракт endpoints /api/memory/stats и
/api/memory/indexer.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.memory_router import build_memory_router
from src.modules.web_routers.memory_router import router as memory_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(memory_router)
    return TestClient(app)


def _factory_client() -> TestClient:
    """Wave S: factory-pattern client with full POST endpoints."""
    ctx = RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )
    app = FastAPI()
    app.include_router(build_memory_router(ctx))
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


# ── POST /api/memory/indexer/flush (Wave S) ───────────────────────────


def test_memory_indexer_flush_returns_ack(monkeypatch) -> None:
    """POST /api/memory/indexer/flush → ack=True + queue_size."""
    monkeypatch.setenv("WEB_API_KEY", "")

    class _FakeIndexer:
        def get_stats(self):
            return _FakeStats()

    with patch(
        "src.core.memory_indexer_worker.get_indexer",
        return_value=_FakeIndexer(),
    ):
        resp = _factory_client().post("/api/memory/indexer/flush")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ack"] is True
    assert data["queue_size"] == 5
    assert "note" in data


def test_memory_indexer_flush_graceful_on_import_error(monkeypatch) -> None:
    """ImportError → graceful {error: indexer_unavailable}."""
    monkeypatch.setenv("WEB_API_KEY", "")
    import builtins

    real_import = builtins.__import__

    def _raising_import(name, *args, **kwargs):
        if name.endswith("memory_indexer_worker"):
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    with patch.object(builtins, "__import__", side_effect=_raising_import):
        resp = _factory_client().post("/api/memory/indexer/flush")

    assert resp.status_code == 200
    assert resp.json() == {"error": "indexer_unavailable"}


def test_memory_indexer_flush_requires_write_access(monkeypatch) -> None:
    """С WEB_API_KEY set, без header → 403."""
    monkeypatch.setenv("WEB_API_KEY", "secret")
    resp = _factory_client().post("/api/memory/indexer/flush")
    assert resp.status_code == 403


# ── /api/memory/search (Wave BB) ──────────────────────────────────────


def test_memory_search_empty_query_returns_error() -> None:
    """Пустой query → ok=False, error=empty_query (no DB hit)."""
    resp = _client().get("/api/memory/search?q=")
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "error": "empty_query"}


def test_memory_search_invalid_mode_returns_error() -> None:
    """Невалидный mode → ok=False, error=invalid_mode."""
    resp = _client().get("/api/memory/search?q=hello&mode=lalala")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] == "invalid_mode"


def test_memory_search_archive_db_missing(tmp_path) -> None:
    """Если archive.db не существует → ok=False, error=archive_db_missing."""

    class _FakePaths:
        db = tmp_path / "nonexistent.db"

        @classmethod
        def default(cls):
            return cls()

    fake_archive = type("M", (), {"ArchivePaths": _FakePaths})
    fake_retrieval = type("M", (), {"HybridRetriever": object})

    import sys

    monkey = {
        "src.core.memory_archive": fake_archive,
        "src.core.memory_retrieval": fake_retrieval,
    }
    saved = {k: sys.modules.get(k) for k in monkey}
    try:
        sys.modules.update(monkey)
        resp = _client().get("/api/memory/search?q=hello")
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "error": "archive_db_missing"}


def test_memory_search_returns_results(tmp_path) -> None:
    """Happy path → результаты от mocked HybridRetriever."""
    from datetime import datetime, timezone

    db_file = tmp_path / "archive.db"
    db_file.write_text("dummy")

    class _FakePaths:
        db = db_file

        @classmethod
        def default(cls):
            return cls()

    class _FakeResult:
        message_id = "msg-1"
        chat_id = "chat-42"
        text_redacted = "hello world preview"
        score = 0.87
        timestamp = datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)

    class _FakeRetriever:
        _vec_available = True

        def __init__(self, archive_paths=None):
            pass

        def search(self, query, chat_id=None, top_k=10):
            return [_FakeResult()]

        def close(self):
            pass

    fake_archive = type("M", (), {"ArchivePaths": _FakePaths})
    fake_retrieval = type("M", (), {"HybridRetriever": _FakeRetriever})

    import sys

    monkey = {
        "src.core.memory_archive": fake_archive,
        "src.core.memory_retrieval": fake_retrieval,
    }
    saved = {k: sys.modules.get(k) for k in monkey}
    try:
        sys.modules.update(monkey)
        resp = _client().get("/api/memory/search?q=hello&limit=5")
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["query"] == "hello"
    assert body["count"] == 1
    assert body["results"][0]["chunk_id"] == "msg-1"
    assert body["results"][0]["chat_id"] == "chat-42"
    assert body["results"][0]["score"] == 0.87


# ── /api/memory/heatmap (Wave BB) ─────────────────────────────────────


def test_memory_heatmap_archive_db_missing(monkeypatch, tmp_path) -> None:
    """Когда archive.db отсутствует → 503 + JSON error."""
    monkeypatch.setattr(
        Path,
        "expanduser",
        lambda self: tmp_path / "krab_memory" / "archive.db",
    )
    resp = _client().get("/api/memory/heatmap")
    assert resp.status_code == 503
    body = resp.json()
    assert "error" in body
    assert "not found" in body["error"]


def test_memory_heatmap_empty_db(monkeypatch, tmp_path) -> None:
    """Пустая archive.db (no messages) → пустой ответ + generated_at."""
    import sqlite3

    db_file = tmp_path / "archive.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE messages (chat_id TEXT, timestamp TEXT)")
    conn.execute("CREATE TABLE chats (chat_id TEXT, title TEXT)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(Path, "expanduser", lambda self: db_file)
    resp = _client().get("/api/memory/heatmap?bucket_hours=24&top_chats=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bucket_hours"] == 24
    assert body["chats"] == []
    assert "generated_at" in body


def test_memory_heatmap_with_data(monkeypatch, tmp_path) -> None:
    """archive.db с данными → топ-чаты с buckets."""
    import sqlite3

    db_file = tmp_path / "archive.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "CREATE TABLE messages (chat_id TEXT, timestamp TEXT)"
    )
    conn.execute("CREATE TABLE chats (chat_id TEXT, title TEXT)")
    conn.executemany(
        "INSERT INTO messages VALUES (?, ?)",
        [
            ("chat-A", "2026-04-10T08:00:00Z"),
            ("chat-A", "2026-04-11T09:00:00Z"),
            ("chat-B", "2026-04-10T08:00:00Z"),
        ],
    )
    conn.execute(
        "INSERT INTO chats VALUES (?, ?)", ("chat-A", "Chat A Title")
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(Path, "expanduser", lambda self: db_file)
    resp = _client().get("/api/memory/heatmap?top_chats=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bucket_hours"] == 24
    chat_ids = [c["chat_id"] for c in body["chats"]]
    assert "chat-A" in chat_ids
    assert "chat-B" in chat_ids
    a_entry = next(c for c in body["chats"] if c["chat_id"] == "chat-A")
    assert a_entry["chat_title"] == "Chat A Title"
    b_entry = next(c for c in body["chats"] if c["chat_id"] == "chat-B")
    # без title fallback на chat_id
    assert b_entry["chat_title"] == "chat-B"


def test_memory_heatmap_clamps_bucket_hours(monkeypatch, tmp_path) -> None:
    """bucket_hours clamped в [1, 8760]."""
    import sqlite3

    db_file = tmp_path / "archive.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE messages (chat_id TEXT, timestamp TEXT)")
    conn.execute("CREATE TABLE chats (chat_id TEXT, title TEXT)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(Path, "expanduser", lambda self: db_file)
    resp = _client().get("/api/memory/heatmap?bucket_hours=99999")
    assert resp.status_code == 200
    assert resp.json()["bucket_hours"] == 8760

    resp2 = _client().get("/api/memory/heatmap?bucket_hours=0")
    assert resp2.status_code == 200
    assert resp2.json()["bucket_hours"] == 1
