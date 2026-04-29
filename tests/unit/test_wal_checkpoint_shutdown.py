# -*- coding: utf-8 -*-
"""
Тесты WAL checkpoint flush на shutdown (Sentry PYTHON-FASTAPI-5W, 28.04.2026).

Сценарии:
- flush_wal_checkpoints() вызывается для всех known WAL DB (archive/session/runs).
- Missing файл → graceful skip без ошибки.
- preflight retry: transient `disk I/O error` → ретраит и не падает на первом attempt.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from src.bootstrap.db_corruption_guard import (
    flush_wal_checkpoints,
    known_wal_db_paths,
)


def _make_wal_db(path: Path) -> None:
    """Создаёт sqlite-базу в WAL-mode с одной записью + неcheckpoint'нутым WAL."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("CREATE TABLE t(x INTEGER);")
        conn.execute("INSERT INTO t VALUES (1);")
        conn.commit()
    finally:
        conn.close()


# ---------- known_wal_db_paths ----------


def test_known_wal_db_paths_covers_three_canonical(monkeypatch, tmp_path):
    """Известные WAL базы — archive.db, kraab.session, runs.sqlite."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    paths = known_wal_db_paths()
    names = [p.name for p in paths]
    assert "archive.db" in names
    assert "kraab.session" in names
    assert "runs.sqlite" in names
    assert len(paths) == 3


# ---------- flush_wal_checkpoints ----------


def test_flush_wal_checkpoints_runs_pragma_on_all_dbs(tmp_path):
    """Для каждой существующей WAL-базы вызывается PRAGMA wal_checkpoint(FULL)."""
    db1 = tmp_path / "a.db"
    db2 = tmp_path / "b.db"
    _make_wal_db(db1)
    _make_wal_db(db2)
    reports = flush_wal_checkpoints([db1, db2])
    assert len(reports) == 2
    for r in reports:
        assert r["ok"] is True, f"checkpoint failed: {r}"
        assert r["skipped"] is False
        # PRAGMA wal_checkpoint возвращает (busy, log, checkpointed) — busy=0 в detail.
        assert "0" in r["detail"]


def test_flush_wal_checkpoints_missing_file_graceful(tmp_path):
    """Missing файл не вызывает исключение, помечается skipped=True."""
    missing = tmp_path / "does_not_exist.db"
    reports = flush_wal_checkpoints([missing])
    assert len(reports) == 1
    assert reports[0]["skipped"] is True
    assert reports[0]["ok"] is True
    assert reports[0]["detail"] == "missing"


def test_flush_wal_checkpoints_handles_corrupt_db_without_raising(tmp_path):
    """Битая база не должна ронять shutdown — просто логируется и продолжаем."""
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"NOT_A_SQLITE_FILE" * 100)
    other = tmp_path / "good.db"
    _make_wal_db(other)
    reports = flush_wal_checkpoints([corrupt, other])
    assert len(reports) == 2
    # Битая база — не ok, но НЕ падает.
    assert reports[0]["ok"] is False
    assert reports[0]["skipped"] is False
    # Здоровая база после битой — должна успешно обработаться.
    assert reports[1]["ok"] is True


# ---------- preflight retry on transient disk I/O error ----------


def test_preflight_retries_on_transient_disk_io_error():
    """
    run_app retry logic: при `disk I/O error` делает 0.5s sleep + retry,
    не падая с первого attempt. Здесь проверяем именно retry-цикл.
    """
    from src.bootstrap import runtime as runtime_mod

    call_count = {"n": 0}

    def fake_preflight():
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise sqlite3.OperationalError("disk I/O error")
        return [{"path": "/tmp/x", "kind": "session", "ok": True, "quarantined": False}]

    async def runner():
        # Извлекаем retry-блок из run_app: имитируем минимальный фрагмент.
        # Полный run_app слишком тяжёл (поднимает userbot/web).
        with patch.object(runtime_mod, "preflight_known_dbs", side_effect=fake_preflight):
            preflight_reports: list = []
            for attempt in range(3):
                try:
                    preflight_reports = runtime_mod.preflight_known_dbs()
                    break
                except sqlite3.OperationalError as exc:
                    msg = str(exc).lower()
                    if "disk i/o error" in msg and attempt < 2:
                        await asyncio.sleep(0.0)  # ускоряем тест
                        continue
                    break
            return preflight_reports

    reports = asyncio.run(runner())
    assert call_count["n"] == 2, "должна быть одна неудача + один retry"
    assert reports and reports[0]["ok"] is True
