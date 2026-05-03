# -*- coding: utf-8 -*-
"""
Shared module: автоматическое восстановление SQLite-сессии через sqlite3 .recover.

Используется как preflight'ом (src/userbot/session.py через db_corruption_guard.py),
так и скриптом scripts/openclaw_runtime_repair.py (Wave 16-J) — DRY-принцип.

Wave 16-N: выделен из db_corruption_guard.py в отдельный модуль, чтобы
избежать дублирования логики между boot-preflight'ом и repair-скриптом.

Жизненный цикл recovery:
1. Idempotency guard — если в последний час уже была попытка (backup существует),
   возвращаем recovered=False + idempotency_blocked=True (caller решает: fail loud).
2. Backup corrupt файла → <path>.bak-corrupt-<ts> (forensics preserved).
3. Cleanup WAL/SHM/journal sidecars (stale frames не должны попасть в recovered базу).
4. sqlite3 <path> ".recover" | sqlite3 <fresh_path> (subprocess, 30s timeout).
5. integrity_check на fresh файле (quick_check + проверка key tables).
6. Atomic replace fresh → original (os.replace).
7. Cleanup sidecar-файлов от fresh (WAL/SHM созданы при step 4).

Public API:
- attempt_recovery(path, *, dry_run=False, idempotency_sec=3600) → dict
- has_recent_recovery_backup(path, *, within_seconds=3600) → bool
- cleanup_sidecars(path) → list[str]
- verify_key_tables(path, tables=None) → tuple[bool, str]
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

# Таблицы, которые ДОЛЖНЫ присутствовать в recovered Pyrogram session.
# Если хотя бы одной нет — recovery считается неполной.
_REQUIRED_TABLES: tuple[str, ...] = ("sessions", "peers", "usernames")

# Timeout для subprocess sqlite3 (секунды).
_SUBPROCESS_TIMEOUT_SEC: float = 30.0

# Idempotency cooldown по умолчанию (секунды).
_DEFAULT_IDEMPOTENCY_SEC: int = 3600


def _clean_env() -> dict[str, str]:
    """Возвращает clean environment для subprocess (без MallocStackLogging и т.п.)."""
    import os

    try:
        # Пробуем импортировать готовый helper из src — он уже добавляет homebrew в PATH.
        import sys

        repo_root = Path(__file__).resolve().parents[2]
        src_root = str(repo_root / "src")
        if src_root not in sys.path:
            sys.path.insert(0, src_root)
        from core.subprocess_env import clean_subprocess_env  # type: ignore[import]

        return clean_subprocess_env()
    except Exception:  # noqa: BLE001 — fallback, не ронять boot
        env = os.environ.copy()
        # Убираем переменные, мешающие subprocess на macOS.
        for key in (
            "MallocStackLogging",
            "MallocStackLoggingNoCompact",
            "MallocScribble",
            "MallocGuardEdges",
            "MallocCheckHeapEach",
        ):
            env.pop(key, None)
        # Добавляем homebrew в PATH для доступа к sqlite3.
        import os as _os

        current_path = env.get("PATH", "")
        path_entries = current_path.split(_os.pathsep) if current_path else []
        for prefix in ("/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin"):
            if prefix not in path_entries:
                path_entries.insert(0, prefix)
        env["PATH"] = _os.pathsep.join(path_entries)
        return env


def _recovery_backup_paths(path: Path) -> list[Path]:
    """Возвращает существующие backup-файлы вида `<name>.bak-corrupt-*` для path."""
    if not path.parent.exists():
        return []
    prefix = f"{path.name}.bak-corrupt-"
    return [p for p in path.parent.iterdir() if p.name.startswith(prefix)]


def has_recent_recovery_backup(
    path: Path, *, within_seconds: int = _DEFAULT_IDEMPOTENCY_SEC
) -> bool:
    """
    Idempotency guard: True если backup-файл был создан недавно (default — 1h).

    Если за последний час уже была попытка auto-recovery, повторная вряд ли
    поможет — лучше fail loudly, чем зацикливаться.
    """
    cutoff = time.time() - within_seconds
    for backup in _recovery_backup_paths(path):
        try:
            mtime = backup.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff:
            return True
    return False


def cleanup_sidecars(path: Path) -> list[str]:
    """
    Удаляет WAL/SHM/journal sidecar-файлы рядом с corrupt session.

    Stale WAL может содержать malformed pages, которые sqlite3 .recover
    повторно прочитает в восстановленную базу — удаляем ДО recovery.
    Возвращает список удалённых путей (для логирования).
    """
    removed: list[str] = []
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists():
            try:
                sidecar.unlink()
                removed.append(str(sidecar))
            except OSError as exc:
                logger.warning(
                    "session_recovery_sidecar_cleanup_failed",
                    path=str(sidecar),
                    error=str(exc),
                )
    return removed


def verify_key_tables(path: Path, tables: tuple[str, ...] | None = None) -> tuple[bool, str]:
    """
    Проверяет наличие key tables в recovered SQLite-файле.

    Pyrogram-сессия без таблицы sessions/peers теряет auth_key и peer-cache —
    такой файл лучше не заменять оригинал.

    Returns (ok, detail) — ok=True если все required tables присутствуют.
    """
    required = tables if tables is not None else _REQUIRED_TABLES
    try:
        conn = sqlite3.connect(str(path), timeout=3.0)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            existing = {row[0].lower() for row in cursor.fetchall()}
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return False, f"table_check_error: {exc}"

    missing = [t for t in required if t.lower() not in existing]
    if missing:
        return False, f"missing_tables: {missing}"
    return True, f"tables_ok ({list(required)})"


def attempt_recovery(
    path: Path,
    *,
    dry_run: bool = False,
    idempotency_sec: int = _DEFAULT_IDEMPOTENCY_SEC,
    timeout_sec: float = _SUBPROCESS_TIMEOUT_SEC,
    required_tables: tuple[str, ...] | None = None,
) -> dict:
    """
    Попытка автоматического восстановления corrupt SQLite-сессии.

    Args:
        path: путь к corrupt sqlite-файлу (краш.session).
        dry_run: если True — только диагностика, файлы не изменяются.
        idempotency_sec: cooldown в секундах (default 3600 = 1h).
        timeout_sec: таймаут subprocess sqlite3 .recover.
        required_tables: таблицы, которые должны присутствовать в recovered файле.

    Returns dict с ключами:
        - recovered: bool — True если файл успешно заменён.
        - idempotency_blocked: bool — True если заблокировано idempotency guard.
        - backup_path: str — путь к сохранённому corrupt-файлу.
        - sidecars_removed: list[str] — удалённые sidecar-файлы.
        - peer_count: int | None — кол-во peers в recovered файле.
        - username_count: int | None
        - sessions_count: int | None
        - detail: str — диагностика.

    Не бросает исключений: caller принимает решение (raise DBCorruptionError или нет).
    """
    result: dict = {
        "recovered": False,
        "idempotency_blocked": False,
        "dry_run": dry_run,
        "backup_path": "",
        "sidecars_removed": [],
        "peer_count": None,
        "username_count": None,
        "sessions_count": None,
        "detail": "",
    }

    # Файл отсутствует — нечего восстанавливать.
    if not path.exists():
        result["detail"] = "missing"
        logger.info("session_recovery_skip_missing", path=str(path))
        return result

    # Idempotency guard: недавний backup → не зацикливаемся.
    if has_recent_recovery_backup(path, within_seconds=idempotency_sec):
        result["idempotency_blocked"] = True
        result["detail"] = f"idempotency_blocked: recent backup exists within {idempotency_sec}s"
        logger.error(
            "session_recovery_idempotency_blocked",
            path=str(path),
            idempotency_sec=idempotency_sec,
        )
        return result

    if dry_run:
        result["detail"] = "dry_run: no changes made"
        logger.info("session_recovery_dry_run", path=str(path))
        return result

    ts = int(time.time())
    backup_path = path.with_name(f"{path.name}.bak-corrupt-{ts}")
    fresh_path = path.with_name(f"{path.name}.recovered-{ts}")

    # 1. Backup corrupt-файла (forensics preserved).
    try:
        shutil.copy2(path, backup_path)
        result["backup_path"] = str(backup_path)
        logger.info(
            "session_recovery_backup_created",
            path=str(path),
            backup=str(backup_path),
        )
    except OSError as exc:
        result["detail"] = f"backup_failed: {exc}"
        logger.error("session_recovery_backup_failed", path=str(path), error=str(exc))
        return result

    # 2. Cleanup sidecars (WAL/SHM/journal) ДО recovery.
    removed = cleanup_sidecars(path)
    result["sidecars_removed"] = removed
    if removed:
        logger.info("session_recovery_sidecars_cleaned", removed=removed)

    # 3. sqlite3 .recover | sqlite3 fresh.
    env = _clean_env()
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
            logger.error("session_recovery_dump_failed", path=str(path), detail=result["detail"])
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
            logger.error("session_recovery_load_failed", path=str(path), detail=result["detail"])
            return result

    except subprocess.TimeoutExpired:
        result["detail"] = f"recover_timeout (>{timeout_sec}s)"
        logger.error("session_recovery_timeout", path=str(path), timeout_sec=timeout_sec)
        return result
    except FileNotFoundError:
        result["detail"] = "sqlite3_not_in_path"
        logger.error("session_recovery_sqlite3_missing", path=str(path))
        return result
    except Exception as exc:  # noqa: BLE001
        result["detail"] = f"recover_unexpected: {exc}"
        logger.error("session_recovery_unexpected", path=str(path), error=str(exc))
        return result

    # 4. Integrity check на recovered файле.
    try:
        uri = f"file:{fresh_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        try:
            cur = conn.execute("PRAGMA quick_check")
            row = cur.fetchone()
            integrity_ok = bool(row and row[0] == "ok")
            integrity_detail = str(row[0]) if row else "no_result"
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        integrity_ok = False
        integrity_detail = f"db_error: {exc}"
    except Exception as exc:  # noqa: BLE001
        integrity_ok = False
        integrity_detail = f"check_error: {exc}"

    if not integrity_ok:
        result["detail"] = f"recovered_still_corrupt: {integrity_detail}"
        logger.error(
            "session_recovery_integrity_failed",
            fresh_path=str(fresh_path),
            detail=integrity_detail,
        )
        try:
            fresh_path.unlink()
        except OSError:
            pass
        return result

    # 5. Verify key tables присутствуют в recovered файле.
    tables_ok, tables_detail = verify_key_tables(
        fresh_path,
        tables=required_tables,
    )
    if not tables_ok:
        result["detail"] = f"recovered_missing_tables: {tables_detail}"
        logger.error(
            "session_recovery_missing_tables",
            fresh_path=str(fresh_path),
            detail=tables_detail,
        )
        try:
            fresh_path.unlink()
        except OSError:
            pass
        return result

    # 6. Best-effort row counts для forensic logging.
    try:
        conn2 = sqlite3.connect(str(fresh_path), timeout=2.0)
        try:
            cur2 = conn2.cursor()
            for table, key in (
                ("peers", "peer_count"),
                ("usernames", "username_count"),
                ("sessions", "sessions_count"),
            ):
                try:
                    cur2.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
                    row2 = cur2.fetchone()
                    if row2:
                        result[key] = int(row2[0])
                except sqlite3.Error:
                    # Таблица может отсутствовать в older session schema.
                    continue
        finally:
            conn2.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("session_recovery_row_count_failed", error=str(exc))

    # 7. Atomic replace: fresh → original.
    try:
        fresh_path.replace(path)
    except OSError as exc:
        result["detail"] = f"atomic_replace_failed: {exc}"
        logger.error("session_recovery_replace_failed", path=str(path), error=str(exc))
        try:
            fresh_path.unlink()
        except OSError:
            pass
        return result

    # 8. Cleanup sidecars от fresh file (WAL/SHM могут появиться при step 4).
    new_sidecars = cleanup_sidecars(path)
    if new_sidecars:
        logger.info("session_recovery_post_replace_sidecars_cleaned", removed=new_sidecars)

    result["recovered"] = True
    result["detail"] = "ok"
    logger.info(
        "session_recovery_success",
        path=str(path),
        backup=str(backup_path),
        peer_count=result["peer_count"],
        username_count=result["username_count"],
        sessions_count=result["sessions_count"],
    )
    return result


__all__ = [
    "attempt_recovery",
    "has_recent_recovery_backup",
    "cleanup_sidecars",
    "verify_key_tables",
]
