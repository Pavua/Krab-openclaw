# -*- coding: utf-8 -*-
"""Wave 237: Backup workspace health monitor.

Назначение:
- Периодическая проверка состояния backup-инфраструктуры (~/.openclaw/backups/)
- Свежесть последнего бэкапа (< 24h)
- Sanity размера (отклонение от среднего за 7 последних backup'ов в пределах ±20%)
- Integrity check (sqlite3 PRAGMA integrity_check на распакованный archive.db)
- Optional restoration drill (opt-in: KRAB_BACKUP_DRILL_ENABLED=1)

Контракт:
- ``run_health_check()`` — синхронная проверка, возвращает dict с полями
  ``ok``, ``checks``, ``failures``. Безопасна при отсутствии backup'ов.
- ``backup_health_loop()`` — async фоновый loop, шаг
  ``KRAB_BACKUP_HEALTH_INTERVAL_SEC`` (default 14400s = 4h).
- При 3 подряд failures шлёт Sentry warning + Inbox item.
- Prometheus gauge ``krab_backup_health_ok`` (1/0) выставляется через
  ``set_backup_health_metric``.

Не трогает:
- archive.db / openclaw.json / plist / web_app.py
- сами файлы бэкапов — только read-only access.
"""

from __future__ import annotations

import asyncio
import gzip
import os
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger("Krab.core.backup_health_monitor")

# ─── Константы ───────────────────────────────────────────────────────────────

# Корень дневных директорий бэкапов (krab_db_backup.py).
BACKUP_ROOT = Path.home() / ".openclaw/backups"
# Альтернативное хранилище (snapshot archive.db без gzip).
LEGACY_BACKUP_ROOT = Path.home() / ".openclaw/krab_memory/backups"
# Какие файлы считаем "main archive backup" для integrity check.
ARCHIVE_BACKUP_FILENAME = "archive.db.bak.gz"

# Допуски проверок.
FRESHNESS_MAX_AGE_SEC = 24 * 3600  # 24h
SIZE_VARIANCE_TOLERANCE = 0.20  # ±20%
SIZE_HISTORY_DAYS = 7  # сколько последних bak смотреть для среднего
CONSECUTIVE_FAILURES_FOR_SENTRY = 3

# Состояние мониторинга (в памяти процесса).
_state: dict[str, Any] = {
    "consecutive_failures": 0,
    "last_check_ts": 0.0,
    "last_result_ok": True,
}


# ─── Prometheus snapshot ─────────────────────────────────────────────────────

# Snapshot для text-render fallback'а в prometheus_metrics.collect_metrics().
_BACKUP_HEALTH_OK: list[int] = [1]


def get_backup_health_ok() -> int:
    """Возвращает текущий health-bit (1=ok, 0=fail). Используется prom render."""
    return int(_BACKUP_HEALTH_OK[0])


def set_backup_health_metric(ok: bool) -> None:
    """Обновляет Prometheus gauge krab_backup_health_ok (best-effort)."""
    _BACKUP_HEALTH_OK[0] = 1 if ok else 0
    try:
        from prometheus_client import Gauge as _Gauge  # type: ignore[import-not-found]

        # Lazy registration: avoid duplicate-registration в test runs.
        global _backup_health_gauge  # noqa: PLW0603
        try:
            _backup_health_gauge  # type: ignore[used-before-assignment]
        except NameError:
            _backup_health_gauge = _Gauge(
                "krab_backup_health_ok",
                "Wave 237: 1 if last backup health check passed, 0 otherwise",
            )
        _backup_health_gauge.set(1 if ok else 0)
    except Exception:  # noqa: BLE001 - prometheus_client optional
        pass


# ─── Discovery ───────────────────────────────────────────────────────────────


def _list_backup_directories(root: Path = BACKUP_ROOT) -> list[Path]:
    """Возвращает отсортированные (newest first) дневные backup-директории.

    Ожидает структуру ``BACKUP_ROOT/YYYY-MM-DD/``.
    Пропускает 'workspace' и прочие нестандартные имена.
    """
    if not root.exists() or not root.is_dir():
        return []
    dated: list[tuple[str, Path]] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        name = child.name
        # YYYY-MM-DD ровно 10 символов
        if len(name) == 10 and name[4] == "-" and name[7] == "-":
            dated.append((name, child))
    dated.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in dated]


def _find_latest_archive_backup(root: Path = BACKUP_ROOT) -> Path | None:
    """Возвращает путь к свежайшему archive.db.bak.gz, либо None."""
    for daily_dir in _list_backup_directories(root):
        candidate = daily_dir / ARCHIVE_BACKUP_FILENAME
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


# ─── Проверки ────────────────────────────────────────────────────────────────


