"""Тесты forget_me.py — privacy scrub по user_id / chat_id из archive.db."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

# scripts/ не является пакетом — добавляем в sys.path динамически.
_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import forget_me  # noqa: E402

# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


def _make_archive(db_path: Path, *, with_vec: bool = True) -> None:
    """Создать минимальную тестовую БД, имитирующую archive.db."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    # chats — упрощённо
    conn.execute(
        "CREATE TABLE chats (chat_id TEXT PRIMARY KEY, kind TEXT, "
        "title TEXT, last_indexed_at TEXT, message_count INTEGER NOT NULL DEFAULT 0);"
    )
    conn.execute(
        "CREATE TABLE messages ("
        "message_id TEXT NOT NULL, chat_id TEXT NOT NULL, sender_id TEXT, "
        "timestamp TEXT NOT NULL, text_redacted TEXT NOT NULL, reply_to_id TEXT, "
        "PRIMARY KEY (chat_id, message_id), "
        "FOREIGN KEY (chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE);"
    )
    conn.execute(
        "CREATE TABLE chunks ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, chunk_id TEXT NOT NULL UNIQUE, "
        "chat_id TEXT NOT NULL, start_ts TEXT NOT NULL, end_ts TEXT NOT NULL, "
        "message_count INTEGER NOT NULL, char_len INTEGER NOT NULL, "
        "text_redacted TEXT NOT NULL);"
    )
    conn.execute(
        "CREATE TABLE chunk_messages ("
        "chunk_id TEXT NOT NULL, message_id TEXT NOT NULL, chat_id TEXT NOT NULL, "
        "PRIMARY KEY (chunk_id, message_id), "
        "FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id) ON DELETE CASCADE, "
        "FOREIGN KEY (chat_id, message_id) REFERENCES messages(chat_id, message_id) ON DELETE CASCADE);"
    )
    conn.execute(
        "CREATE TABLE message_media_summaries ("
        "chat_id TEXT NOT NULL, message_id TEXT NOT NULL, media_type TEXT NOT NULL, "
        "summary TEXT NOT NULL, model_name TEXT, generated_at TEXT NOT NULL, "
        "PRIMARY KEY (chat_id, message_id));"
    )
    conn.execute(
        "CREATE TABLE response_feedback ("
        "chat_id TEXT NOT NULL, message_id TEXT NOT NULL, "
        "positive_count INTEGER NOT NULL DEFAULT 0, "
        "negative_count INTEGER NOT NULL DEFAULT 0, "
        "last_updated_at TEXT NOT NULL, "
        "PRIMARY KEY (chat_id, message_id));"
    )
    if with_vec:
        # Эмулируем vec_chunks как обычную таблицу (sqlite-vec не нужен в тесте).
        conn.execute(
            "CREATE TABLE vec_chunks (rowid INTEGER PRIMARY KEY, embedding BLOB);"
        )

    # Тестовые данные:
    # chat A: пользователь 100 (3 msg) + пользователь 200 (1 msg)
    # chat B: пользователь 100 (2 msg)
    conn.execute("INSERT INTO chats VALUES ('A', 'private', 'A', NULL, 0);")
    conn.execute("INSERT INTO chats VALUES ('B', 'private', 'B', NULL, 0);")

    rows_msg = [
        ("m1", "A", "100", "2026-04-01T00:00:00Z", "hello A", None),
        ("m2", "A", "100", "2026-04-01T00:01:00Z", "hello A2", None),
        ("m3", "A", "100", "2026-04-01T00:02:00Z", "hello A3", None),
        ("m4", "A", "200", "2026-04-01T00:03:00Z", "from 200", None),
        ("m5", "B", "100", "2026-04-02T00:00:00Z", "hello B", None),
        ("m6", "B", "100", "2026-04-02T00:01:00Z", "hello B2", None),
    ]
    conn.executemany(
        "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?);", rows_msg
    )

    # Chunks: c1 содержит m1,m2 (chat A); c2 — m3,m4 (chat A); c3 — m5,m6 (chat B)
    chunks = [
        ("c1", "A", "2026-04-01T00:00:00Z", "2026-04-01T00:01:00Z", 2, 14, "hello A hello A2"),
        ("c2", "A", "2026-04-01T00:02:00Z", "2026-04-01T00:03:00Z", 2, 17, "hello A3 from 200"),
        ("c3", "B", "2026-04-02T00:00:00Z", "2026-04-02T00:01:00Z", 2, 14, "hello B hello B2"),
    ]
    conn.executemany(
        "INSERT INTO chunks (chunk_id, chat_id, start_ts, end_ts, message_count, char_len, text_redacted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?);",
        chunks,
    )
    chunk_msgs = [
        ("c1", "m1", "A"), ("c1", "m2", "A"),
        ("c2", "m3", "A"), ("c2", "m4", "A"),
        ("c3", "m5", "B"), ("c3", "m6", "B"),
    ]
    conn.executemany("INSERT INTO chunk_messages VALUES (?, ?, ?);", chunk_msgs)

    # media summaries: одна на m1
    conn.execute(
        "INSERT INTO message_media_summaries VALUES ('A', 'm1', 'photo', 'cat', 'gpt', '2026-04-01T00:00:00Z');"
    )

    # response_feedback в chat A и B
    conn.execute(
        "INSERT INTO response_feedback VALUES ('A', 'r1', 1, 0, '2026-04-01T00:00:00Z');"
    )
    conn.execute(
        "INSERT INTO response_feedback VALUES ('B', 'r2', 0, 1, '2026-04-02T00:00:00Z');"
    )

    if with_vec:
        # Векторы для всех 3 chunks (rowid == chunks.id)
        ids = [r[0] for r in conn.execute("SELECT id FROM chunks ORDER BY id;").fetchall()]
        conn.executemany(
            "INSERT INTO vec_chunks (rowid, embedding) VALUES (?, ?);",
            [(i, b"\x00" * 8) for i in ids],
        )

    conn.commit()
    conn.close()


