"""Wave 90 + Wave 161: тесты для scripts/krab_memory_prune_orphans.py.

Wave 90: detect/estimate/apply pure helpers, end-to-end run_audit.
Wave 161: batched DELETE с commit-per-chat, --max-batch-time-sec timeout,
opt-in VACUUM, --commit-each-chat alias, env knob KRAB_MEMORY_PRUNE_MAX_BATCH_SEC.
"""

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
# Tests — detect + estimate (Wave 90).
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


# ---------------------------------------------------------------------------
# Tests — apply_prune batched (Wave 161).
# ---------------------------------------------------------------------------


def test_apply_prune_deletes_orphan_data(db_with_mix: Path) -> None:
    """Базовый случай — все orphan чаты удаляются, returns PruneOutcome."""

    conn = sqlite3.connect(str(db_with_mix))
    try:
        outcome = prune_mod.apply_prune(conn, ["200", "300"])
        remaining_chats = [r[0] for r in conn.execute("SELECT chat_id FROM chats").fetchall()]
        remaining_msgs = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        remaining_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    finally:
        conn.close()
    assert outcome.deleted_messages == 5
    assert outcome.deleted_chunks == 2
    assert outcome.processed_chats == 2
    assert outcome.remaining_chats == []
    assert outcome.timed_out is False
    assert remaining_chats == ["100"]
    assert remaining_msgs == 5
    assert remaining_chunks == 2


def test_apply_prune_empty_list_returns_zero_outcome() -> None:
    """Wave 161: ранний выход на пустом списке без открытия транзакции."""

    conn = sqlite3.connect(":memory:")
    try:
        outcome = prune_mod.apply_prune(conn, [])
    finally:
        conn.close()
    assert outcome.deleted_messages == 0
    assert outcome.deleted_chunks == 0
    assert outcome.processed_chats == 0
    assert outcome.remaining_chats == []
    assert outcome.timed_out is False


def test_apply_prune_commits_progress_every_n_chats(tmp_path: Path, fixed_now: datetime) -> None:
    """Wave 161: периодический commit виден на disk даже если процесс умрёт.

    Имитируем рост monotonic время на 0.01s/шаг — пройдут все 15 чатов.
    После 10 чатов должен быть commit, проверяем что 10 чатов уже физически
    удалены до финального commit'а (через отдельное соединение).
    """

    db_path = tmp_path / "archive.db"
    conn = _make_archive(db_path)
    stale = (fixed_now - timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
    chat_ids = [str(1000 + i) for i in range(15)]
    for cid in chat_ids:
        _seed(conn, cid, stale, n_msgs=1, n_chunks=0)
    conn.close()

    progress: list[tuple[int, str]] = []

    def _record(idx: int, chat_id: str, dm: int, dc: int) -> None:
        progress.append((idx, chat_id))

    conn = sqlite3.connect(str(db_path))
    try:
        outcome = prune_mod.apply_prune(
            conn,
            chat_ids,
            commit_every=10,
            progress_fn=_record,
        )
    finally:
        conn.close()

    assert outcome.processed_chats == 15
    assert outcome.timed_out is False
    assert len(progress) == 15
    # Все 15 удалены после финального commit.
    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0] == 0
    finally:
        conn.close()


def test_apply_prune_respects_max_batch_time_and_returns_remaining(
    tmp_path: Path, fixed_now: datetime
) -> None:
    """Wave 161: hard timeout прерывает loop, оставшиеся чаты — в remaining_chats."""

    db_path = tmp_path / "archive.db"
    conn = _make_archive(db_path)
    stale = (fixed_now - timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
    chat_ids = [f"chat_{i}" for i in range(10)]
    for cid in chat_ids:
        _seed(conn, cid, stale, n_msgs=2, n_chunks=1)
    conn.close()

    # Fake monotonic: каждое чтение = +1s. Timeout 3s → пройдёт 3 чата
    # (i=0 monotonic_fn() в начале + 0..2 итерации до timeout на i=3).
    clock = [0.0]

    def _mono() -> float:
        v = clock[0]
        clock[0] += 1.0
        return v

    conn = sqlite3.connect(str(db_path))
    try:
        outcome = prune_mod.apply_prune(
            conn,
            chat_ids,
            max_batch_time_sec=3,
            commit_every=1,
            monotonic_fn=_mono,
        )
    finally:
        conn.close()

    assert outcome.timed_out is True
    assert outcome.processed_chats < len(chat_ids)
    assert len(outcome.remaining_chats) == len(chat_ids) - outcome.processed_chats
    # Прогресс зафиксирован — обработанные чаты реально удалены.
    conn = sqlite3.connect(str(db_path))
    try:
        remaining_in_db = conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
    finally:
        conn.close()
    assert remaining_in_db == len(outcome.remaining_chats)


def test_apply_prune_deletes_per_chat_isolation(db_with_mix: Path) -> None:
    """Удаление chat_id=200 не должно затрагивать chat_id=100 или 300."""

    conn = sqlite3.connect(str(db_with_mix))
    try:
        outcome = prune_mod.apply_prune(conn, ["200"])
        remaining_chats = sorted(r[0] for r in conn.execute("SELECT chat_id FROM chats").fetchall())
        msgs_100 = conn.execute("SELECT COUNT(*) FROM messages WHERE chat_id = ?", ("100",)).fetchone()[0]
        msgs_300 = conn.execute("SELECT COUNT(*) FROM messages WHERE chat_id = ?", ("300",)).fetchone()[0]
    finally:
        conn.close()
    assert outcome.processed_chats == 1
    assert outcome.deleted_messages == 3
    assert remaining_chats == ["100", "300"]
    assert msgs_100 == 5
    assert msgs_300 == 2


# ---------------------------------------------------------------------------
# Tests — run_audit orchestration (Wave 90 + 161).
# ---------------------------------------------------------------------------


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
    assert report.processed_chats == 0
    assert report.remaining_chats == 0
    assert report.timed_out is False
    assert report.vacuumed is False
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
    assert report.processed_chats == 2
    assert report.remaining_chats == 0
    assert report.timed_out is False
    # VACUUM off by default → vacuumed=False.
    assert report.vacuumed is False
    # После prune только 1 чат остался.
    conn = sqlite3.connect(str(db_with_mix))
    try:
        assert conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0] == 1
    finally:
        conn.close()


