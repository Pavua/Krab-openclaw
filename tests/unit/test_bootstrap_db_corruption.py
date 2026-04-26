# -*- coding: utf-8 -*-
"""
Тесты DB corruption circuit breaker (Session 26).

Сценарии:
- integrity_check на здоровой БД → ok=True
- integrity_check на отсутствующем файле → ok=True (missing — не ошибка)
- integrity_check на corrupt-файле → ok=False + corruption marker
- is_corruption_error: положительные/отрицательные кейсы
- quarantine_db_file: rename + sidecar (-wal/-shm) move
- preflight_known_dbs: corrupt → quarantined=True; healthy → quarantined=False
- preflight_known_dbs: missing → ok=True, quarantined=False (не false-positive)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.bootstrap.db_corruption_guard import (
    KnownDb,
    integrity_check,
    is_corruption_error,
    preflight_known_dbs,
    quarantine_db_file,
)


def _make_healthy_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE t(x INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
    finally:
        conn.close()


def _make_corrupt_db(path: Path) -> None:
    """Создаёт файл с magic header SQLite, но битым телом → 'malformed'."""
    # SQLite header — 16 bytes 'SQLite format 3\0' плюс мусор.
    header = b"SQLite format 3\x00"
    path.write_bytes(header + b"\x00" * 100 + b"GARBAGE_PAGES" * 50)


# ---------- is_corruption_error ----------


def test_is_corruption_error_positive_malformed() -> None:
    assert is_corruption_error("database disk image is malformed")
    assert is_corruption_error(
        sqlite3.DatabaseError("database disk image is malformed")
    )


def test_is_corruption_error_disk_io_excluded_from_hard_markers() -> None:
    """Session 26 lesson: disk I/O error — transient OS issue, не corruption.

    False positive case 26.04: Pyrogram открыл WAL когда file system был busy,
    integrity_check на quarantined session показал 'ok', 380 peers, valid auth_key.
    Quarantine был ложным. disk I/O error удалён из _HARD_CORRUPTION_MARKERS.
    """
    assert not is_corruption_error("disk I/O error")


def test_is_corruption_error_positive_not_a_database() -> None:
    assert is_corruption_error("file is not a database")


def test_is_corruption_error_negative_locked() -> None:
    # locked — transient, НЕ должен trigger quarantine.
    assert not is_corruption_error("database is locked")


def test_is_corruption_error_negative_random() -> None:
    assert not is_corruption_error("connection timed out")
    assert not is_corruption_error(ValueError("normal error"))


# ---------- integrity_check ----------


def test_integrity_check_healthy(tmp_path: Path) -> None:
    db = tmp_path / "ok.db"
    _make_healthy_db(db)
    ok, detail = integrity_check(db)
    assert ok is True
    assert detail == "ok"


def test_integrity_check_missing_returns_ok(tmp_path: Path) -> None:
    # Несуществующий файл = "missing" = НЕ ошибка (DB будет создана при write).
    ok, detail = integrity_check(tmp_path / "absent.db")
    assert ok is True
    assert detail == "missing"


def test_integrity_check_corrupt_detected(tmp_path: Path) -> None:
    db = tmp_path / "bad.db"
    _make_corrupt_db(db)
    ok, detail = integrity_check(db)
    assert ok is False
    assert is_corruption_error(detail), f"detail not corruption-like: {detail!r}"


# ---------- quarantine_db_file ----------


def test_quarantine_renames_with_timestamp(tmp_path: Path) -> None:
    db = tmp_path / "kraab.session"
    db.write_bytes(b"corrupt-content")
    new_path = quarantine_db_file(db)
    assert new_path  # non-empty
    assert not db.exists()
    assert Path(new_path).exists()
    assert "corrupt-" in Path(new_path).name
    assert Path(new_path).name.startswith("kraab.session.corrupt-")


def test_quarantine_moves_sidecars(tmp_path: Path) -> None:
    db = tmp_path / "archive.db"
    db.write_bytes(b"x")
    wal = tmp_path / "archive.db-wal"
    wal.write_bytes(b"wal")
    shm = tmp_path / "archive.db-shm"
    shm.write_bytes(b"shm")
    new_path = quarantine_db_file(db)
    assert new_path
    # main file moved
    assert not db.exists()
    # sidecars moved with same timestamp suffix
    assert not wal.exists()
    assert not shm.exists()
    siblings = list(tmp_path.iterdir())
    sidecar_names = [s.name for s in siblings]
    assert any("archive.db-wal.corrupt-" in n for n in sidecar_names)
    assert any("archive.db-shm.corrupt-" in n for n in sidecar_names)


def test_quarantine_missing_file_noop(tmp_path: Path) -> None:
    result = quarantine_db_file(tmp_path / "nope.db")
    assert result == ""


# ---------- preflight_known_dbs ----------


def test_preflight_corrupt_db_quarantined(tmp_path: Path) -> None:
    bad = tmp_path / "bad.session"
    _make_corrupt_db(bad)
    reports = preflight_known_dbs(
        known_dbs=[KnownDb(path=bad, kind="session", critical=True)]
    )
    assert len(reports) == 1
    r = reports[0]
    assert r["ok"] is False
    assert r["quarantined"] is True
    assert r["quarantine_path"]
    assert not bad.exists()  # реально переименован


def test_preflight_healthy_db_not_quarantined(tmp_path: Path) -> None:
    good = tmp_path / "good.db"
    _make_healthy_db(good)
    reports = preflight_known_dbs(
        known_dbs=[KnownDb(path=good, kind="archive", critical=False)]
    )
    assert len(reports) == 1
    r = reports[0]
    assert r["ok"] is True
    assert r["quarantined"] is False
    assert good.exists()  # остался на месте


def test_preflight_missing_db_not_flagged(tmp_path: Path) -> None:
    # Отсутствующая БД — НЕ false-positive.
    missing = tmp_path / "absent.db"
    reports = preflight_known_dbs(
        known_dbs=[KnownDb(path=missing, kind="archive", critical=False)]
    )
    assert len(reports) == 1
    r = reports[0]
    assert r["ok"] is True
    assert r["quarantined"] is False
    assert r["detail"] == "missing"


def test_preflight_mixed_only_corrupt_quarantined(tmp_path: Path) -> None:
    good = tmp_path / "good.db"
    _make_healthy_db(good)
    bad = tmp_path / "bad.db"
    _make_corrupt_db(bad)
    reports = preflight_known_dbs(
        known_dbs=[
            KnownDb(path=good, kind="archive", critical=False),
            KnownDb(path=bad, kind="session", critical=True),
        ]
    )
    by_kind = {r["kind"]: r for r in reports}
    assert by_kind["archive"]["quarantined"] is False
    assert by_kind["session"]["quarantined"] is True
    # critical+quarantined → bootstrap должен exit (проверяем флаг)
    assert by_kind["session"]["critical"] is True


# ---------- false-positive prevention ----------


def test_normal_startup_no_false_positive(tmp_path: Path) -> None:
    """
    Здоровая инфраструктура (несколько здоровых DB) не должна triggernуть
    ни одного quarantine — это базовая защита от false-positive в проде.
    """
    db1 = tmp_path / "kraab.session"
    db2 = tmp_path / "archive.db"
    _make_healthy_db(db1)
    _make_healthy_db(db2)
    reports = preflight_known_dbs(
        known_dbs=[
            KnownDb(path=db1, kind="session", critical=True),
            KnownDb(path=db2, kind="archive", critical=False),
        ]
    )
    assert all(r["ok"] for r in reports)
    assert all(not r["quarantined"] for r in reports)
    assert db1.exists() and db2.exists()
