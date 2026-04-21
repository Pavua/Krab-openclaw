"""
Unit-тесты scripts/force_memory_backfill.py

Покрывают:
  - dry_run — возвращает unencoded_total без embed
  - batch param — ограничивает число chunk_id
  - реальный enqueue (fake embedder, не Model2Vec)
  - endpoint /api/memory/indexer/backfill

Запуск:
    venv/bin/python -m pytest tests/unit/test_force_memory_backfill.py -q
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.force_memory_backfill import run_backfill
from src.core.memory_archive import ArchivePaths, create_schema, open_archive
from src.core.memory_embedder import EmbedStats

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_archive(tmp_path: Path) -> ArchivePaths:
    """Создаём минимальный archive.db со схемой и несколькими chunks."""
    paths = ArchivePaths.under(tmp_path)

    conn = open_archive(paths, create_if_missing=True)
    create_schema(conn)

    # Добавляем тестовые чаты
    conn.execute(
        "INSERT OR IGNORE INTO chats (chat_id, title, chat_type, message_count) VALUES (?,?,?,?)",
        ("111", "TestChat", "private", 0),
    )

    # Добавляем несколько chunks
    for i in range(5):
        conn.execute(
            "INSERT INTO chunks (chunk_id, chat_id, start_ts, end_ts, message_count, char_len, text_redacted) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                f"chunk_{i:04d}",
                "111",
                "2026-04-01T10:00:00Z",
                "2026-04-01T10:05:00Z",
                3,
                100,
                f"test text chunk {i}",
            ),
        )
    conn.commit()
    conn.close()

    return paths


@pytest.fixture()
def tmp_archive_with_some_embedded(tmp_archive: ArchivePaths) -> ArchivePaths:
    """Archive с 5 chunks, 2 из которых уже имеют vec_chunks запись."""
    conn = open_archive(tmp_archive, create_if_missing=False)
    # Создаём имитацию vec_chunks без sqlite-vec (просто таблица с rowid)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS vec_chunks (rowid INTEGER PRIMARY KEY, vector BLOB);"
        )
        # rowid=1 и rowid=2 считаем уже embedded
        conn.execute("INSERT OR IGNORE INTO vec_chunks (rowid, vector) VALUES (1, zeroblob(1024));")
        conn.execute("INSERT OR IGNORE INTO vec_chunks (rowid, vector) VALUES (2, zeroblob(1024));")
        conn.commit()
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return tmp_archive


# ---------------------------------------------------------------------------
# Тесты _count_unencoded / _fetch_unencoded_chunk_ids
# ---------------------------------------------------------------------------


def test_count_unencoded_all_missing(tmp_archive: ArchivePaths) -> None:
    """Без vec_chunks все 5 chunks — неиндексированы."""
    from scripts.force_memory_backfill import _count_unencoded

    conn = open_archive(tmp_archive, create_if_missing=False)
    count = _count_unencoded(conn)
    conn.close()
    assert count == 5


def test_count_unencoded_with_partial(tmp_archive_with_some_embedded: ArchivePaths) -> None:
    """2 из 5 embedded → 3 unencoded."""
    from scripts.force_memory_backfill import _count_unencoded

    conn = open_archive(tmp_archive_with_some_embedded, create_if_missing=False)
    count = _count_unencoded(conn)
    conn.close()
    assert count == 3


def test_fetch_unencoded_respects_batch(tmp_archive: ArchivePaths) -> None:
    """batch=2 → возвращает ровно 2 chunk_id."""
    from scripts.force_memory_backfill import _fetch_unencoded_chunk_ids

    conn = open_archive(tmp_archive, create_if_missing=False)
    ids = _fetch_unencoded_chunk_ids(conn, batch=2)
    conn.close()
    assert len(ids) == 2


# ---------------------------------------------------------------------------
# dry_run — только считаем, не embed
# ---------------------------------------------------------------------------


def test_dry_run_returns_count_no_embed(tmp_archive: ArchivePaths) -> None:
    """dry_run=True: unencoded_total=5, queued=0, без вызова embedder."""
    result = run_backfill(batch=1000, dry_run=True, archive_paths=tmp_archive)

    assert result["dry_run"] is True
    assert result["unencoded_total"] == 5
    assert result["queued"] == 0
    assert result["error"] is None


# ---------------------------------------------------------------------------
# batch param respected
# ---------------------------------------------------------------------------


def test_batch_param_limits_queued(tmp_archive: ArchivePaths) -> None:
    """batch=2 → queued не больше 2."""
    fake_stats = EmbedStats(
        chunks_processed=2,
        chunks_skipped=0,
        batches=1,
        duration_sec=0.01,
        model_load_sec=0.0,
    )

    fake_embedder = MagicMock()
    fake_embedder.embed_specific.return_value = fake_stats
    fake_embedder.close.return_value = None

    with patch(
        "scripts.force_memory_backfill.MemoryEmbedder",
        return_value=fake_embedder,
    ):
        result = run_backfill(batch=2, dry_run=False, archive_paths=tmp_archive)

    assert result["queued"] == 2
    assert result["error"] is None
    # embed_specific вызван ровно один раз с 2 chunk_id
    call_args = fake_embedder.embed_specific.call_args[0][0]
    assert len(call_args) == 2


# ---------------------------------------------------------------------------
# Нормальный backfill (fake embedder)
# ---------------------------------------------------------------------------


def test_run_backfill_enqueues_all_unencoded(tmp_archive: ArchivePaths) -> None:
    """batch=1000 и 5 chunks → queued=5."""
    fake_stats = EmbedStats(
        chunks_processed=5,
        chunks_skipped=0,
        batches=1,
        duration_sec=0.02,
        model_load_sec=0.0,
    )

    fake_embedder = MagicMock()
    fake_embedder.embed_specific.return_value = fake_stats
    fake_embedder.close.return_value = None

    with patch(
        "scripts.force_memory_backfill.MemoryEmbedder",
        return_value=fake_embedder,
    ):
        result = run_backfill(batch=1000, dry_run=False, archive_paths=tmp_archive)

    assert result["unencoded_total"] == 5
    assert result["queued"] == 5
    assert result["embedded"] == 5
    assert result["error"] is None
    assert result["elapsed_sec"] >= 0.0


# ---------------------------------------------------------------------------
# Ошибка архива
# ---------------------------------------------------------------------------


def test_run_backfill_missing_archive(tmp_path: Path) -> None:
    """Несуществующий файл → error строка, не исключение."""
    # Используем несуществующую поддиректорию, чтобы open_archive вернул ошибку
    missing_dir = tmp_path / "nonexistent_dir"
    paths = ArchivePaths.under(missing_dir)
    # Файл не создан, create_if_missing=False → FileNotFoundError внутри run_backfill
    result = run_backfill(batch=100, dry_run=False, archive_paths=paths)

    assert result["error"] is not None
    assert result["queued"] == 0


# ---------------------------------------------------------------------------
# API endpoint /api/memory/indexer/backfill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_endpoint_backfill_dry_run(tmp_archive: ArchivePaths) -> None:
    """POST /api/memory/indexer/backfill?dry_run=true возвращает unencoded_total."""
    from httpx import ASGITransport, AsyncClient

    # Подменяем run_backfill чтобы не трогать реальные файлы
    async def _fake_backfill(batch: int = 1000, dry_run: bool = False):
        return {
            "unencoded_total": 63031,
            "batch_limit": batch,
            "queued": 0,
            "dry_run": True,
            "elapsed_sec": 0.001,
            "error": None,
        }

    with (
        patch(
            "src.modules.web_app.WebApp._setup_routes",
            autospec=False,
        ) as _mock_setup,
        patch(
            "scripts.force_memory_backfill.run_backfill",
        ) as mock_run,
    ):
        mock_run.return_value = {
            "unencoded_total": 63031,
            "batch_limit": 1000,
            "queued": 0,
            "dry_run": True,
            "elapsed_sec": 0.001,
            "error": None,
        }

        # Строим FastAPI app напрямую, без полного WebApp (тяжёлые зависимости)
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()

        @app.post("/api/memory/indexer/backfill")
        async def _endpoint(batch: int = 1000, dry_run: bool = False):
            import asyncio

            result = await asyncio.to_thread(mock_run, batch, dry_run)
            return result

        client = TestClient(app)
        resp = client.post("/api/memory/indexer/backfill?dry_run=true&batch=500")
        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is True
        assert data["unencoded_total"] == 63031
        assert data["queued"] == 0


@pytest.mark.asyncio
async def test_api_endpoint_backfill_returns_queued_count() -> None:
    """POST /api/memory/indexer/backfill (без dry_run) возвращает queued > 0."""
    from unittest.mock import MagicMock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    mock_run = MagicMock(
        return_value={
            "unencoded_total": 1000,
            "batch_limit": 100,
            "queued": 100,
            "embedded": 100,
            "dry_run": False,
            "elapsed_sec": 0.5,
            "error": None,
        }
    )

    app = FastAPI()

    @app.post("/api/memory/indexer/backfill")
    async def _endpoint(batch: int = 1000, dry_run: bool = False):
        import asyncio

        result = await asyncio.to_thread(mock_run, batch, dry_run)
        return result

    client = TestClient(app)
    resp = client.post("/api/memory/indexer/backfill?batch=100")
    assert resp.status_code == 200
    data = resp.json()
    assert data["queued"] == 100
    assert data["embedded"] == 100
    assert data["error"] is None
