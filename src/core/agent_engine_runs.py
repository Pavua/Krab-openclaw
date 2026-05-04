# -*- coding: utf-8 -*-
"""Helpers для записи и запроса agent_engine_runs в archive.db.

Wave 17-B (Hermes Phase C): A/B instrumentation для сравнения
OpenClaw vs Hermes по latency, tokens, success rate.

Таблица создаётся лениво при первом использовании (backward compat).
Существующий archive.db продолжает работать без изменений.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

# Путь к archive.db — совпадает с memory_archive.DEFAULT_ARCHIVE_PATH
_DEFAULT_DB_PATH = Path("~/.openclaw/krab_memory/archive.db").expanduser()

# DDL новой таблицы — IF NOT EXISTS, backward compat
_DDL_ENGINE_RUNS = """
CREATE TABLE IF NOT EXISTS agent_engine_runs (
    run_id TEXT PRIMARY KEY,
    started_at INTEGER NOT NULL,
    finished_at INTEGER,
    engine TEXT NOT NULL,
    chat_id TEXT,
    room TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    tool_calls INTEGER DEFAULT 0,
    latency_ms_ttfb INTEGER,
    latency_ms_total INTEGER,
    success INTEGER DEFAULT 0,
    fallback_engine TEXT,
    user_signal TEXT,
    cost_usd REAL DEFAULT 0
);
"""

_DDL_ENGINE_RUNS_IDX_ENGINE = """
CREATE INDEX IF NOT EXISTS idx_agent_engine_runs_engine
    ON agent_engine_runs(engine);
"""

_DDL_ENGINE_RUNS_IDX_STARTED = """
CREATE INDEX IF NOT EXISTS idx_agent_engine_runs_started
    ON agent_engine_runs(started_at DESC);
