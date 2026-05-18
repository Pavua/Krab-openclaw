#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Krab backup retention preview tool (S66 W5).

Preview what krab_backup_retention_sweep.py would delete с given env params.
Doesn't modify env, doesn't execute deletes. Just shows summary.

Usage:
    python scripts/krab_backup_preview.py
    python scripts/krab_backup_preview.py --days 7 --keep-recent 3
    python scripts/krab_backup_preview.py --days 3 --json

Опирается на build_default_targets() из krab_backup_retention_sweep.py,
но переопределяет keep_recent + max_age_days CLI-аргументами (не env-mutating).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Добавляем scripts/ в sys.path, чтобы импортировать sibling-модуль.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import krab_backup_retention_sweep as sweep_mod  # noqa: E402

_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ── helpers ────────────────────────────────────────────────────────────────────


def _bytes_to_mb(num: int) -> float:
    return round(num / (1024 * 1024), 2)


def _dir_size_bytes(path: Path) -> int:
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


def _entry_size(entry: Path) -> int:
    try:
        if entry.is_file():
            return entry.stat().st_size
        return _dir_size_bytes(entry)
    except OSError:
        return 0


@dataclass
class CategoryPreview:
    name: str
    path: str
    exists: bool
    total_count: int = 0
    total_bytes: int = 0
    would_delete_count: int = 0
    would_delete_bytes: int = 0
    oldest_age_days: float | None = None


# ── ядро preview ───────────────────────────────────────────────────────────────


def preview_target(
    target: sweep_mod.RetentionTarget,
    *,
    keep_recent: int,
    max_age_days: int,
) -> CategoryPreview:
    """Симулирует политику retention для одной цели — без удалений."""
    cp = CategoryPreview(name=target.name, path=str(target.path), exists=target.path.exists())
    if not cp.exists:
        return cp

    # переопределяем поля target (preview не мутирует исходник, копию не делаем —
    # _collect_entries читает только path/entry_kind/name_filter).
    entries = sweep_mod._collect_entries(target)
    if not entries:
        return cp

    try:
        entries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return cp

    age_cutoff = time.time() - max_age_days * 86400
    now = time.time()
    oldest_mtime: float | None = None

    for idx, entry in enumerate(entries):
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue

        size = _entry_size(entry)
        cp.total_count += 1
        cp.total_bytes += size
        if oldest_mtime is None or mtime < oldest_mtime:
            oldest_mtime = mtime

        is_within_top_n = idx < keep_recent
        is_young_enough = mtime >= age_cutoff
        if is_within_top_n or is_young_enough:
            continue

        cp.would_delete_count += 1
        cp.would_delete_bytes += size

    if oldest_mtime is not None:
        cp.oldest_age_days = round((now - oldest_mtime) / 86400, 1)

    return cp


def run_preview(
    *,
    keep_recent: int,
    max_age_days: int,
    home: Path | None = None,
) -> dict:
    """Возвращает агрегированный preview по всем целям."""
    targets = sweep_mod.build_default_targets(home=home)
    # переопределяем политику для preview.
    for t in targets:
        t.keep_recent = keep_recent
        t.max_age_days = max_age_days

    cats: list[CategoryPreview] = [
        preview_target(t, keep_recent=keep_recent, max_age_days=max_age_days) for t in targets
    ]

    total_count = sum(c.total_count for c in cats)
    total_bytes = sum(c.total_bytes for c in cats)
    wd_count = sum(c.would_delete_count for c in cats)
    wd_bytes = sum(c.would_delete_bytes for c in cats)

    return {
        "ts": time.time(),
        "params": {"keep_recent": keep_recent, "max_age_days": max_age_days},
        "categories": [
            {
                "name": c.name,
                "path": c.path,
                "exists": c.exists,
                "total_count": c.total_count,
                "total_mb": _bytes_to_mb(c.total_bytes),
                "would_delete_count": c.would_delete_count,
                "would_delete_mb": _bytes_to_mb(c.would_delete_bytes),
                "oldest_age_days": c.oldest_age_days,
            }
            for c in cats
        ],
        "total": {
            "count": total_count,
            "total_mb": _bytes_to_mb(total_bytes),
            "would_delete_count": wd_count,
            "would_delete_mb": _bytes_to_mb(wd_bytes),
        },
    }


# ── CLI ────────────────────────────────────────────────────────────────────────


def _format_human(report: dict) -> str:
    lines: list[str] = []
    p = report["params"]
    lines.append(
        f"=== Backup retention preview [keep_recent={p['keep_recent']}, max_age_days={p['max_age_days']}] ==="
    )
    for c in report["categories"]:
        status = "ok" if c["exists"] else "missing"
        oldest = (
            f"oldest={c['oldest_age_days']}d" if c["oldest_age_days"] is not None else "oldest=-"
        )
        lines.append(
            f"  {c['name']:<24} [{status}] "
            f"count={c['total_count']:>3} ({c['total_mb']:.1f} MB)  "
            f"would_delete={c['would_delete_count']:>3} ({c['would_delete_mb']:.1f} MB)  "
            f"{oldest}"
        )
    t = report["total"]
    lines.append(
        f"--- Total: {t['count']} entries ({t['total_mb']:.1f} MB), "
        f"would delete {t['would_delete_count']} ({t['would_delete_mb']:.1f} MB freed) ---"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="S66 W5: preview retention policy без выполнения удалений",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=sweep_mod.DEFAULT_MAX_AGE_DAYS,
        help=f"max_age_days override (default {sweep_mod.DEFAULT_MAX_AGE_DAYS})",
    )
    parser.add_argument(
        "--keep-recent",
        type=int,
        default=sweep_mod.DEFAULT_KEEP_RECENT,
        help=f"keep_recent override (default {sweep_mod.DEFAULT_KEEP_RECENT})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="JSON output вместо человекочитаемого",
    )
    args = parser.parse_args(argv)

    if args.days < 0 or args.keep_recent < 0:
        print("ERROR: --days и --keep-recent должны быть >= 0", file=sys.stderr)
        return 2

    report = run_preview(keep_recent=args.keep_recent, max_age_days=args.days)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        print(_format_human(report))

    return 0


if __name__ == "__main__":
    sys.exit(main())
