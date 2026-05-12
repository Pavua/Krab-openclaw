# -*- coding: utf-8 -*-
"""
src/core/swarm_activity_log.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Wave 89: Персистентный activity log для всех swarm-запусков.

Зачем нужен:

- swarm_task_board хранит активные/recent задачи (Kanban), но протухает.
- swarm_artifact_store хранит результаты, но без agregated stats.
- Нет способа ответить на вопросы вроде «что свёрм делал в среду?» или
  «какая средняя latency creative за неделю?» без парсинга логов руками.

Решение: SQLite таблица `swarm_activity` с двухфазной записью:

1. `log_swarm_start(team, topic) → activity_id` сразу при входе в run_round.
2. `log_swarm_complete(activity_id, status, latency_ms, artifact_ref, errors)`
   в finally блоке.

Best-effort: все ошибки IO/SQLite ловятся и логируются как warning, hot path
не падает.

Инварианты:
- DB файл создаётся лениво (на первом write).
- Schema идемпотентна (CREATE TABLE IF NOT EXISTS + индексы).
- `query_recent` / `stats_by_team` возвращают копии (list of dicts).
- Timestamps хранятся как `INTEGER ts` (epoch seconds, UTC).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)


_DEFAULT_DB_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "swarm_activity.db"


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS swarm_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    team TEXT NOT NULL,
    topic TEXT NOT NULL,
    status TEXT NOT NULL,
    latency_ms INTEGER,
    artifact_ref TEXT,
    errors TEXT
);
CREATE INDEX IF NOT EXISTS idx_swarm_activity_team ON swarm_activity(team);
CREATE INDEX IF NOT EXISTS idx_swarm_activity_ts ON swarm_activity(ts DESC);
CREATE INDEX IF NOT EXISTS idx_swarm_activity_status ON swarm_activity(status);
"""


