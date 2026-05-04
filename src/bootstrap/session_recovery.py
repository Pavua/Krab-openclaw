# -*- coding: utf-8 -*-
"""
Session recovery helpers — Wave 16-N / Wave 18-A.

Публичный API:
- `attempt_recovery(path, *, idempotency_sec)` — auto-recovery через sqlite3 .recover
- `cleanup_old_backups(session_dir, *, keep_recent, max_age_days, dry_run)` — retention policy
  для session backup-файлов (Wave 18-A)

Используется:
- `src/userbot/session.py` — _main_session_integrity_preflight
- `scripts/openclaw_runtime_repair.py` — repair_session_integrity (Step 3)
- `scripts/openclaw_runtime_repair.py` — cleanup_session_backups_step (Step 5)
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)

# ── константы ────────────────────────────────────────────────────────────────

# Категории backup-файлов для retention cleanup (Wave 18-A).
# Ключ — имя категории (для логов), значение — glob-паттерн.
_BACKUP_CATEGORIES: dict[str, str] = {
    "bak-corrupt": "*.bak-corrupt-*",
    "bak-malformed": "*.bak-malformed-*",
    "corrupt": "*.corrupt-*",
    "broken": "*.broken-*",
    "pre-recover": "*.pre-recover-*",
    "empty": "*.empty-*",
    "legacy-bak": "*.bak.*",
}

# Защищённые live-файлы — никогда не удаляются.
_PROTECTED_NAMES: frozenset[str] = frozenset(
    {
        "kraab.session",
        "kraab.session-wal",
        "kraab.session-shm",
    }
)

# Суффиксы sidecar-файлов, которые удаляются вместе с main backup.
_SIDECAR_SUFFIXES: tuple[str, ...] = ("-wal", "-shm", "-journal")


# ── internal helpers ──────────────────────────────────────────────────────────


def _integrity_check(path: Path, *, timeout_sec: float = 5.0) -> tuple[bool, str]:
    """PRAGMA integrity_check через read-only URI. Возвращает (ok, detail)."""
    if not path.exists():
        return True, "missing"
    uri = f"file:{path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=timeout_sec)
        try:
            cur = conn.execute("PRAGMA integrity_check;")
            row = cur.fetchone()
            result = (row[0] if row else "").strip().lower()
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return True, f"check_skipped:{exc}"
    return (True, "ok") if result == "ok" else (False, result or "unknown")


def _has_recent_backup(path: Path, *, within_seconds: int = 3600) -> bool:
    """True если bak-corrupt-* backup свежее within_seconds секунд."""
    cutoff = time.time() - within_seconds
    prefix = f"{path.name}.bak-corrupt-"
    for p in path.parent.iterdir():
        if p.name.startswith(prefix):
            try:
                if p.stat().st_mtime >= cutoff:
                    return True
            except OSError:
                continue
    return False


def _cleanup_sidecars(path: Path) -> list[str]:
    """Удаляет WAL/SHM/journal sidecar рядом с corrupt session перед recovery."""
    removed: list[str] = []
    for suffix in _SIDECAR_SUFFIXES:
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists():
            try:
                sidecar.unlink()
                removed.append(str(sidecar))
            except OSError as exc:  # pragma: no cover
                logger.warning("sidecar_cleanup_failed", path=str(sidecar), error=str(exc))
    return removed


def _clean_subprocess_env() -> dict[str, str]:
    """Возвращает окружение для subprocess без MallocStackLogging и т.п."""
    import os

    try:
        from src.core.subprocess_env import clean_subprocess_env  # type: ignore[import]

        return clean_subprocess_env()
    except ImportError:
        env = os.environ.copy()
        for key in (
            "MallocStackLogging",
            "MallocStackLoggingNoCompact",
            "MallocScribble",
            "MallocGuardEdges",
            "MallocCheckHeapEach",
        ):
            env.pop(key, None)
        return env


# ── public API ────────────────────────────────────────────────────────────────


def attempt_recovery(
    path: Path,
    *,
    idempotency_sec: int = 3600,
    timeout_sec: float = 30.0,
) -> dict:
    """
    Auto-recovery corrupt SQLite через sqlite3 .recover CLI.

    Поток:
    1. Idempotency guard: если bak-corrupt-* < idempotency_sec → return early.
    2. Backup path → path.bak-corrupt-{ts}.
    3. Cleanup WAL/SHM sidecars (stale pages не должны попасть в recovered DB).
    4. `sqlite3 broken ".recover" | sqlite3 fresh` (subprocess, timeout_sec).
    5. integrity_check recovered file.
    6. Atomic replace fresh → original.

    Args:
        path: Путь к SQLite session файлу.
        idempotency_sec: Cooldown между recovery-попытками (0 = отключить guard).
        timeout_sec: Таймаут для subprocess sqlite3.

    Returns:
        dict с ключами: recovered, backup_path, sidecars_removed, peer_count,
        username_count, sessions_count, detail.

    Не бросает исключения — caller сам решает exit strategy.
    """
    result: dict = {
        "recovered": False,
        "backup_path": "",
        "sidecars_removed": [],
        "peer_count": None,
        "username_count": None,
        "sessions_count": None,
        "detail": "",
    }

    if not path.exists():
        result["detail"] = "missing"
        return result

    # 1. Idempotency guard
    if idempotency_sec > 0 and _has_recent_backup(path, within_seconds=idempotency_sec):
        result["detail"] = "idempotency_guard:recent_backup_exists"
        return result

    ts = int(time.time())
    backup_path = path.with_name(f"{path.name}.bak-corrupt-{ts}")
    fresh_path = path.with_name(f"{path.name}.recovered-{ts}")

    # 2. Backup (для forensics)
    try:
        shutil.copy2(path, backup_path)
        result["backup_path"] = str(backup_path)
    except OSError as exc:
        result["detail"] = f"backup_failed:{exc}"
        logger.error("session_recovery_backup_failed", path=str(path), error=str(exc))
        return result

    # 3. Cleanup sidecars
    removed = _cleanup_sidecars(path)
    result["sidecars_removed"] = removed
    if removed:
        logger.info("session_recovery_sidecars_cleaned", removed=removed)

    # 4. sqlite3 .recover | sqlite3 fresh
    env = _clean_subprocess_env()
    try:
        dump = subprocess.run(
            ["sqlite3", str(path), ".recover"],
            capture_output=True,
            timeout=timeout_sec,
            check=False,
            env=env,
        )
        if dump.returncode != 0 and not dump.stdout:
            result["detail"] = (
                f"recover_dump_failed rc={dump.returncode} "
                f"stderr={dump.stderr.decode('utf-8', errors='replace')[:200]}"
            )
            return result

        load = subprocess.run(
            ["sqlite3", str(fresh_path)],
            input=dump.stdout,
            capture_output=True,
            timeout=timeout_sec,
            check=False,
            env=env,
        )
        if load.returncode != 0:
            result["detail"] = (
                f"recover_load_failed rc={load.returncode} "
                f"stderr={load.stderr.decode('utf-8', errors='replace')[:200]}"
            )
            return result

    except subprocess.TimeoutExpired:
        result["detail"] = "recover_timeout"
        return result
    except FileNotFoundError:
        result["detail"] = "sqlite3_not_in_path"
        return result
    except Exception as exc:  # noqa: BLE001
        result["detail"] = f"recover_unexpected:{exc}"
        return result

    # 5. Verify integrity recovered file
    ok, detail = _integrity_check(fresh_path)
    if not ok:
        result["detail"] = f"recovered_still_corrupt:{detail}"
        try:
            fresh_path.unlink()
        except OSError:
            pass
        return result

    # 5b. Best-effort подсчёт строк (forensics)
    try:
        conn = sqlite3.connect(str(fresh_path), timeout=2.0)
        try:
            cur = conn.cursor()
            for table, key in (
                ("peers", "peer_count"),
                ("usernames", "username_count"),
                ("sessions", "sessions_count"),
            ):
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
                    row = cur.fetchone()
                    if row:
                        result[key] = int(row[0])
                except sqlite3.Error:
                    # Разные версии Pyrogram — разная схема, отсутствие таблицы не фатально
                    continue
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("session_recovery_row_count_failed", error=str(exc))

    # 6. Atomic replace
    try:
        fresh_path.replace(path)
    except OSError as exc:
        result["detail"] = f"atomic_replace_failed:{exc}"
        try:
            fresh_path.unlink()
        except OSError:
            pass
        return result

    result["recovered"] = True
    result["detail"] = "ok"
    logger.info(
        "session_recovery_success",
        path=str(path),
        backup=str(backup_path),
        peers=result["peer_count"],
    )
    return result


def cleanup_old_backups(
    session_dir: Path,
    *,
    keep_recent: int = 3,
    max_age_days: int = 14,
    dry_run: bool = False,
) -> dict:
    """
    Retention policy для session backup-файлов (Wave 18-A).

    Удаляет старые backup files, оставляя по N свежих в каждой категории.
    Файлы моложе max_age_days всегда сохраняются (независимо от keep_recent).

    Категории:
        bak-corrupt-*, bak-malformed-*, corrupt-*, broken-*, pre-recover-*,
        empty-*, legacy-bak (bak.*)

    Защищённые live-файлы (kraab.session, -wal, -shm) никогда не удаляются.

    Sidecar-файлы (-wal, -shm) удаляются вместе с main backup файлом.

    Args:
        session_dir: Директория с session-файлами (data/sessions/).
        keep_recent: Сколько свежих файлов оставить в каждой категории.
        max_age_days: Файлы моложе этого порога не удаляются.
        dry_run: Если True — только анализ, без удаления.

    Returns:
        dict: {
            "removed": list[str],    — удалённые пути
            "kept": list[str],       — оставленные пути
            "bytes_freed": int,      — освобождено байт
            "dry_run": bool,
            "categories": dict,      — по-категориям: {cat: {removed, kept}}
        }
    """
    if not session_dir.exists():
        return {
            "removed": [],
            "kept": [],
            "bytes_freed": 0,
            "dry_run": dry_run,
            "categories": {},
        }

    # Порог возраста (Unix timestamp)
    age_cutoff = time.time() - max_age_days * 86400

    all_removed: list[str] = []
    all_kept: list[str] = []
    bytes_freed: int = 0
    categories_report: dict = {}

    for category, glob_pattern in _BACKUP_CATEGORIES.items():
        # Найти все файлы категории, исключая защищённые и сайдкары (обрабатываем отдельно)
        candidates: list[Path] = []
        for p in session_dir.glob(glob_pattern):
            # Пропускаем защищённые live-файлы
            if p.name in _PROTECTED_NAMES:
                continue
            # Пропускаем сайдкары: они будут удалены вместе с main backup
            if any(p.name.endswith(sfx) for sfx in _SIDECAR_SUFFIXES):
                continue
            candidates.append(p)

        if not candidates:
            categories_report[category] = {"removed": [], "kept": []}
            continue

        # Сортировка по mtime descending (свежие первые)
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        cat_removed: list[str] = []
        cat_kept: list[str] = []

        for idx, fpath in enumerate(candidates):
            try:
                mtime = fpath.stat().st_mtime
            except OSError:
                # Файл исчез между glob и stat — пропускаем
                continue

            # Условие удаления: файл за пределами top-N И старее max_age_days
            is_within_top_n = idx < keep_recent
            is_young_enough = mtime >= age_cutoff

            if is_within_top_n or is_young_enough:
                # Оставляем
                cat_kept.append(str(fpath))
                all_kept.append(str(fpath))
            else:
                # Удаляем: сначала считаем байты, потом удаляем
                files_to_delete = [fpath]
                # Добавляем sidecar-файлы
                for sfx in _SIDECAR_SUFFIXES:
                    sidecar = fpath.with_name(fpath.name + sfx)
                    if sidecar.exists():
                        files_to_delete.append(sidecar)

                for del_path in files_to_delete:
                    try:
                        size = del_path.stat().st_size
                        bytes_freed += size
                        if not dry_run:
                            del_path.unlink()
                        cat_removed.append(str(del_path))
                        all_removed.append(str(del_path))
                        logger.debug(
                            "session_backup_removed",
                            path=str(del_path),
                            size_kb=size // 1024,
                            dry_run=dry_run,
                        )
                    except OSError as exc:
                        logger.warning(
                            "session_backup_remove_failed",
                            path=str(del_path),
                            error=str(exc),
                        )

        categories_report[category] = {"removed": cat_removed, "kept": cat_kept}

    if all_removed or all_kept:
        logger.info(
            "session_backup_cleanup_done",
            removed=len(all_removed),
            kept=len(all_kept),
            bytes_freed=bytes_freed,
            dry_run=dry_run,
        )

    return {
        "removed": all_removed,
        "kept": all_kept,
        "bytes_freed": bytes_freed,
        "dry_run": dry_run,
        "categories": categories_report,
    }


__all__ = [
    "attempt_recovery",
    "cleanup_old_backups",
]
