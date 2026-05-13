# -*- coding: utf-8 -*-
"""Wave 237: тесты для src/core/backup_health_monitor.py.

11 тестов:
1. check_freshness — нет backup'ов → fail
2. check_freshness — свежий backup → ok
3. check_freshness — старый backup (>24h) → fail
4. check_size_variance — недостаточно истории → ok (skip)
5. check_size_variance — в пределах ±20% → ok
6. check_size_variance — отклонение >20% → fail
7. check_integrity — корректный .gz backup → ok
8. check_integrity — повреждённый .gz → fail
9. check_restoration_drill — env gate (по умолчанию skip)
10. check_restoration_drill — opt-in → ok
11. run_health_check — оркестрация (full path с реальными файлами)
"""

from __future__ import annotations

import gzip
import os
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core import backup_health_monitor as bhm

# ─── Fixtures ────────────────────────────────────────────────────────────────


def _make_sqlite_db(path: Path, *, rows: int = 5) -> None:
    """Создаёт мини sqlite DB."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    for i in range(rows):
        conn.execute("INSERT INTO t (v) VALUES (?)", (f"row_{i}",))
    conn.commit()
    conn.close()


def _make_gzipped_backup(daily_dir: Path, *, rows: int = 5) -> Path:
    """Создаёт archive.db.bak.gz в указанной директории."""
    daily_dir.mkdir(parents=True, exist_ok=True)
    raw = daily_dir / "archive.db"
    _make_sqlite_db(raw, rows=rows)
    gz_path = daily_dir / bhm.ARCHIVE_BACKUP_FILENAME
    with open(raw, "rb") as src, gzip.open(gz_path, "wb") as dst:
        dst.write(src.read())
    raw.unlink()
    return gz_path


@pytest.fixture()
def backup_root(tmp_path: Path) -> Path:
    """Корень backups/ для теста."""
    root = tmp_path / "backups"
    root.mkdir()
    return root


@pytest.fixture(autouse=True)
def _reset_state():
    """Сбрасывает state между тестами."""
    bhm._state["consecutive_failures"] = 0
    bhm._state["last_check_ts"] = 0.0
    bhm._state["last_result_ok"] = True
    yield


# ─── Тесты ───────────────────────────────────────────────────────────────────


def test_freshness_no_backup(backup_root: Path) -> None:
    """Нет backup'ов → check_freshness fail с reason."""
    result = bhm.check_freshness(None)
    assert result["ok"] is False
    assert result["reason"] == "no_backup_found"


def test_freshness_fresh_backup(backup_root: Path) -> None:
    """Свежий backup (<24h) → ok."""
    daily = backup_root / "2026-05-14"
    gz = _make_gzipped_backup(daily)
    result = bhm.check_freshness(gz, now_ts=time.time())
    assert result["ok"] is True
    assert result["age_seconds"] < bhm.FRESHNESS_MAX_AGE_SEC


def test_freshness_old_backup(backup_root: Path) -> None:
    """Старый backup (>24h) → fail."""
    daily = backup_root / "2026-05-10"
    gz = _make_gzipped_backup(daily)
    # Сдвигаем mtime на 25h назад.
    old_ts = time.time() - 25 * 3600
    os.utime(gz, (old_ts, old_ts))
    result = bhm.check_freshness(gz)
    assert result["ok"] is False
    assert result["age_seconds"] >= bhm.FRESHNESS_MAX_AGE_SEC


def test_size_variance_insufficient_history(backup_root: Path) -> None:
    """Один backup → недостаточно истории → ok (skip)."""
    daily = backup_root / "2026-05-14"
    _make_gzipped_backup(daily)
    result = bhm.check_size_variance(root=backup_root)
    assert result["ok"] is True
    assert result.get("reason") == "insufficient_history"


def test_size_variance_within_tolerance(backup_root: Path) -> None:
    """Размеры в пределах ±20% → ok."""
    for date, rows in [
        ("2026-05-14", 100),  # latest
        ("2026-05-13", 95),
        ("2026-05-12", 105),
        ("2026-05-11", 100),
    ]:
        _make_gzipped_backup(backup_root / date, rows=rows)
    result = bhm.check_size_variance(root=backup_root)
    assert result["ok"] is True
    assert result["deviation"] <= bhm.SIZE_VARIANCE_TOLERANCE