@pytest.fixture
def archive_db(tmp_path: Path) -> Path:
    db = tmp_path / "archive.db"
    _make_archive(db)
    return db


@pytest.fixture
def audit_log(tmp_path: Path) -> Path:
    return tmp_path / "forget_me_audit.log"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_dry_run_does_not_mutate_db(archive_db: Path, audit_log: Path) -> None:
    """Dry-run (без --apply) не должен менять БД, только писать в audit."""
    rc = forget_me.main(
        [
            "--user-id", "100",
            "--db", str(archive_db),
            "--audit-log", str(audit_log),
        ]
    )
    assert rc == 0

    conn = sqlite3.connect(str(archive_db))
    try:
        msgs = conn.execute("SELECT COUNT(*) FROM messages;").fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM chunks;").fetchone()[0]
    finally:
        conn.close()

    assert msgs == 6, "messages не должны измениться при dry-run"
    assert chunks == 3, "chunks не должны измениться при dry-run"

    # Audit-log записан с dry_run=True
    lines = audit_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["dry_run"] is True
    assert payload["applied"] is False
    assert payload["target_kind"] == "user_id"
    assert payload["message_count"] == 5  # m1-m3, m5, m6


def test_user_id_scrub_removes_messages_and_chunks(
    archive_db: Path, audit_log: Path
) -> None:
    """--apply --user-id удаляет messages пользователя и связанные chunks."""
    rc = forget_me.main(
        [
            "--user-id", "100",
            "--db", str(archive_db),
            "--audit-log", str(audit_log),
            "--apply",
        ]
    )
    assert rc == 0

    conn = sqlite3.connect(str(archive_db))
    try:
        # У 200 одно сообщение (m4) — должно остаться.
        rows = conn.execute(
            "SELECT message_id, sender_id FROM messages ORDER BY message_id;"
        ).fetchall()
        assert rows == [("m4", "200")]

        # Любой chunk, содержавший хоть одно сообщение user 100, удаляется
        # целиком — privacy-correct: chunk.text_redacted склеен из всех
        # участвующих messages, surgical-removal по тексту невозможен.
        chunk_ids = {r[0] for r in conn.execute("SELECT chunk_id FROM chunks;").fetchall()}
        # c1 (m1,m2 user100), c2 (m3 user100, m4 user200), c3 (m5,m6 user100)
        # → все три удалены, потому что у каждого был хотя бы один user 100.
        assert chunk_ids == set(), "все chunks с участием user 100 должны исчезнуть"

        # chunk_messages — соответственно пусто (FK CASCADE от chunks).
        cm = conn.execute("SELECT COUNT(*) FROM chunk_messages;").fetchone()[0]
        assert cm == 0

        # media_summary на m1 ушёл (m1 удалён, осиротевшая summary удалена).
        ms = conn.execute(
            "SELECT chat_id, message_id FROM message_media_summaries;"
        ).fetchall()
        assert ms == []
    finally:
        conn.close()


