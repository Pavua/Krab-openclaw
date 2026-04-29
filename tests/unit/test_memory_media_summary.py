"""Feature E — Multi-Modal Memory: тесты sidecar-таблицы media-summaries.

Покрытие:
  1. schema migrate — create_schema создаёт message_media_summaries + индексы
  2. save summary — record_media_summary UPSERT работает + перезаписывает
  3. retrieve includes summary — fetch_media_summaries_for_chunks возвращает
     summary для chunk'а, в котором есть media-сообщение
  4. missing graceful — fetch_*/record_* без таблицы не падают, возвращают
     {}/None/False
  5. backfill dry-run — find_candidates находит media-кандидатов и не пишет
     в БД при отсутствии --apply
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

# Доступ к scripts/ для теста backfill
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.core.memory_archive import (
    augment_chunk_text_with_media,
    create_schema,
    ensure_message_media_summaries_table,
    fetch_media_summaries_for_chunks,
    fetch_media_summary,
    list_tables,
    record_media_summary,
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    """Свежая in-memory БД с полной схемой."""
    c = sqlite3.connect(":memory:")
    create_schema(c)
    return c


@pytest.fixture
def empty_conn() -> sqlite3.Connection:
    """In-memory без схемы — для проверки graceful degradation."""
    return sqlite3.connect(":memory:")


def _seed_chunk_with_message(
    conn: sqlite3.Connection,
    chat_id: str = "100",
    msg_id: str = "5001",
    chunk_id: str = "chunk-A",
) -> None:
    """Минимальный seed: chat + message + chunk + chunk_messages."""
    conn.executescript(
        f"""
        INSERT INTO chats(chat_id, title, chat_type, message_count)
            VALUES ('{chat_id}', 'test', 'private', 1);
        INSERT INTO messages(message_id, chat_id, sender_id, timestamp, text_redacted)
            VALUES ('{msg_id}', '{chat_id}', 'u1', '2026-04-28T12:00:00Z', '[photo]');
        INSERT INTO chunks(chunk_id, chat_id, start_ts, end_ts, message_count, char_len, text_redacted)
            VALUES ('{chunk_id}', '{chat_id}', '2026-04-28T12:00:00Z', '2026-04-28T12:00:00Z', 1, 7, '[photo]');
        INSERT INTO chunk_messages(chunk_id, message_id, chat_id)
            VALUES ('{chunk_id}', '{msg_id}', '{chat_id}');
        """
    )
    conn.commit()


# 1. Schema migrate
def test_schema_creates_media_summaries_table(conn: sqlite3.Connection) -> None:
    tables = list_tables(conn)
    assert "message_media_summaries" in tables
    # Индексы на месте
    cur = conn.execute("PRAGMA index_list('message_media_summaries');")
    idx_names = {row[1] for row in cur.fetchall()}
    assert "idx_media_summaries_message" in idx_names
    assert "idx_media_summaries_type" in idx_names


# 2. Save summary (UPSERT + перезапись)
def test_record_media_summary_upsert(conn: sqlite3.Connection) -> None:
    ok = record_media_summary(
        conn, "100", "5001", "PHOTO", "котик на подоконнике", model_name="gemini-3-pro"
    )
    assert ok is True

    # media_type должен нормализоваться в lower-case
    fetched = fetch_media_summary(conn, "100", "5001")
    assert fetched == "котик на подоконнике"
    row = conn.execute(
        "SELECT media_type, model_name FROM message_media_summaries WHERE chat_id=? AND message_id=?",
        ("100", "5001"),
    ).fetchone()
    assert row[0] == "photo"
    assert row[1] == "gemini-3-pro"

    # UPSERT перезаписывает
    record_media_summary(conn, "100", "5001", "photo", "новое описание")
    assert fetch_media_summary(conn, "100", "5001") == "новое описание"

    # Пустой summary — отказ
    assert record_media_summary(conn, "100", "5002", "photo", "   ") is False


# 3. Retrieval: fetch_media_summaries_for_chunks возвращает media для chunk'а
def test_fetch_media_summaries_for_chunks(conn: sqlite3.Connection) -> None:
    _seed_chunk_with_message(conn, chat_id="100", msg_id="5001", chunk_id="chunk-A")
    record_media_summary(conn, "100", "5001", "photo", "кот на крыше")

    result = fetch_media_summaries_for_chunks(conn, ["chunk-A", "chunk-Missing"])
    assert "chunk-A" in result
    assert result["chunk-A"] == ["кот на крыше"]
    assert "chunk-Missing" not in result

    # augment_chunk_text_with_media приклеивает summary к тексту chunk'а
    augmented = augment_chunk_text_with_media("[photo]", result["chunk-A"])
    assert "[media] кот на крыше" in augmented
    assert "[photo]" in augmented


# 4. Graceful degradation без таблицы
def test_missing_table_graceful(empty_conn: sqlite3.Connection) -> None:
    # Без таблицы — все API-функции возвращают пустой/None/False вместо exception
    assert fetch_media_summary(empty_conn, "1", "1") is None
    assert fetch_media_summaries_for_chunks(empty_conn, ["x"]) == {}
    # record упадёт в sqlite.Error → False
    assert record_media_summary(empty_conn, "1", "1", "photo", "x") is False

    # ensure_* помогает после факта
    assert ensure_message_media_summaries_table(empty_conn) is True
    assert "message_media_summaries" in list_tables(empty_conn)
    # И теперь record работает
    assert record_media_summary(empty_conn, "1", "1", "photo", "x") is True

    # augment_chunk_text_with_media безопасен с None / []
    assert augment_chunk_text_with_media("text", None) == "text"
    assert augment_chunk_text_with_media("text", []) == "text"


# 5. Backfill dry-run
def test_backfill_finds_candidates_dry_run(tmp_path: Path) -> None:
    from scripts.memory_backfill_media import _detect_media_type, find_candidates

    db_path = tmp_path / "archive.db"
    conn = sqlite3.connect(db_path)
    create_schema(conn)
    # Seed: photo без summary + photo с summary + текстовое сообщение
    conn.executescript(
        """
        INSERT INTO chats(chat_id, title, chat_type, message_count)
            VALUES ('200', 'g', 'group', 3);
        INSERT INTO messages(message_id, chat_id, sender_id, timestamp, text_redacted) VALUES
            ('1', '200', 'u', '2026-04-28T10:00:00Z', '[photo] no caption'),
            ('2', '200', 'u', '2026-04-28T10:01:00Z', '[видео] no caption'),
            ('3', '200', 'u', '2026-04-28T10:02:00Z', 'просто текст');
        """
    )
    conn.commit()
    # Один photo уже описан — не должен попасть в кандидаты
    record_media_summary(conn, "200", "1", "photo", "уже описан")

    candidates = find_candidates(conn, limit=10)
    ids = {(c, m) for c, m, _ in candidates}
    assert ("200", "2") in ids  # видео без summary
    assert ("200", "1") not in ids  # уже описан
    assert ("200", "3") not in ids  # текст не media

    # Detect type
    assert _detect_media_type("[видео] hello") == "video"
    assert _detect_media_type("[photo] hello") == "photo"
    assert _detect_media_type("[animation] hello") == "animation"

    # Сам dry-run ничего не пишет — count summaries не растёт
    n_before = conn.execute("SELECT COUNT(*) FROM message_media_summaries").fetchone()[0]
    # find_candidates — read-only
    find_candidates(conn, limit=10)
    n_after = conn.execute("SELECT COUNT(*) FROM message_media_summaries").fetchone()[0]
    assert n_before == n_after

    conn.close()
