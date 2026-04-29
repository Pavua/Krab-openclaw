"""Unit-тесты для Feature L: Memory Consolidation (scripts/memory_consolidate.py)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import memory_consolidate as mc
from src.core.memory_archive import create_schema


def _populate(conn: sqlite3.Connection, chat_id: str, *, days_old: int, count: int) -> None:
    """Засевает chat + N подряд идущих chunks с end_ts = now - days_old."""
    base = datetime.now(timezone.utc) - timedelta(days=days_old)
    conn.execute(
        "INSERT OR IGNORE INTO chats (chat_id, title, chat_type, message_count) VALUES (?,?,?,0);",
        (chat_id, f"chat-{chat_id}", "private"),
    )
    for i in range(count):
        ts = (base + timedelta(minutes=i * 10)).isoformat(timespec="seconds")
        end = (base + timedelta(minutes=i * 10 + 5)).isoformat(timespec="seconds")
        conn.execute(
            """
            INSERT INTO chunks
                (chunk_id, chat_id, start_ts, end_ts, message_count, char_len, text_redacted)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (f"{chat_id}-c{i}", chat_id, ts, end, 1, 20, f"text-{chat_id}-{i}"),
        )
    conn.commit()


@pytest.fixture
def archive_db(tmp_path: Path) -> Path:
    db = tmp_path / "archive.db"
    conn = sqlite3.connect(str(db))
    try:
        create_schema(conn)
    finally:
        conn.close()
    return db


def test_ensure_consolidation_columns_idempotent(archive_db: Path):
    """ALTER TABLE добавляет колонки один раз; повторный вызов не падает."""
    conn = sqlite3.connect(str(archive_db))
    try:
        mc.ensure_consolidation_columns(conn)
        mc.ensure_consolidation_columns(conn)  # должен пройти без ошибок
        cols = {row[1] for row in conn.execute("PRAGMA table_info(chunks);").fetchall()}
        assert "consolidated_into" in cols
        assert "retrieval_count" in cols
        assert "validator_confirmed_at" in cols
    finally:
        conn.close()


def test_find_candidates_groups_by_chat_and_proximity(archive_db: Path):
    """Старые подряд идущие chunks одного chat'а образуют группу."""
    conn = sqlite3.connect(str(archive_db))
    try:
        mc.ensure_consolidation_columns(conn)
        # chat A: 8 старых соседних (>= MIN_GROUP_SIZE=5).
        _populate(conn, "chatA", days_old=120, count=8)
        # chat B: 2 старых — мало, не должен попасть.
        _populate(conn, "chatB", days_old=120, count=2)
        # chat C: свежие — игнорировать.
        _populate(conn, "chatC", days_old=10, count=10)
        groups = mc.find_consolidation_candidates(conn, age_days=90)
        assert len(groups) == 1
        assert groups[0].chat_id == "chatA"
        assert groups[0].size == 8
    finally:
        conn.close()


def test_apply_consolidation_marks_originals(archive_db: Path):
    """apply_consolidation создаёт new chunk и помечает оригиналы consolidated_into."""
    conn = sqlite3.connect(str(archive_db))
    try:
        mc.ensure_consolidation_columns(conn)
        _populate(conn, "chatX", days_old=100, count=6)
        groups = mc.find_consolidation_candidates(conn, age_days=90)
        assert groups
        new_id = mc.apply_consolidation(conn, groups[0], "summary text")
        # Оригиналы помечены.
        marked = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE consolidated_into = ?;", (new_id,)
        ).fetchone()[0]
        assert marked == 6
        # New chunk существует и не помечен сам.
        new_row = conn.execute(
            "SELECT consolidated_into, text_redacted FROM chunks WHERE chunk_id = ?;",
            (new_id,),
        ).fetchone()
        assert new_row[0] is None
        assert new_row[1] == "summary text"
    finally:
        conn.close()


def test_run_dry_run_does_not_mutate(archive_db: Path):
    """Dry-run находит группы, но ничего не пишет в БД."""
    conn = sqlite3.connect(str(archive_db))
    try:
        mc.ensure_consolidation_columns(conn)
        _populate(conn, "chatY", days_old=200, count=7)
    finally:
        conn.close()

    out_lines: list[str] = []
    result = mc.run(archive_db, age_days=90, dry_run=True, output=out_lines.append)
    assert result["groups"] == 1
    assert result["applied"] == 0
    assert result["chunks_compressed"] == 7
    assert any("[dry]" in line for line in out_lines)

    # Verify no rows mutated.
    conn = sqlite3.connect(str(archive_db))
    try:
        marked = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE consolidated_into IS NOT NULL;"
        ).fetchone()[0]
        assert marked == 0
    finally:
        conn.close()
