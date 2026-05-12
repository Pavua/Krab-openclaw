#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wave 172: backup retention sweep.

Обобщённый sweeper для трёх backup-папок, которые накапливались без retention
до Wave 171 (8 ГБ освободили вручную):

1. ~/.openclaw/krab_memory/backups/         — daily archive.db snapshots
   (от ai.krab.db-backup-daily через krab_db_backup.py копия)
   Files: archive-YYYYMMDD.db (~500 МБ каждый)
   Policy: keep_recent=3, max_age_days=7

2. ~/.openclaw/backups/workspace/           — daily workspace tarballs
   (от workspace_backup.sh)
   Files: workspace_YYYYMMDD_HHMMSS.tar.gz (~250 МБ каждый)
   Policy: keep_recent=3, max_age_days=14

3. ~/.openclaw/backups/YYYY-MM-DD/          — daily DB-set каталоги
   (от krab_db_backup.py)
   Dirs: 2026-MM-DD/ (~200 МБ каждый)
   Policy: keep_recent=3, max_age_days=14

Дополняет Wave 18-A (cleanup_old_backups для session-backups).

CLI:
  python scripts/krab_backup_retention_sweep.py --dry-run
  python scripts/krab_backup_retention_sweep.py            # реальный sweep
  python scripts/krab_backup_retention_sweep.py --json     # JSON-отчёт

Env overrides:
  KRAB_BACKUP_RETENTION_KEEP_RECENT (default per-target)
  KRAB_BACKUP_RETENTION_MAX_AGE_DAYS (default per-target)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# Добавляем корень репозитория в sys.path для импорта src.*
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.core.logger import get_logger  # noqa: E402

logger = get_logger(__name__)


# ── Константы по умолчанию ─────────────────────────────────────────────────────

DEFAULT_KEEP_RECENT = 3
DEFAULT_MAX_AGE_DAYS = 14

# Регексп для каталогов вида 2026-05-12 (ISO date).
_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ── Конфигурация одной цели sweep'а ────────────────────────────────────────────


@dataclass
class RetentionTarget:
    """Описание одной папки + политики retention."""

    name: str
    path: Path
    keep_recent: int
    max_age_days: int
    # Какой тип элементов смотрим: "file" — обычные файлы; "dir" — каталоги
    # с именем YYYY-MM-DD.
    entry_kind: str
    # Опциональный фильтр имени (для files).
    name_filter: Callable[[str], bool] | None = None


@dataclass
class TargetReport:
    """Отчёт по одной цели."""

    name: str
    path: str
    kind: str
    exists: bool
    keep_recent: int
    max_age_days: int
    removed: list[dict] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    bytes_freed: int = 0
    dry_run: bool = False
    error: str | None = None


# ── Helpers ────────────────────────────────────────────────────────────────────


def _bytes_to_mb(num: int) -> float:
    return round(num / (1024 * 1024), 2)


def _dir_size_bytes(path: Path) -> int:
    """Рекурсивно считает размер каталога. Тихо игнорит OSError."""
    total = 0
    try:
        for entry in path.rglob("*"):
            try:
                if entry.is_file():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


