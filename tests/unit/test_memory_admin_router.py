# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.memory_admin_router`` — Wave 184 (Session 48).

Покрытие сосредоточено на factory-pattern + sqlite-helpers + endpoints.
Используем tmp_path для создания fake archive.db, чтобы тесты не зависели
от реальной ~/.openclaw/krab_memory/.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers import memory_admin_router as mar
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.memory_admin_router import build_memory_admin_router

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_client(
    *,
    write_access_raises: Exception | None = None,
) -> TestClient:
    """Создаёт TestClient с подмененным write_access."""

    def _assert_write(*a: Any, **kw: Any) -> None:
        if write_access_raises is not None:
            raise write_access_raises

    ctx = RouterContext(
        deps={},
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=_assert_write,
    )
    app = FastAPI()
    app.include_router(build_memory_admin_router(ctx))
    return TestClient(app)


@pytest.fixture
def fake_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Создаёт fake archive.db с messages/chunks/vec_chunks/vec_chunks_meta/peers."""
    archive_dir = tmp_path / "krab_memory"
    archive_dir.mkdir()
    db_path = archive_dir / "archive.db"

    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, text TEXT)")
    conn.execute("INSERT INTO messages (text) VALUES ('hi'), ('bye'), ('echo')")
    conn.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, msg_id INTEGER)")
    conn.execute("INSERT INTO chunks (msg_id) VALUES (1), (2)")
    conn.execute("CREATE TABLE vec_chunks (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO vec_chunks DEFAULT VALUES")
    conn.execute("INSERT INTO vec_chunks DEFAULT VALUES")
    conn.execute("CREATE TABLE vec_chunks_meta (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO vec_chunks_meta DEFAULT VALUES")
    conn.execute("INSERT INTO vec_chunks_meta DEFAULT VALUES")
    conn.execute("CREATE TABLE peers (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO peers DEFAULT VALUES")
    conn.commit()
    conn.close()

    monkeypatch.setenv("KRAB_ARCHIVE_DB", str(db_path))
    return db_path


@pytest.fixture
def fake_archive_desync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fake archive с рассинхронизированным vec_chunks vs vec_chunks_meta."""
    db_path = tmp_path / "archive.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE vec_chunks (id INTEGER PRIMARY KEY)")
    for _ in range(5):
        conn.execute("INSERT INTO vec_chunks DEFAULT VALUES")
    conn.execute("CREATE TABLE vec_chunks_meta (id INTEGER PRIMARY KEY)")
    for _ in range(3):
        conn.execute("INSERT INTO vec_chunks_meta DEFAULT VALUES")
    conn.commit()
    conn.close()
    monkeypatch.setenv("KRAB_ARCHIVE_DB", str(db_path))
    return db_path


