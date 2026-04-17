#!/usr/bin/env python3
"""
Phase 2 migration: гарантируем наличие ``vec_chunks`` sqlite-vec таблицы
в ``archive.db``.

Существующий ``memory_embedder.create_vec_table()`` создаёт таблицу через
``CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(...)``,
что идемпотентно. Этот скрипт просто вызывает его поверх уже
существующей БД и печатает итоговое состояние.

Usage:
    venv/bin/python scripts/memory_phase2_migration.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.core.memory_embedder import DEFAULT_DIM, create_vec_table  # noqa: E402

DB_PATH = Path("~/.openclaw/krab_memory/archive.db").expanduser()


def main() -> int:
    if not DB_PATH.exists():
        print(f"[ERR] DB not found: {DB_PATH}")
        return 1

    conn = sqlite3.connect(DB_PATH)
    try:
        create_vec_table(conn, dim=DEFAULT_DIM)
        total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

        # Перед SELECT vec_chunks нужно загрузить расширение (create_vec_table
        # его уже загрузил, но фиксируем явно на случай повторного open).
        import sqlite_vec  # type: ignore[import-not-found]

        conn.enable_load_extension(True)
        try:
            sqlite_vec.load(conn)
        finally:
            conn.enable_load_extension(False)

        existing = conn.execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0]
        print(f"[OK] vec_chunks table ready (dim={DEFAULT_DIM})")
        print(f"     chunks.total     = {total}")
        print(f"     vec_chunks.rows  = {existing}")
        print(f"     pending to embed = {total - existing}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
