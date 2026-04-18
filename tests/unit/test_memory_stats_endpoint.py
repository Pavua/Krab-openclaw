"""Тесты для src.core.memory_stats.collect_memory_stats."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.core.memory_stats import collect_memory_stats


def _make_db(path: Path, *, with_vec: bool = True) -> None:
    """Создаёт минимальную schema archive.db с тестовыми данными."""

    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE chats (
                chat_id TEXT PRIMARY KEY,
                title TEXT,
                chat_type TEXT,
                last_indexed_at TEXT,
                message_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE messages (
                message_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                sender_id TEXT,
                timestamp TEXT NOT NULL,
                text_redacted TEXT NOT NULL,
                reply_to_id TEXT,
                PRIMARY KEY (chat_id, message_id)
            );
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chunk_id TEXT NOT NULL UNIQUE,
                chat_id TEXT NOT NULL,
                start_ts TEXT NOT NULL,
                end_ts TEXT NOT NULL,
                message_count INTEGER NOT NULL,
                char_len INTEGER NOT NULL,
                text_redacted TEXT NOT NULL
            );
            """
        )
        if with_vec:
            conn.execute(
                "CREATE TABLE vec_chunks_rowids (rowid INTEGER PRIMARY KEY, id, chunk_id INTEGER, chunk_offset INTEGER)"
            )

        # Populate: 3 chats с разным количеством сообщений
        chats = [("c1", 5), ("c2", 3), ("c3", 1)]
        for chat_id, cnt in chats:
            conn.execute(
                "INSERT INTO chats(chat_id, message_count) VALUES(?, ?)", (chat_id, cnt)
            )
            for i in range(cnt):
                ts = f"2026-04-{10 + i:02d}T12:00:00Z"
                conn.execute(
                    "INSERT INTO messages(message_id, chat_id, timestamp, text_redacted) "
                    "VALUES(?, ?, ?, ?)",
                    (f"{chat_id}-{i}", chat_id, ts, "hello"),
                )

        # 4 chunks, из них 2 encoded
        for i in range(4):
            conn.execute(
                "INSERT INTO chunks(chunk_id, chat_id, start_ts, end_ts, message_count, char_len, text_redacted) "
                "VALUES(?, 'c1', '2026-04-10T00:00:00Z', '2026-04-10T01:00:00Z', 1, 5, 'hello')",
                (f"chunk-{i}",),
            )
        if with_vec:
            for i in range(2):
                conn.execute(
                    "INSERT INTO vec_chunks_rowids(rowid, id, chunk_id, chunk_offset) VALUES(?, ?, ?, ?)",
                    (i + 1, i + 1, 1, i),
                )
        conn.commit()
    finally:
        conn.close()


def test_memory_stats_missing_db(tmp_path: Path) -> None:
    result = collect_memory_stats(tmp_path / "does_not_exist.db")
    assert result["exists"] is False
    assert "error" in result
    assert "archive.db" in result["error"]


def test_memory_stats_basic(tmp_path: Path) -> None:
    db = tmp_path / "archive.db"
    _make_db(db)

    result = collect_memory_stats(db)
    assert result["exists"] is True
    assert result["total_messages"] == 9  # 5 + 3 + 1
    assert result["total_chunks"] == 4
    assert result["encoded_chunks"] == 2
    assert result["encoding_coverage_pct"] == 50.0
    assert result["db_size_bytes"] > 0
    assert isinstance(result["db_size_mb"], float)
    assert result["oldest_message_ts"] == "2026-04-10T12:00:00Z"
    assert result["newest_message_ts"] is not None


def test_memory_stats_top_chats_sorted_desc(tmp_path: Path) -> None:
    db = tmp_path / "archive.db"
    _make_db(db)

    result = collect_memory_stats(db)
    chats = result["top_chats"]
    assert len(chats) == 3
    assert chats[0]["chat_id"] == "c1"
    assert chats[0]["count"] == 5
    assert chats[1]["chat_id"] == "c2"
    assert chats[2]["chat_id"] == "c3"
    # descending order
    counts = [c["count"] for c in chats]
    assert counts == sorted(counts, reverse=True)


def test_memory_stats_no_vec_table(tmp_path: Path) -> None:
    """Если sqlite-vec не инициализирован, encoded_chunks=0, coverage=0."""

    db = tmp_path / "archive.db"
    _make_db(db, with_vec=False)

    result = collect_memory_stats(db)
    assert result["total_chunks"] == 4
    assert result["encoded_chunks"] == 0
    assert result["encoding_coverage_pct"] == 0.0


def test_memory_stats_empty_db(tmp_path: Path) -> None:
    """DB существует, но таблицы пусты."""

    db = tmp_path / "archive.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "CREATE TABLE messages(message_id TEXT, chat_id TEXT, timestamp TEXT, text_redacted TEXT)"
        )
        conn.execute("CREATE TABLE chunks(chunk_id TEXT)")
        conn.commit()
    finally:
        conn.close()

    result = collect_memory_stats(db)
    assert result["exists"] is True
    assert result["total_messages"] == 0
    assert result["total_chunks"] == 0
    assert result["encoded_chunks"] == 0
    assert result["encoding_coverage_pct"] == 0.0
    assert result["top_chats"] == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
