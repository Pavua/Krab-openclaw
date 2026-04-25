#!/usr/bin/env python3
"""
memory_doctor.py — health check для archive.db (FTS5 + vec_chunks + chunks).

Запуск:
    venv/bin/python3 scripts/memory_doctor.py            # read-only diagnostic
    venv/bin/python3 scripts/memory_doctor.py --fix      # авто-починка orphans

Что проверяет:
1. **chunks ↔ vec_chunks alignment** — каждый chunk.id должен иметь
   соответствующую vec_chunks.rowid строку (vector embedding).
2. **chunk_messages ↔ messages alignment** — все message_id в chunk_messages
   должны существовать в messages.
3. **chunk_messages ↔ chunks alignment** — все chunk_id в chunk_messages
   должны существовать в chunks.
4. **indexer_state coverage** — для каждого chat_id в indexer_state
   должны быть chunks (sanity check).
5. **vec0 config** — vec_chunks_meta содержит indexed_at + model_dim +
   model_name (это **внутренние** поля vec0, НЕ метаданные чанков!).
6. **FTS5 sync** — messages_fts.rowid coverage относительно chunks.id
   (FTS5 indexes chunks.text_redacted via content='chunks', НЕ messages).

При --fix:
- Удаляет orphan chunk_messages rows (chunk_id или message_id не существует).
- Удаляет chunks без vector embedding (после warning prompt).
- НЕ трогает vec_chunks_meta (vec0 internal).

Note (2026-04-25 finding):
    "vec_chunks_meta desync" в Session 13 backlog — **misdiagnosis**.
    vec_chunks_meta это (key, value) config таблица созданная vec0,
    НЕ chunk metadata. Реальный desync check — chunks.id vs vec_chunks.rowid.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import sqlite_vec  # type: ignore[import-untyped]

DEFAULT_DB = Path.home() / ".openclaw" / "krab_memory" / "archive.db"


def connect(db_path: Path) -> sqlite3.Connection:
    """Открывает archive.db с подключённым vec0 расширением."""
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    return conn


def check_chunks_vec_alignment(c: sqlite3.Cursor) -> dict:
    """chunks.id vs vec_chunks.rowid — должны совпадать 1-к-1."""
    n_chunks = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    n_vec = c.execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0]
    chunks_no_vec = c.execute(
        "SELECT COUNT(*) FROM chunks WHERE id NOT IN (SELECT rowid FROM vec_chunks)"
    ).fetchone()[0]
    vec_orphans = c.execute(
        "SELECT COUNT(*) FROM vec_chunks WHERE rowid NOT IN (SELECT id FROM chunks)"
    ).fetchone()[0]
    return {
        "chunks_total": n_chunks,
        "vec_chunks_total": n_vec,
        "chunks_without_vec": chunks_no_vec,
        "vec_orphans": vec_orphans,
        "ok": chunks_no_vec == 0 and vec_orphans == 0,
    }


def check_chunk_messages(c: sqlite3.Cursor) -> dict:
    """chunk_messages → chunks + messages."""
    cm_total = c.execute("SELECT COUNT(*) FROM chunk_messages").fetchone()[0]
    orphan_chunk = c.execute(
        "SELECT COUNT(*) FROM chunk_messages cm "
        "WHERE cm.chunk_id NOT IN (SELECT chunk_id FROM chunks)"
    ).fetchone()[0]
    # messages has composite PK (message_id, chat_id) — match on both.
    orphan_msg = c.execute(
        "SELECT COUNT(*) FROM chunk_messages cm "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM messages m "
        "  WHERE m.message_id = cm.message_id AND m.chat_id = cm.chat_id"
        ")"
    ).fetchone()[0]
    return {
        "chunk_messages_total": cm_total,
        "orphan_chunk_id": orphan_chunk,
        "orphan_message_id": orphan_msg,
        "ok": orphan_chunk == 0 and orphan_msg == 0,
    }


def check_indexer_state(c: sqlite3.Cursor) -> dict:
    """indexer_state coverage — для каждого chat_id должны быть chunks."""
    rows = c.execute("SELECT chat_id, last_message_id FROM indexer_state").fetchall()
    chat_ids = {r[0] for r in rows}
    chats_with_chunks = {
        r[0] for r in c.execute("SELECT DISTINCT chat_id FROM chunks").fetchall()
    }
    missing = chat_ids - chats_with_chunks
    return {
        "indexer_state_chats": len(chat_ids),
        "chats_with_chunks": len(chats_with_chunks),
        "missing_chunks_for_chats": sorted(missing),
        "ok": not missing,
    }


def check_vec0_config(c: sqlite3.Cursor) -> dict:
    """vec_chunks_meta = vec0 internal config (key, value)."""
    rows = dict(c.execute("SELECT key, value FROM vec_chunks_meta").fetchall())
    expected = {"indexed_at", "model_dim", "model_name"}
    missing_keys = expected - set(rows)
    return {
        "config": rows,
        "missing_keys": sorted(missing_keys),
        "ok": not missing_keys,
    }


def check_fts(c: sqlite3.Cursor) -> dict:
    """messages_fts.rowid должен совпадать с chunks.id (FTS5 content='chunks').

    Note: FTS5 индексирует chunks.text_redacted, не индивидуальные messages
    (см. CREATE VIRTUAL TABLE messages_fts ... content='chunks').
    Поэтому корректная проверка — fts.rowid coverage относительно chunks.id.
    """
    n_chunks = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    n_fts = c.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
    # Chunks без FTS строки (rowid mismatch)
    fts_orphans = c.execute(
        "SELECT COUNT(*) FROM chunks "
        "WHERE id NOT IN (SELECT rowid FROM messages_fts)"
    ).fetchone()[0]
    delta = n_chunks - n_fts
    return {
        "chunks_total": n_chunks,
        "fts_total": n_fts,
        "chunks_without_fts": fts_orphans,
        "delta": delta,
        "ok": fts_orphans == 0 and abs(delta) < 100,
    }


def fix_chunk_message_orphans(c: sqlite3.Cursor) -> int:
    """Удаляет orphan rows. Возвращает кол-во удалённых."""
    deleted = 0
    n = c.execute(
        "DELETE FROM chunk_messages "
        "WHERE chunk_id NOT IN (SELECT chunk_id FROM chunks)"
    ).rowcount
    deleted += n or 0
    n = c.execute(
        "DELETE FROM chunk_messages "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM messages m "
        "  WHERE m.message_id = chunk_messages.message_id "
        "    AND m.chat_id = chunk_messages.chat_id"
        ")"
    ).rowcount
    deleted += n or 0
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--fix", action="store_true", help="Auto-fix orphans")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args()

    if not args.db.exists() or args.db.stat().st_size == 0:
        print(f"ERROR: {args.db} not found or empty", file=sys.stderr)
        return 2

    conn = connect(args.db)
    c = conn.cursor()

    results = {
        "db_path": str(args.db),
        "db_size_mb": round(args.db.stat().st_size / 1024 / 1024, 1),
        "chunks_vec_alignment": check_chunks_vec_alignment(c),
        "chunk_messages": check_chunk_messages(c),
        "indexer_state": check_indexer_state(c),
        "vec0_config": check_vec0_config(c),
        "fts": check_fts(c),
    }

    all_ok = all(
        v["ok"] for k, v in results.items() if isinstance(v, dict) and "ok" in v
    )

    if args.fix and not results["chunk_messages"]["ok"]:
        deleted = fix_chunk_message_orphans(c)
        conn.commit()
        results["fix_applied"] = {"deleted_chunk_messages": deleted}

    if args.json:
        import json
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(f"=== memory_doctor — {args.db}")
        print(f"Size: {results['db_size_mb']} MB")
        for section, data in results.items():
            if not isinstance(data, dict) or "ok" not in data:
                continue
            mark = "✅" if data["ok"] else "⚠️"
            print(f"\n{mark} {section}")
            for k, v in data.items():
                if k == "ok":
                    continue
                print(f"   {k}: {v}")
        print(f"\n{'✅ ALL CHECKS PASSED' if all_ok else '⚠️  ISSUES FOUND'}")

    conn.close()
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