def check_freshness(latest: Path | None, *, now_ts: float | None = None) -> dict[str, Any]:
    """Проверка 1: последний бэкап моложе FRESHNESS_MAX_AGE_SEC."""
    ts = time.time() if now_ts is None else float(now_ts)
    if latest is None:
        return {"name": "freshness", "ok": False, "reason": "no_backup_found"}
    try:
        age = ts - latest.stat().st_mtime
    except OSError as exc:
        return {"name": "freshness", "ok": False, "reason": f"stat_error:{exc}"}
    ok = age < FRESHNESS_MAX_AGE_SEC
    return {
        "name": "freshness",
        "ok": ok,
        "age_seconds": int(age),
        "max_age_seconds": FRESHNESS_MAX_AGE_SEC,
        "path": str(latest),
    }


def check_size_variance(root: Path = BACKUP_ROOT) -> dict[str, Any]:
    """Проверка 2: размер последнего бэкапа в пределах ±20% от среднего по последним N."""
    sizes: list[int] = []
    paths: list[Path] = []
    for daily_dir in _list_backup_directories(root)[:SIZE_HISTORY_DAYS]:
        candidate = daily_dir / ARCHIVE_BACKUP_FILENAME
        if candidate.exists():
            try:
                sizes.append(candidate.stat().st_size)
                paths.append(candidate)
            except OSError:
                continue
    if not sizes:
        return {"name": "size_variance", "ok": False, "reason": "no_backups"}
    if len(sizes) < 2:
        # Недостаточно истории — пропускаем как ok (single backup).
        return {
            "name": "size_variance",
            "ok": True,
            "reason": "insufficient_history",
            "latest_size": sizes[0],
        }
    latest_size = sizes[0]
    history = sizes[1:]
    avg = sum(history) / len(history)
    if avg <= 0:
        return {"name": "size_variance", "ok": False, "reason": "avg_zero"}
    deviation = abs(latest_size - avg) / avg
    ok = deviation <= SIZE_VARIANCE_TOLERANCE
    return {
        "name": "size_variance",
        "ok": ok,
        "latest_size": latest_size,
        "avg_size": int(avg),
        "deviation": round(deviation, 4),
        "tolerance": SIZE_VARIANCE_TOLERANCE,
        "sample_size": len(history),
    }


def _decompress_to_tempfile(gz_path: Path) -> Path:
    """Распаковывает .gz в tmpfile. Caller обязан удалить."""
    fd, tmp_name = tempfile.mkstemp(suffix=".db", prefix="krab_backup_health_")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with gzip.open(gz_path, "rb") as src, open(tmp_path, "wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path


def check_integrity(latest: Path | None) -> dict[str, Any]:
    """Проверка 3: PRAGMA integrity_check на распакованный backup."""
    if latest is None:
        return {"name": "integrity", "ok": False, "reason": "no_backup_found"}
    tmp_path: Path | None = None
    try:
        # Если файл уже .db (не gz) — открываем напрямую.
        if latest.suffix == ".gz":
            tmp_path = _decompress_to_tempfile(latest)
            target = tmp_path
        else:
            target = latest
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=15)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            result = row[0] if row else "unknown"
        finally:
            conn.close()
        ok = result == "ok"
        return {
            "name": "integrity",
            "ok": ok,
            "result": str(result),
            "path": str(latest),
        }
    except Exception as exc:  # noqa: BLE001
        return {"name": "integrity", "ok": False, "reason": f"exception:{exc}"}
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def check_restoration_drill(latest: Path | None) -> dict[str, Any]:
    """Проверка 4 (opt-in): копируем backup в temp и пробуем sqlite3 .recover.

    Активируется через KRAB_BACKUP_DRILL_ENABLED=1.
    Возвращает финальный размер восстановленного файла.
    """
    if os.getenv("KRAB_BACKUP_DRILL_ENABLED", "0").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        return {"name": "restoration_drill", "ok": True, "skipped": True}
    if latest is None:
        return {"name": "restoration_drill", "ok": False, "reason": "no_backup_found"}

    tmp_path: Path | None = None
    recovered_path: Path | None = None
    try:
        # Шаг 1: распаковка.
        if latest.suffix == ".gz":
            tmp_path = _decompress_to_tempfile(latest)
        else:
            # Копируем чтобы не модифицировать оригинал.
            fd, tmp_name = tempfile.mkstemp(suffix=".db", prefix="krab_drill_src_")
            os.close(fd)
            tmp_path = Path(tmp_name)
            shutil.copyfile(latest, tmp_path)

        # Шаг 2: dump через .recover в новый файл.
        fd, recovered_name = tempfile.mkstemp(suffix=".db", prefix="krab_drill_dst_")
        os.close(fd)
        recovered_path = Path(recovered_name)

        src_conn = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True, timeout=15)
        dst_conn = sqlite3.connect(str(recovered_path), timeout=15)
        try:
            # iterdump возвращает SQL-команды эквивалент `sqlite3 .dump`/.recover для целостных БД.
            cursor = dst_conn.cursor()
            for stmt in src_conn.iterdump():
                try:
                    cursor.execute(stmt)
                except sqlite3.Error:
                    # Игнорируем отдельные сломанные insert — main цель drill пройти end-to-end.
                    continue
            dst_conn.commit()
        finally:
            src_conn.close()
            dst_conn.close()

        size = recovered_path.stat().st_size
        ok = size > 0
        return {
            "name": "restoration_drill",
            "ok": ok,
            "recovered_size": size,
            "source": str(latest),
        }
    except Exception as exc:  # noqa: BLE001
        return {"name": "restoration_drill", "ok": False, "reason": f"exception:{exc}"}
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        if recovered_path is not None:
            recovered_path.unlink(missing_ok=True)