@pytest.fixture
def fake_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fake krab_main.log с retrieval_summary events."""
    log_path = tmp_path / "krab_main.log"
    lines = [
        '{"event": "other_event", "timestamp": "2026-05-13T10:00:00"}\n',
        '{"event": "memory_retrieval_summary", "timestamp": "2026-05-13T10:01:00", '
        '"mode": "hybrid", "total_ms": 42.5, "fts_hits": 8, "vec_hits": 7, '
        '"merged_hits": 10, "mmr_reranked": 5}\n',
        "2026-05-13T10:02:00 event=memory_retrieval_summary mode=fts total_ms=12.1 "
        "fts_hits=3 vec_hits=0 merged_hits=3 mmr_reranked=3\n",
        '{"event": "unrelated_event"}\n',
    ]
    log_path.write_text("".join(lines), encoding="utf-8")
    monkeypatch.setenv("KRAB_LOG_FILE", str(log_path))
    return log_path


# ---------------------------------------------------------------------------
# _archive_path / _archive_table_counts_sync
# ---------------------------------------------------------------------------


def test_archive_path_uses_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KRAB_ARCHIVE_DB", str(tmp_path / "custom.db"))
    assert mar._archive_path() == tmp_path / "custom.db"


def test_archive_path_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KRAB_ARCHIVE_DB", raising=False)
    assert mar._archive_path().name == "archive.db"


def test_table_counts_returns_rows(fake_archive: Path) -> None:
    res = mar._archive_table_counts_sync()
    assert res["ok"] is True
    assert res["exists"] is True
    assert res["size_bytes"] is not None and res["size_bytes"] > 0
    assert res["tables"]["messages"] == 3
    assert res["tables"]["chunks"] == 2
    assert res["tables"]["vec_chunks"] == 2
    assert res["featured"]["messages"] == 3
    assert res["featured"]["peers"] == 1


def test_table_counts_missing_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KRAB_ARCHIVE_DB", str(tmp_path / "doesnotexist.db"))
    res = mar._archive_table_counts_sync()
    assert res["ok"] is False
    assert res["exists"] is False
    assert res["error"] == "archive_db_not_found"


# ---------------------------------------------------------------------------
# vec_chunks_meta health
# ---------------------------------------------------------------------------


def test_vec_meta_health_in_sync(fake_archive: Path) -> None:
    res = mar._vec_chunks_meta_health_sync()
    assert res["vec_chunks_count"] == 2
    assert res["vec_chunks_meta_count"] == 2
    assert res["in_sync"] is True
    assert res["delta"] == 0


def test_vec_meta_health_desync(fake_archive_desync: Path) -> None:
    res = mar._vec_chunks_meta_health_sync()
    assert res["vec_chunks_count"] == 5
    assert res["vec_chunks_meta_count"] == 3
    assert res["in_sync"] is False
    assert res["delta"] == 2


def test_vec_meta_health_no_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KRAB_ARCHIVE_DB", str(tmp_path / "no.db"))
    res = mar._vec_chunks_meta_health_sync()
    assert res["ok"] is False


# ---------------------------------------------------------------------------
# encoder info
# ---------------------------------------------------------------------------


def test_encoder_info_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KRAB_RAG_ENCODER", raising=False)
    monkeypatch.delenv("KRAB_RAG_ENCODER_DIM", raising=False)
    info = mar._encoder_info()
    assert "all-MiniLM" in info["encoder_env"]
    assert info["embedding_dim"] == 384
    assert info["phase2_enabled"] in (True, False)


def test_encoder_info_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_RAG_ENCODER", "custom/model")
    monkeypatch.setenv("KRAB_RAG_ENCODER_DIM", "512")
    monkeypatch.setenv("KRAB_RAG_PHASE2_ENABLED", "1")
    info = mar._encoder_info()
    assert info["encoder_env"] == "custom/model"
    assert info["embedding_dim"] == 512
    assert info["phase2_enabled"] is True


# ---------------------------------------------------------------------------
# Recent events parsing
# ---------------------------------------------------------------------------


def test_parse_recent_finds_json_event(fake_log: Path) -> None:
    events = mar._parse_retrieval_events_sync(limit=50)
    assert len(events) >= 1
    json_event = next((e for e in events if e.get("mode") == "hybrid"), None)
    assert json_event is not None
    assert json_event.get("fts_hits") == 8


def test_parse_recent_finds_kv_event(fake_log: Path) -> None:
    events = mar._parse_retrieval_events_sync(limit=50)
    # key=value формат должен попасть тоже.
    modes = {str(e.get("mode")) for e in events}
    assert "fts" in modes or "hybrid" in modes


def test_parse_recent_skips_unrelated(fake_log: Path) -> None:
    events = mar._parse_retrieval_events_sync(limit=50)
    raws = " ".join(e.get("raw", "") for e in events)
    assert "unrelated_event" not in raws


def test_parse_recent_no_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KRAB_LOG_FILE", str(tmp_path / "missing.log"))
    events = mar._parse_retrieval_events_sync(limit=10)
    assert events == []


# ---------------------------------------------------------------------------
# /api/admin/memory/stats
# ---------------------------------------------------------------------------


def test_stats_endpoint(fake_archive: Path) -> None:
    client = _make_client()
    resp = client.get("/api/admin/memory/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["archive"]["exists"] is True
    assert body["archive"]["featured_counts"]["messages"] == 3
    assert body["encoder"]["embedding_dim"] >= 128
    assert "metrics" in body
    assert "vec_meta_health" in body


def test_stats_endpoint_no_archive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KRAB_ARCHIVE_DB", str(tmp_path / "missing.db"))
    client = _make_client()
    resp = client.get("/api/admin/memory/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["archive"]["exists"] is False


# ---------------------------------------------------------------------------
# /api/admin/memory/recent
# ---------------------------------------------------------------------------


def test_recent_endpoint(fake_log: Path) -> None:
    client = _make_client()
    resp = client.get("/api/admin/memory/recent?limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert isinstance(body["events"], list)
    assert body["count"] == len(body["events"])
    assert body["count"] >= 1


def test_recent_limit_validation() -> None:
    client = _make_client()
    resp = client.get("/api/admin/memory/recent?limit=99999")
    # Pydantic returns 422 on validation error.
    assert resp.status_code == 422


def test_recent_endpoint_no_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KRAB_LOG_FILE", str(tmp_path / "missing.log"))
    client = _make_client()
    resp = client.get("/api/admin/memory/recent")
    assert resp.status_code == 200
    assert resp.json()["events"] == []


# ---------------------------------------------------------------------------
# /api/admin/memory/search
# ---------------------------------------------------------------------------


def test_search_blocked_when_write_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    client = _make_client(write_access_raises=HTTPException(status_code=403, detail="forbidden"))
    resp = client.post("/api/admin/memory/search?query=test")
    assert resp.status_code == 403


def test_search_rejects_empty_query(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()
    # FastAPI Query with min_length=1 → 422 на пустую строку.
    resp = client.post("/api/admin/memory/search?query=")
    assert resp.status_code == 422


def test_search_rejects_bad_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()
    resp = client.post("/api/admin/memory/search?query=hi&chat_id=abc;DROP")
    assert resp.status_code == 400
    assert "invalid_chat_id" in resp.json()["detail"]


def test_search_succeeds_with_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Search должен вернуть валидную структуру даже когда retriever — stub."""

    def fake_search_archive(**kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "message_id": "msg-1",
                "chat_id": "123",
                "text_redacted": "hello world",
                "timestamp": None,
                "score": 0.42,
                "context_before": [],
                "context_after": [],
            }
        ]

    import src.core.memory_adapter as ma

    monkeypatch.setattr(ma, "search_archive", fake_search_archive)
    client = _make_client()
    resp = client.post("/api/admin/memory/search?query=hello&top_k=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["count"] == 1
    assert body["results"][0]["text_redacted"] == "hello world"


def test_search_handles_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**kwargs: Any) -> Any:
        raise RuntimeError("kaboom")

    import src.core.memory_adapter as ma

    monkeypatch.setattr(ma, "search_archive", boom)
    client = _make_client()
    resp = client.post("/api/admin/memory/search?query=hi")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "kaboom" in (body.get("error") or "")