"""


def _get_db_path() -> Path:
    """Путь к archive.db (можно переопределить в тестах через env)."""
    import os

    env_path = os.environ.get("KRAB_ARCHIVE_DB_PATH")
    if env_path:
        return Path(env_path)
    return _DEFAULT_DB_PATH


def _ensure_table(conn: sqlite3.Connection) -> None:
    """Создаёт таблицу и индексы если не существуют."""
    conn.execute(_DDL_ENGINE_RUNS)
    conn.execute(_DDL_ENGINE_RUNS_IDX_ENGINE)
    conn.execute(_DDL_ENGINE_RUNS_IDX_STARTED)
    conn.commit()


def _open_conn() -> sqlite3.Connection | None:
    """Открывает соединение с archive.db. None если DB недоступна."""
    db_path = _get_db_path()
    if not db_path.exists():
        # DB не инициализирована — не создаём её сами (это задача memory_archive)
        logger.debug("agent_engine_runs_db_not_found", path=str(db_path))
        return None
    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        _ensure_table(conn)
        return conn
    except sqlite3.Error as exc:
        logger.warning("agent_engine_runs_db_open_failed", error=str(exc))
        return None


def record_engine_run(
    *,
    engine: str,
    chat_id: str | None = None,
    room: str | None = None,
    started_at_ms: int | None = None,
    finished_at_ms: int | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    reasoning_tokens: int = 0,
    tool_calls: int = 0,
    latency_ms_ttfb: int | None = None,
    latency_ms_total: int | None = None,
    success: bool = False,
    fallback_engine: str | None = None,
    user_signal: str | None = None,
    cost_usd: float = 0.0,
) -> str | None:
    """Записывает run в archive.db. Возвращает run_id или None при ошибке.

    Fail-safe — ошибки записи не пробрасываются в caller.
    """
    run_id = str(uuid.uuid4())
    now_ms = int(time.time() * 1000)
    started_at = started_at_ms if started_at_ms is not None else now_ms
    finished_at = finished_at_ms if finished_at_ms is not None else now_ms

    conn = _open_conn()
    if conn is None:
        return None

    try:
        conn.execute(
            """
            INSERT INTO agent_engine_runs (
                run_id, started_at, finished_at, engine,
                chat_id, room,
                prompt_tokens, completion_tokens, reasoning_tokens, tool_calls,
                latency_ms_ttfb, latency_ms_total,
                success, fallback_engine, user_signal, cost_usd
            ) VALUES (
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?
            )
            """,
            (
                run_id,
                started_at,
                finished_at,
                engine,
                chat_id,
                room,
                prompt_tokens,
                completion_tokens,
                reasoning_tokens,
                tool_calls,
                latency_ms_ttfb,
                latency_ms_total,
                1 if success else 0,
                fallback_engine,
                user_signal,
                cost_usd,
            ),
        )
        conn.commit()
        return run_id
    except sqlite3.Error as exc:
        logger.warning("agent_engine_runs_record_failed", error=str(exc))
        return None
    finally:
        conn.close()


def get_engine_comparison(window_days: int = 7) -> dict[str, Any]:
    """Сравнение OpenClaw vs Hermes за последние window_days дней.

    Возвращает dict с агрегатами по engine:
    {
        "openclaw": {"runs": N, "success_rate": 0.95, "avg_latency_ms": 450, ...},
        "hermes":   {...},
        "window_days": 7,
        "generated_at": "...",
    }
    """
    conn = _open_conn()
    if conn is None:
        return {"error": "db_unavailable", "window_days": window_days}

    cutoff_ms = int((time.time() - window_days * 86400) * 1000)

    try:
        rows = conn.execute(
            """
            SELECT
                engine,
                COUNT(*) AS runs,
                SUM(success) AS successes,
                AVG(latency_ms_total) AS avg_latency_ms,
                AVG(latency_ms_ttfb) AS avg_ttfb_ms,
                SUM(prompt_tokens) AS total_prompt_tokens,
                SUM(completion_tokens) AS total_completion_tokens,
                SUM(tool_calls) AS total_tool_calls,
                SUM(cost_usd) AS total_cost_usd,
                COUNT(fallback_engine) AS fallback_count
            FROM agent_engine_runs
            WHERE started_at >= ?
            GROUP BY engine
            """,
            (cutoff_ms,),
        ).fetchall()

        result: dict[str, Any] = {
            "window_days": window_days,
            "generated_at": _now_iso(),
            "engines": {},
        }
        for row in rows:
            engine = row["engine"]
            runs = row["runs"] or 0
            successes = row["successes"] or 0
            result["engines"][engine] = {
                "runs": runs,
                "success_rate": round(successes / runs, 4) if runs > 0 else 0.0,
                "avg_latency_ms": round(row["avg_latency_ms"] or 0, 1),
                "avg_ttfb_ms": round(row["avg_ttfb_ms"] or 0, 1),
                "total_prompt_tokens": int(row["total_prompt_tokens"] or 0),
                "total_completion_tokens": int(row["total_completion_tokens"] or 0),
                "total_tool_calls": int(row["total_tool_calls"] or 0),
                "total_cost_usd": round(row["total_cost_usd"] or 0.0, 6),
                "fallback_count": int(row["fallback_count"] or 0),
            }
        return result
    except sqlite3.Error as exc:
        logger.warning("agent_engine_comparison_failed", error=str(exc))
        return {"error": str(exc), "window_days": window_days}
    finally:
        conn.close()


def list_engine_runs(
    engine: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Список runs из archive.db.

    engine=None — все движки. Сортировка: новые первые.
    """
    conn = _open_conn()
    if conn is None:
        return []

    try:
        if engine:
            rows = conn.execute(
                """
                SELECT * FROM agent_engine_runs
                WHERE engine = ?
                ORDER BY started_at DESC
                LIMIT ? OFFSET ?
                """,
                (engine, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM agent_engine_runs
                ORDER BY started_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()

        return [dict(row) for row in rows]
    except sqlite3.Error as exc:
        logger.warning("agent_engine_list_runs_failed", error=str(exc))
        return []
    finally:
        conn.close()


def _now_iso() -> str:
    """Текущее UTC время в ISO 8601."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