def test_chat_id_scrub_removes_all_chat_data(
    archive_db: Path, audit_log: Path
) -> None:
    """--apply --chat-id удаляет всё содержимое чата."""
    rc = forget_me.main(
        [
            "--chat-id", "A",
            "--db", str(archive_db),
            "--audit-log", str(audit_log),
            "--apply",
        ]
    )
    assert rc == 0

    conn = sqlite3.connect(str(archive_db))
    try:
        msgs = conn.execute(
            "SELECT chat_id, message_id FROM messages ORDER BY chat_id, message_id;"
        ).fetchall()
        # Только chat B сообщения остались
        assert all(c == "B" for c, _ in msgs)
        assert len(msgs) == 2

        chunk_ids = {r[0] for r in conn.execute("SELECT chunk_id FROM chunks;").fetchall()}
        assert chunk_ids == {"c3"}

        ms = conn.execute(
            "SELECT chat_id FROM message_media_summaries;"
        ).fetchall()
        assert ms == []  # m1 был в chat A
    finally:
        conn.close()


def test_also_vec_chunks_optional(archive_db: Path, audit_log: Path) -> None:
    """Без --also-vec-chunks записи в vec_chunks остаются; с флагом — удаляются."""
    # Без флага
    rc = forget_me.main(
        [
            "--chat-id", "A",
            "--db", str(archive_db),
            "--audit-log", str(audit_log),
            "--apply",
        ]
    )
    assert rc == 0
    conn = sqlite3.connect(str(archive_db))
    try:
        # chunks A (c1, c2) удалены — остался только c3 (rowid соответственно).
        # vec_chunks не трогали — там должны остаться все 3 строки.
        vec_count = conn.execute("SELECT COUNT(*) FROM vec_chunks;").fetchone()[0]
        assert vec_count == 3, "без --also-vec-chunks vec_chunks не трогаем"
    finally:
        conn.close()

    # Пересоздадим БД и попробуем с флагом
    archive_db.unlink()
    _make_archive(archive_db)

    rc = forget_me.main(
        [
            "--chat-id", "A",
            "--db", str(archive_db),
            "--audit-log", str(audit_log),
            "--apply",
            "--also-vec-chunks",
        ]
    )
    assert rc == 0
    conn = sqlite3.connect(str(archive_db))
    try:
        vec_count = conn.execute("SELECT COUNT(*) FROM vec_chunks;").fetchone()[0]
        # c1 и c2 удалены из chunks → их rowids тоже выкинуты из vec_chunks.
        # Остался только c3 → 1 строка.
        assert vec_count == 1
    finally:
        conn.close()


def test_audit_log_appended_jsonl(archive_db: Path, audit_log: Path) -> None:
    """Audit-log пишется в JSONL append-only с правильными полями."""
    # dry-run
    forget_me.main(
        [
            "--chat-id", "A",
            "--db", str(archive_db),
            "--audit-log", str(audit_log),
        ]
    )
    # apply
    forget_me.main(
        [
            "--chat-id", "A",
            "--db", str(archive_db),
            "--audit-log", str(audit_log),
            "--apply",
            "--also-feedback",
        ]
    )

    lines = audit_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    second = json.loads(lines[1])

    assert first["dry_run"] is True
    assert first["applied"] is False
    assert first["target_kind"] == "chat_id"
    assert first["target_value"] == "A"
    assert first["message_count"] == 4  # m1-m4

    assert second["dry_run"] is False
    assert second["applied"] is True
    # response_feedback должен был учитываться при --also-feedback
    assert second["response_feedback_count"] == 1
    # После apply chat A сообщений уже нет — но второй вызов планировал
    # на свежей БД (после первого dry-run), так что message_count == 4.
    assert second["message_count"] == 4

    # Каждая строка — валидный JSON, есть timestamp.
    assert "timestamp" in first and "timestamp" in second
