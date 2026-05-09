# -*- coding: utf-8 -*-
"""Wave 49-F: тесты StateSnapshotManager."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from src.core.state_snapshots import (
    DEFAULT_INTERVAL_MINUTES,
    DEFAULT_KEEP_COUNT,
    STATE_FILES_TO_SNAPSHOT,
    StateSnapshotManager,
)


@pytest.fixture
def runtime_state_dir(tmp_path: Path) -> Path:
    """Изолированная runtime-state директория."""
    return tmp_path / "krab_runtime_state"


@pytest.fixture
def manager(runtime_state_dir: Path) -> StateSnapshotManager:
    """Менеджер с тестовой runtime директорией."""
    runtime_state_dir.mkdir(parents=True, exist_ok=True)
    return StateSnapshotManager(runtime_state_dir=runtime_state_dir)


def _seed_state_files(runtime_state_dir: Path, *, files: list[str] | None = None) -> dict[str, str]:
    """Создаёт фейковые state-файлы для теста, возвращает их content."""
    targets = files if files is not None else list(STATE_FILES_TO_SNAPSHOT)
    contents: dict[str, str] = {}
    for filename in targets:
        path = runtime_state_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        if filename.endswith(".jsonl"):
            content = '{"event":"test","file":"' + filename + '"}\n'
        else:
            content = json.dumps({"file": filename, "marker": "seed"}, ensure_ascii=False)
        path.write_text(content, encoding="utf-8")
        contents[filename] = content
    return contents


def test_snapshot_creates_directory_and_files(
    manager: StateSnapshotManager, runtime_state_dir: Path
) -> None:
    """snapshot_now создаёт snapshot-директорию и копирует все existing файлы."""
    contents = _seed_state_files(runtime_state_dir)
    result = manager.snapshot_now(reason="test")

    assert result["reason"] == "test"
    assert len(result["copied"]) == len(contents)
    assert result["skipped"] == []

    snap_dir = Path(result["path"])
    assert snap_dir.exists() and snap_dir.is_dir()
    for filename in contents:
        backup = snap_dir / f"{filename}.bak"
        assert backup.exists(), f"missing backup for {filename}"
        assert backup.read_text(encoding="utf-8") == contents[filename]


def test_snapshot_atomic_write(manager: StateSnapshotManager, runtime_state_dir: Path) -> None:
    """После snapshot не остаётся .tmp артефактов в snapshot-директории."""
    _seed_state_files(runtime_state_dir)
    result = manager.snapshot_now()
    snap_dir = Path(result["path"])
    leftover_tmps = [p for p in snap_dir.iterdir() if p.suffix == ".tmp"]
    assert leftover_tmps == [], f"tmp-файлы остались: {leftover_tmps}"


def test_snapshot_skips_missing_source_files(
    manager: StateSnapshotManager, runtime_state_dir: Path
) -> None:
    """Если source-файла нет — корректно пропускаем без падения."""
    # Только один из файлов.
    _seed_state_files(runtime_state_dir, files=["inbox_state.json"])
    result = manager.snapshot_now()
    assert len(result["copied"]) == 1
    assert len(result["skipped"]) == len(STATE_FILES_TO_SNAPSHOT) - 1
    assert "inbox_state.json" not in result["skipped"]


def test_list_snapshots_returns_sorted(
    manager: StateSnapshotManager, runtime_state_dir: Path
) -> None:
    """list_snapshots возвращает snapshots в reverse chronological order."""
    _seed_state_files(runtime_state_dir)
    first = manager.snapshot_now()
    # Гарантируем разные timestamps (compact ISO имеет посекундное разрешение).
    time.sleep(1.1)
    second = manager.snapshot_now()
    rows = manager.list_snapshots()
    assert len(rows) == 2
    # Новые первыми.
    assert rows[0]["timestamp"] == second["timestamp"]
    assert rows[1]["timestamp"] == first["timestamp"]


def test_cleanup_removes_old_beyond_keep_count(
    manager: StateSnapshotManager, runtime_state_dir: Path
) -> None:
    """cleanup_old удаляет snapshots сверх keep_count."""
    _seed_state_files(runtime_state_dir)

    # Создаём 5 snapshot-директорий с явными timestamps.
    snap_root = runtime_state_dir / "snapshots"
    for i in range(5):
        d = snap_root / f"2026010{i}T120000Z"
        d.mkdir(parents=True)
        (d / "inbox_state.json.bak").write_text("seed", encoding="utf-8")

    deleted = manager.cleanup_old(keep_count=2, max_age_days=999)
    assert deleted == 3
    remaining = sorted(p.name for p in snap_root.iterdir() if p.is_dir())
    # Остаются 2 самых новых (по reverse-сортировке timestamp).
    assert remaining == ["20260103T120000Z", "20260104T120000Z"]


def test_cleanup_removes_old_beyond_max_age(
    manager: StateSnapshotManager, runtime_state_dir: Path
) -> None:
    """cleanup_old удаляет snapshots старше max_age_days по mtime."""
    snap_root = runtime_state_dir / "snapshots"
    snap_root.mkdir(parents=True)

    # Старая директория — mtime 30 дней назад.
    old_dir = snap_root / "20260101T000000Z"
    old_dir.mkdir()
    (old_dir / "inbox_state.json.bak").write_text("old", encoding="utf-8")
    old_ts = time.time() - 30 * 86400
    os.utime(old_dir, (old_ts, old_ts))

    # Свежая директория — сегодня.
    fresh_dir = snap_root / "20260510T000000Z"
    fresh_dir.mkdir()
    (fresh_dir / "inbox_state.json.bak").write_text("fresh", encoding="utf-8")

    deleted = manager.cleanup_old(keep_count=999, max_age_days=7)
    assert deleted == 1
    assert not old_dir.exists()
    assert fresh_dir.exists()


def test_restore_overwrites_current_state(
    manager: StateSnapshotManager, runtime_state_dir: Path
) -> None:
    """restore возвращает state из snapshot обратно в runtime_state_dir."""
    _seed_state_files(runtime_state_dir)
    result = manager.snapshot_now()
    snap_ts = result["timestamp"]

    # Меняем текущее состояние ("corruption simulation").
    target = runtime_state_dir / "inbox_state.json"
    target.write_text('{"corrupted": true}', encoding="utf-8")
    assert "corrupted" in target.read_text(encoding="utf-8")

    # Restore.
    restore_result = manager.restore(snap_ts)
    assert "inbox_state.json" in restore_result["restored"]

    restored_content = target.read_text(encoding="utf-8")
    assert "corrupted" not in restored_content
    assert "seed" in restored_content


def test_restore_creates_backup_of_current_before_overwrite(
    manager: StateSnapshotManager, runtime_state_dir: Path
) -> None:
    """Перед restore текущий state бэкапится в _pre_restore_<ts>/."""
    _seed_state_files(runtime_state_dir)
    snap = manager.snapshot_now()

    # Подменяем содержимое — это пойдёт в pre-restore backup.
    target = runtime_state_dir / "inbox_state.json"
    target.write_text('{"current": "value"}', encoding="utf-8")

    restore_result = manager.restore(snap["timestamp"])
    pre_dir = Path(restore_result["pre_restore_backup"])
    assert pre_dir.exists()
    assert pre_dir.name.startswith("_pre_restore_")
    pre_backup = pre_dir / "inbox_state.json.bak"
    assert pre_backup.exists()
    assert '{"current": "value"}' in pre_backup.read_text(encoding="utf-8")


def test_scheduler_interval_configurable_via_env(
    manager: StateSnapshotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KRAB_STATE_SNAPSHOT_INTERVAL_MINUTES управляет cadence."""
    # Default — без env.
    monkeypatch.delenv("KRAB_STATE_SNAPSHOT_INTERVAL_MINUTES", raising=False)
    assert manager.interval_minutes == DEFAULT_INTERVAL_MINUTES

    # Override.
    monkeypatch.setenv("KRAB_STATE_SNAPSHOT_INTERVAL_MINUTES", "15")
    assert manager.interval_minutes == 15

    # Невалидное значение → fallback на default.
    monkeypatch.setenv("KRAB_STATE_SNAPSHOT_INTERVAL_MINUTES", "not-a-number")
    assert manager.interval_minutes == DEFAULT_INTERVAL_MINUTES

    # Минимальное — clamp до 1 мин.
    monkeypatch.setenv("KRAB_STATE_SNAPSHOT_INTERVAL_MINUTES", "0")
    assert manager.interval_minutes == 1


