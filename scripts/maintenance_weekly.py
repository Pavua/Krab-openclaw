#!/usr/bin/env python3
"""
Weekly maintenance для Krab runtime.

Operations:
1. archive.db VACUUM — reclaim space после DELETE
2. krab_main.log rotation — gzip старый + truncate (keep last 10MB)
3. search_cache.db + history_cache.db — VACUUM
4. /tmp/krab-chrome — purge older-than-7d (dedicated Chrome cache)

Usage:
    python scripts/maintenance_weekly.py           # dry-run preview
    python scripts/maintenance_weekly.py --execute # actual run

Можно добавить в cron:
    0 3 * * 0  /path/to/venv/bin/python /path/to/scripts/maintenance_weekly.py --execute
"""

from __future__ import annotations

import argparse
import gzip
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

ARCHIVE_DB = Path("~/.openclaw/krab_memory/archive.db").expanduser()
SEARCH_CACHE_DB = Path("~/.openclaw/krab_runtime_state/search_cache.db").expanduser()
HISTORY_CACHE_DB = Path("~/.openclaw/krab_runtime_state/history_cache.db").expanduser()
KRAB_LOG = Path("~/.openclaw/krab_runtime_state/krab_main.log").expanduser()
DEDICATED_CHROME_DIR = Path("/tmp/krab-chrome")
LOG_MAX_SIZE_MB = 10
LOG_KEEP_ROTATIONS = 5


def vacuum_sqlite(path: Path, *, dry_run: bool = True) -> dict:
    """VACUUM SQLite DB, reclaim pages after DELETE ops."""
    if not path.exists():
        return {"path": str(path), "skipped": "not_exists"}
    size_before = path.stat().st_size
    if dry_run:
        return {
            "path": str(path),
            "size_before_mb": round(size_before / 1024 / 1024, 2),
            "dry_run": True,
        }
    try:
        conn = sqlite3.connect(str(path))
        conn.execute("VACUUM")
        conn.close()
    except Exception as e:  # noqa: BLE001 — best-effort maintenance
        return {"path": str(path), "error": str(e)}
    size_after = path.stat().st_size
    return {
        "path": str(path),
        "size_before_mb": round(size_before / 1024 / 1024, 2),
        "size_after_mb": round(size_after / 1024 / 1024, 2),
        "reclaimed_mb": round((size_before - size_after) / 1024 / 1024, 2),
    }


def rotate_log(path: Path, max_mb: int = LOG_MAX_SIZE_MB, *, dry_run: bool = True) -> dict:
    """Rotate log if оно больше max_mb — gzip + truncate original."""
    if not path.exists():
        return {"path": str(path), "skipped": "not_exists"}
    size_mb = path.stat().st_size / 1024 / 1024
    if size_mb < max_mb:
        return {
            "path": str(path),
            "size_mb": round(size_mb, 2),
            "skipped": "under_threshold",
        }

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    gz_path = path.parent / f"{path.name}.{ts}.gz"

    if dry_run:
        return {
            "path": str(path),
            "size_mb": round(size_mb, 2),
            "would_rotate_to": str(gz_path),
        }

    try:
        # Gzip copy
        with open(path, "rb") as f_in:
            with gzip.open(gz_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        # Truncate original (keep file open by process writers)
        with open(path, "w"):
            pass
    except Exception as e:  # noqa: BLE001
        return {"path": str(path), "error": str(e)}

    # Cleanup old rotations beyond LOG_KEEP_ROTATIONS
    old_rotations = sorted(
        path.parent.glob(f"{path.name}.*.gz"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for old in old_rotations[LOG_KEEP_ROTATIONS:]:
        try:
            old.unlink()
            removed += 1
        except Exception:  # noqa: BLE001
            pass

    return {
        "path": str(path),
        "size_before_mb": round(size_mb, 2),
        "rotated_to": str(gz_path),
        "old_rotations_removed": removed,
    }


def purge_dedicated_chrome(max_age_days: int = 7, *, dry_run: bool = True) -> dict:
    """Purge files older than max_age_days из dedicated Chrome cache."""
    if not DEDICATED_CHROME_DIR.exists():
        return {"path": str(DEDICATED_CHROME_DIR), "skipped": "not_exists"}

    cutoff = time.time() - max_age_days * 86400
    total_size = 0
    candidates: list[Path] = []
    for p in DEDICATED_CHROME_DIR.rglob("*"):
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                candidates.append(p)
                total_size += p.stat().st_size
        except Exception:  # noqa: BLE001
            pass

    if dry_run:
        return {
            "path": str(DEDICATED_CHROME_DIR),
            "candidates_count": len(candidates),
            "total_size_mb": round(total_size / 1024 / 1024, 2),
            "dry_run": True,
        }

    deleted = 0
    for p in candidates:
        try:
            p.unlink()
            deleted += 1
        except Exception:  # noqa: BLE001
            pass
    return {
        "path": str(DEDICATED_CHROME_DIR),
        "deleted": deleted,
        "freed_mb": round(total_size / 1024 / 1024, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Krab weekly maintenance")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually run operations (default: dry-run)",
    )
    args = parser.parse_args()
    dry = not args.execute

    print(f"Krab Weekly Maintenance ({'DRY-RUN' if dry else 'EXECUTE'})")
    print(f"Time: {datetime.now().isoformat(timespec='seconds')}\n")

    print("1. VACUUM archive.db...")
    print("  ", vacuum_sqlite(ARCHIVE_DB, dry_run=dry))

    print("\n2. VACUUM search_cache.db...")
    print("  ", vacuum_sqlite(SEARCH_CACHE_DB, dry_run=dry))

    print("\n3. VACUUM history_cache.db...")
    print("  ", vacuum_sqlite(HISTORY_CACHE_DB, dry_run=dry))

    print(f"\n4. Rotate krab_main.log (>{LOG_MAX_SIZE_MB}MB)...")
    print("  ", rotate_log(KRAB_LOG, LOG_MAX_SIZE_MB, dry_run=dry))

    print("\n5. Purge dedicated Chrome cache (>7d)...")
    print("  ", purge_dedicated_chrome(7, dry_run=dry))

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
