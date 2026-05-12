"""Тесты Wave 105: backup integrity verification."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts import krab_backup_verify as bv

# --- SHA256 stability ---


def test_sha256_stable_for_same_content(tmp_path: Path) -> None:
    """SHA256 одинаков для одинакового содержимого."""
    f1 = tmp_path / "a.bin"
    f2 = tmp_path / "b.bin"
    payload = b"krab-wave105-payload" * 100
    f1.write_bytes(payload)
    f2.write_bytes(payload)
    assert bv.compute_sha256(f1) == bv.compute_sha256(f2)
    assert len(bv.compute_sha256(f1)) == 64  # hex digest


def test_sha256_differs_for_different_content(tmp_path: Path) -> None:
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"AAA")
    b.write_bytes(b"BBB")
    assert bv.compute_sha256(a) != bv.compute_sha256(b)


def test_sha256_missing_file_returns_empty(tmp_path: Path) -> None:
    assert bv.compute_sha256(tmp_path / "nope.bin") == ""


# --- sqlite integrity ---


def _make_valid_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()


def test_integrity_check_passes_on_valid_db(tmp_path: Path) -> None:
    db = tmp_path / "good.db"
    _make_valid_db(db)
    status, detail = bv.sqlite_integrity_check(db)
    assert status == "ok"
    assert detail == ""


def test_integrity_check_fails_on_corrupt_db(tmp_path: Path) -> None:
    """Перезаписываем середину DB файла мусором → integrity_check failed/error."""
    db = tmp_path / "bad.db"
    _make_valid_db(db)
    # Корраптим page 2 (после header)
    raw = bytearray(db.read_bytes())
    # Сохраняем magic header SQLite, но ломаем pages
    for i in range(100, min(len(raw), 4000)):
        raw[i] = 0xAB
    db.write_bytes(bytes(raw))

    status, _detail = bv.sqlite_integrity_check(db)
    assert status in ("failed", "error"), f"expected failed/error, got {status}"


def test_integrity_check_missing_file(tmp_path: Path) -> None:
    status, detail = bv.sqlite_integrity_check(tmp_path / "absent.db")
    assert status == "error"
    assert "file_missing" in detail


# --- discover_backups + run_verify ---


def test_discover_walks_repo_and_home_state(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    (repo / "data" / "sessions").mkdir(parents=True)
    (repo / "data" / "memory").mkdir(parents=True)
    (home / "backups").mkdir(parents=True)

    # Создаём backup-файлы
    (repo / "data" / "sessions" / "owner.session.bak.123").write_bytes(b"x")
    (repo / "data" / "memory" / "archive.db.bak_20260512").write_bytes(b"y")
    (home / "backups" / "snapshot_001.json").write_bytes(b"z")
    (home / "swarm_task_board.json.bak_20260430_015306").write_bytes(b"w")
    # Не-backup файл (не должен быть найден)
    (repo / "data" / "sessions" / "owner.session").write_bytes(b"live")

    found = bv.discover_backups(repo, home)
    found_names = {p.name for p in found}
    assert "owner.session.bak.123" in found_names
    assert "archive.db.bak_20260512" in found_names
    assert "snapshot_001.json" in found_names
    assert "swarm_task_board.json.bak_20260430_015306" in found_names
    assert "owner.session" not in found_names


def test_run_verify_full_shape(tmp_path: Path) -> None:
    """Smoke: report содержит обязательные поля и считает corrupt."""
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    (repo / "data" / "sessions").mkdir(parents=True)
    (home / "backups").mkdir(parents=True)

    # Valid session bak (не sqlite по эвристике .session → попробует open)
    good_db = repo / "data" / "sessions" / "owner.session.bak.1"
    _make_valid_db(good_db)
    # Plain JSON backup
    (home / "backups" / "state.json.bak_1").write_bytes(b'{"k":1}')

    report = bv.run_verify(repo, home)
    out = report.to_dict()

    assert set(out.keys()) >= {
        "timestamp",
        "total_backups",
        "total_size_mb",
        "corrupt_count",
        "corrupt_files",
        "files",
    }
    assert out["total_backups"] == 2
    assert out["total_size_mb"] >= 0.0
    assert isinstance(out["files"], list)
    assert len(out["files"]) == 2
    for f in out["files"]:
        assert "sha256" in f and len(f["sha256"]) == 64
        assert "integrity" in f
    # Valid sqlite — should be "ok"; json — "skipped"
    integrities = {Path(f["path"]).name: f["integrity"] for f in out["files"]}
    assert integrities["owner.session.bak.1"] == "ok"
    assert integrities["state.json.bak_1"] == "skipped"
    assert out["corrupt_count"] == 0


def test_run_verify_detects_corrupt(tmp_path: Path) -> None:
    """Corrupt .db → corrupt_count >= 1, corrupt_files содержит путь."""
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    (repo / "data" / "memory").mkdir(parents=True)
    home.mkdir(parents=True)

    bad = repo / "data" / "memory" / "archive.db.bak_corrupt"
    _make_valid_db(bad)
    raw = bytearray(bad.read_bytes())
    for i in range(100, min(len(raw), 4000)):
        raw[i] = 0xCD
    bad.write_bytes(bytes(raw))

    report = bv.run_verify(repo, home)
    assert report.corrupt_count >= 1
    assert any("archive.db.bak_corrupt" in c["path"] for c in report.corrupt_files)


# --- Rolling log ---


def test_rolling_log_keeps_last_n(tmp_path: Path) -> None:
    """append_rolling_log оставляет последние `keep` запусков."""
    state_dir = tmp_path / "state"
    fake_report = bv.VerifyReport(
        timestamp="2026-05-12T05:00:00+00:00",
        total_backups=0,
        total_size_mb=0.0,
        corrupt_count=0,
    )
    for _ in range(15):
        bv.append_rolling_log(state_dir, fake_report, keep=10)

    raw = json.loads((state_dir / "backup_verify_log.json").read_text())
    assert isinstance(raw["runs"], list)
    assert len(raw["runs"]) == 10


def test_rolling_log_recovers_from_corrupt_file(tmp_path: Path) -> None:
    """Если файл log сломан — переписывается заново без ошибки."""
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "backup_verify_log.json").write_text("{not-json")
    fake_report = bv.VerifyReport(
        timestamp="2026-05-12T05:00:00+00:00",
        total_backups=0,
        total_size_mb=0.0,
        corrupt_count=0,
    )
    bv.append_rolling_log(state_dir, fake_report, keep=5)
    raw = json.loads((state_dir / "backup_verify_log.json").read_text())
    assert len(raw["runs"]) == 1


# --- total_size_mb compute ---


def test_total_size_mb_computed_correctly(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    (repo / "data" / "sessions").mkdir(parents=True)
    home.mkdir(parents=True)

    payload_size = 2 * 1024 * 1024  # 2 MB
    (repo / "data" / "sessions" / "owner.session.bak.x").write_bytes(b"\x00" * payload_size)

    report = bv.run_verify(repo, home, run_integrity=False)
    assert report.total_backups == 1
    # 2 MB ≈ 2.0 (allow small tolerance)
    assert 1.9 < report.total_size_mb < 2.1


# --- is_sqlite_file heuristic ---


def test_is_sqlite_file_by_extension(tmp_path: Path) -> None:
    p = tmp_path / "foo.db.bak"
    p.write_bytes(b"random")
    assert bv.is_sqlite_file(p) is True


def test_is_sqlite_file_by_magic_header(tmp_path: Path) -> None:
    p = tmp_path / "noext"
    p.write_bytes(b"SQLite format 3\x00" + b"x" * 100)
    assert bv.is_sqlite_file(p) is True


def test_is_sqlite_file_negative(tmp_path: Path) -> None:
    p = tmp_path / "state.json.bak_1"
    p.write_bytes(b'{"k":1}')
    assert bv.is_sqlite_file(p) is False


# --- main() exit code ---


def test_main_exits_zero_when_no_corruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    repo.mkdir()
    home.mkdir()
    monkeypatch.setenv("KRAB_RUNTIME_STATE_DIR", str(home))
    rc = bv.main(
        [
            "--repo",
            str(repo),
            "--home-state",
            str(home),
            "--no-publish",
            "--no-log",
        ]
    )
    assert rc == 0


def test_main_exits_nonzero_when_corruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    (repo / "data" / "memory").mkdir(parents=True)
    home.mkdir()

    bad = repo / "data" / "memory" / "archive.db.bak_corrupt"
    _make_valid_db(bad)
    raw = bytearray(bad.read_bytes())
    for i in range(100, min(len(raw), 4000)):
        raw[i] = 0xCD
    bad.write_bytes(bytes(raw))

    monkeypatch.setenv("KRAB_RUNTIME_STATE_DIR", str(home))
    rc = bv.main(
        [
            "--repo",
            str(repo),
            "--home-state",
            str(home),
            "--no-publish",
            "--no-log",
        ]
    )
    assert rc == 1