def _env_int(name: str, default: int) -> int:
    """Безопасно читает целое из env. Возвращает default при некорректном значении."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
        return value if value >= 0 else default
    except ValueError:
        return default


# ── Сборка целей по умолчанию ─────────────────────────────────────────────────


def build_default_targets(home: Path | None = None) -> list[RetentionTarget]:
    """Базовая конфигурация: три цели, описанные в docstring модуля."""
    home = home or Path.home()
    openclaw = home / ".openclaw"

    # Env-overrides применяются к ВСЕМ целям — но max_age_days для DB snapshots
    # принудительно остаётся коротким (7) если override не задан, потому что
    # каждый файл ~500 МБ.
    env_keep = _env_int("KRAB_BACKUP_RETENTION_KEEP_RECENT", DEFAULT_KEEP_RECENT)
    env_age_set = "KRAB_BACKUP_RETENTION_MAX_AGE_DAYS" in os.environ
    env_age = _env_int("KRAB_BACKUP_RETENTION_MAX_AGE_DAYS", DEFAULT_MAX_AGE_DAYS)

    return [
        RetentionTarget(
            name="krab_memory_backups",
            path=openclaw / "krab_memory" / "backups",
            keep_recent=env_keep,
            # Для огромных DB snapshots: 7 дней по умолчанию (тяжелее workspace).
            max_age_days=env_age if env_age_set else 7,
            entry_kind="file",
            # archive-YYYYMMDD.db
            name_filter=lambda n: n.startswith("archive-") and n.endswith(".db"),
        ),
        RetentionTarget(
            name="workspace_tarballs",
            path=openclaw / "backups" / "workspace",
            keep_recent=env_keep,
            max_age_days=env_age,
            entry_kind="file",
            # workspace_YYYYMMDD_HHMMSS.tar.gz
            name_filter=lambda n: n.startswith("workspace_") and n.endswith(".tar.gz"),
        ),
        RetentionTarget(
            name="dated_backup_dirs",
            path=openclaw / "backups",
            keep_recent=env_keep,
            max_age_days=env_age,
            entry_kind="dir",
        ),
    ]


# ── Ядро sweep'а ───────────────────────────────────────────────────────────────


def _collect_entries(target: RetentionTarget) -> list[Path]:
    """Собирает кандидаты для retention из target.path."""
    if not target.path.exists() or not target.path.is_dir():
        return []

    entries: list[Path] = []
    for p in target.path.iterdir():
        if target.entry_kind == "file":
            if not p.is_file():
                continue
            if target.name_filter is not None and not target.name_filter(p.name):
                continue
        elif target.entry_kind == "dir":
            if not p.is_dir():
                continue
            # Только каталоги вида YYYY-MM-DD (не подметаем "workspace" или другие).
            if not _DATE_DIR_RE.match(p.name):
                continue
        else:
            continue
        entries.append(p)

    return entries


def sweep_target(target: RetentionTarget, *, dry_run: bool) -> TargetReport:
    """Применяет retention к одной цели. Безопасно для несуществующих папок."""
    report = TargetReport(
        name=target.name,
        path=str(target.path),
        kind=target.entry_kind,
        exists=target.path.exists(),
        keep_recent=target.keep_recent,
        max_age_days=target.max_age_days,
        dry_run=dry_run,
    )

    if not report.exists:
        # Graceful: папка ещё не создана — это не ошибка.
        logger.info(
            "backup_retention_target_missing",
            target=target.name,
            path=str(target.path),
        )
        return report

    try:
        entries = _collect_entries(target)
    except OSError as exc:
        report.error = f"collect_failed: {exc}"
        logger.error(
            "backup_retention_collect_failed",
            target=target.name,
            error=str(exc),
        )
        return report

    if not entries:
        logger.info(
            "backup_retention_empty",
            target=target.name,
            path=str(target.path),
        )
        return report

    # Сортируем по mtime descending — свежие сверху.
    try:
        entries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError as exc:
        report.error = f"sort_failed: {exc}"
        return report

    age_cutoff = time.time() - target.max_age_days * 86400

    for idx, entry in enumerate(entries):
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            # Файл/папка исчез между итерациями — пропускаем.
            continue

        is_within_top_n = idx < target.keep_recent
        is_young_enough = mtime >= age_cutoff

        if is_within_top_n or is_young_enough:
            report.kept.append(str(entry))
            continue

        # Кандидат на удаление: считаем размер ПЕРЕД удалением.
        try:
            if entry.is_file():
                size = entry.stat().st_size
            else:
                size = _dir_size_bytes(entry)
        except OSError as exc:
            logger.warning(
                "backup_retention_stat_failed",
                path=str(entry),
                error=str(exc),
            )
            continue

        size_mb = _bytes_to_mb(size)
        logger.info(
            "backup_retention_delete",
            target=target.name,
            path=str(entry),
            size_mb=size_mb,
            age_days=round((time.time() - mtime) / 86400, 1),
            dry_run=dry_run,
        )

        if not dry_run:
            try:
                if entry.is_file():
                    entry.unlink()
                else:
                    shutil.rmtree(entry)
            except OSError as exc:
                logger.error(
                    "backup_retention_delete_failed",
                    path=str(entry),
                    error=str(exc),
                )
                continue

        report.removed.append({"path": str(entry), "size_mb": size_mb})
        report.bytes_freed += size

    if report.removed or report.kept:
        logger.info(
            "backup_retention_target_done",
            target=target.name,
            removed=len(report.removed),
            kept=len(report.kept),
            bytes_freed_mb=_bytes_to_mb(report.bytes_freed),
            dry_run=dry_run,
        )

    return report


def run_sweep(
    targets: list[RetentionTarget] | None = None, *, dry_run: bool = False
) -> dict:
    """Запускает sweep по всем целям, возвращает агрегированный отчёт."""
    targets = targets or build_default_targets()

    target_reports: list[TargetReport] = []
    total_removed = 0
    total_bytes = 0

    for target in targets:
        rep = sweep_target(target, dry_run=dry_run)
        target_reports.append(rep)
        total_removed += len(rep.removed)
        total_bytes += rep.bytes_freed

    summary = {
        "ts": time.time(),
        "dry_run": dry_run,
        "targets": [
            {
                "name": r.name,
                "path": r.path,
                "kind": r.kind,
                "exists": r.exists,
                "keep_recent": r.keep_recent,
                "max_age_days": r.max_age_days,
                "removed_count": len(r.removed),
                "kept_count": len(r.kept),
                "bytes_freed": r.bytes_freed,
                "bytes_freed_mb": _bytes_to_mb(r.bytes_freed),
                "removed": r.removed,
                "error": r.error,
            }
            for r in target_reports
        ],
        "total_removed": total_removed,
        "total_bytes_freed": total_bytes,
        "total_bytes_freed_mb": _bytes_to_mb(total_bytes),
    }

    logger.info(
        "backup_retention_sweep_done",
        total_removed=total_removed,
        total_mb=summary["total_bytes_freed_mb"],
        dry_run=dry_run,
    )

    return summary


# ── CLI ────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Wave 172: retention sweep для krab_memory/backups + workspace + dated dirs",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только анализ, без удаления",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Печатать JSON-отчёт (вместо человекочитаемого)",
    )
    args = parser.parse_args(argv)

    summary = run_sweep(dry_run=args.dry_run)

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    else:
        mode = "DRY-RUN" if args.dry_run else "LIVE"
        print(f"=== Backup retention sweep [{mode}] ===")
        for t in summary["targets"]:
            status = "ok" if t["exists"] else "missing"
            err = f"  ERROR: {t['error']}" if t["error"] else ""
            print(
                f"  {t['name']:<24} [{status}] removed={t['removed_count']:>3} "
                f"kept={t['kept_count']:>3} freed={t['bytes_freed_mb']:.1f} MB"
                f"{err}"
            )
            for r in t["removed"]:
                print(f"      - {r['path']}  ({r['size_mb']:.1f} MB)")
        print(
            f"--- Total: removed {summary['total_removed']} entries, "
            f"freed {summary['total_bytes_freed_mb']:.1f} MB ---"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