def test_restore_unknown_timestamp_raises(manager: StateSnapshotManager) -> None:
    """restore с несуществующим timestamp бросает FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        manager.restore("99999999T999999Z")


def test_cleanup_preserves_pre_restore_backups_within_age(
    manager: StateSnapshotManager, runtime_state_dir: Path
) -> None:
    """_pre_restore_ backups не подпадают под keep_count, только под max_age."""
    snap_root = runtime_state_dir / "snapshots"
    snap_root.mkdir(parents=True)

    # 3 обычных snapshot.
    for i in range(3):
        d = snap_root / f"2026020{i}T120000Z"
        d.mkdir()
        (d / "inbox_state.json.bak").write_text("x", encoding="utf-8")
    # 1 pre_restore (свежий).
    pre = snap_root / "_pre_restore_20260201T120000Z"
    pre.mkdir()
    (pre / "inbox_state.json.bak").write_text("y", encoding="utf-8")

    deleted = manager.cleanup_old(keep_count=1, max_age_days=999)
    # Удалятся только 2 обычных (из 3, оставляем 1). _pre_restore не трогаем.
    assert deleted == 2
    assert pre.exists()


def test_default_keep_count_constant() -> None:
    """Sanity check константы retention."""
    assert DEFAULT_KEEP_COUNT == 24
    assert DEFAULT_INTERVAL_MINUTES == 60