def test_size_variance_out_of_tolerance(backup_root: Path) -> None:
    """Latest сильно меньше → fail (>20%)."""
    # Latest заметно меньше истории (truncation simulation).
    _make_gzipped_backup(backup_root / "2026-05-14", rows=1)
    for date in ("2026-05-13", "2026-05-12", "2026-05-11"):
        _make_gzipped_backup(backup_root / date, rows=10000)
    result = bhm.check_size_variance(root=backup_root)
    assert result["ok"] is False
    assert result["deviation"] > bhm.SIZE_VARIANCE_TOLERANCE


def test_integrity_healthy_gz(backup_root: Path) -> None:
    """Корректный .gz backup → integrity ok."""
    daily = backup_root / "2026-05-14"
    gz = _make_gzipped_backup(daily)
    result = bhm.check_integrity(gz)
    assert result["ok"] is True
    assert result["result"] == "ok"


def test_integrity_corrupt_gz(tmp_path: Path) -> None:
    """Повреждённый .gz → fail (gunzip error)."""
    gz = tmp_path / "broken.bak.gz"
    gz.write_bytes(b"not a real gzip stream at all")
    result = bhm.check_integrity(gz)
    assert result["ok"] is False
    assert "reason" in result


def test_restoration_drill_disabled_by_default(backup_root: Path) -> None:
    """KRAB_BACKUP_DRILL_ENABLED не выставлен → skipped."""
    daily = backup_root / "2026-05-14"
    gz = _make_gzipped_backup(daily)
    # Гарантия что env unset.
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("KRAB_BACKUP_DRILL_ENABLED", None)
        result = bhm.check_restoration_drill(gz)
    assert result["ok"] is True
    assert result.get("skipped") is True


def test_restoration_drill_opt_in(backup_root: Path) -> None:
    """KRAB_BACKUP_DRILL_ENABLED=1 → drill пробегает и возвращает recovered_size."""
    daily = backup_root / "2026-05-14"
    gz = _make_gzipped_backup(daily, rows=20)
    with patch.dict(os.environ, {"KRAB_BACKUP_DRILL_ENABLED": "1"}):
        result = bhm.check_restoration_drill(gz)
    assert result["ok"] is True
    assert (
        result.get("skipped") is None or result.get("skipped") is False or "skipped" not in result
    )
    assert int(result.get("recovered_size", 0)) > 0


def test_run_health_check_full(backup_root: Path) -> None:
    """run_health_check end-to-end: создаём дневные backups, проверяем структуру отчёта."""
    for date, rows in [
        ("2026-05-14", 100),
        ("2026-05-13", 100),
        ("2026-05-12", 95),
    ]:
        _make_gzipped_backup(backup_root / date, rows=rows)

    result = bhm.run_health_check(root=backup_root)
    assert "ok" in result
    assert "checks" in result
    assert "failures" in result
    assert "timestamp" in result
    # Должно быть ровно 4 проверки.
    assert len(result["checks"]) == 4
    names = {c["name"] for c in result["checks"]}
    assert names == {"freshness", "size_variance", "integrity", "restoration_drill"}
    # freshness + integrity должны быть ok для свежих корректных backup'ов.
    fresh = next(c for c in result["checks"] if c["name"] == "freshness")
    integrity = next(c for c in result["checks"] if c["name"] == "integrity")
    assert fresh["ok"] is True
    assert integrity["ok"] is True


def test_set_backup_health_metric_updates_snapshot() -> None:
    """set_backup_health_metric изменяет snapshot для prom render."""
    bhm.set_backup_health_metric(True)
    assert bhm.get_backup_health_ok() == 1
    bhm.set_backup_health_metric(False)
    assert bhm.get_backup_health_ok() == 0
    bhm.set_backup_health_metric(True)
    assert bhm.get_backup_health_ok() == 1
