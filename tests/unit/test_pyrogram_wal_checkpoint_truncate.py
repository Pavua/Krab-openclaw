# -*- coding: utf-8 -*-
"""
Session 33 P1: тесты для checkpoint_session_wal().

Проверяет, что best-effort WAL TRUNCATE действительно сжимает sidecar после
shutdown'а Pyrogram-сессии. Покрывает:
  - happy path: создаём WAL, вызываем helper, sidecar обнуляется
  - idempotent: отсутствующий .session — silent skip (None, без exception)
  - не бросает наружу при битом файле
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.userbot.session import checkpoint_session_wal


def _create_wal_session(path: Path) -> None:
    """Создаёт sqlite-базу в WAL-режиме с накопленными фреймами."""
    with sqlite3.connect(str(path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, data BLOB)")
        # Записываем достаточно данных, чтобы WAL гарантированно был не пуст.
        for i in range(200):
            conn.execute("INSERT INTO t (data) VALUES (?)", (b"x" * 1024,))
        conn.commit()


def test_checkpoint_truncates_wal(tmp_path: Path) -> None:
    session_file = tmp_path / "kraab.session"
    _create_wal_session(session_file)

    wal_file = tmp_path / "kraab.session-wal"
    assert wal_file.exists(), "WAL sidecar should exist after writes in WAL mode"
    wal_size_before = wal_file.stat().st_size
    assert wal_size_before > 0

    result = checkpoint_session_wal(session_file)

    assert result is not None
    assert result["session_path"] == str(session_file)
    # frames_checkpointed должен быть >= 0; pages_in_wal был > 0 до checkpoint.
    assert result["pages_in_wal"] is not None
    assert result["frames_checkpointed"] is not None
    # После TRUNCATE WAL обнуляется (либо сам файл усечён до 0).
    if wal_file.exists():
        assert wal_file.stat().st_size == 0, (
            f"WAL not truncated: {wal_file.stat().st_size} bytes (was {wal_size_before})"
        )


def test_checkpoint_missing_file_is_silent(tmp_path: Path) -> None:
    """Idempotent: на свежем запуске .session ещё нет — вернуть None без шума."""
    missing = tmp_path / "nonexistent.session"
    assert not missing.exists()
    result = checkpoint_session_wal(missing)
    assert result is None


def test_checkpoint_corrupt_file_does_not_raise(tmp_path: Path) -> None:
    """Битый файл — best-effort, должен залогировать warning и вернуть None."""
    corrupt = tmp_path / "corrupt.session"
    corrupt.write_bytes(b"not a sqlite database, just garbage")
    # Не должно бросать.
    result = checkpoint_session_wal(corrupt)
    assert result is None


def test_checkpoint_accepts_str_and_path(tmp_path: Path) -> None:
    session_file = tmp_path / "k.session"
    _create_wal_session(session_file)

    r1 = checkpoint_session_wal(str(session_file))
    r2 = checkpoint_session_wal(session_file)
    assert r1 is not None
    assert r2 is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
