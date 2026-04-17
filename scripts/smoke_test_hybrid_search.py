#!/usr/bin/env python3
"""
Smoke test для hybrid re-ranker против реального archive.db.

Запуск:
    venv/bin/python scripts/smoke_test_hybrid_search.py [query ...]

Если query не передан — прогоняет несколько дефолтных запросов.
Измеряет время end-to-end и печатает top-10 результатов с rrf_score,
fts_rank, semantic_score, sources.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# Добавляем корень проекта в sys.path для импорта src.*
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.memory_hybrid_reranker import ARCHIVE_DB, hybrid_search  # noqa: E402


def run_query(query: str, limit: int = 10) -> None:
    print(f"\n--- query: {query!r} ---")
    t0 = time.perf_counter()
    results = hybrid_search(query, limit=limit)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(f"elapsed: {elapsed_ms:.1f} ms | hits: {len(results)}")
    for i, r in enumerate(results, start=1):
        preview = (r.text[:140] + "…") if len(r.text) > 140 else r.text
        print(
            f"  {i:2d}. rrf={r.rrf_score:.5f} "
            f"fts={r.fts_rank} sem={r.semantic_score} "
            f"src={r.sources} id={r.chunk_id}"
        )
        print(f"      {preview!r}")


def main() -> int:
    if not ARCHIVE_DB.exists():
        print(f"ARCHIVE_DB not found: {ARCHIVE_DB}", file=sys.stderr)
        return 1

    queries = sys.argv[1:] or [
        "dashboard",
        "swarm research",
        "translator voice",
        "hammerspoon",
    ]
    for q in queries:
        run_query(q)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
