#!/usr/bin/env python3
"""
Krab Daily Maintenance (launchd, FREE) — runs daily at 02:07.

Combines 2 routines in one script (fewer plists):
  1. Archive.db backup → ~/.openclaw/krab_memory/backups/YYYYMMDD.db
     keep last 7 backups, delete older
  2. Log rotation — openclaw.log + gateway.log > 100 MB rotated
     to .log.1 (previous .log.1 → .log.2 ... keep 3 generations)

Silent if nothing to do. Sentry notify only on failures.
"""
from __future__ import annotations

import datetime as _dt
import gzip
import json
import os
import shutil
import sys
from pathlib import Path

STATE_DIR = Path(
    os.getenv("KRAB_RUNTIME_STATE_DIR", str(Path.home() / ".openclaw" / "krab_runtime_state"))
)
STATS_FILE = STATE_DIR / "daily_maintenance.json"
LOG_FILE = STATE_DIR / "daily_maintenance.log"

ARCHIVE_DB = Path(
    os.getenv("KRAB_ARCHIVE_DB", str(Path.home() / ".openclaw" / "krab_memory" / "archive.db"))
)
BACKUP_DIR = Path(
    os.getenv("KRAB_BACKUP_DIR", str(Path.home() / ".openclaw" / "krab_memory" / "backups"))
)
BACKUP_KEEP_DAYS = int(os.getenv("KRAB_BACKUP_KEEP_DAYS", "7"))

LOG_ROTATE_THRESHOLD_MB = int(os.getenv("KRAB_LOG_ROTATE_THRESHOLD_MB", "100"))
LOG_ROTATE_KEEP_GENERATIONS = int(os.getenv("KRAB_LOG_ROTATE_KEEP", "3"))
LOG_FILES_TO_ROTATE = [
    Path("/Users/pablito/Antigravity_AGENTS/Краб/openclaw.log"),
    Path.home() / ".openclaw" / "logs" / "gateway.log",
    Path.home() / ".openclaw" / "logs" / "gateway.err.log",
]


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _log(msg: str, level: str = "info") -> None:
    line = f"{_now_iso()} [{level:7s}] {msg}"
    print(line, flush=True)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def backup_archive_db() -> dict:
    """Copy archive.db to backups dir with YYYYMMDD prefix. Prune older than KEEP_DAYS."""
    result = {"backed_up": False, "backup_path": None, "pruned": 0, "error": None}

    if not ARCHIVE_DB.exists():
        result["error"] = f"archive.db not found at {ARCHIVE_DB}"
        return result

    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        today = _dt.date.today().strftime("%Y%m%d")
        backup_path = BACKUP_DIR / f"archive-{today}.db"

        # Skip if сегодня уже backup есть (idempotent)
        if backup_path.exists():
            result["backup_path"] = str(backup_path)
            result["note"] = "already_backed_up_today"
        else:
            shutil.copy2(ARCHIVE_DB, backup_path)
            result["backed_up"] = True
            result["backup_path"] = str(backup_path)
            result["size_mb"] = backup_path.stat().st_size // (1024 * 1024)

        # Prune: keep only backups from last KEEP_DAYS days
        cutoff = _dt.date.today() - _dt.timedelta(days=BACKUP_KEEP_DAYS)
        pruned = 0
        for p in sorted(BACKUP_DIR.glob("archive-*.db")):
            # Parse date из имени файла
            try:
                stem = p.stem.replace("archive-", "")
                file_date = _dt.datetime.strptime(stem, "%Y%m%d").date()
                if file_date < cutoff:
                    p.unlink()
                    pruned += 1
            except ValueError:
                continue  # skip malformed names
        result["pruned"] = pruned
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"

    return result


def rotate_log(path: Path) -> dict:
    """Rotate single log file if > threshold MB. Keep N generations as .gz."""
    result = {"path": str(path), "rotated": False, "size_mb": 0, "error": None}

    if not path.exists():
        return result

    try:
        size_mb = path.stat().st_size // (1024 * 1024)
        result["size_mb"] = size_mb

        if size_mb < LOG_ROTATE_THRESHOLD_MB:
            return result  # no rotation needed

        # Shift generations: .log.N → .log.(N+1) (compressed as .gz)
        for gen in range(LOG_ROTATE_KEEP_GENERATIONS, 0, -1):
            prev_gen = path.with_suffix(path.suffix + f".{gen}.gz")
            next_gen = path.with_suffix(path.suffix + f".{gen + 1}.gz")
            if gen == LOG_ROTATE_KEEP_GENERATIONS and prev_gen.exists():
                prev_gen.unlink()  # oldest gen — delete
                continue
            if prev_gen.exists():
                prev_gen.rename(next_gen)

        # Compress current log → .log.1.gz
        current_gen = path.with_suffix(path.suffix + ".1.gz")
        with path.open("rb") as f_in, gzip.open(current_gen, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)

        # Truncate original to 0 bytes (приложение продолжит писать)
        path.write_text("")

        result["rotated"] = True
        result["compressed_to"] = str(current_gen)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"

    return result


def main() -> int:
    summary = {
        "timestamp_utc": _now_iso(),
        "archive_backup": {},
        "log_rotations": [],
        "errors": [],
    }

    # 1. Archive.db backup
    backup = backup_archive_db()
    summary["archive_backup"] = backup
    if backup.get("error"):
        _log(f"backup_error: {backup['error']}", "error")
        summary["errors"].append(f"backup: {backup['error']}")
    elif backup.get("backed_up"):
        _log(
            f"backup_ok path={backup['backup_path']} size={backup.get('size_mb', '?')}MB "
            f"pruned={backup['pruned']}",
            "info",
        )
    else:
        _log(f"backup_skip: {backup.get('note', 'already_done')}", "debug")

    # 2. Log rotations
    for log_path in LOG_FILES_TO_ROTATE:
        rotation = rotate_log(log_path)
        summary["log_rotations"].append(rotation)
        if rotation.get("error"):
            _log(f"rotate_error path={log_path} error={rotation['error']}", "warning")
            summary["errors"].append(f"rotate {log_path.name}: {rotation['error']}")
        elif rotation.get("rotated"):
            _log(
                f"rotated path={log_path.name} size={rotation['size_mb']}MB "
                f"→ {rotation['compressed_to']}",
                "info",
            )

    # Persist stats
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATS_FILE.write_text(json.dumps(summary, indent=2))
    except OSError:
        pass

    if summary["errors"]:
        _log(f"maintenance_completed_with_errors count={len(summary['errors'])}", "warning")
        return 1
    _log("maintenance_ok", "info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
