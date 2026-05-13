#!/usr/bin/env python3
"""
krab_memory_vacuum.py — Wave 201: VACUUM archive.db после prune (Wave 188).

Контекст:
    Wave 188 prune удалил orphan-строки из archive.db, но **файл не сжался**
    автоматически — SQLite метит pages как свободные и переиспользует их при
    последующих INSERT, но физический размер на диске не уменьшается. Чтобы
    реально вернуть место (~517 MB → ~344 MB), нужен `VACUUM`, который
    переписывает всю БД в новый файл с компактной раскладкой страниц.

Особенности VACUUM:
    - **Exclusive lock** на 5-10 минут (нет concurrent writes/reads).
    - **Disk requirement**: ~2× размер исходной БД (новая БД строится рядом).
    - **Безопасен**: автоматический rollback при сбое.

Pre-checks (все обязательны, abort при провале — кроме --force):
    1. `PRAGMA integrity_check` → должен вернуть "ok".
    2. Свободное место на диске ≥ 2 × db_size_bytes.
    3. Krab не пишет в БД (WAL = 0 bytes ИЛИ нет активной записи).

CLI:
    venv/bin/python scripts/krab_memory_vacuum.py --dry-run     # только estimates
    venv/bin/python scripts/krab_memory_vacuum.py               # реальный VACUUM
    venv/bin/python scripts/krab_memory_vacuum.py --force       # skip Krab-active check

Audit:
    `~/.openclaw/krab_runtime_state/vacuum_audit.json` — последний запуск
    (size before/after, integrity status, elapsed, success flag).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_ARCHIVE_DB = Path("~/.openclaw/krab_memory/archive.db").expanduser()
DEFAULT_AUDIT_FILE = Path("~/.openclaw/krab_runtime_state/vacuum_audit.json").expanduser()

# WAL > этого размера = "Krab активно пишет"; 4096 байт = ~1 страница (низкий шум).
DEFAULT_ACTIVE_WAL_THRESHOLD_BYTES = 4096
# Множитель свободного места на диске относительно размера БД.
DISK_SPACE_MULTIPLIER = 2.0


# ---------------------------------------------------------------------------
# Pure helpers (тестируемые отдельно).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VacuumAudit:
    """Аудит-запись одного запуска (dry-run или реальный VACUUM)."""

    audit_ts: str
    db_path: str
    size_before_mb: float
    size_after_mb: float
    saved_mb: float
    integrity_before: str
    integrity_after: str
    elapsed_sec: float
    dry_run: bool
    forced: bool
    success: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_utc() -> datetime:
    """Тестируемый wrapper."""

    return datetime.now(timezone.utc)


def db_size_bytes(db_path: Path) -> int:
    """Размер основного файла БД (без WAL/SHM)."""

    return db_path.stat().st_size if db_path.exists() else 0


def free_disk_bytes(path: Path) -> int:
    """Свободное место на том же томе, где лежит БД."""

    parent = path.parent if path.parent.exists() else Path.home()
    return shutil.disk_usage(parent).free


def check_disk_reservation(
    db_path: Path,
    *,
    multiplier: float = DISK_SPACE_MULTIPLIER,
) -> tuple[bool, int, int]:
    """Возвращает `(ok, required_bytes, free_bytes)`.

    VACUUM строит новую БД рядом → нужно ≥ multiplier × db_size свободно.
    """

    db_bytes = db_size_bytes(db_path)
    required = int(db_bytes * multiplier)
    free = free_disk_bytes(db_path)
    return free >= required, required, free


def check_integrity(db_path: Path) -> str:
    """`PRAGMA integrity_check` → "ok" если БД здорова, иначе текст ошибки."""

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return row[0] if row else "unknown"
    finally:
        conn.close()


def detect_krab_active(
    db_path: Path,
    *,
    wal_threshold_bytes: int = DEFAULT_ACTIVE_WAL_THRESHOLD_BYTES,
) -> tuple[bool, str]:
    """Эвристика: пишет ли Krab сейчас в БД.

    Стратегия:
        1. Размер WAL > threshold = активная запись.
        2. Пытаемся открыть БД с `BEGIN EXCLUSIVE` (с short busy_timeout) —
           если кто-то держит write-lock, мы упадём на `database is locked`.

    Returns:
        `(is_active, reason)`. `reason` — человекочитаемая причина.
    """

    wal = db_path.with_suffix(db_path.suffix + "-wal")
    if wal.exists():
        wal_size = wal.stat().st_size
        if wal_size > wal_threshold_bytes:
            return True, f"WAL size {wal_size} > threshold {wal_threshold_bytes}"

    # Пробуем взять exclusive lock на короткий момент.
    try:
        conn = sqlite3.connect(str(db_path), timeout=1.0)
        try:
            conn.execute("PRAGMA busy_timeout = 500")
            conn.execute("BEGIN EXCLUSIVE")
            conn.execute("ROLLBACK")
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        return True, f"cannot acquire exclusive lock: {exc}"

    return False, "no write activity detected"


def run_vacuum(
    db_path: Path,
    *,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> float:
    """Выполняет `VACUUM`. Возвращает elapsed_sec.

    `VACUUM` неявно открывает свою транзакцию — autocommit режим обязателен.
    """

    started = monotonic_fn()
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()
    return monotonic_fn() - started


def persist_audit(audit_path: Path, audit: VacuumAudit) -> None:
    """Сохраняет audit-запись атомарно (tmp → rename)."""

    audit_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = audit_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(audit.to_dict(), indent=2, ensure_ascii=False))
    tmp.replace(audit_path)


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------


def run(
    db_path: Path,
    *,
    audit_path: Path,
    dry_run: bool,
    force: bool,
    now_fn: Callable[[], datetime] = _now_utc,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> VacuumAudit:
    """End-to-end: pre-checks → (optional) VACUUM → post-checks → persist."""

    if not db_path.exists():
        raise FileNotFoundError(f"archive.db not found: {db_path}")

    size_before = round(db_size_bytes(db_path) / (1024 * 1024), 2)

    # Pre-check 1: integrity.
    integrity_before = check_integrity(db_path)
    if integrity_before != "ok":
        audit = VacuumAudit(
            audit_ts=now_fn().isoformat(),
            db_path=str(db_path),
            size_before_mb=size_before,
            size_after_mb=size_before,
            saved_mb=0.0,
            integrity_before=integrity_before,
            integrity_after="not_run",
            elapsed_sec=0.0,
            dry_run=dry_run,
            forced=force,
            success=False,
            error=f"integrity check failed before VACUUM: {integrity_before}",
        )
        persist_audit(audit_path, audit)
        return audit

    # Pre-check 2: disk reservation.
    disk_ok, required, free = check_disk_reservation(db_path)
    if not disk_ok:
        audit = VacuumAudit(
            audit_ts=now_fn().isoformat(),
            db_path=str(db_path),
            size_before_mb=size_before,
            size_after_mb=size_before,
            saved_mb=0.0,
            integrity_before=integrity_before,
            integrity_after="not_run",
            elapsed_sec=0.0,
            dry_run=dry_run,
            forced=force,
            success=False,
            error=(
                f"insufficient disk: need {required} bytes, have {free} bytes "
                f"(VACUUM requires {DISK_SPACE_MULTIPLIER}× db size)"
            ),
        )
        persist_audit(audit_path, audit)
        return audit

    # Pre-check 3: Krab active writes (skippable via --force).
    if not force:
        active, reason = detect_krab_active(db_path)
        if active:
            audit = VacuumAudit(
                audit_ts=now_fn().isoformat(),
                db_path=str(db_path),
                size_before_mb=size_before,
                size_after_mb=size_before,
                saved_mb=0.0,
                integrity_before=integrity_before,
                integrity_after="not_run",
                elapsed_sec=0.0,
                dry_run=dry_run,
                forced=False,
                success=False,
                error=(
                    f"Krab appears active ({reason}). "
                    "Stop Krab via 'new Stop Krab.command' then re-run, "
                    "or pass --force to override (NOT recommended)."
                ),
            )
            persist_audit(audit_path, audit)
            return audit

    # Dry-run: estimate only (~33% reduction is typical post-prune).
    if dry_run:
        estimated_after = round(size_before * 0.66, 2)
        audit = VacuumAudit(
            audit_ts=now_fn().isoformat(),
            db_path=str(db_path),
            size_before_mb=size_before,
            size_after_mb=estimated_after,
            saved_mb=round(size_before - estimated_after, 2),
            integrity_before=integrity_before,
            integrity_after="not_run",
            elapsed_sec=0.0,
            dry_run=True,
            forced=force,
            success=True,
            error=None,
        )
        persist_audit(audit_path, audit)
        return audit

    # Real VACUUM.
    try:
        elapsed = run_vacuum(db_path, monotonic_fn=monotonic_fn)
    except sqlite3.Error as exc:
        audit = VacuumAudit(
            audit_ts=now_fn().isoformat(),
            db_path=str(db_path),
            size_before_mb=size_before,
            size_after_mb=size_before,
            saved_mb=0.0,
            integrity_before=integrity_before,
            integrity_after="not_run",
            elapsed_sec=0.0,
            dry_run=False,
            forced=force,
            success=False,
            error=f"VACUUM failed: {exc}",
        )
        persist_audit(audit_path, audit)
        return audit

    size_after = round(db_size_bytes(db_path) / (1024 * 1024), 2)
    integrity_after = check_integrity(db_path)

    audit = VacuumAudit(
        audit_ts=now_fn().isoformat(),
        db_path=str(db_path),
        size_before_mb=size_before,
        size_after_mb=size_after,
        saved_mb=round(size_before - size_after, 2),
        integrity_before=integrity_before,
        integrity_after=integrity_after,
        elapsed_sec=round(elapsed, 3),
        dry_run=False,
        forced=force,
        success=integrity_after == "ok",
        error=None if integrity_after == "ok" else f"post-VACUUM integrity: {integrity_after}",
    )
    persist_audit(audit_path, audit)
    return audit


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Krab archive.db VACUUM (Wave 201)")
    parser.add_argument("--db", type=Path, default=DEFAULT_ARCHIVE_DB)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT_FILE)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только estimate — VACUUM не запускается.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip Krab-active check (dangerous — может corrupt'ить БД).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        audit = run(
            args.db,
            audit_path=args.audit,
            dry_run=args.dry_run,
            force=args.force,
        )
    except FileNotFoundError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(audit.to_dict(), indent=2, ensure_ascii=False))
    return 0 if audit.success else 1


if __name__ == "__main__":
    sys.exit(main())
