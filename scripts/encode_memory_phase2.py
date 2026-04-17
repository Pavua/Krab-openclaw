#!/usr/bin/env python3
"""
Phase 2 encoder: прогоняет все chunks из ``archive.db`` через Model2Vec
и сохраняет 256-dim векторы в ``vec_chunks`` (sqlite-vec virtual table).

Идемпотентен: повторный запуск индексирует только те chunks, для которых
ещё нет вектора (LEFT JOIN vec_chunks ON rowid = chunks.id).

Usage:
    venv/bin/python scripts/encode_memory_phase2.py [--limit N] [--dry-run] [--force]

Flags:
    --limit N    Ограничить число chunks в обработке (для measurement).
    --dry-run    Не писать в БД, только вывести сколько chunks на очереди.
    --force      Полностью пересоздать vec_chunks (DROP + CREATE + encode all).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.core.memory_embedder import (  # noqa: E402
    DEFAULT_BATCH_SIZE,
    DEFAULT_DIM,
    MemoryEmbedder,
    create_vec_table,
    serialize_f32,
)
from src.core.memory_embeddings import get_embedding_model  # noqa: E402

DB_PATH = Path("~/.openclaw/krab_memory/archive.db").expanduser()


def _load_vec_extension(conn: sqlite3.Connection) -> None:
    import sqlite_vec  # type: ignore[import-not-found]

    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)


def _count_pending(conn: sqlite3.Connection) -> tuple[int, int]:
    """Вернуть (total_chunks, pending_to_embed)."""
    total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    pending = conn.execute(
        """
        SELECT COUNT(*)
        FROM chunks AS c
        LEFT JOIN vec_chunks AS v ON v.rowid = c.id
        WHERE v.rowid IS NULL
        """
    ).fetchone()[0]
    return total, pending


def _encode_limited(limit: int, batch_size: int) -> tuple[int, float]:
    """
    Ручной encode с ограничением limit — для throughput-измерений.

    Возвращает (processed, elapsed_seconds).
    """
    conn = sqlite3.connect(DB_PATH)
    _load_vec_extension(conn)
    create_vec_table(conn, dim=DEFAULT_DIM)

    rows = conn.execute(
        """
        SELECT c.id, c.chunk_id, c.text_redacted
        FROM chunks AS c
        LEFT JOIN vec_chunks AS v ON v.rowid = c.id
        WHERE v.rowid IS NULL
        ORDER BY c.id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    if not rows:
        conn.close()
        return (0, 0.0)

    print(f"  loading Model2Vec model...")
    model = get_embedding_model()
    print(f"  model loaded — starting encode of {len(rows)} chunks...")

    t0 = time.perf_counter()
    processed = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        texts = [r[2] or "" for r in batch]
        vecs = model.encode(texts)
        payload = [(batch[i][0], serialize_f32(vecs[i])) for i in range(len(batch))]
        conn.executemany(
            "INSERT INTO vec_chunks(rowid, vector) VALUES (?, ?)", payload
        )
        conn.commit()
        processed += len(batch)
        elapsed = time.perf_counter() - t0
        rate = processed / elapsed if elapsed > 0 else 0.0
        print(f"  [{processed}/{len(rows)}] {rate:.1f} chunks/sec — {elapsed:.1f}s")

    total_elapsed = time.perf_counter() - t0
    conn.close()
    return (processed, total_elapsed)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="DROP vec_chunks + re-encode all chunks from scratch",
    )
    parser.add_argument(
        "--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="batch size for encode"
    )
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"[ERR] DB not found: {DB_PATH}")
        return 1

    # 1. Проверяем текущее состояние.
    conn = sqlite3.connect(DB_PATH)
    _load_vec_extension(conn)
    create_vec_table(conn, dim=DEFAULT_DIM)
    total, pending = _count_pending(conn)
    conn.close()

    print(f"Phase 2 encoder")
    print(f"  DB: {DB_PATH}")
    print(f"  chunks.total     = {total}")
    print(f"  pending to embed = {pending}")
    print(f"  batch_size       = {args.batch_size}")

    if args.dry_run:
        print("[dry-run] not writing anything.")
        return 0

    if pending == 0 and not args.force:
        print("[OK] nothing to do — all chunks already have vectors.")
        return 0

    # 2. Encode (limited or full).
    if args.limit is not None:
        print(f"\n[limit={args.limit}] measuring throughput...")
        processed, elapsed = _encode_limited(args.limit, args.batch_size)
        rate = processed / elapsed if elapsed > 0 else 0.0
        print(f"\n[OK] encoded {processed} chunks in {elapsed:.1f}s ({rate:.1f}/sec)")
        return 0

    # 3. Full run через MemoryEmbedder (он уже идемпотентный).
    print("\n[full] running MemoryEmbedder.embed_all_unindexed()...")
    t0 = time.perf_counter()
    embedder = MemoryEmbedder(batch_size=args.batch_size)
    try:
        if args.force:
            stats = embedder.rebuild_all()
        else:
            stats = embedder.embed_all_unindexed()
    finally:
        embedder.close()
    elapsed = time.perf_counter() - t0

    print(
        f"\n[OK] processed={stats.chunks_processed} "
        f"skipped={stats.chunks_skipped} "
        f"batches={stats.batches} "
        f"duration={stats.duration_sec:.1f}s "
        f"model_load={stats.model_load_sec:.1f}s "
        f"wall={elapsed:.1f}s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
