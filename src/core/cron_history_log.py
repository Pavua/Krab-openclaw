# -*- coding: utf-8 -*-
"""Wave 115: read-interface к cron_history.db.

Wrapper (`scripts/krab_cron_wrap.py`) пишет строки. Этот модуль предоставляет
read-only API для bridge/API endpoint/dashboard: `query_recent(label, limit)`
и `stats_by_label()`.

Singleton pattern, как у `moderation_audit_log`:
- `cron_history_log.configure_default_path(...)` из bootstrap'а
- lazy connection per request (read-only через `sqlite3.connect(uri, mode=ro)`)
- любая ошибка → warn + пустой результат, не бросать exception в hot path
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)


def _classify_exit(exit_code: int) -> str:
    """ok если 0, fail иначе. Wave 115."""
    return "ok" if int(exit_code) == 0 else "fail"


class CronHistoryLog:
    """Read interface к cron_history.db.

    Запись делает только wrapper (`scripts/krab_cron_wrap.py`). Этот класс
    нужен в основном Python-runtime'е для API/dashboard.
    """

    def __init__(self, *, storage_path: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._storage_path: Path | None = storage_path

    def configure_default_path(self, storage_path: Path) -> None:
        """Установить путь к .db. Идемпотентно."""
        with self._lock:
            self._storage_path = storage_path

    def _open_ro(self) -> sqlite3.Connection | None:
        """Открыть БД в read-only режиме. None если файла нет/path не задан."""
        path = self._storage_path
        if path is None:
            return None
        if not path.exists():
            # Wrapper ещё не успел создать БД — это нормально для свежей системы.
            return None
        try:
            uri = f"file:{path}?mode=ro"
            return sqlite3.connect(uri, uri=True, timeout=2.0)
        except sqlite3.Error as exc:
            logger.warning(
                "cron_history_open_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

    def query_recent(
        self,
        *,
        label: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Последние записи DESC по start_ts. Опциональный фильтр label."""
        try:
            limit_int = int(limit)
        except (TypeError, ValueError):
            limit_int = 50
        if limit_int <= 0:
            return []
        if limit_int > 1000:
            limit_int = 1000

        clauses: list[str] = []
        params: list[Any] = []
        if label is not None:
            label_str = str(label).strip()
            if label_str:
                clauses.append("label = ?")
                params.append(label_str)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT id, label, start_ts, end_ts, exit_code, duration_sec, "
            "stdout_size_bytes, stderr_size_bytes "
            f"FROM cron_history {where} ORDER BY start_ts DESC, id DESC LIMIT ?"
        )
        params.append(limit_int)

        with self._lock:
            conn = self._open_ro()
            if conn is None:
                return []
            try:
                cursor = conn.execute(sql, tuple(params))
                rows = cursor.fetchall()
            except sqlite3.Error as exc:
                logger.warning(
                    "cron_history_query_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return []
            finally:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass

        result: list[dict[str, Any]] = []
        for row in rows:
            id_, lbl, sts, ets, ec, dur, so_sz, se_sz = row
            result.append(
                {
                    "id": id_,
                    "label": lbl,
                    "start_ts": sts,
                    "end_ts": ets,
                    "exit_code": int(ec),
                    "exit_class": _classify_exit(ec),
                    "duration_sec": float(dur),
                    "stdout_size_bytes": int(so_sz),
                    "stderr_size_bytes": int(se_sz),
                }
            )
        return result

    def stats_by_label(self) -> list[dict[str, Any]]:
        """Aggregate stats: per label — count, last_run, avg_duration, fail_pct."""
        sql = (
            "SELECT label, "
            "       COUNT(*) AS total, "
            "       SUM(CASE WHEN exit_code = 0 THEN 1 ELSE 0 END) AS ok_count, "
            "       SUM(CASE WHEN exit_code <> 0 THEN 1 ELSE 0 END) AS fail_count, "
            "       AVG(duration_sec) AS avg_duration, "
            "       MAX(start_ts) AS last_run "
            "FROM cron_history "
            "GROUP BY label "
            "ORDER BY last_run DESC"
        )
        with self._lock:
            conn = self._open_ro()
            if conn is None:
                return []
            try:
                cursor = conn.execute(sql)
                rows = cursor.fetchall()
            except sqlite3.Error as exc:
                logger.warning(
                    "cron_history_stats_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return []
            finally:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass

        out: list[dict[str, Any]] = []
        for row in rows:
            label, total, ok_count, fail_count, avg_dur, last_run = row
            total_int = int(total or 0)
            fail_int = int(fail_count or 0)
            fail_pct = (fail_int / total_int * 100.0) if total_int else 0.0
            out.append(
                {
                    "label": label,
                    "total": total_int,
                    "ok_count": int(ok_count or 0),
                    "fail_count": fail_int,
                    "fail_pct": round(fail_pct, 2),
                    "avg_duration_sec": round(float(avg_dur or 0.0), 3),
                    "last_run": last_run,
                }
            )
        return out


cron_history_log = CronHistoryLog()
