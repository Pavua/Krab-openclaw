# -*- coding: utf-8 -*-
"""
Вспомогательные функции для команды !reset — многослойная очистка истории.

Включает:
- Очистку archive.db (memory_archive): messages, chunks, chunk_messages, indexer_state.
- Опциональная очистка session jsonl-файлов OpenClaw (по chat_id → session_id mapping,
  если таковой появится). Сейчас OpenClaw сам ротирует sessions/*.jsonl, поэтому в
  !reset мы для OpenClaw-слоя используем openclaw_client.clear_session().
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .logger import get_logger

logger = get_logger(__name__)

# Путь к archive.db — тот же что использует memory_archive/memory_indexer_worker.
_ARCHIVE_DB_PATH = Path.home() / ".openclaw" / "krab_memory" / "archive.db"


def clear_archive_db_for_chat(chat_id: str, db_path: Path | None = None) -> int:
    """Удаляет все messages+chunks+indexer_state для chat_id из archive.db.

    Возвращает количество удалённых messages (до операции). Если база не
    существует — возвращает 0. При ошибке sqlite — логирует и возвращает 0.
    """
    path = Path(db_path) if db_path else _ARCHIVE_DB_PATH
    if not path.exists():
        logger.info("archive_db_not_found", path=str(path), chat_id=chat_id)
        return 0

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(path))
        # Включаем foreign keys, чтобы CASCADE отработал на chunk_messages/vec_chunks
        conn.execute("PRAGMA foreign_keys = ON")

        cur = conn.execute("SELECT COUNT(*) FROM messages WHERE chat_id = ?", (str(chat_id),))
        count = int(cur.fetchone()[0])

        # Порядок важен: сначала chunk_messages (FK на chunks и messages),
        # затем chunks (FK ссылается на chats), затем messages, затем indexer_state.
        conn.execute("DELETE FROM chunk_messages WHERE chat_id = ?", (str(chat_id),))
        conn.execute("DELETE FROM chunks WHERE chat_id = ?", (str(chat_id),))
        conn.execute("DELETE FROM messages WHERE chat_id = ?", (str(chat_id),))
        # indexer_state: сбрасываем прогресс индексатора для чата,
        # чтобы на следующем запуске начал с чистой доски.
        conn.execute("DELETE FROM indexer_state WHERE chat_id = ?", (str(chat_id),))
        # chats-запись оставляем: title/chat_type могут пригодиться.
        conn.commit()
        logger.info(
            "archive_db_cleared_for_chat",
            chat_id=chat_id,
            deleted_messages=count,
        )
        return count
    except sqlite3.Error as exc:
        logger.warning("archive_db_clear_failed", chat_id=chat_id, error=str(exc))
        return 0
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


def count_archive_messages_for_chat(chat_id: str, db_path: Path | None = None) -> int:
    """Возвращает количество messages в archive.db для chat_id.

    Используется для dry-run превью. Если БД нет — 0.
    """
    path = Path(db_path) if db_path else _ARCHIVE_DB_PATH
    if not path.exists():
        return 0
    try:
        with sqlite3.connect(str(path)) as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE chat_id = ?",
                (str(chat_id),),
            )
            return int(cur.fetchone()[0])
    except sqlite3.Error as exc:
        logger.warning("archive_db_count_failed", chat_id=chat_id, error=str(exc))
        return 0


def list_archive_chats(
    db_path: Path | None = None,
    limit: int = 20,
) -> list[dict]:
    """Возвращает список чатов из archive.db с количеством сообщений.

    Каждый элемент: {"chat_id": str, "title": str | None, "message_count": int}.
    Отсортировано по убыванию message_count. Если БД нет — пустой список.
    """
    path = Path(db_path) if db_path else _ARCHIVE_DB_PATH
    if not path.exists():
        return []
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            # Считаем messages per chat_id напрямую из таблицы messages,
            # join с chats за title (LEFT JOIN — чат может быть без записи в chats).
            rows = conn.execute(
                """
                SELECT m.chat_id,
                       c.title,
                       COUNT(*) AS message_count
                FROM messages m
                LEFT JOIN chats c ON c.chat_id = m.chat_id
                GROUP BY m.chat_id
                ORDER BY message_count DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("archive_db_list_chats_failed", error=str(exc))
        return []
    return [{"chat_id": r[0], "title": r[1], "message_count": r[2]} for r in rows]


def delete_archive_messages_before(
    cutoff_ts: int,
    db_path: Path | None = None,
) -> int:
    """Удаляет messages старше cutoff_ts (unix-timestamp) из archive.db.

    Также удаляет осиротевшие chunks (у которых не осталось chunk_messages).
    Возвращает количество удалённых messages. При ошибке — 0.
    """
    path = Path(db_path) if db_path else _ARCHIVE_DB_PATH
    if not path.exists():
        logger.info("archive_db_not_found_for_date_delete", path=str(path))
        return 0

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(path))
        conn.execute("PRAGMA foreign_keys = ON")

        cur = conn.execute("SELECT COUNT(*) FROM messages WHERE date < ?", (cutoff_ts,))
        count = int(cur.fetchone()[0])
        if count == 0:
            return 0

        # Удаляем chunk_messages, ссылающиеся на удаляемые messages.
        conn.execute(
            "DELETE FROM chunk_messages WHERE message_id IN "
            "(SELECT message_id FROM messages WHERE date < ?)",
            (cutoff_ts,),
        )
        # Удаляем chunks, у которых не осталось chunk_messages (осиротевшие).
        conn.execute(
            "DELETE FROM chunks WHERE chunk_id NOT IN "
            "(SELECT DISTINCT chunk_id FROM chunk_messages)"
        )
        # Удаляем сами messages.
        conn.execute("DELETE FROM messages WHERE date < ?", (cutoff_ts,))
        conn.commit()
        logger.info("archive_db_deleted_before_date", cutoff_ts=cutoff_ts, deleted=count)
        return count
    except sqlite3.Error as exc:
        logger.warning("archive_db_delete_before_failed", cutoff_ts=cutoff_ts, error=str(exc))
        return 0
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
