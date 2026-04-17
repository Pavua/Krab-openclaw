#!/usr/bin/env python3
"""
Smoke-тест семантического поиска через Model2Vec + sqlite-vec.

Выполняет KNN по ``vec_chunks`` для нескольких фраз и печатает top-K
chunks с cosine similarity. Используется как финальный verify после
``scripts/encode_memory_phase2.py``.

Usage:
    venv/bin/python scripts/smoke_test_memory_semantic.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.core.memory_embedder import serialize_f32  # noqa: E402
from src.core.memory_embeddings import encode_text, get_embedding_model  # noqa: E402

DB_PATH = Path("~/.openclaw/krab_memory/archive.db").expanduser()
TOP_K = 5


def _load_vec(conn: sqlite3.Connection) -> None:
    import sqlite_vec  # type: ignore[import-not-found]

    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)


def semantic_search(conn: sqlite3.Connection, query: str, top_k: int = TOP_K):
    """vec KNN поверх vec_chunks + JOIN в chunks для текста."""
    q_emb = encode_text(query)
    q_blob = serialize_f32(q_emb)
    rows = conn.execute(
        """
        SELECT v.rowid, v.distance, c.chunk_id, c.chat_id, c.text_redacted
        FROM vec_chunks AS v
        JOIN chunks AS c ON c.id = v.rowid
        WHERE v.vector MATCH ?
          AND k = ?
        ORDER BY v.distance
        """,
        (q_blob, top_k),
    ).fetchall()
    return rows


def main() -> int:
    if not DB_PATH.exists():
        print(f"[ERR] DB not found: {DB_PATH}")
        return 1

    print("loading Model2Vec...")
    get_embedding_model()
    print("model loaded.\n")

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    _load_vec(conn)

    queries = [
        "что делает Krab",
        "проактивность агентов",
        "Memory Layer Phase 2",
        "LM Studio local model",
        "голосовой переводчик",
    ]

    for q in queries:
        print(f"Query: {q}")
        hits = semantic_search(conn, q)
        if not hits:
            print("  (no results)")
            continue
        for rowid, dist, chunk_id, chat_id, text in hits:
            sim = 1.0 - float(dist)  # vec0 cosine distance ≈ 1 - similarity
            snippet = (text or "").replace("\n", " ")[:100]
            print(f"  [sim={sim:.3f}] rowid={rowid} chat={chat_id} :: {snippet}")
        print()

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
