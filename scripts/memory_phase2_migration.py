"""Phase 2 migration: добавляет колонку ``embedding BLOB`` в chunks table.

Идемпотентно: если колонка уже существует — no-op.

НЕ запускать автоматически в production. Запуск вручную::

    venv/bin/python scripts/memory_phase2_migration.py

Цель колонки — опциональное denormalized хранилище embedding прямо в
``chunks`` (в дополнение к ``vec_chunks`` sqlite-vec-таблице). Это
упрощает миграции/бэкапы: одна строка chunks несёт и текст, и его вектор.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("~/.openclaw/krab_memory/archive.db").expanduser()


def migrate(db_path: Path = DB_PATH) -> int:
    """Добавить колонку embedding BLOB, если её ещё нет. Возвращает exit-code."""
    if not db_path.exists():
        print(f"archive.db not found: {db_path}")
        return 1

    conn = sqlite3.connect(str(db_path))
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(chunks)")]
        if "embedding" in cols:
            print("embedding column already exists (idempotent, no-op)")
            return 0

        conn.execute("ALTER TABLE chunks ADD COLUMN embedding BLOB")
        conn.commit()
        print("Added embedding column to chunks table")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(migrate())