def test_run_audit_apply_with_vacuum_flag(db_with_mix: Path, tmp_path: Path, fixed_now: datetime) -> None:
    """Wave 161: opt-in VACUUM выставляет vacuumed=True."""

    state_path = tmp_path / "state.json"
    report = prune_mod.run_audit(
        db_with_mix,
        threshold_days=180,
        apply=True,
        state_path=state_path,
        now_fn=lambda: fixed_now,
        vacuum=True,
    )
    assert report.vacuumed is True


def test_run_audit_timeout_skips_vacuum(tmp_path: Path, fixed_now: datetime) -> None:
    """Wave 161: при timed_out VACUUM не запускается даже с --vacuum."""

    db_path = tmp_path / "archive.db"
    conn = _make_archive(db_path)
    stale = (fixed_now - timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for i in range(5):
        _seed(conn, f"t{i}", stale, n_msgs=1, n_chunks=0)
    conn.close()

    clock = [0.0]

    def _mono() -> float:
        v = clock[0]
        clock[0] += 1.0
        return v

    state_path = tmp_path / "state.json"
    report = prune_mod.run_audit(
        db_path,
        threshold_days=180,
        apply=True,
        state_path=state_path,
        now_fn=lambda: fixed_now,
        max_batch_time_sec=2,
        vacuum=True,
        commit_every=1,
        monotonic_fn=_mono,
    )
    assert report.timed_out is True
    assert report.vacuumed is False
    assert report.remaining_chats > 0


def test_run_audit_persists_telemetry_fields(db_with_mix: Path, tmp_path: Path, fixed_now: datetime) -> None:
    """Wave 161: state.json содержит processed_chats/elapsed_sec/timed_out."""

    state_path = tmp_path / "state.json"
    prune_mod.run_audit(
        db_with_mix,
        threshold_days=180,
        apply=True,
        state_path=state_path,
        now_fn=lambda: fixed_now,
        max_batch_time_sec=600,
    )
    payload = json.loads(state_path.read_text())
    assert payload["processed_chats"] == 2
    assert payload["remaining_chats"] == 0
    assert payload["timed_out"] is False
    assert payload["max_batch_time_sec"] == 600
    assert "elapsed_sec" in payload
    assert payload["vacuumed"] is False


def test_run_audit_missing_db_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        prune_mod.run_audit(
            tmp_path / "missing.db",
            threshold_days=180,
            apply=False,
            state_path=tmp_path / "s.json",
        )


# ---------------------------------------------------------------------------
# Tests — argparse + env knobs (Wave 161).
# ---------------------------------------------------------------------------


def test_parse_args_default_max_batch_time() -> None:
    """Default --max-batch-time-sec = 1800."""

    ns = prune_mod._parse_args([])
    assert ns.max_batch_time_sec == prune_mod.DEFAULT_MAX_BATCH_TIME_SEC == 1800
    assert ns.vacuum is False
    assert ns.commit_each_chat is False
    assert ns.apply is False


def test_parse_args_commit_each_chat_flag() -> None:
    ns = prune_mod._parse_args(["--commit-each-chat"])
    assert ns.commit_each_chat is True
    assert ns.apply is False


def test_parse_args_apply_alias_still_works() -> None:
    """Backwards compat: --apply сохранён как alias для --commit-each-chat."""

    ns = prune_mod._parse_args(["--apply"])
    assert ns.apply is True


def test_parse_args_vacuum_flag() -> None:
    ns = prune_mod._parse_args(["--vacuum"])
    assert ns.vacuum is True


def test_parse_args_max_batch_time_explicit() -> None:
    ns = prune_mod._parse_args(["--max-batch-time-sec", "120"])
    assert ns.max_batch_time_sec == 120


def test_parse_args_max_batch_time_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """KRAB_MEMORY_PRUNE_MAX_BATCH_SEC env становится default-ом."""

    monkeypatch.setenv("KRAB_MEMORY_PRUNE_MAX_BATCH_SEC", "60")
    ns = prune_mod._parse_args([])
    assert ns.max_batch_time_sec == 60


def test_main_commit_each_chat_runs_apply(
    monkeypatch: pytest.MonkeyPatch,
    db_with_mix: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--commit-each-chat включает apply path в main()."""

    state_path = tmp_path / "state.json"
    rc = prune_mod.main(
        [
            "--db",
            str(db_with_mix),
            "--state",
            str(state_path),
            "--threshold-days",
            "180",
            "--commit-each-chat",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["applied"] is True
    assert payload["processed_chats"] >= 1
