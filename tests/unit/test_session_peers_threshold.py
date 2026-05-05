# -*- coding: utf-8 -*-
"""
Тесты Wave 24-B: peers threshold check + stale WAL/SHM cleanup.

Проверяют три новые функции из src/bootstrap/session_recovery.py:
- check_peers_count() — threshold guard против pristine/wiped DB
- cleanup_stale_wal_shm() — удаление stale WAL/SHM без live writer'а
- any_pyrofork_holds_session() — lsof-based проверка живого writer'а
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import src.bootstrap.session_recovery as sr_mod
from src.bootstrap.session_recovery import (
    MIN_PEERS_THRESHOLD,
    any_pyrofork_holds_session,
    check_peers_count,
    cleanup_stale_wal_shm,
)

# ── helpers ────────────────────────────────────────────────────────────────────


def _make_session(path: Path, *, peer_rows: int = 0) -> Path:
    """Создаёт минимальный валидный Pyrogram .session файл."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS sessions (dc_id INTEGER, auth_key BLOB)")
        conn.execute("INSERT INTO sessions VALUES (2, X'00')")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS peers "
            "(id INTEGER PRIMARY KEY, access_hash INTEGER, type TEXT)"
        )
        conn.execute("CREATE TABLE IF NOT EXISTS usernames (id INTEGER, username TEXT)")
        for i in range(peer_rows):
            conn.execute(
                "INSERT INTO peers VALUES (?, ?, 'user')",
                (i + 1, (i + 1) * 100),
            )
        conn.commit()
    finally:
        conn.close()
    return path


# ── tests: check_peers_count ──────────────────────────────────────────────────


def test_check_peers_count_healthy(tmp_path):
    """Peers >= threshold → (True, count)."""
    sess = tmp_path / "kraab.session"
    _make_session(sess, peer_rows=MIN_PEERS_THRESHOLD + 10)

    healthy, count = check_peers_count(sess)

    assert healthy is True
    assert count == MIN_PEERS_THRESHOLD + 10


def test_check_peers_count_below_threshold(tmp_path):
    """Peers < threshold → (False, count)."""
    sess = tmp_path / "kraab.session"
    _make_session(sess, peer_rows=5)

    healthy, count = check_peers_count(sess)

    assert healthy is False
    assert count == 5


def test_check_peers_count_malformed_db(tmp_path):
    """Malformed/locked DB → (False, 0) — recovery через другой path."""
    sess = tmp_path / "kraab.session"
    sess.write_bytes(b"not a sqlite database at all" * 10)

    healthy, count = check_peers_count(sess)

    assert healthy is False
    assert count == 0


def test_check_peers_count_env_override(tmp_path, monkeypatch):
    """KRAB_SESSION_MIN_PEERS_THRESHOLD env var переопределяет порог."""
    monkeypatch.setenv("KRAB_SESSION_MIN_PEERS_THRESHOLD", "10")
    # Перечитываем константу после patching env
    monkeypatch.setattr(sr_mod, "MIN_PEERS_THRESHOLD", 10)

    sess = tmp_path / "kraab.session"
    _make_session(sess, peer_rows=15)  # 15 >= 10 → healthy

    healthy, count = check_peers_count(sess)

    assert healthy is True
    assert count == 15


def test_check_peers_count_missing_file(tmp_path):
    """Отсутствующий файл → (True, 0) — fresh install, не ошибка."""
    sess = tmp_path / "kraab.session"
    assert not sess.exists()

    healthy, count = check_peers_count(sess)

    assert healthy is True
    assert count == 0


# ── tests: cleanup_stale_wal_shm ─────────────────────────────────────────────


def test_cleanup_stale_wal_shm_no_live_process(tmp_path):
    """Нет live writer'а → WAL/SHM удаляются, возвращает True."""
    sess = tmp_path / "kraab.session"
    _make_session(sess)
    wal = tmp_path / "kraab.session-wal"
    shm = tmp_path / "kraab.session-shm"
    wal.write_bytes(b"stale wal frames")
    shm.write_bytes(b"stale shm data")

    with patch(
        "src.bootstrap.session_recovery.any_pyrofork_holds_session",
        return_value=False,
    ):
        cleaned = cleanup_stale_wal_shm(sess)

    assert cleaned is True
    assert not wal.exists()
    assert not shm.exists()


def test_cleanup_stale_wal_shm_live_process_no_cleanup(tmp_path):
    """Есть live pyrofork writer → WAL/SHM НЕ удаляются, возвращает False."""
    sess = tmp_path / "kraab.session"
    _make_session(sess)
    wal = tmp_path / "kraab.session-wal"
    shm = tmp_path / "kraab.session-shm"
    wal.write_bytes(b"live wal frames")
    shm.write_bytes(b"live shm data")

    with patch(
        "src.bootstrap.session_recovery.any_pyrofork_holds_session",
        return_value=True,
    ):
        cleaned = cleanup_stale_wal_shm(sess)

    assert cleaned is False
    # Файлы должны остаться нетронутыми
    assert wal.exists()
    assert shm.exists()


def test_cleanup_stale_wal_shm_no_files(tmp_path):
    """WAL/SHM файлов нет → no-op, возвращает False."""
    sess = tmp_path / "kraab.session"
    _make_session(sess)
    # WAL и SHM не создаём намеренно

    # Не должен вызывать lsof если нет файлов
    with patch("src.bootstrap.session_recovery.any_pyrofork_holds_session") as mock_lsof:
        cleaned = cleanup_stale_wal_shm(sess)

    assert cleaned is False
    mock_lsof.assert_not_called()


# ── tests: any_pyrofork_holds_session ────────────────────────────────────────


def test_any_pyrofork_holds_session_lsof_returns_pid(tmp_path):
    """lsof возвращает PID → True (файл открыт)."""
    sess = tmp_path / "kraab.session"
    _make_session(sess)

    mock_result = MagicMock()
    mock_result.stdout = "12345\n"

    with patch("subprocess.run", return_value=mock_result):
        holds = any_pyrofork_holds_session(sess)

    assert holds is True


def test_any_pyrofork_holds_session_lsof_empty(tmp_path):
    """lsof возвращает пустой stdout → False (файл не открыт)."""
    sess = tmp_path / "kraab.session"
    _make_session(sess)

    mock_result = MagicMock()
    mock_result.stdout = ""

    with patch("subprocess.run", return_value=mock_result):
        holds = any_pyrofork_holds_session(sess)

    assert holds is False


def test_any_pyrofork_holds_session_lsof_unavailable(tmp_path):
    """lsof недоступен (FileNotFoundError) → fail-safe False."""
    sess = tmp_path / "kraab.session"
    _make_session(sess)

    with patch("subprocess.run", side_effect=FileNotFoundError("lsof not found")):
        holds = any_pyrofork_holds_session(sess)

    assert holds is False
