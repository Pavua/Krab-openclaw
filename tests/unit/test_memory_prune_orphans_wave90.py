"""Wave 90: тесты для scripts/krab_memory_prune_orphans.py."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Загружаем модуль из scripts/ напрямую (не пакетный).
_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "krab_memory_prune_orphans.py"
)
_spec = importlib.util.spec_from_file_location("krab_memory_prune_orphans", _SCRIPT)
assert _spec and _spec.loader
prune_mod = importlib.util.module_from_spec(_spec)
sys.modules["krab_memory_prune_orphans"] = prune_mod
_spec.loader.exec_module(prune_mod)


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


def _make_archive(db_path: Path) -> sqlite3.Connection:
    """Минимальная schema archive.db для тестов (без vec0/fts5)."""

    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE chats (
            chat_id TEXT PRIMARY KEY,
            title TEXT,
            chat_type TEXT,
            last_indexed_at TEXT,
            message_count INTEGER NOT NULL DEFAULT 0
        ) WITHOUT ROWID;
        CREATE TABLE messages (
            message_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            sender_id TEXT,
            timestamp TEXT NOT NULL,
            text_redacted TEXT NOT NULL,
            reply_to_id TEXT,
            PRIMARY KEY (chat_id, message_id),
            FOREIGN KEY (chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
        ) WITHOUT ROWID;
        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_id TEXT NOT NULL UNIQUE,
            chat_id TEXT NOT NULL,
            start_ts TEXT NOT NULL,
            end_ts TEXT NOT NULL,
            message_count INTEGER NOT NULL,
            char_len INTEGER NOT NULL,
            text_redacted TEXT NOT NULL,
            FOREIGN KEY (chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
        );
        """
    )
    return conn