class SwarmActivityLog:
    """Потокобезопасный SQLite-логгер активности свёрма.

    Используется как module-level singleton (`swarm_activity_log` ниже).
    Принимает `db_path` ТОЛЬКО для unit-тестов; в рантайме singleton
    инициализируется через `configure_default_path()` из bootstrap.
    """

    def __init__(self, *, db_path: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._db_path: Path | None = db_path
        self._initialized: bool = False
        if db_path is not None:
            self._ensure_schema()

    # ---- Configuration --------------------------------------------------

    def configure_default_path(self, db_path: Path) -> None:
        """Устанавливает путь к SQLite файлу и инициализирует schema.

        Вызывается один раз при bootstrap. Идемпотентно при повторном
        вызове — schema CREATE IF NOT EXISTS.
        """
        with self._lock:
            self._db_path = db_path
            self._initialized = False
            self._ensure_schema()

    # ---- Public API -----------------------------------------------------

    def log_swarm_start(self, team: str, topic: str) -> int | None:
        """Регистрирует начало swarm-запроса. Возвращает activity_id (rowid).

        При ошибках возвращает None — caller продолжает работу без записи.
        """
        team_clean = (team or "unknown").strip()[:64] or "unknown"
        topic_clean = (topic or "").strip()[:2000]
        ts = int(time.time())
        try:
            with self._lock:
                conn = self._connect()
                if conn is None:
                    return None
                try:
                    cur = conn.execute(
                        "INSERT INTO swarm_activity "
                        "(ts, team, topic, status, latency_ms, artifact_ref, errors) "
                        "VALUES (?, ?, ?, 'started', NULL, NULL, NULL)",
                        (ts, team_clean, topic_clean),
                    )
                    conn.commit()
                    return int(cur.lastrowid) if cur.lastrowid is not None else None
                finally:
                    conn.close()
        except (sqlite3.Error, OSError) as exc:
            logger.warning(
                "swarm_activity_log_start_failed",
                team=team_clean,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

    def log_swarm_complete(
        self,
        activity_id: int | None,
        *,
        status: str = "done",
        latency_ms: int | None = None,
        artifact_ref: str | None = None,
        errors: list[str] | None = None,
    ) -> bool:
        """Обновляет запись со status/latency/artifact/errors.

        `activity_id=None` → no-op (если start не записался).
        Возвращает True если запись обновлена.
        """
        if activity_id is None:
            return False
        status_clean = (status or "done").strip()[:32] or "done"
        latency_int: int | None
        try:
            latency_int = int(latency_ms) if latency_ms is not None else None
        except (TypeError, ValueError):
            latency_int = None
        artifact_clean = (artifact_ref or None) and str(artifact_ref)[:512]
        errors_json: str | None
        if errors:
            try:
                errors_json = json.dumps(list(errors), ensure_ascii=False)[:4000]
            except (TypeError, ValueError):
                errors_json = None
        else:
            errors_json = None

        try:
            with self._lock:
                conn = self._connect()
                if conn is None:
                    return False
                try:
                    cur = conn.execute(
                        "UPDATE swarm_activity SET status=?, latency_ms=?, "
                        "artifact_ref=?, errors=? WHERE id=?",
                        (status_clean, latency_int, artifact_clean, errors_json, int(activity_id)),
                    )
                    conn.commit()
                    return cur.rowcount > 0
                finally:
                    conn.close()
        except (sqlite3.Error, OSError) as exc:
            logger.warning(
                "swarm_activity_log_complete_failed",
                activity_id=activity_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return False

    def query_recent(self, limit: int = 20, team: str | None = None) -> list[dict[str, Any]]:
        """Возвращает последние записи (по убыванию ts).

        `team=None` → все команды. `limit` clamp в [1, 1000].
        """
        try:
            safe_limit = max(1, min(int(limit), 1000))
        except (TypeError, ValueError):
            safe_limit = 20

        try:
            with self._lock:
                conn = self._connect()
                if conn is None:
                    return []
                try:
                    if team:
                        rows = conn.execute(
                            "SELECT id, ts, team, topic, status, latency_ms, "
                            "artifact_ref, errors FROM swarm_activity "
                            "WHERE team=? ORDER BY ts DESC, id DESC LIMIT ?",
                            (str(team).strip()[:64], safe_limit),
                        ).fetchall()
                    else:
                        rows = conn.execute(
                            "SELECT id, ts, team, topic, status, latency_ms, "
                            "artifact_ref, errors FROM swarm_activity "
                            "ORDER BY ts DESC, id DESC LIMIT ?",
                            (safe_limit,),
                        ).fetchall()
                finally:
                    conn.close()
        except (sqlite3.Error, OSError) as exc:
            logger.warning(
                "swarm_activity_log_query_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []

        return [self._row_to_dict(row) for row in rows]

    def stats_by_team(self) -> dict[str, dict[str, Any]]:
        """Возвращает агрегированную статистику по командам.

        Формат: ``{team: {count, done, failed, started, avg_latency_ms, success_rate}}``.
        success_rate = done / (done + failed); если нет завершённых — 0.0.
        """
        try:
            with self._lock:
                conn = self._connect()
                if conn is None:
                    return {}
                try:
                    rows = conn.execute(
                        "SELECT team, status, COUNT(*) AS cnt, "
                        "AVG(latency_ms) AS avg_lat "
                        "FROM swarm_activity GROUP BY team, status"
                    ).fetchall()
                finally:
                    conn.close()
        except (sqlite3.Error, OSError) as exc:
            logger.warning(
                "swarm_activity_log_stats_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return {}

        out: dict[str, dict[str, Any]] = {}
        # rows: (team, status, cnt, avg_lat)
        # Накапливаем latency только для status=done (failed/started не репрезентативны).
        for team, status, cnt, avg_lat in rows:
            bucket = out.setdefault(
                str(team),
                {
                    "count": 0,
                    "started": 0,
                    "done": 0,
                    "failed": 0,
                    "avg_latency_ms": None,
                    "success_rate": 0.0,
                },
            )
            bucket["count"] += int(cnt)
            if status == "done":
                bucket["done"] += int(cnt)
                if avg_lat is not None:
                    bucket["avg_latency_ms"] = round(float(avg_lat), 1)
            elif status == "failed":
                bucket["failed"] += int(cnt)
            elif status == "started":
                bucket["started"] += int(cnt)

        for bucket in out.values():
            done = bucket["done"]
            failed = bucket["failed"]
            total_finished = done + failed
            if total_finished > 0:
                bucket["success_rate"] = round(done / total_finished, 4)
        return out

    # ---- Internal -------------------------------------------------------

    def _connect(self) -> sqlite3.Connection | None:
        """Открывает соединение. None если путь не настроен."""
        path = self._db_path
        if path is None:
            return None
        if not self._initialized:
            self._ensure_schema()
            if not self._initialized:
                return None
        # check_same_thread=False — мы держим свой RLock.
        return sqlite3.connect(
            str(path),
            timeout=5.0,
            isolation_level=None,
            check_same_thread=False,
        )

    def _ensure_schema(self) -> None:
        path = self._db_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path), timeout=5.0)
            try:
                conn.executescript(_SCHEMA_SQL)
                conn.commit()
            finally:
                conn.close()
            self._initialized = True
        except (sqlite3.Error, OSError) as exc:
            logger.warning(
                "swarm_activity_log_schema_init_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )

    @staticmethod
    def _row_to_dict(row: tuple) -> dict[str, Any]:
        errors_raw = row[7]
        errors_list: list[str] = []
        if errors_raw:
            try:
                parsed = json.loads(errors_raw)
                if isinstance(parsed, list):
                    errors_list = [str(x) for x in parsed]
            except (json.JSONDecodeError, TypeError):
                errors_list = []
        return {
            "id": int(row[0]),
            "ts": int(row[1]),
            "team": str(row[2]),
            "topic": str(row[3]),
            "status": str(row[4]),
            "latency_ms": int(row[5]) if row[5] is not None else None,
            "artifact_ref": row[6],
            "errors": errors_list,
        }


# Module-level singleton (pattern: chat_ban_cache, inbox_service).
swarm_activity_log = SwarmActivityLog()


def configure_default_swarm_activity_log() -> None:
    """Convenience bootstrap helper: настраивает singleton на дефолтный путь."""
    swarm_activity_log.configure_default_path(_DEFAULT_DB_PATH)
