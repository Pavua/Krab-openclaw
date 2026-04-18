"""Юнит-тесты для scripts/archive_maintenance.py."""

from __future__ import annotations

import gzip
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

# Модуль лежит в scripts/, грузим напрямую
_MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "archive_maintenance.py"
)


@pytest.fixture
def am(monkeypatch, tmp_path):
    """Загрузить модуль свежим экземпляром с перенаправленными путями."""
    spec = importlib.util.spec_from_file_location(
        "archive_maintenance_test", _MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["archive_maintenance_test"] = module
    spec.loader.exec_module(module)

    # Редиректим пути на tmp_path
    monkeypatch.setattr(module, "ARCHIVE_DB", tmp_path / "archive.db")
    monkeypatch.setattr(module, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(module, "ROTATED_DIR", tmp_path / "logs" / "archive")
    monkeypatch.setattr(module, "MAX_LOG_SIZE_MB", 1)  # 1 MB для быстроты
    yield module
    sys.modules.pop("archive_maintenance_test", None)


def _make_db(path: Path) -> None:
    """Создать валидную SQLite базу с данными для VACUUM-теста."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, payload TEXT)")
    conn.executemany(
        "INSERT INTO t(payload) VALUES (?)",
        [("x" * 1024,) for _ in range(500)],
    )
    conn.execute("DELETE FROM t WHERE id > 100")  # освобождаем страницы
    conn.commit()
    conn.close()


def test_vacuum_missing_archive(am):
    result = am.vacuum_archive(dry_run=False)
    assert result["status"] == "missing"


def test_vacuum_dry_run_no_write(am):
    _make_db(am.ARCHIVE_DB)
    mtime_before = am.ARCHIVE_DB.stat().st_mtime
    size_before = am.ARCHIVE_DB.stat().st_size

    result = am.vacuum_archive(dry_run=True)

    assert result["status"] == "dry_run"
    assert result["before_size"] == size_before
    assert result["integrity"] == "ok"
    # Файл не изменён
    assert am.ARCHIVE_DB.stat().st_mtime == mtime_before


def test_vacuum_executes_and_reclaims(am):
    _make_db(am.ARCHIVE_DB)
    before = am.ARCHIVE_DB.stat().st_size

    result = am.vacuum_archive(dry_run=False)

    assert result["status"] == "ok"
    assert result["before_size"] == before
    assert result["after_size"] <= before
    assert "elapsed_sec" in result


def test_vacuum_integrity_failed_on_corrupt(am):
    # Мусорный файл под видом SQLite — sqlite3 часто такое открывает,
    # но integrity_check ловит, либо DatabaseError.
    am.ARCHIVE_DB.write_bytes(b"SQLite format 3\x00" + b"\x00" * 4096)

    result = am.vacuum_archive(dry_run=False)

    assert result["status"] in {"integrity_failed", "error"}


def test_rotate_logs_threshold_skipped(am, tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    small = logs / "small.log"
    small.write_bytes(b"x" * 1024)  # 1 KB — ниже порога

    results = am.rotate_logs(dry_run=False)

    assert results == []
    assert small.exists() and small.stat().st_size == 1024


def test_rotate_logs_over_threshold_dry_run(am, tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    big = logs / "big.log"
    big.write_bytes(b"x" * (am.MAX_LOG_SIZE_MB * 1024 * 1024 + 16))

    results = am.rotate_logs(dry_run=True)

    assert len(results) == 1
    assert results[0]["action"] == "would_rotate"
    assert results[0]["file"] == "big.log"
    # Файл не тронут
    assert big.stat().st_size > am.MAX_LOG_SIZE_MB * 1024 * 1024


def test_rotate_logs_executes(am, tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    big = logs / "big.log"
    payload = b"L" * (am.MAX_LOG_SIZE_MB * 1024 * 1024 + 128)
    big.write_bytes(payload)

    results = am.rotate_logs(dry_run=False)

    assert len(results) == 1
    assert results[0]["action"] == "rotated"
    # Оригинал truncated
    assert big.stat().st_size == 0
    # Создан .gz в archive/
    rotated_files = list((logs / "archive").glob("big_*.log.gz"))
    assert len(rotated_files) == 1
    with gzip.open(rotated_files[0], "rb") as f:
        assert f.read() == payload


def test_build_report_skip_flags(am):
    report = am.build_report(dry_run=True, skip_vacuum=True, skip_logs=True)
    assert "vacuum" not in report
    assert "logs_rotated" not in report
    assert report["dry_run"] is True
    assert "timestamp" in report


def test_human_size(am):
    assert am.human_size(0) == "0.0 B"
    assert am.human_size(2048) == "2.0 KB"
    assert am.human_size(5 * 1024 * 1024) == "5.0 MB"
