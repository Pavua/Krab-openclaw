#!/usr/bin/env python3
"""
archive.db VACUUM + log rotation automation.

Комплементарно к scripts/maintenance_weekly.py (Wave 21-C):
- Pre/post vacuum size + integrity_check (PRAGMA integrity_check)
- VACUUM archive.db с замером reclaimed space
- Rotation больших файлов logs/*.log (>100MB) → logs/archive/<stem>_<ts>.log.gz
  (основной krab_main.log ротируется maintenance_weekly.py в ~/.openclaw/...)

Usage:
    python scripts/archive_maintenance.py [--dry-run] [--skip-vacuum] [--skip-logs]
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

# Пути runtime (override из тестов через monkeypatch модуля)
ARCHIVE_DB = Path.home() / ".openclaw" / "krab_memory" / "archive.db"
LOGS_DIR = Path("/Users/pablito/Antigravity_AGENTS/Краб/logs")
ROTATED_DIR = LOGS_DIR / "archive"
MAX_LOG_SIZE_MB = 100


def human_size(bytes_: float) -> str:
    """Человекочитаемый размер файла."""
    value = float(bytes_)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def vacuum_archive(dry_run: bool = False) -> dict:
    """VACUUM archive.db с integrity_check и замером reclaimed."""
    db_path = ARCHIVE_DB
    if not db_path.exists():
        return {"status": "missing", "db_path": str(db_path)}

    before_size = db_path.stat().st_size

    conn = sqlite3.connect(str(db_path))
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            return {
                "status": "integrity_failed",
                "message": str(integrity),
                "db_path": str(db_path),
            }

        if dry_run:
            return {
                "status": "dry_run",
                "before_size": before_size,
                "before_human": human_size(before_size),
                "integrity": integrity,
            }

        t0 = time.monotonic()
        conn.execute("VACUUM")
        elapsed = time.monotonic() - t0
    except sqlite3.DatabaseError as exc:
        return {
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    finally:
        conn.close()

    after_size = db_path.stat().st_size
    reclaimed = before_size - after_size
    return {
        "status": "ok",
        "before_size": before_size,
        "before_human": human_size(before_size),
        "after_size": after_size,
        "after_human": human_size(after_size),
        "reclaimed_bytes": reclaimed,
        "reclaimed_human": human_size(reclaimed),
        "elapsed_sec": round(elapsed, 2),
    }


def rotate_logs(dry_run: bool = False) -> list[dict]:
    """Ротация logs/*.log > MAX_LOG_SIZE_MB в logs/archive/<stem>_<ts>.log.gz."""
    results: list[dict] = []
    if not LOGS_DIR.exists():
        return results

    if not dry_run:
        ROTATED_DIR.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    max_bytes = MAX_LOG_SIZE_MB * 1024 * 1024

    for log_file in sorted(LOGS_DIR.glob("*.log")):
        try:
            size = log_file.stat().st_size
        except OSError as exc:
            results.append(
                {
                    "file": log_file.name,
                    "action": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            continue

        if size < max_bytes:
            continue

        target = ROTATED_DIR / f"{log_file.stem}_{stamp}.log.gz"
        if dry_run:
            results.append(
                {
                    "file": log_file.name,
                    "action": "would_rotate",
                    "size": size,
                    "size_human": human_size(size),
                    "target": target.name,
                }
            )
            continue

        try:
            with log_file.open("rb") as src, gzip.open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            # Truncate — процессы с open fd продолжат писать в тот же inode
            log_file.write_text("")
            results.append(
                {
                    "file": log_file.name,
                    "action": "rotated",
                    "size": size,
                    "size_human": human_size(size),
                    "target": target.name,
                }
            )
        except (OSError, gzip.BadGzipFile) as exc:
            results.append(
                {
                    "file": log_file.name,
                    "action": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    return results


def build_report(
    *, dry_run: bool, skip_vacuum: bool, skip_logs: bool
) -> dict:
    """Сформировать итоговый JSON-отчёт."""
    report: dict = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "dry_run": dry_run,
    }
    if not skip_vacuum:
        report["vacuum"] = vacuum_archive(dry_run=dry_run)
    if not skip_logs:
        report["logs_rotated"] = rotate_logs(dry_run=dry_run)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="archive.db maintenance")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-vacuum", action="store_true")
    parser.add_argument("--skip-logs", action="store_true")
    args = parser.parse_args()

    report = build_report(
        dry_run=args.dry_run,
        skip_vacuum=args.skip_vacuum,
        skip_logs=args.skip_logs,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
