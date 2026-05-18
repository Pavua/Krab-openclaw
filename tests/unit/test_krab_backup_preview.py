# -*- coding: utf-8 -*-
"""Тесты S66 W5: scripts/krab_backup_preview.py."""

from __future__ import annotations

import io
import json
import os
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import krab_backup_preview as preview_mod  # noqa: E402


def _make_file(path: Path, *, size: int = 1024, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _ts_days_ago(days: float) -> float:
    return time.time() - days * 86400


def _seed_home(tmp_path: Path) -> Path:
    """Создаёт ~/.openclaw + наполняет krab_memory/backups четырьмя файлами."""
    home = tmp_path
    backups_dir = home / ".openclaw" / "krab_memory" / "backups"
    # 5 файлов: 3 свежих (1, 2, 3 дня), 2 старых (20, 25 дней)
    _make_file(backups_dir / "archive-20260518.db", size=2048, mtime=_ts_days_ago(1))
    _make_file(backups_dir / "archive-20260517.db", size=2048, mtime=_ts_days_ago(2))
    _make_file(backups_dir / "archive-20260516.db", size=2048, mtime=_ts_days_ago(3))
    _make_file(backups_dir / "archive-20260428.db", size=2048, mtime=_ts_days_ago(20))
    _make_file(backups_dir / "archive-20260423.db", size=2048, mtime=_ts_days_ago(25))
    return home


def test_preview_default_args(tmp_path: Path) -> None:
    """Default keep_recent=3, max_age_days=14 → старые удаляются за пределами top-3."""
    home = _seed_home(tmp_path)
    report = preview_mod.run_preview(keep_recent=3, max_age_days=14, home=home)

    assert report["params"] == {"keep_recent": 3, "max_age_days": 14}
    cat = next(c for c in report["categories"] if c["name"] == "krab_memory_backups")
    assert cat["exists"] is True
    assert cat["total_count"] == 5
    # 2 файла старше 14 дней И не в top-3 keep_recent → would_delete=2
    assert cat["would_delete_count"] == 2
    assert cat["oldest_age_days"] is not None
    assert cat["oldest_age_days"] >= 24.0

    # total aggregate
    assert report["total"]["would_delete_count"] == 2


def test_preview_custom_days(tmp_path: Path) -> None:
    """--days 5 → файлы старше 5 дней (20d, 25d) попадают в would_delete."""
    home = _seed_home(tmp_path)
    # keep_recent=0 чтобы исключить top-N защиту
    report = preview_mod.run_preview(keep_recent=0, max_age_days=5, home=home)

    cat = next(c for c in report["categories"] if c["name"] == "krab_memory_backups")
    # Файлы 1d/2d/3d — внутри cutoff; 20d/25d — за пределами → would_delete=2
    assert cat["would_delete_count"] == 2


def test_preview_keep_recent_protection(tmp_path: Path) -> None:
    """keep_recent=5 защищает всё, даже самые старые."""
    home = _seed_home(tmp_path)
    report = preview_mod.run_preview(keep_recent=5, max_age_days=1, home=home)

    cat = next(c for c in report["categories"] if c["name"] == "krab_memory_backups")
    assert cat["total_count"] == 5
    # top-5 покрывает всё → нечего удалять
    assert cat["would_delete_count"] == 0
    assert cat["would_delete_mb"] == 0.0


def test_preview_json_output_structure(tmp_path: Path) -> None:
    """--json печатает валидный JSON с ожидаемыми полями."""
    home = _seed_home(tmp_path)
    # monkey-patch build_default_targets через run_preview(home=...) уже OK.
    # Дёргаем main() с --json, перехватываем stdout.
    # Чтобы main увидел наш home — патчим build_default_targets через подмену Path.home.
    orig_home = Path.home
    Path.home = staticmethod(lambda: home)  # type: ignore[assignment]
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = preview_mod.main(["--json", "--days", "14", "--keep-recent", "3"])
        assert rc == 0
        data = json.loads(buf.getvalue())
    finally:
        Path.home = orig_home  # type: ignore[assignment]

    assert data["params"] == {"keep_recent": 3, "max_age_days": 14}
    assert "categories" in data and isinstance(data["categories"], list)
    assert "total" in data
    for c in data["categories"]:
        assert {
            "name",
            "path",
            "exists",
            "total_count",
            "total_mb",
            "would_delete_count",
            "would_delete_mb",
            "oldest_age_days",
        } <= set(c.keys())
    assert {"count", "total_mb", "would_delete_count", "would_delete_mb"} <= set(
        data["total"].keys()
    )


def test_preview_handles_missing_backup_dir(tmp_path: Path) -> None:
    """Если папок ~/.openclaw/krab_memory/backups нет — graceful, exists=False."""
    home = tmp_path  # абсолютно пусто, даже .openclaw нет
    report = preview_mod.run_preview(keep_recent=3, max_age_days=7, home=home)

    # 4 категории всегда, все exists=False (т.к. ничего не создано)
    assert len(report["categories"]) == 4
    for c in report["categories"]:
        assert c["exists"] is False
        assert c["total_count"] == 0
        assert c["would_delete_count"] == 0
        assert c["oldest_age_days"] is None
    assert report["total"]["count"] == 0
    assert report["total"]["would_delete_count"] == 0


def test_preview_main_negative_args_returns_2(tmp_path: Path) -> None:
    """--days -1 → exit code 2."""
    rc = preview_mod.main(["--days", "-1"])
    assert rc == 2


def test_preview_human_output_smoke(tmp_path: Path) -> None:
    """Default (без --json) формат: заголовок + строки категорий + Total."""
    home = _seed_home(tmp_path)
    orig_home = Path.home
    Path.home = staticmethod(lambda: home)  # type: ignore[assignment]
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = preview_mod.main([])
        assert rc == 0
        out = buf.getvalue()
    finally:
        Path.home = orig_home  # type: ignore[assignment]

    assert "Backup retention preview" in out
    assert "krab_memory_backups" in out
    assert "Total:" in out
