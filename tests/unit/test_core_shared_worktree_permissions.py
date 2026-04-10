# -*- coding: utf-8 -*-
"""
Тесты для src/core/shared_worktree_permissions.py.

Покрывает:
- scan_shared_worktree_writability: несуществующий root, пустой root, файлы с ограниченными правами
- normalize_shared_worktree_permissions: dry_run, реальный chmod, пропуск чужих владельцев,
  пропуск symlink'ов, папка не существует
- sample_non_writable_shared_items: формат ответа, relative_path
- вспомогательные функции: _should_skip, _normalized_mode_for_dir, _normalized_mode_for_file
"""

from __future__ import annotations

import stat
from pathlib import Path
from unittest.mock import patch

from src.core.shared_worktree_permissions import (
    _normalized_mode_for_dir,
    _normalized_mode_for_file,
    _should_skip,
    normalize_shared_worktree_permissions,
    sample_non_writable_shared_items,
    scan_shared_worktree_writability,
)

# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


class TestShouldSkip:
    """Проверяем логику исключения нерелевантных путей."""

    def test_venv_dir_skipped(self, tmp_path):
        """Каталог venv должен быть исключён из обхода."""
        venv = tmp_path / "venv" / "lib" / "python3.13"
        venv.mkdir(parents=True)
        assert _should_skip(tmp_path, venv) is True

    def test_pycache_skipped(self, tmp_path):
        """__pycache__ в src тоже пропускается."""
        cache = tmp_path / "src" / "__pycache__"
        cache.mkdir(parents=True)
        assert _should_skip(tmp_path, cache) is True

    def test_relevant_src_file_not_skipped(self, tmp_path):
        """Файл в src/ не должен исключаться."""
        py_file = tmp_path / "src" / "module.py"
        py_file.parent.mkdir(parents=True)
        py_file.touch()
        assert _should_skip(tmp_path, py_file) is False

    def test_handoff_artifacts_skipped(self, tmp_path):
        """artifacts/handoff_* исключаются как тяжёлые архивы."""
        handoff = tmp_path / "artifacts" / "handoff_2026_01_01" / "README.md"
        handoff.parent.mkdir(parents=True)
        handoff.touch()
        assert _should_skip(tmp_path, handoff) is True

    def test_path_outside_root_not_skipped(self, tmp_path):
        """Путь вне root возвращает False (нет частей для анализа)."""
        outside = Path("/tmp/some_random_dir")
        assert _should_skip(tmp_path, outside) is False


class TestNormalizedModes:
    """Проверяем корректность вычисления нормализованных режимов прав."""

    def test_dir_gets_group_write_and_sgid(self):
        """Каталог должен получить group-write и setgid."""
        base = 0o755  # drwxr-xr-x
        result = _normalized_mode_for_dir(base)
        assert result & stat.S_IWGRP, "group-write должен быть выставлен"
        assert result & stat.S_ISGID, "setgid должен быть выставлен"

    def test_file_gets_group_write(self):
        """Файл должен получить group-read и group-write."""
        base = 0o644  # rw-r--r--
        result = _normalized_mode_for_file(base)
        assert result & stat.S_IRGRP
        assert result & stat.S_IWGRP

    def test_executable_file_keeps_group_exec(self):
        """Исполняемый файл должен получить group-execute."""
        base = 0o755  # rwxr-xr-x
        result = _normalized_mode_for_file(base)
        assert result & stat.S_IXGRP, "group-execute должен сохраниться"

    def test_non_executable_file_no_group_exec(self):
        """Неисполняемый файл не должен получить group-execute."""
        base = 0o644
        result = _normalized_mode_for_file(base)
        assert not (result & stat.S_IXGRP), "group-execute не должен выставляться"


# ---------------------------------------------------------------------------
# scan_shared_worktree_writability
# ---------------------------------------------------------------------------


class TestScanSharedWorktreeWritability:
    """Проверяем readiness-отчёт по правам доступа."""

    def test_nonexistent_root_returns_exists_false(self, tmp_path):
        """Несуществующий root возвращает exists=False без ошибки."""
        report = scan_shared_worktree_writability(tmp_path / "nonexistent")
        assert report["exists"] is False
        assert report["checked_entries"] == 0
        assert report["sample_paths"] == []

    def test_empty_root_all_writable(self, tmp_path):
        """Пустая (и доступная для записи) директория: нет не-writable записей."""
        report = scan_shared_worktree_writability(tmp_path)
        assert report["exists"] is True
        assert report["non_writable_entries_count"] == 0

    def test_detects_read_only_file(self, tmp_path):
        """Файл с правами 0o444 должен появиться в non_writable."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        ro_file = src_dir / "locked.py"
        ro_file.write_text("# locked", encoding="utf-8")
        ro_file.chmod(0o444)

        try:
            report = scan_shared_worktree_writability(tmp_path)
            assert report["non_writable_files_count"] >= 1
            paths = [item["path"] for item in report["sample_paths"]]
            assert str(ro_file) in paths
        finally:
            ro_file.chmod(0o644)  # восстанавливаем для очистки

    def test_limit_caps_sample_paths(self, tmp_path):
        """Параметр limit ограничивает количество записей в sample_paths."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        ro_files = []
        for i in range(5):
            f = src_dir / f"ro_{i}.py"
            f.write_text("# ro", encoding="utf-8")
            f.chmod(0o444)
            ro_files.append(f)

        try:
            report = scan_shared_worktree_writability(tmp_path, limit=2)
            assert len(report["sample_paths"]) <= 2
        finally:
            for f in ro_files:
                f.chmod(0o644)


