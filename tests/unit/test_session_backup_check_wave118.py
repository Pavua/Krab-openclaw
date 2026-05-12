# -*- coding: utf-8 -*-
"""Wave 118: tests для scripts/krab_session_backup_check.py.

Покрытие:
- valid backup (sessions table читается, peers count корректный)
- corrupted backup (sessions table отсутствует)
- backup с пустой sessions table (auth_ok=False)
- peer count чтение
- glob discovery + sidecar исключение
- missing sessions dir
- run_check end-to-end
- metric set_counts no-op без prometheus_client
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

# Import script as module (register в sys.modules для dataclass introspection)
_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "scripts"
    / "krab_session_backup_check.py"
)
_spec = importlib.util.spec_from_file_location("krab_session_backup_check", _SCRIPT_PATH)
assert _spec and _spec.loader
sbc = importlib.util.module_from_spec(_spec)
sys.modules["krab_session_backup_check"] = sbc
_spec.loader.exec_module(sbc)


def _make_valid_session_db(path: Path, *, peers: int = 0) -> None:
    """Создаёт mini-Pyrofork session db со схемой sessions+peers."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE sessions ("
            "dc_id INTEGER, test_mode INTEGER, auth_key BLOB, "
            "date INTEGER, user_id INTEGER, is_bot INTEGER)"
        )
        conn.execute(
            "INSERT INTO sessions VALUES (4, 0, x'00aabb', 0, 12345, 0)"
        )
        conn.execute(
            "CREATE TABLE peers ("
            "id INTEGER PRIMARY KEY, access_hash INTEGER, type TEXT, "
            "username TEXT, phone_number TEXT, last_update_on INTEGER)"
        )
        for i in range(peers):
            conn.execute(
                "INSERT INTO peers VALUES (?, ?, 'user', NULL, NULL, 0)",
                (i + 1000, i * 7),
            )
        conn.commit()
    finally:
        conn.close()


def _make_corrupted_db(path: Path) -> None:
    """Создаёт sqlite БЕЗ таблицы sessions — auth_ok=False."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE other (x INTEGER)")
        conn.commit()
    finally:
        conn.close()


def _make_empty_sessions_db(path: Path) -> None:
    """Sessions table есть, но пустая → auth_ok=False (sessions_empty)."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE sessions ("
            "dc_id INTEGER, test_mode INTEGER, auth_key BLOB, "
            "date INTEGER, user_id INTEGER, is_bot INTEGER)"
        )
        conn.execute(
            "CREATE TABLE peers (id INTEGER PRIMARY KEY)"
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def sessions_dir(tmp_path: Path) -> Path:
    d = tmp_path / "sessions"
    d.mkdir()
    return d


# ───────────────────────── unit tests ──────────────────────────


def test_check_auth_key_valid(sessions_dir: Path) -> None:
    backup = sessions_dir / "kraab.session.bak.1234567890"
    _make_valid_session_db(backup, peers=5)
    ok, reason = sbc.check_auth_key(backup)
    assert ok is True
    assert reason == ""


def test_check_auth_key_corrupted_missing_table(sessions_dir: Path) -> None:
    backup = sessions_dir / "kraab.session.bak.broken"
    _make_corrupted_db(backup)
    ok, reason = sbc.check_auth_key(backup)
    assert ok is False
    assert "sessions_read_failed" in reason


def test_check_auth_key_empty_sessions(sessions_dir: Path) -> None:
    backup = sessions_dir / "kraab.session.bak.empty"
    _make_empty_sessions_db(backup)
    ok, reason = sbc.check_auth_key(backup)
    assert ok is False
    assert reason == "sessions_empty"


def test_read_peer_count(sessions_dir: Path) -> None:
    backup = sessions_dir / "kraab.session.bak.peers"
    _make_valid_session_db(backup, peers=42)
    assert sbc.read_peer_count(backup) == 42


def test_read_peer_count_missing_table(sessions_dir: Path) -> None:
    backup = sessions_dir / "kraab.session.bak.nopeers"
    _make_corrupted_db(backup)
    assert sbc.read_peer_count(backup) is None


def test_discover_session_backups_filters_sidecars(sessions_dir: Path) -> None:
    # main + sidecars (shm/wal)
    main_backup = sessions_dir / "kraab.session.bak.20260501"
    _make_valid_session_db(main_backup)
    (sessions_dir / "kraab.session.bak.20260501-shm").write_bytes(b"\x00" * 32)
    (sessions_dir / "kraab.session.bak.20260501-wal").write_bytes(b"\x00" * 32)
    # Не bak.* — не должен попасть
    (sessions_dir / "kraab.session").write_bytes(b"\x00" * 32)

    found = sbc.discover_session_backups(sessions_dir)
    paths = [p.name for p in found]
    assert "kraab.session.bak.20260501" in paths
    assert all("-shm" not in p and "-wal" not in p for p in paths)
    assert "kraab.session" not in paths


def test_discover_session_backups_missing_dir(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    assert sbc.discover_session_backups(missing) == []


def test_run_check_end_to_end_mixed(sessions_dir: Path) -> None:
    """Two valid + one corrupted → counts корректны."""
    _make_valid_session_db(sessions_dir / "kraab.session.bak.1", peers=10)
    _make_valid_session_db(sessions_dir / "kraab.session.bak.2", peers=0)
    _make_corrupted_db(sessions_dir / "kraab.session.bak.3")
    # Sidecar — must be filtered
    (sessions_dir / "kraab.session.bak.1-wal").write_bytes(b"\x00")

    report = sbc.run_check(sessions_dir)
    assert report.total_session_backups == 3
    assert report.valid == 2
    assert report.corrupt == 1
    assert len(report.files) == 3
    # peer_counts dict содержит все three paths
    assert len(report.peer_counts) == 3
    # Один из valid имеет peer_count=10
    peer_values = sorted(
        v for v in report.peer_counts.values() if v is not None
    )
    assert 10 in peer_values
    assert 0 in peer_values


def test_report_to_dict_shape(sessions_dir: Path) -> None:
    _make_valid_session_db(sessions_dir / "kraab.session.bak.shape", peers=3)
    report = sbc.run_check(sessions_dir)
    d = report.to_dict()
    assert set(d.keys()) >= {
        "timestamp",
        "total_session_backups",
        "valid",
        "corrupt",
        "peer_counts",
        "files",
    }
    assert d["valid"] == 1
    assert d["corrupt"] == 0
    assert isinstance(d["files"], list)
    rec = d["files"][0]
    assert "sha256" in rec and len(rec["sha256"]) == 64
    assert rec["auth_ok"] is True
    assert rec["peer_count"] == 3


def test_metrics_module_set_counts_no_op_without_prometheus() -> None:
    """set_counts должен быть best-effort: не падать никогда."""
    from src.core.metrics import session_backup as sb_metrics

    # Не должен бросать, даже если prometheus_client отсутствует
    sb_metrics.set_counts(valid=5, corrupt=0)
    sb_metrics.set_counts(valid=0, corrupt=2)
    # negative значения clamp'ятся
    sb_metrics.set_counts(valid=-1, corrupt=-5)
