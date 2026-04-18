"""Сбор статистики Memory Layer (archive.db) для Dashboard V4 / MCP tool."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def default_archive_db_path() -> Path:
    """Возвращает канонический путь до archive.db Memory Layer."""

    return Path.home() / ".openclaw" / "krab_memory" / "archive.db"


def collect_memory_stats(db_path: Path | None = None) -> dict[str, Any]:
    """Собирает статистику Memory Layer.

    Поля результата:
      - exists: bool — существует ли archive.db
      - total_messages, total_chunks, encoded_chunks
      - encoding_coverage_pct — процент закодированных chunks
      - db_size_bytes / db_size_mb
      - oldest_message_ts / newest_message_ts
      - top_chats — топ-10 по количеству сообщений
    """

    path = db_path if db_path is not None else default_archive_db_path()
    if not path.exists():
        return {"exists": False, "error": "archive.db не найдена", "path": str(path)}

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        stats: dict[str, Any] = {"exists": True, "path": str(path)}

        stats["total_messages"] = _count(conn, "messages")
        # В реальной схеме таблица называется `chunks`, но поддерживаем и legacy `memory_chunks`.
        total_chunks = _count(conn, "chunks")
        if total_chunks == 0:
            total_chunks = _count(conn, "memory_chunks")
        stats["total_chunks"] = total_chunks

        # Закодированные chunks: sqlite-vec держит их в vec_chunks_rowids; legacy — колонка embedding.
        encoded = _count(conn, "vec_chunks_rowids")
        if encoded == 0:
            encoded = _count_where(
                conn, "memory_chunks", "embedding IS NOT NULL"
            )
        stats["encoded_chunks"] = encoded

        size = path.stat().st_size
        stats["db_size_bytes"] = size
        stats["db_size_mb"] = round(size / 1024 / 1024, 2)

        try:
            row = conn.execute(
                "SELECT MIN(timestamp) AS oldest, MAX(timestamp) AS newest FROM messages"
            ).fetchone()
            stats["oldest_message_ts"] = row["oldest"] if row else None
            stats["newest_message_ts"] = row["newest"] if row else None
        except sqlite3.OperationalError:
            stats["oldest_message_ts"] = None
            stats["newest_message_ts"] = None

        try:
            rows = conn.execute(
                "SELECT chat_id, COUNT(*) AS cnt FROM messages "
                "GROUP BY chat_id ORDER BY cnt DESC LIMIT 10"
            ).fetchall()
            stats["top_chats"] = [
                {"chat_id": r["chat_id"], "count": r["cnt"]} for r in rows
            ]
        except sqlite3.OperationalError:
            stats["top_chats"] = []

        if stats["total_chunks"] > 0:
            stats["encoding_coverage_pct"] = round(
                100.0 * stats["encoded_chunks"] / stats["total_chunks"], 1
            )
        else:
            stats["encoding_coverage_pct"] = 0.0

        return stats
    finally:
        conn.close()


def _count(conn: sqlite3.Connection, table: str) -> int:
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


def _count_where(conn: sqlite3.Connection, table: str, where: str) -> int:
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0
