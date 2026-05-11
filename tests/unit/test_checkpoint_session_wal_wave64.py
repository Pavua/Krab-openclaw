# -*- coding: utf-8 -*-
"""
Wave 64: checkpoint_session_wal должен быть graceful no-op в DELETE mode.

После переключения Pyrogram session в journal_mode=DELETE (Wave 64), WAL
не существует. PRAGMA wal_checkpoint(TRUNCATE) на DELETE-mode базе вернёт
(busy=-1, log_frames=-1, checkpointed=-1) или fail silently — checkpoint
бессмыслен.

Поведение:
- checkpoint_session_wal на DELETE-mode session: возвращает dict с пометкой
  что checkpoint пропущен (либо None — graceful), без warning'а.
- На WAL-mode session — работает как раньше (backward compat).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.userbot.session import checkpoint_session_wal


def _create_delete_session(path: Path) -> None:
    """Создаёт sqlite DB в journal_mode=DELETE."""
    with sqlite3.connect(str(path)) as conn:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, data BLOB)")
        for i in range(20):
            conn.execute("INSERT INTO t (data) VALUES (?)", (b"x" * 256,))
        conn.commit()


def _create_wal_session(path: Path) -> None:
    """Создаёт sqlite-базу в WAL-режиме."""
    with sqlite3.connect(str(path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, data BLOB)")
        for i in range(20):
            conn.execute("INSERT INTO t (data) VALUES (?)", (b"x" * 256,))
        conn.commit()


def test_checkpoint_on_delete_mode_is_no_op(tmp_path: Path) -> None:
    """
    В DELETE mode WAL не существует. checkpoint_session_wal не должен
    падать или генерировать ложные warning'и — просто graceful skip.

    Допустимый результат:
    - None (skipped silently)
    - dict с frames_checkpointed=0 (или -1, busy_check возвращает -1 если нет WAL)
    """
    session_file = tmp_path / "kraab.session"
    _create_delete_session(session_file)

    # WAL sidecar не должен существовать
    wal_file = tmp_path / "kraab.session-wal"
    assert not wal_file.exists(), "WAL sidecar shouldn't exist after writes in DELETE mode"

    # checkpoint должен не падать
    result = checkpoint_session_wal(session_file)
    # Допустимо: None (skipped) или dict со значениями для WAL=0 frames
    if result is not None:
        # frames_checkpointed может быть 0 (нет WAL) или -1 (busy_check)
        # Главное — нет exception и нет реального warning.
        assert "session_path" in result
        # На DELETE-mode базе, frames_checkpointed обычно 0 или -1.
        # Главное — функция не упала.
        frames = result.get("frames_checkpointed")
        # SQLite на DELETE mode при wal_checkpoint обычно возвращает -1
        # или (0, 0, 0). Никаких положительных frames быть не может.
        assert frames is None or int(frames) <= 0, (
            f"DELETE mode should report 0 or -1 checkpointed frames, got {frames}"
        )


def test_checkpoint_on_wal_mode_still_works(tmp_path: Path) -> None:
    """
    Backward compat: на WAL-mode DB checkpoint всё ещё работает.
    Wave 64 не должна сломать legacy path (на случай если кто-то держит WAL).
    """
    session_file = tmp_path / "kraab.session"
    _create_wal_session(session_file)

    wal_file = tmp_path / "kraab.session-wal"
    assert wal_file.exists(), "WAL sidecar should exist for WAL-mode DB"

    result = checkpoint_session_wal(session_file)
    assert result is not None
    # На WAL-mode базе должны быть real frames до checkpoint.
    # После TRUNCATE WAL обнуляется.


def test_checkpoint_missing_file_silent(tmp_path: Path) -> None:
    """Backward compat: missing file → None (как и раньше)."""
    missing = tmp_path / "nonexistent.session"
    result = checkpoint_session_wal(missing)
    assert result is None


def test_checkpoint_corrupt_file_no_raise(tmp_path: Path) -> None:
    """Backward compat: corrupt file → graceful (None), не raise."""
    corrupt = tmp_path / "corrupt.session"
    corrupt.write_bytes(b"not a sqlite database, garbage")
    result = checkpoint_session_wal(corrupt)
    assert result is None