def _seed(conn: sqlite3.Connection, chat_id: str, last_msg_iso: str, n_msgs: int = 3, n_chunks: int = 1) -> None:
    conn.execute(
        "INSERT INTO chats(chat_id, title, chat_type, last_indexed_at, message_count) VALUES (?, ?, ?, ?, ?)",
        (chat_id, f"chat_{chat_id}", "private", last_msg_iso, n_msgs),
    )
    for i in range(n_msgs):
        # Все msg timestamp одинаковы = last_msg_iso (для простоты).
        conn.execute(
            "INSERT INTO messages(message_id, chat_id, sender_id, timestamp, text_redacted) VALUES (?, ?, ?, ?, ?)",
            (f"m{i}", chat_id, "u1", last_msg_iso, f"text {i}"),
        )
    for j in range(n_chunks):
        conn.execute(
            "INSERT INTO chunks(chunk_id, chat_id, start_ts, end_ts, message_count, char_len, text_redacted) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"ck_{chat_id}_{j}", chat_id, last_msg_iso, last_msg_iso, n_msgs, 100, "concat"),
        )
    conn.commit()


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 5, 12, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def db_with_mix(tmp_path: Path, fixed_now: datetime) -> Path:
    db_path = tmp_path / "archive.db"
    conn = _make_archive(db_path)
    fresh = (fixed_now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stale = (fixed_now - timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _seed(conn, "100", fresh, n_msgs=5, n_chunks=2)
    _seed(conn, "200", stale, n_msgs=3, n_chunks=1)
    _seed(conn, "300", stale, n_msgs=2, n_chunks=1)
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------


def test_detect_orphan_chats_separates_fresh_from_stale(db_with_mix: Path, fixed_now: datetime) -> None:
    conn = sqlite3.connect(str(db_with_mix))
    try:
        orphans, accessible = prune_mod.detect_orphan_chats(
            conn, threshold_days=180, now_fn=lambda: fixed_now
        )
    finally:
        conn.close()
    assert set(orphans) == {"200", "300"}
    assert accessible == ["100"]


def test_detect_chat_without_messages_is_orphan(tmp_path: Path, fixed_now: datetime) -> None:
    db_path = tmp_path / "archive.db"
    conn = _make_archive(db_path)
    # Пустой чат — только запись в chats, ни одного сообщения.
    conn.execute(
        "INSERT INTO chats(chat_id, title, chat_type, last_indexed_at, message_count) VALUES (?, ?, ?, ?, ?)",
        ("999", "empty", "private", None, 0),
    )
    conn.commit()
    try:
        orphans, accessible = prune_mod.detect_orphan_chats(
            conn, threshold_days=180, now_fn=lambda: fixed_now
        )
    finally:
        conn.close()
    assert orphans == ["999"]
    assert accessible == []


def test_estimate_savings_counts_rows_and_mb(db_with_mix: Path) -> None:
    conn = sqlite3.connect(str(db_with_mix))
    try:
        msgs, chunks, mb = prune_mod.estimate_savings(conn, ["200", "300"])
    finally:
        conn.close()
    assert msgs == 5  # 3 + 2
    assert chunks == 2  # 1 + 1
    # 5*600 + 2*3072 = 3000 + 6144 = 9144 bytes ≈ 0.01 MB
    assert mb == pytest.approx(0.01, abs=0.01)


def test_estimate_savings_empty_list_returns_zeros(db_with_mix: Path) -> None:
    conn = sqlite3.connect(str(db_with_mix))
    try:
        assert prune_mod.estimate_savings(conn, []) == (0, 0, 0.0)
    finally:
        conn.close()


def test_make_backup_creates_copy(tmp_path: Path, fixed_now: datetime) -> None:
    db_path = tmp_path / "archive.db"
    db_path.write_bytes(b"sqlite-payload")
    backup = prune_mod.make_backup(db_path, now_fn=lambda: fixed_now)
    assert backup.exists()
    assert backup.read_bytes() == b"sqlite-payload"
    assert backup.name.startswith("archive.db.pre-prune-")


def test_apply_prune_deletes_orphan_data(db_with_mix: Path) -> None:
    conn = sqlite3.connect(str(db_with_mix))
    try:
        deleted_msgs, deleted_chunks = prune_mod.apply_prune(conn, ["200", "300"])
        # Остался только чат 100.
        remaining_chats = [r[0] for r in conn.execute("SELECT chat_id FROM chats").fetchall()]
        remaining_msgs = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        remaining_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    finally:
        conn.close()
    assert deleted_msgs == 5
    assert deleted_chunks == 2
    assert remaining_chats == ["100"]
    assert remaining_msgs == 5
    assert remaining_chunks == 2


def test_run_audit_dry_run_does_not_modify_db(db_with_mix: Path, tmp_path: Path, fixed_now: datetime) -> None:
    state_path = tmp_path / "state.json"
    report = prune_mod.run_audit(
        db_with_mix,
        threshold_days=180,
        apply=False,
        state_path=state_path,
        now_fn=lambda: fixed_now,
    )
    assert report.orphan_candidates == 2
    assert report.accessible == 1
    assert report.applied is False
    assert report.backup_path is None
    # DB не тронут.
    conn = sqlite3.connect(str(db_with_mix))
    try:
        assert conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0] == 3
    finally:
        conn.close()
    # State persisted.
    payload = json.loads(state_path.read_text())
    assert payload["orphan_candidates"] == 2
    assert payload["threshold_days"] == 180


def test_run_audit_apply_creates_backup_and_prunes(db_with_mix: Path, tmp_path: Path, fixed_now: datetime) -> None:
    state_path = tmp_path / "state.json"
    report = prune_mod.run_audit(
        db_with_mix,
        threshold_days=180,
        apply=True,
        state_path=state_path,
        now_fn=lambda: fixed_now,
    )
    assert report.applied is True
    assert report.backup_path is not None
    assert Path(report.backup_path).exists()
    # После prune только 1 чат остался.
    conn = sqlite3.connect(str(db_with_mix))
    try:
        assert conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0] == 1
    finally:
        conn.close()


def test_run_audit_missing_db_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        prune_mod.run_audit(
            tmp_path / "missing.db",
            threshold_days=180,
            apply=False,
            state_path=tmp_path / "s.json",
        )
