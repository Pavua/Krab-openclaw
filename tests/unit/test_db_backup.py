"""Wave 44-D: Тесты для scripts/krab_db_backup.py.

6 тестов:
1. verify_source_integrity — healthy DB → True
2. verify_source_integrity — corrupt DB → False
3. backup_db — создаёт .gz file и meta.json
4. backup_db — skips если integrity_check fails
5. cleanup_old — удаляет старые директории
6. main() — создаёт dated dir, backups всех существующих DBs
"""

import gzip
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Добавляем корень проекта в path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.krab_db_backup import (
    CRITICAL_DBS,
    backup_db,
    cleanup_old,
    get_peers_count,
    main,
    verify_source_integrity,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def healthy_db(tmp_path: Path) -> Path:
    """Создаёт корректную sqlite DB с таблицей peers."""
    db_path = tmp_path / "kraab.session"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE peers (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO peers VALUES (1, 'test_peer')")
    conn.execute("INSERT INTO peers VALUES (2, 'another_peer')")
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def corrupt_db(tmp_path: Path) -> Path:
    """Создаёт файл с невалидным sqlite содержимым."""
    db_path = tmp_path / "corrupt.session"
    db_path.write_bytes(b"This is not a valid sqlite database file at all!!! CORRUPT")
    return db_path


@pytest.fixture()
def backup_dir(tmp_path: Path) -> Path:
    """Временная директория для backup'ов."""
    d = tmp_path / "backups"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Test 1: verify_source_integrity — healthy DB → True
# ---------------------------------------------------------------------------


def test_verify_source_integrity_healthy(healthy_db: Path) -> None:
    """Здоровая DB возвращает (True, 'ok')."""
    ok, detail = verify_source_integrity(healthy_db)
    assert ok is True
    assert detail == "ok"


# ---------------------------------------------------------------------------
# Test 2: verify_source_integrity — corrupt DB → False
# ---------------------------------------------------------------------------


def test_verify_source_integrity_corrupt(corrupt_db: Path) -> None:
    """Corrupt DB возвращает (False, <detail>)."""
    ok, detail = verify_source_integrity(corrupt_db)
    assert ok is False
    # detail должен содержать описание проблемы
    assert detail != "ok"


# ---------------------------------------------------------------------------
# Test 3: backup_db — создаёт .gz file и metadata
# ---------------------------------------------------------------------------


def test_backup_db_creates_gz_and_meta(healthy_db: Path, backup_dir: Path) -> None:
    """backup_db создаёт сжатый backup с peers_count."""
    result = backup_db(healthy_db, backup_dir)

    assert result["ok"] is True
    assert result["integrity"] == "ok"
    assert result["peers_count"] == 2  # 2 peers в healthy_db fixture

    # Файл .gz создан
    gz_path = Path(result["backup"])
    assert gz_path.exists()
    assert gz_path.suffix == ".gz"

    # gzip корректен — можно распаковать
    with gzip.open(gz_path, "rb") as f:
        data = f.read()
    assert len(data) > 0

    # Распакованное — валидная sqlite DB
    tmp_restored = backup_dir / "restored.db"
    tmp_restored.write_bytes(data)
    conn = sqlite3.connect(str(tmp_restored))
    rows = conn.execute("SELECT count(*) FROM peers").fetchone()[0]
    conn.close()
    assert rows == 2

    # source_size и compressed_size заполнены
    assert result["source_size"] > 0
    assert result["compressed_size"] > 0


# ---------------------------------------------------------------------------
# Test 4: backup_db — skips если integrity_check fails
# ---------------------------------------------------------------------------


def test_backup_db_skips_corrupt(corrupt_db: Path, backup_dir: Path) -> None:
    """backup_db пропускает corrupted DB без создания файлов."""
    result = backup_db(corrupt_db, backup_dir)

    assert result["ok"] is False
    assert result.get("skipped") is True
    assert "source_integrity_failed" in result.get("reason", "")

    # Никаких gz файлов не создано
    gz_files = list(backup_dir.glob("*.gz"))
    assert len(gz_files) == 0


# ---------------------------------------------------------------------------
# Test 5: cleanup_old — удаляет старые директории
# ---------------------------------------------------------------------------


def test_cleanup_old(tmp_path: Path) -> None:
    """cleanup_old удаляет директории старше retention_days."""
    backup_root = tmp_path / "backups"
    backup_root.mkdir()

    # Создаём директории: старые и свежие
    today = datetime.now(timezone.utc)
    old_date = (today - timedelta(days=20)).strftime("%Y-%m-%d")
    recent_date = (today - timedelta(days=3)).strftime("%Y-%m-%d")
    today_str = today.strftime("%Y-%m-%d")

    old_dir = backup_root / old_date
    recent_dir = backup_root / recent_date
    today_dir = backup_root / today_str

    for d in [old_dir, recent_dir, today_dir]:
        d.mkdir()
        (d / "test.txt").write_text("data")

    # Также создаём директорию с нестандартным именем — не должна удаляться
    non_date_dir = backup_root / "misc"
    non_date_dir.mkdir()

    with patch("scripts.krab_db_backup.BACKUP_ROOT", backup_root):
        removed = cleanup_old(retention_days=14)

    # Старая директория удалена
    assert old_date in removed
    assert not old_dir.exists()

    # Свежие директории остались
    assert recent_dir.exists()
    assert today_dir.exists()

    # misc-директория не тронута
    assert non_date_dir.exists()

    # Только одна директория удалена
    assert len(removed) == 1


# ---------------------------------------------------------------------------
# Test 6: main() — создаёт dated dir, backups всех существующих DBs
# ---------------------------------------------------------------------------


def test_main_creates_backups(tmp_path: Path, healthy_db: Path) -> None:
    """main() создаёт dated dir и backup'ит существующие DBs."""
    backup_root = tmp_path / "backups"

    # Используем только одну существующую DB (healthy_db)
    # Остальные — несуществующие пути
    fake_dbs = [
        healthy_db,
        tmp_path / "nonexistent_archive.db",
        tmp_path / "nonexistent_runs.sqlite",
    ]

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with (
        patch("scripts.krab_db_backup.BACKUP_ROOT", backup_root),
        patch("scripts.krab_db_backup.CRITICAL_DBS", fake_dbs),
        patch("scripts.krab_db_backup.RETENTION_DAYS", 14),
    ):
        rc = main()

    # Успешный выход
    assert rc == 0

    # Dated dir создана
    dated_dir = backup_root / today
    assert dated_dir.exists()

    # Backup и meta.json для существующей DB
    gz_files = list(dated_dir.glob("*.gz"))
    assert len(gz_files) == 1

    meta_files = list(dated_dir.glob("*.meta.json"))
    assert len(meta_files) == 1

    # Meta содержит корректные данные
    meta = json.loads(meta_files[0].read_text())
    assert meta["ok"] is True
    assert meta["integrity"] == "ok"
