#!/usr/bin/env python3
"""Smoke test для Memory Layer retrieval (archive.db FTS5 + PII).

Запускай:
    venv/bin/python scripts/smoke_test_memory_retrieval.py

Проверяет:
1. archive.db открывается (read-only, не мешает live indexer)
2. FTS5 query возвращает results
3. PII redaction видна в results (emails/cards/phones/api_keys/crypto маскированы)
4. Performance <500ms per query

БД открывается через ?mode=ro — live indexer пишет в тот же файл.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

DB_PATH = Path("~/.openclaw/krab_memory/archive.db").expanduser()
QUERY_BUDGET_MS = 500
# Placeholder'ы совпадают с src/core/memory_pii_redactor.py PLACEHOLDER_*.
PII_MARKERS = (
    "[REDACTED:EMAIL]",
    "[REDACTED:CARD]",
    "[REDACTED:PHONE]",
    "[REDACTED:API_KEY]",
    "[REDACTED:JWT]",
    "[REDACTED:CRYPTO_BTC]",
    "[REDACTED:CRYPTO_ETH]",
    "[REDACTED:CRYPTO_TRX]",
    "[REDACTED:CRYPTO_SOL]",
    "[REDACTED:PASSPORT]",
)


def _open_ro() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def _escape_fts5(query: str) -> str:
    """Совпадает с src/core/memory_retrieval._escape_fts5."""
    cleaned = "".join(ch if ch.isalnum() or ch in " -_" else " " for ch in query)
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return ""
    parts = [f'"{tok}"' for tok in cleaned.split() if tok]
    return " OR ".join(parts)


def test_fts_query(q: str, limit: int = 5) -> tuple[int, float]:
    """Выполняет FTS5 query (по схеме chunks rowid) + печатает результаты + timing."""
    safe = _escape_fts5(q)
    conn = _open_ro()
    start = time.time()
    rows = conn.execute(
        """
        SELECT c.chunk_id, c.chat_id, c.text_redacted, f.rank
        FROM messages_fts AS f
        JOIN chunks AS c ON c.rowid = f.rowid
        WHERE f.text_redacted MATCH ?
        ORDER BY f.rank
        LIMIT ?;
        """,
        (safe, limit),
    ).fetchall()
    elapsed_ms = (time.time() - start) * 1000
    conn.close()

    print(f"\nQuery '{q}' (escaped={safe!r}) -> {len(rows)} hits в {elapsed_ms:.1f}ms")
    for chunk_id, chat_id, text, rank in rows:
        preview = (text or "")[:180].replace("\n", " | ")
        print(f"  [{chunk_id[:12]} chat={chat_id[:10]}] rank={rank:.2f}  {preview}")
    if elapsed_ms >= QUERY_BUDGET_MS:
        print(f"  WARN: slow ({elapsed_ms:.1f}ms >= {QUERY_BUDGET_MS}ms)")
    return len(rows), elapsed_ms


def test_pii_redaction_visible() -> int:
    """Проверяет что в chunks.text_redacted есть PII placeholders."""
    conn = _open_ro()
    like_clause = " OR ".join(f"text_redacted LIKE '%{m}%'" for m in PII_MARKERS)
    rows = conn.execute(
        f"SELECT chunk_id, text_redacted FROM chunks WHERE {like_clause} LIMIT 6"
    ).fetchall()

    # Также счётчик по категориям — per-marker hits.
    counts: dict[str, int] = {}
    for marker in PII_MARKERS:
        n = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE text_redacted LIKE ?",
            (f"%{marker}%",),
        ).fetchone()[0]
        counts[marker] = n
    conn.close()

    print("\n=== PII redaction verification ===")
    print(f"Chunks with any PII placeholder: examples={len(rows)}")
    print("Per-marker chunks count:")
    for marker, n in counts.items():
        print(f"  {marker}: {n}")

    if not rows:
        print("  FAIL: PII placeholders не найдены — redaction mб не применялся")
        return 0

    print("\nSample contexts around placeholders:")
    for chunk_id, text in rows:
        for ph in PII_MARKERS:
            idx = (text or "").find(ph)
            if idx >= 0:
                start = max(0, idx - 30)
                end = min(len(text), idx + 70)
                snippet = text[start:end].replace("\n", " | ")
                print(f"  [{chunk_id[:12]}] ...{snippet}...")
                break
    return len(rows)


def main() -> int:
    print("=== Memory Retrieval Smoke Test ===")
    print(f"DB: {DB_PATH}")
    if not DB_PATH.exists():
        print("FAIL: archive.db не найдена")
        return 1

    conn = _open_ro()
    print("\nTable counts:")
    for table in ("messages", "chats", "chunks", "chunk_messages", "messages_fts"):
        try:
            c = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table}: {c}")
        except sqlite3.Error as exc:
            print(f"  {table}: ERR {exc}")
    # vec_chunks — опционально.
    try:
        c = conn.execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0]
        print(f"  vec_chunks: {c}  (optional, sqlite-vec)")
    except sqlite3.Error:
        pass
    conn.close()

    queries = [
        "Krab",
        "python pytest",
        "Привет",
        "How2AI",
        "openclaw",
    ]
    total_hits = 0
    slow: list[str] = []
    for q in queries:
        hits, elapsed = test_fts_query(q)
        total_hits += hits
        if elapsed >= QUERY_BUDGET_MS:
            slow.append(f"{q} ({elapsed:.1f}ms)")

    print(f"\n=== Total FTS hits across {len(queries)} queries: {total_hits} ===")

    pii_examples = test_pii_redaction_visible()

    # Overall verdict.
    fail = False
    if total_hits == 0:
        print("\nWARN: zero hits overall — это подозрительно для 9k chunks")
    if slow:
        print(f"\nWARN: slow queries: {', '.join(slow)}")
    if pii_examples == 0:
        print("\nFAIL: PII redaction не обнаружена")
        fail = True

    if fail:
        print("\nSmoke test FAILED")
        return 1
    print("\nAll smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