# ---------------------------------------------------------------------------
# sample_non_writable_shared_items
# ---------------------------------------------------------------------------


class TestSampleNonWritableSharedItems:
    """Проверяем alias-обёртку с relative_path."""

    def test_nonexistent_returns_zero_count(self, tmp_path):
        """Несуществующий root: non_writable_count = 0, exists = False."""
        result = sample_non_writable_shared_items(tmp_path / "ghost")
        assert result["exists"] is False
        assert result["non_writable_count"] == 0
        assert result["samples"] == []

    def test_relative_path_computed_correctly(self, tmp_path):
        """samples содержат relative_path относительно root."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        ro = src_dir / "check.py"
        ro.write_text("# chk", encoding="utf-8")
        ro.chmod(0o444)

        try:
            result = sample_non_writable_shared_items(tmp_path)
            assert result["non_writable_count"] >= 1
            rel_paths = [s["relative_path"] for s in result["samples"]]
            assert any("check.py" in rp for rp in rel_paths)
        finally:
            ro.chmod(0o644)


# ---------------------------------------------------------------------------
# normalize_shared_worktree_permissions
# ---------------------------------------------------------------------------


class TestNormalizeSharedWorktreePermissions:
    """Проверяем нормализацию прав shared worktree."""

    def test_nonexistent_root_returns_ok_false(self, tmp_path):
        """Несуществующий root: ok=False, exists=False."""
        result = normalize_shared_worktree_permissions(tmp_path / "nowhere")
        assert result["ok"] is False
        assert result["exists"] is False
        assert result["changed_count"] == 0

    def test_dry_run_does_not_change_file(self, tmp_path):
        """dry_run=True: нет реального chmod, но changed_count > 0 если нужно."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        f = src_dir / "mod.py"
        f.write_text("# mod", encoding="utf-8")
        # owner-only без group-write
        f.chmod(0o600)

        try:
            result = normalize_shared_worktree_permissions(tmp_path, dry_run=True)
            # В dry_run changed_count считается, но файл реально не меняется
            assert result["dry_run"] is True
            assert result["changed_count"] >= 1
            # Реальные права не изменились
            assert stat.S_IMODE(f.stat().st_mode) == 0o600
        finally:
            f.chmod(0o644)

    def test_real_run_applies_chmod(self, tmp_path):
        """Реальный прогон добавляет group-write к файлу без group-write."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        f = src_dir / "writable.py"
        f.write_text("# writable", encoding="utf-8")
        f.chmod(0o600)  # owner-only

        result = normalize_shared_worktree_permissions(tmp_path, dry_run=False)
        assert result["changed_count"] >= 1
        new_mode = stat.S_IMODE(f.stat().st_mode)
        assert new_mode & stat.S_IWGRP, "после нормализации файл должен быть group-writable"

    def test_skips_foreign_owner(self, tmp_path):
        """Файл с чужим uid пропускается, skipped_foreign_owner_count > 0."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        f = src_dir / "foreign.py"
        f.write_text("# foreign", encoding="utf-8")

        with patch("src.core.shared_worktree_permissions.os.getuid", return_value=0):
            # uid владельца файла != 0 (мок), файл будет считаться чужим
            result = normalize_shared_worktree_permissions(tmp_path, dry_run=True)

        assert result["skipped_foreign_owner_count"] >= 1

    def test_already_normalized_file_no_change(self, tmp_path):
        """Файл уже с group-write не считается изменённым."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        f = src_dir / "already_ok.py"
        f.write_text("# ok", encoding="utf-8")
        # Выставляем уже нужные права: rw-rw-r--
        f.chmod(0o664)

        result = normalize_shared_worktree_permissions(tmp_path, dry_run=False)
        # Файл already_ok.py не должен попасть в changed_paths
        changed = [e["path"] for e in result["changed_paths"]]
        assert str(f) not in changed
