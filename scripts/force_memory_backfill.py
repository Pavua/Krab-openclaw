"""Kick the indexer to pick up all unencoded chunks.

Запрос к archive.db находит chunks без embedding в vec_chunks (через LEFT JOIN).
Затем enqueue batch в MemoryEmbedder.embed_all_unindexed() или,
если указан --via-queue, раскидывает chunk_id через memory_indexer singleton.

Runs:
  1. Query DB для чанков без embedding в vec_chunks
  2. Enqueue batch в memory_indexer_worker queue (или embed_all_unindexed напрямую)
  3. Report progress

Usage:
    venv/bin/python scripts/force_memory_backfill.py [--batch 1000] [--dry-run]
    venv/bin/python scripts/force_memory_backfill.py --batch 500 --dry-run
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

# Корень проекта — два уровня вверх от scripts/
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.core.memory_archive import ArchivePaths, open_archive  # noqa: E402

try:
    from src.core.memory_embedder import MemoryEmbedder  # noqa: E402
except ImportError:
    MemoryEmbedder = None  # type: ignore[assignment,misc]


def _count_unencoded(conn: sqlite3.Connection) -> int:
    """Количество chunks без вектора в vec_chunks."""
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM chunks AS c
            LEFT JOIN vec_chunks AS v ON v.rowid = c.id
            WHERE v.rowid IS NULL;
            """
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        # vec_chunks ещё не создан — все chunks неиндексированы
        return conn.execute("SELECT COUNT(*) FROM chunks;").fetchone()[0]


def _fetch_unencoded_chunk_ids(conn: sqlite3.Connection, batch: int) -> list[str]:
    """Fetch chunk_id для неиндексированных chunks (лимит batch)."""
    try:
        rows = conn.execute(
            """
            SELECT c.chunk_id
            FROM chunks AS c
            LEFT JOIN vec_chunks AS v ON v.rowid = c.id
            WHERE v.rowid IS NULL
            ORDER BY c.id
            LIMIT ?;
            """,
            (batch,),
        ).fetchall()
    except sqlite3.OperationalError:
        # vec_chunks не существует — берём все chunks
        rows = conn.execute(
            "SELECT chunk_id FROM chunks ORDER BY id LIMIT ?;",
            (batch,),
        ).fetchall()
    return [r[0] for r in rows]


def run_backfill(
    batch: int = 1000,
    dry_run: bool = False,
    archive_paths: ArchivePaths | None = None,
) -> dict:
    """
    Основная логика backfill.

    Возвращает словарь с результатами:
        unencoded_total: int  — число chunks без embedding до запуска
        batch_limit: int      — запрошенный размер batch
        queued: int           — сколько chunk_id передано embedder'у (0 при dry_run)
        dry_run: bool
        elapsed_sec: float
        error: str | None
    """
    paths = archive_paths or ArchivePaths.default()
    t0 = time.perf_counter()

    try:
        conn = open_archive(paths, create_if_missing=False)  # type: ignore[call-arg]
    except (FileNotFoundError, Exception) as exc:
        return {
            "unencoded_total": 0,
            "batch_limit": batch,
            "queued": 0,
            "dry_run": dry_run,
            "elapsed_sec": round(time.perf_counter() - t0, 3),
            "error": f"cannot open archive: {exc}",
        }

    try:
        unencoded_total = _count_unencoded(conn)

        if dry_run:
            conn.close()
            return {
                "unencoded_total": unencoded_total,
                "batch_limit": batch,
                "queued": 0,
                "dry_run": True,
                "elapsed_sec": round(time.perf_counter() - t0, 3),
                "error": None,
            }

        chunk_ids = _fetch_unencoded_chunk_ids(conn, batch)
        conn.close()

        if not chunk_ids:
            return {
                "unencoded_total": 0,
                "batch_limit": batch,
                "queued": 0,
                "dry_run": False,
                "elapsed_sec": round(time.perf_counter() - t0, 3),
                "error": None,
            }

        # Используем MemoryEmbedder.embed_specific() для enqueue batch
        # (не вызывает encode — только находит chunks; реальный embed внутри)
        # embed_specific сам DELETE старые + INSERT новые векторы.
        if MemoryEmbedder is None:
            return {
                "unencoded_total": unencoded_total,
                "batch_limit": batch,
                "queued": 0,
                "dry_run": False,
                "elapsed_sec": round(time.perf_counter() - t0, 3),
                "error": "MemoryEmbedder unavailable (model2vec/sqlite-vec not installed)",
            }

        embedder = MemoryEmbedder(archive_paths=paths)
        stats = embedder.embed_specific(chunk_ids)
        embedder.close()

        return {
            "unencoded_total": unencoded_total,
            "batch_limit": batch,
            "queued": len(chunk_ids),
            "embedded": stats.chunks_processed,
            "dry_run": False,
            "elapsed_sec": round(time.perf_counter() - t0, 3),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        try:
            conn.close()
        except Exception:
            pass
        return {
            "unencoded_total": 0,
            "batch_limit": batch,
            "queued": 0,
            "dry_run": dry_run,
            "elapsed_sec": round(time.perf_counter() - t0, 3),
            "error": str(exc),
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Force-backfill memory embeddings for unencoded chunks."
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=1000,
        help="Сколько chunk_id обработать за один запуск (default: 1000)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только подсчитать неиндексированные chunks, не запускать embed",
    )
    args = parser.parse_args()

    result = run_backfill(batch=args.batch, dry_run=args.dry_run)

    if result.get("error"):
        print(f"[ERROR] {result['error']}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print(f"[DRY-RUN] Unencoded chunks: {result['unencoded_total']}")
        print(f"          Would process up to: {result['batch_limit']}")
    else:
        print(f"[OK] Unencoded before: {result['unencoded_total']}")
        print(f"     Chunk IDs sent to embedder: {result['queued']}")
        print(f"     Embedded: {result.get('embedded', '?')}")
        print(f"     Elapsed: {result['elapsed_sec']}s")


if __name__ == "__main__":
    main()