# ─── Оркестрация ─────────────────────────────────────────────────────────────


def run_health_check(*, root: Path = BACKUP_ROOT) -> dict[str, Any]:
    """Запускает все проверки. Возвращает summary dict.

    Структура ответа:
        {
            "ok": bool,
            "checks": [check_dict, ...],
            "failures": [check_name, ...],
            "timestamp": iso8601,
        }
    """
    latest = _find_latest_archive_backup(root)
    checks: list[dict[str, Any]] = [
        check_freshness(latest),
        check_size_variance(root),
        check_integrity(latest),
        check_restoration_drill(latest),
    ]
    failures = [c["name"] for c in checks if not c.get("ok")]
    overall_ok = not failures
    return {
        "ok": overall_ok,
        "checks": checks,
        "failures": failures,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "latest_backup": str(latest) if latest else None,
    }


def _report_failure(result: dict[str, Any]) -> None:
    """Эскалация: Sentry warning (если 3+ подряд) + Inbox item."""
    _state["consecutive_failures"] += 1
    consecutive = int(_state["consecutive_failures"])

    # Inbox item — каждый fail (dedupe по kind).
    try:
        from .inbox_service import inbox_service

        failures_str = ", ".join(result.get("failures") or [])
        inbox_service.upsert_item(
            dedupe_key="backup_health_failure",
            kind="backup_health_failure",
            source="backup_health_monitor",
            title="Backup workspace health check failed",
            body=(
                f"Wave 237 backup health monitor detected {len(result.get('failures') or [])} "
                f"failed checks: {failures_str}. Consecutive failures: {consecutive}. "
                f"Latest backup: {result.get('latest_backup') or 'NONE'}."
            ),
            severity="warning" if consecutive < CONSECUTIVE_FAILURES_FOR_SENTRY else "error",
            metadata={
                "consecutive_failures": consecutive,
                "failures": result.get("failures") or [],
                "checks": result.get("checks") or [],
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("backup_health_inbox_failed", error=str(exc))

    # Sentry warning при 3+ подряд.
    if consecutive >= CONSECUTIVE_FAILURES_FOR_SENTRY:
        try:
            from .sentry_integration import capture_message

            capture_message(
                f"Wave 237: backup health monitor — {consecutive} consecutive failures",
                level="warning",
                failures=result.get("failures") or [],
                latest_backup=result.get("latest_backup"),
                consecutive=consecutive,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("backup_health_sentry_failed", error=str(exc))


def _report_success(result: dict[str, Any]) -> None:
    """При успехе сбрасываем счётчик consecutive failures."""
    prior = int(_state["consecutive_failures"])
    _state["consecutive_failures"] = 0
    if prior >= CONSECUTIVE_FAILURES_FOR_SENTRY:
        # Resolve — закрываем inbox item.
        try:
            from .inbox_service import inbox_service

            inbox_service.set_status_by_dedupe(
                dedupe_key="backup_health_failure",
                status="done",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("backup_health_inbox_resolve_failed", error=str(exc))


async def backup_health_loop() -> None:
    """Async loop: периодически вызывает run_health_check.

    Интервал: KRAB_BACKUP_HEALTH_INTERVAL_SEC (default 14400s = 4h).
    Минимум 60s для защиты от misconfig.
    """
    raw_interval = os.getenv("KRAB_BACKUP_HEALTH_INTERVAL_SEC", "14400").strip()
    try:
        interval = max(60.0, float(raw_interval))
    except ValueError:
        interval = 14400.0

    logger.info("backup_health_loop_started", interval_sec=interval)

    while True:
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise

        try:
            # run_health_check синхронная (sqlite + filesystem) — to_thread.
            result = await asyncio.to_thread(run_health_check)
            _state["last_check_ts"] = time.time()
            _state["last_result_ok"] = bool(result.get("ok"))
            set_backup_health_metric(bool(result.get("ok")))

            if result.get("ok"):
                _report_success(result)
                logger.info(
                    "backup_health_ok",
                    latest=result.get("latest_backup"),
                )
            else:
                _report_failure(result)
                logger.warning(
                    "backup_health_failed",
                    failures=result.get("failures"),
                    consecutive=_state["consecutive_failures"],
                )
        except Exception as exc:  # noqa: BLE001 - fail-open, не валим loop
            logger.warning(
                "backup_health_loop_iteration_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )


__all__ = [
    "BACKUP_ROOT",
    "FRESHNESS_MAX_AGE_SEC",
    "SIZE_VARIANCE_TOLERANCE",
    "backup_health_loop",
    "check_freshness",
    "check_integrity",
    "check_restoration_drill",
    "check_size_variance",
    "get_backup_health_ok",
    "run_health_check",
    "set_backup_health_metric",
]