# ---------------------------------------------------------------------------
# /admin/memory HTML
# ---------------------------------------------------------------------------


def test_admin_memory_page_returns_html() -> None:
    client = _make_client()
    resp = client.get("/admin/memory")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "Memory Admin" in resp.text


def test_admin_memory_page_has_polling_and_search() -> None:
    client = _make_client()
    resp = client.get("/admin/memory")
    assert "fetchStats" in resp.text
    assert "runSearch" in resp.text
    assert "setInterval" in resp.text


def test_admin_memory_page_renders_sections() -> None:
    client = _make_client()
    resp = client.get("/admin/memory")
    # Все 4 главные секции должны быть.
    assert "Архив (archive.db)" in resp.text
    assert "Retrieval metrics" in resp.text
    assert "Search archive" in resp.text
    assert "Recent retrievals" in resp.text


# ---------------------------------------------------------------------------
# Metric snapshot helper
# ---------------------------------------------------------------------------


def test_collect_metric_samples_none() -> None:
    assert mar._collect_metric_samples(None) == []


def test_retrieval_metrics_snapshot_keys() -> None:
    snap = mar._retrieval_metrics_snapshot()
    assert "mode_total" in snap
    assert "outcome_total" in snap
    assert "duration_summary" in snap


# ---------------------------------------------------------------------------
# JSON roundtrip — stats payload должен сериализоваться
# ---------------------------------------------------------------------------


def test_stats_payload_json_serializable(fake_archive: Path) -> None:
    client = _make_client()
    resp = client.get("/api/admin/memory/stats")
    # Должен JSON-парситься без ошибок.
    data = json.loads(resp.text)
    assert "archive" in data
    assert "metrics" in data
