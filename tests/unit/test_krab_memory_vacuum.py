"""Wave 201: тесты для scripts/krab_memory_vacuum.py.

Покрытие:
    - check_integrity ok/corrupt
    - check_disk_reservation enough/insufficient
    - detect_krab_active WAL threshold
    - run_vacuum реально сжимает БД
    - run() pre-check abort при integrity fail
    - run() pre-check abort при insufficient disk
    - run() --dry-run возвращает estimate без записи в БД
    - run() --force обходит Krab-active check
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Загружаем модуль из scripts/ напрямую (не пакетный).
_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "krab_memory_vacuum.py"
_spec = importlib.util.spec_from_file_location("krab_memory_vacuum", _SCRIPT)
assert _spec and _spec.loader
vac_mod = importlib.util.module_from_spec(_spec)
sys.modules["krab_memory_vacuum"] = vac_mod
_spec.loader.exec_module(vac_mod)


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


def _make_dummy_db(db_path: Path, *, rows: int = 1000) -> None:
    """Создаёт небольшую SQLite БД с тестовыми данными для VACUUM."""

    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, payload TEXT)")
    conn.executemany(
        "INSERT INTO t (payload) VALUES (?)",
        [("x" * 200,) for _ in range(rows)],
    )
    conn.commit()
    # Удаляем половину — создаём пустые страницы (как после prune).
    conn.execute("DELETE FROM t WHERE id % 2 = 0")
    conn.commit()
    conn.close()


@pytest.fixture
def fixed_now():
    return lambda: datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Pure helpers.
# ---------------------------------------------------------------------------


def test_check_integrity_ok(tmp_path: Path) -> None:
    db = tmp_path / "ok.db"
    _make_dummy_db(db, rows=10)
    assert vac_mod.check_integrity(db) == "ok"


def test_check_disk_reservation_enough(tmp_path: Path) -> None:
    db = tmp_path / "ok.db"
    _make_dummy_db(db, rows=10)
    ok, required, free = vac_mod.check_disk_reservation(db)
    assert required > 0
    assert free > 0
    # На любом dev-боксе свободно несравнимо больше чем 2× ~50 КБ.
    assert ok is True


def test_check_disk_reservation_insufficient(tmp_path: Path) -> None:
    db = tmp_path / "ok.db"
    _make_dummy_db(db, rows=10)
    # Подменяем free_disk_bytes → возвращаем 0.
    with patch.object(vac_mod, "free_disk_bytes", return_value=0):
        ok, required, free = vac_mod.check_disk_reservation(db)
        assert ok is False
        assert free == 0
        assert required > 0


def test_detect_krab_active_wal_threshold(tmp_path: Path) -> None:
    db = tmp_path / "ok.db"
    _make_dummy_db(db, rows=10)
    wal = db.with_suffix(db.suffix + "-wal")
    wal.write_bytes(b"x" * 50_000)  # > 4096 threshold
    active, reason = vac_mod.detect_krab_active(db)
    assert active is True
    assert "WAL size" in reason


def test_detect_krab_active_no_wal(tmp_path: Path) -> None:
    db = tmp_path / "ok.db"
    _make_dummy_db(db, rows=10)
    # WAL не существует → exclusive lock доступен → not active.
    active, _ = vac_mod.detect_krab_active(db)
    assert active is False


def test_run_vacuum_shrinks_db(tmp_path: Path) -> None:
    db = tmp_path / "fat.db"
    _make_dummy_db(db, rows=5000)
    before = vac_mod.db_size_bytes(db)
    elapsed = vac_mod.run_vacuum(db)
    after = vac_mod.db_size_bytes(db)
    assert elapsed >= 0
    assert after < before, f"VACUUM не сжал БД: before={before} after={after}"


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------


def test_run_aborts_on_integrity_fail(tmp_path: Path, fixed_now) -> None:
    db = tmp_path / "broken.db"
    _make_dummy_db(db, rows=10)
    audit_path = tmp_path / "audit.json"

    with patch.object(vac_mod, "check_integrity", return_value="malformed"):
        audit = vac_mod.run(
            db,
            audit_path=audit_path,
            dry_run=False,
            force=False,
            now_fn=fixed_now,
        )
    assert audit.success is False
    assert audit.integrity_before == "malformed"
    assert "integrity check failed" in (audit.error or "")
    assert audit_path.exists()


def test_run_aborts_on_insufficient_disk(tmp_path: Path, fixed_now) -> None:
    db = tmp_path / "ok.db"
    _make_dummy_db(db, rows=10)
    audit_path = tmp_path / "audit.json"

    with patch.object(vac_mod, "free_disk_bytes", return_value=10):
        audit = vac_mod.run(
            db,
            audit_path=audit_path,
            dry_run=False,
            force=False,
            now_fn=fixed_now,
        )
    assert audit.success is False
    assert "insufficient disk" in (audit.error or "")


def test_run_dry_run_does_not_modify_db(tmp_path: Path, fixed_now) -> None:
    db = tmp_path / "ok.db"
    _make_dummy_db(db, rows=200)
    audit_path = tmp_path / "audit.json"
    size_before = vac_mod.db_size_bytes(db)

    audit = vac_mod.run(
        db,
        audit_path=audit_path,
        dry_run=True,
        force=False,
        now_fn=fixed_now,
    )
    assert audit.dry_run is True
    assert audit.success is True
    assert audit.integrity_after == "not_run"
    assert vac_mod.db_size_bytes(db) == size_before
    data = json.loads(audit_path.read_text())
    assert data["dry_run"] is True


def test_run_force_skips_krab_active_check(tmp_path: Path, fixed_now) -> None:
    db = tmp_path / "ok.db"
    _make_dummy_db(db, rows=200)
    audit_path = tmp_path / "audit.json"

    # Симулируем активного Krab — без --force упало бы.
    with patch.object(vac_mod, "detect_krab_active", return_value=(True, "WAL size huge")):
        audit = vac_mod.run(
            db,
            audit_path=audit_path,
            dry_run=False,
            force=True,
            now_fn=fixed_now,
        )
    assert audit.forced is True
    assert audit.success is True
    assert audit.integrity_after == "ok"


def test_run_success_path_real_vacuum(tmp_path: Path, fixed_now) -> None:
    db = tmp_path / "ok.db"
    _make_dummy_db(db, rows=2000)
    audit_path = tmp_path / "audit.json"
    size_before = vac_mod.db_size_bytes(db)

    audit = vac_mod.run(
        db,
        audit_path=audit_path,
        dry_run=False,
        force=False,
        now_fn=fixed_now,
    )
    assert audit.success is True
    assert audit.integrity_after == "ok"
    assert audit.size_after_mb <= audit.size_before_mb
    assert vac_mod.db_size_bytes(db) < size_before
    data = json.loads(audit_path.read_text())
    assert data["success"] is True
    assert data["audit_ts"] == fixed_now().isoformat()


def test_run_missing_db_raises(tmp_path: Path, fixed_now) -> None:
    db = tmp_path / "nope.db"
    audit_path = tmp_path / "audit.json"
    with pytest.raises(FileNotFoundError):
        vac_mod.run(
            db,
            audit_path=audit_path,
            dry_run=False,
            force=False,
            now_fn=fixed_now,
        )
