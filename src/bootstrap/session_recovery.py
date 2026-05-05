# -*- coding: utf-8 -*-
"""
Shared module: автоматическое восстановление SQLite-сессии через sqlite3 .recover.

Используется как preflight'ом (src/userbot/session.py через db_corruption_guard.py),
так и скриптом scripts/openclaw_runtime_repair.py (Wave 16-J) — DRY-принцип.

Wave 16-N: выделен из db_corruption_guard.py в отдельный модуль, чтобы
избежать дублирования логики между boot-preflight'ом и repair-скриптом.

Wave 18-A: cleanup_old_backups — retention policy для session backup-файлов.

Wave 24-B: peers threshold check + stale WAL/SHM cleanup перед integrity_check.
- any_pyrofork_holds_session(path) — lsof-проверка живого pyrofork writer'а
- cleanup_stale_wal_shm(path) — удаление stale WAL/SHM без live writer'а
- check_peers_count(path) — проверка кол-ва peers vs MIN_PEERS_THRESHOLD
- MIN_PEERS_THRESHOLD — константа (env: KRAB_SESSION_MIN_PEERS_THRESHOLD, default 50)

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
- cleanup_old_backups(session_dir, *, keep_recent, max_age_days, dry_run) → dict
- any_pyrofork_holds_session(path) → bool
- cleanup_stale_wal_shm(path) → bool
- check_peers_count(path) → tuple[bool, int]
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

# ── Wave 24-B: peers threshold ────────────────────────────────────────────────

# Минимальное кол-во peers в healthy session.
# Если меньше — DB pristine/empty/wiped; требует recovery (не работает с Telegram).
# Можно переопределить через env: KRAB_SESSION_MIN_PEERS_THRESHOLD.
MIN_PEERS_THRESHOLD: int = int(os.environ.get("KRAB_SESSION_MIN_PEERS_THRESHOLD", "50"))

# ── константы ────────────────────────────────────────────────────────────────

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


# ── Wave 18-A: retention policy constants ─────────────────────────────────────

# Категории backup-файлов и glob-паттерны для каждой.
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


# ── Wave 24-B helpers ────────────────────────────────────────────────────────


def any_pyrofork_holds_session(session_path: Path) -> bool:
    """Проверяет через lsof: есть ли живой процесс с открытым session-файлом.

    Возвращает True если хотя бы один PID держит файл открытым.
    Fail-safe: если lsof недоступен или упал → False (можно безопасно чистить).
    """
    try:
        out = subprocess.run(
            ["lsof", "-t", str(session_path)],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return bool(out.stdout.strip())
    except Exception:  # noqa: BLE001
        # lsof недоступен или timeout → fail-safe: разрешаем cleanup
        return False


def cleanup_stale_wal_shm(session_path: Path) -> bool:
    """Удаляет stale WAL/SHM если нет живого pyrofork writer'а.

    Признаки stale:
    - kraab.session-wal или kraab.session-shm существует
    - НЕТ живого процесса, держащего session-файл (проверяется через lsof)

    Вызывается ПЕРЕД integrity_check, чтобы устранить "disk I/O error"
    от stale WAL, оставленного после non-clean shutdown.

    Returns:
        True если хотя бы один файл был удалён.
    """
    wal = session_path.with_name(session_path.name + "-wal")
    shm = session_path.with_name(session_path.name + "-shm")

    if not wal.exists() and not shm.exists():
        return False

    # Проверяем: есть ли живой процесс, который держит session-файл
    if any_pyrofork_holds_session(session_path):
        logger.debug(
            "stale_wal_shm_skip_live_writer",
            session=str(session_path),
            wal_exists=wal.exists(),
            shm_exists=shm.exists(),
        )
        return False

    # Нет живого writer'а → удаляем stale sidecar'ы
    cleaned = False
    for sidecar in (wal, shm):
        if sidecar.exists():
            try:
                sidecar.unlink(missing_ok=True)
                cleaned = True
                logger.info(
                    "stale_wal_shm_cleaned",
                    path=str(sidecar),
                )
            except OSError as exc:
                logger.warning(
                    "stale_wal_shm_cleanup_failed",
                    path=str(sidecar),
                    error=str(exc),
                )
    return cleaned


def check_peers_count(session_path: Path) -> tuple[bool, int]:
    """Проверяет кол-во peers в session DB против MIN_PEERS_THRESHOLD.

    Healthy DB должна содержать >= MIN_PEERS_THRESHOLD peers.
    Если меньше — DB pristine/empty/wiped: integrity_check может вернуть "ok",
    но Telegram userbot не сможет резолвить peer'ы и будет молча игнорировать
    входящие сообщения.

    Returns:
        (healthy, peers_count) — healthy=True если peers >= MIN_PEERS_THRESHOLD.
        При OperationalError (malformed/locked) возвращает (False, 0), что
        тригерит recovery через основной integrity-path.
    """
    if not session_path.exists():
        # Нет файла → fresh install, не ошибка
        return True, 0

    try:
        uri = f"file:{session_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            cur = conn.execute("SELECT count(*) FROM peers")
            row = cur.fetchone()
            count = int(row[0]) if row else 0
            healthy = count >= MIN_PEERS_THRESHOLD
            return healthy, count
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        # OperationalError / DatabaseError: malformed/locked/not-a-database.
        # recovery тригернётся через основной integrity_check path.
        return False, 0
    except Exception:  # noqa: BLE001
        # Нестандартная ошибка → не блокируем boot
        return True, 0


__all__ = [
    "attempt_recovery",
    "has_recent_recovery_backup",
    "cleanup_sidecars",
    "verify_key_tables",
    "cleanup_old_backups",
    # Wave 24-B
    "MIN_PEERS_THRESHOLD",
    "any_pyrofork_holds_session",
    "cleanup_stale_wal_shm",
    "check_peers_count",
]
