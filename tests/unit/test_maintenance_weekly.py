"""Tests for scripts/maintenance_weekly.py (archive.db VACUUM + log rotation)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

# Add scripts/ to sys.path so we can import maintenance_weekly
REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import maintenance_weekly as mw  # noqa: E402


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(100)])
    conn.commit()
    conn.close()


def test_vacuum_sqlite_dry_run(tmp_path):
    db = tmp_path / "test.db"
    _make_db(db)

    r = mw.vacuum_sqlite(db, dry_run=True)
    assert r["dry_run"] is True
    assert "size_before_mb" in r
    assert r["path"] == str(db)


def test_vacuum_sqlite_executes(tmp_path):
    db = tmp_path / "real.db"
    _make_db(db)
    # Delete rows to create fragmentation for VACUUM to reclaim
    conn = sqlite3.connect(str(db))
    conn.execute("DELETE FROM t")
    conn.commit()
    conn.close()

    r = mw.vacuum_sqlite(db, dry_run=False)
    assert "size_before_mb" in r
    assert "size_after_mb" in r
    assert "reclaimed_mb" in r
    assert "error" not in r


def test_vacuum_sqlite_missing_path():
    r = mw.vacuum_sqlite(Path("/nonexistent_krab_vacuum.db"), dry_run=False)
    assert r["skipped"] == "not_exists"


def test_rotate_log_missing_path():
    r = mw.rotate_log(Path("/nonexistent_krab_rotate.log"), dry_run=False)
    assert r["skipped"] == "not_exists"


def test_rotate_log_under_threshold(tmp_path):
    log = tmp_path / "test.log"
    log.write_text("small content")
    r = mw.rotate_log(log, max_mb=10, dry_run=False)
    assert r["skipped"] == "under_threshold"


def test_rotate_log_dry_run(tmp_path):
    log = tmp_path / "big.log"
    log.write_text("x" * (2 * 1024 * 1024))  # 2MB
    r = mw.rotate_log(log, max_mb=1, dry_run=True)
    assert "would_rotate_to" in r
    # Original still intact after dry-run
    assert log.stat().st_size >= 2 * 1024 * 1024


def test_rotate_log_executes(tmp_path):
    log = tmp_path / "big.log"
    # 2MB > 1MB threshold
    log.write_text("x" * (2 * 1024 * 1024))

    r = mw.rotate_log(log, max_mb=1, dry_run=False)
    assert "rotated_to" in r
    assert Path(r["rotated_to"]).exists()
    # Original truncated
    assert log.stat().st_size < 100


def test_rotate_log_cleanup_old_rotations(tmp_path, monkeypatch):
    """Ensure old rotations beyond LOG_KEEP_ROTATIONS are removed."""
    monkeypatch.setattr(mw, "LOG_KEEP_ROTATIONS", 2)

    log = tmp_path / "app.log"
    # Create stale .gz rotations with different mtimes
    import time

    for i in range(5):
        gz = tmp_path / f"app.log.2026010{i}_000000.gz"
        gz.write_bytes(b"old")
        # Vary mtime so sort order is deterministic
        import os

        os.utime(gz, (time.time() - (10 - i) * 60, time.time() - (10 - i) * 60))

    # Trigger rotation
    log.write_text("x" * (2 * 1024 * 1024))
    r = mw.rotate_log(log, max_mb=1, dry_run=False)
    assert "rotated_to" in r
    # After rotation we have 5 old + 1 new = 6 total, keep 2 newest → remove 4
    assert r["old_rotations_removed"] == 4


def test_purge_dedicated_chrome_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(mw, "DEDICATED_CHROME_DIR", tmp_path / "nope")
    r = mw.purge_dedicated_chrome(7, dry_run=True)
    assert r["skipped"] == "not_exists"


def test_purge_dedicated_chrome_dry_run(monkeypatch, tmp_path):
    chrome_dir = tmp_path / "chrome"
    chrome_dir.mkdir()
    old_file = chrome_dir / "old.cache"
    old_file.write_bytes(b"x" * 1024)
    # Set mtime to 10 days ago
    import os
    import time

    os.utime(old_file, (time.time() - 10 * 86400, time.time() - 10 * 86400))

    new_file = chrome_dir / "new.cache"
    new_file.write_bytes(b"x" * 1024)

    monkeypatch.setattr(mw, "DEDICATED_CHROME_DIR", chrome_dir)
    r = mw.purge_dedicated_chrome(7, dry_run=True)
    assert r["candidates_count"] == 1
    assert r["dry_run"] is True
    # Nothing deleted on dry-run
    assert old_file.exists()


def test_purge_dedicated_chrome_executes(monkeypatch, tmp_path):
    chrome_dir = tmp_path / "chrome"
    chrome_dir.mkdir()
    old_file = chrome_dir / "old.cache"
    old_file.write_bytes(b"x" * 2048)
    import os
    import time

    os.utime(old_file, (time.time() - 10 * 86400, time.time() - 10 * 86400))
    new_file = chrome_dir / "new.cache"
    new_file.write_bytes(b"x" * 2048)

    monkeypatch.setattr(mw, "DEDICATED_CHROME_DIR", chrome_dir)
    r = mw.purge_dedicated_chrome(7, dry_run=False)
    assert r["deleted"] == 1
    assert not old_file.exists()
    assert new_file.exists()


def test_main_dry_run_smoke(capsys, monkeypatch, tmp_path):
    """Smoke-test main() end-to-end in dry-run."""
    # Redirect all paths to tmp to avoid touching real runtime
    monkeypatch.setattr(mw, "ARCHIVE_DB", tmp_path / "archive.db")
    monkeypatch.setattr(mw, "SEARCH_CACHE_DB", tmp_path / "search.db")
    monkeypatch.setattr(mw, "HISTORY_CACHE_DB", tmp_path / "history.db")
    monkeypatch.setattr(mw, "KRAB_LOG", tmp_path / "krab_main.log")
    monkeypatch.setattr(mw, "DEDICATED_CHROME_DIR", tmp_path / "chrome")

    monkeypatch.setattr(sys, "argv", ["maintenance_weekly.py"])
    rc = mw.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "VACUUM archive.db" in out
    assert "Rotate krab_main.log" in out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
