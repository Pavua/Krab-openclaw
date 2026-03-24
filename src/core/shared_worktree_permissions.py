# -*- coding: utf-8 -*-
"""
shared_worktree_permissions.py — единая логика проверки и выравнивания прав shared worktree.

Что это:
- небольшой helper для multi-account режима, который умеет:
  1) находить owner-only файлы и каталоги в shared worktree;
  2) нормализовать права без `chown`, только через безопасный `chmod`.

Зачем:
- `Краб-active` публикуется как быстрый coding-root для USER2/USER3;
- если после публикации часть файлов остаётся с owner-only правами,
  следующая учётка ломается уже на `apply_patch` или записи docs/tests;
- эту truth лучше держать в одном модуле, чтобы publish/readiness/repair
  использовали одинаковые правила и не расходились по поведению.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any, Iterator


SKIP_DIR_NAMES = {
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "logs",
}

SCAN_ROOT_NAMES = (
    ".git",
    "docs",
    "scripts",
    "src",
    "tests",
)


def _relative_parts(root: Path, path: Path) -> tuple[str, ...]:
    """Возвращает относительные сегменты пути; на сбое отдаёт пустой tuple."""
    try:
        return path.relative_to(root).parts
    except ValueError:
        return ()


def _should_skip(root: Path, path: Path) -> bool:
    """Отсекает тяжёлые и нерелевантные каталоги, которые не нужны для coding loop."""
    parts = _relative_parts(root, path)
    if not parts:
        return False
    if any(part in SKIP_DIR_NAMES for part in parts):
        return True
    if parts[0] == "artifacts" and len(parts) > 1 and parts[1].startswith("handoff_"):
        return True
    return False


def _iter_scan_paths(root: Path) -> Iterator[Path]:
    """Обходит только те зоны shared worktree, которые реально важны для разработки."""
    if not root.exists():
        return

    yield root
    for child in sorted(root.iterdir(), key=lambda item: (item.name == ".git", item.name)):
        if _should_skip(root, child):
            continue
        if child.name in SCAN_ROOT_NAMES:
            yield child
            if child.is_dir():
                for path in child.rglob("*"):
                    if _should_skip(root, path):
                        continue
                    yield path
            continue
        if child.is_file() and child.suffix in {".command", ".md", ".json", ".py"}:
            yield child


def _iter_repair_paths(root: Path) -> Iterator[Path]:
    """
    Обходит только coding-critical зоны для remediation.

    Полное дерево здесь не нужно:
    - `temp/`, старые артефакты и случайные логи не влияют на coding loop;
    - слишком широкий chmod даёт шум и длинные отчёты без практической пользы;
    - для multi-account важнее починить `.git`, `docs`, `scripts`, `src`, `tests`
      и верхнеуровневые launcher/doc/json файлы.
    """
    yield from _iter_scan_paths(root)


def _permission_entry(path: Path) -> dict[str, Any]:
    """Снимает компактную structured-сводку по одному пути."""
    try:
        st = path.lstat()
    except OSError as exc:
        return {
            "path": str(path),
            "exists": False,
            "kind": "missing",
            "writable": False,
            "mode": "",
            "error": str(exc),
        }

    if stat.S_ISLNK(st.st_mode):
        kind = "symlink"
    elif stat.S_ISDIR(st.st_mode):
        kind = "dir"
    else:
        kind = "file"
    return {
        "path": str(path),
        "exists": True,
        "kind": kind,
        "writable": bool(os.access(path, os.W_OK)),
        "mode": stat.filemode(st.st_mode),
        "error": "",
    }


def scan_shared_worktree_writability(root: Path, *, limit: int = 25) -> dict[str, Any]:
    """Возвращает пригодный для readiness отчёт по owner-only хвостам внутри shared worktree."""
    root = Path(root)
    if not root.exists():
        return {
            "path": str(root),
            "exists": False,
            "checked_entries": 0,
            "non_writable_entries_count": 0,
            "non_writable_files_count": 0,
            "non_writable_dirs_count": 0,
            "sample_paths": [],
        }

    checked_entries = 0
    non_writable_entries_count = 0
    non_writable_files_count = 0
    non_writable_dirs_count = 0
    sample_paths: list[dict[str, Any]] = []

    for path in _iter_scan_paths(root):
        checked_entries += 1
        item = _permission_entry(path)
        if item["kind"] == "symlink":
            continue
        if item["writable"]:
            continue
        non_writable_entries_count += 1
        if item["kind"] == "dir":
            non_writable_dirs_count += 1
        elif item["kind"] == "file":
            non_writable_files_count += 1
        if len(sample_paths) < max(1, int(limit or 25)):
            sample_paths.append(item)

    return {
        "path": str(root),
        "exists": True,
        "checked_entries": checked_entries,
        "non_writable_entries_count": non_writable_entries_count,
        "non_writable_files_count": non_writable_files_count,
        "non_writable_dirs_count": non_writable_dirs_count,
        "sample_paths": sample_paths,
    }


def sample_non_writable_shared_items(root: Path, *, limit: int = 12) -> dict[str, Any]:
    """
    Возвращает компактный readiness-friendly payload.

    Этот alias нужен, чтобы launcher/readiness слой мог оперировать короткими
    ключами `non_writable_count` и `samples`, не таща весь подробный отчёт.
    """
    report = scan_shared_worktree_writability(root, limit=limit)
    samples: list[dict[str, Any]] = []
    for item in report.get("sample_paths") or []:
        if not isinstance(item, dict):
            continue
        raw_path = Path(str(item.get("path") or ""))
        try:
            relative_path = str(raw_path.relative_to(Path(root)))
        except ValueError:
            relative_path = str(raw_path)
        samples.append(
            {
                **item,
                "relative_path": relative_path,
            }
        )
    return {
        "path": str(root),
        "exists": bool(report.get("exists")),
        "checked_entries": int(report.get("checked_entries") or 0),
        "non_writable_count": int(report.get("non_writable_entries_count") or 0),
        "non_writable_files_count": int(report.get("non_writable_files_count") or 0),
        "non_writable_dirs_count": int(report.get("non_writable_dirs_count") or 0),
        "samples": samples,
    }


def _normalized_mode_for_dir(current_mode: int) -> int:
    """Делает каталог group-writable и наследующим общую группу."""
    return current_mode | stat.S_IRGRP | stat.S_IWGRP | stat.S_IXGRP | stat.S_ISGID


def _normalized_mode_for_file(current_mode: int) -> int:
    """Делает файл group-writable, сохраняя текущую исполняемость."""
    desired = current_mode | stat.S_IRGRP | stat.S_IWGRP
    if current_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        desired |= stat.S_IXGRP
    return desired


def normalize_shared_worktree_permissions(root: Path, *, dry_run: bool = False, limit: int = 40) -> dict[str, Any]:
    """
    Нормализует права shared worktree без смены владельца.

    Почему именно так:
    - `chown` между учётками опасен и не нужен;
    - нам достаточно гарантировать group-write + setgid для каталога, чтобы
      следующие файлы рождались в общей группе и обе учётки могли писать.
    """
    root = Path(root)
    if not root.exists():
        return {
            "ok": False,
            "path": str(root),
            "exists": False,
            "dry_run": dry_run,
            "changed_count": 0,
            "error_count": 0,
            "changed_paths": [],
            "errors": [],
        }

    changed_count = 0
    error_count = 0
    skipped_foreign_owner_count = 0
    changed_paths: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    skipped_foreign_owner_paths: list[dict[str, str]] = []
    current_uid = os.getuid()

    for path in _iter_repair_paths(root):
        try:
            st = path.lstat()
        except OSError as exc:
            error_count += 1
            if len(errors) < limit:
                errors.append({"path": str(path), "error": str(exc)})
            continue

        if stat.S_ISLNK(st.st_mode):
            continue

        current_mode = stat.S_IMODE(st.st_mode)
        if st.st_uid != current_uid:
            skipped_foreign_owner_count += 1
            if len(skipped_foreign_owner_paths) < limit:
                skipped_foreign_owner_paths.append(
                    {
                        "path": str(path),
                        "owner_uid": str(st.st_uid),
                    }
                )
            continue
        desired_mode = _normalized_mode_for_dir(current_mode) if stat.S_ISDIR(st.st_mode) else _normalized_mode_for_file(current_mode)
        if desired_mode == current_mode:
            continue

        if not dry_run:
            try:
                os.chmod(path, desired_mode)
            except OSError as exc:
                error_count += 1
                if len(errors) < limit:
                    errors.append({"path": str(path), "error": str(exc)})
                continue

        changed_count += 1
        if len(changed_paths) < limit:
            changed_paths.append(
                {
                    "path": str(path),
                    "kind": "dir" if stat.S_ISDIR(st.st_mode) else "file",
                    "before": stat.filemode(st.st_mode),
                    "after": stat.filemode(desired_mode),
                }
            )

    return {
        "ok": error_count == 0 and skipped_foreign_owner_count == 0,
        "path": str(root),
        "exists": True,
        "dry_run": dry_run,
        "changed_count": changed_count,
        "error_count": error_count,
        "skipped_foreign_owner_count": skipped_foreign_owner_count,
        "changed_paths": changed_paths,
        "errors": errors,
        "skipped_foreign_owner_paths": skipped_foreign_owner_paths,
    }


__all__ = [
    "normalize_shared_worktree_permissions",
    "sample_non_writable_shared_items",
    "scan_shared_worktree_writability",
]
