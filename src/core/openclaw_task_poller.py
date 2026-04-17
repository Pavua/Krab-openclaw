# -*- coding: utf-8 -*-
"""
Поллер состояния задач OpenClaw Gateway (Phase 4+ watchdog).

Читает ~/.openclaw/tasks/runs.sqlite напрямую (без WS/HTTP).
Используется LLM flow для:
  1. Показа реального прогресса (tool calls, stages) в Telegram notice
  2. Обнаружения зависших задач (last_event_at stale > N секунд)
  3. HTTP-пинга gateway health (/healthz) каждые 30 сек — если мёртв, сообщаем
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from structlog import get_logger

logger = get_logger(__name__)

RUNS_DB_PATH = Path("~/.openclaw/tasks/runs.sqlite").expanduser()
# Если last_event_at старше этого — считаем задачу зависшей (для UI-иконки).
STALE_THRESHOLD_SEC = 90.0
# Порог стагнации для hard-cancel текущего LLM-call (default 120s, override env).
# Отличается от STALE_THRESHOLD_SEC: stale — индикация, stagnation — триггер отмены.
STAGNATION_THRESHOLD_SEC = float(os.getenv("LLM_STAGNATION_THRESHOLD_SEC", "120"))
# Gateway HTTP endpoint для liveness probe
GATEWAY_HEALTH_URL = "http://127.0.0.1:18789/healthz"
GATEWAY_HEALTH_TIMEOUT_SEC = 5.0


@dataclass(frozen=True)
class TaskState:
    """Снимок состояния активной задачи OpenClaw."""

    task_id: str
    status: str  # running | queued | succeeded | failed
    label: str
    progress_summary: str
    last_event_at_ms: int
    is_stale: bool  # last_event_at > STALE_THRESHOLD_SEC назад


def poll_active_tasks() -> list[TaskState]:
    """
    Читает running/queued задачи из runs.sqlite.

    Возвращает пустой список если БД недоступна или нет активных задач.
    Не бросает исключений — только логирует debug.
    """
    if not RUNS_DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{RUNS_DB_PATH}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT task_id, status, label, progress_summary, last_event_at "
                "FROM task_runs WHERE status IN ('running', 'queued') "
                "ORDER BY last_event_at DESC;"
            ).fetchall()
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as exc:
        logger.debug("openclaw_task_poller_error", error=str(exc))
        return []

    now_ms = int(time.time() * 1000)
    result: list[TaskState] = []
    for row in rows:
        task_id, status, label, progress, last_event_ms = row
        age_sec = (now_ms - (last_event_ms or 0)) / 1000.0
        result.append(
            TaskState(
                task_id=str(task_id or ""),
                status=str(status or ""),
                label=str(label or ""),
                progress_summary=str(progress or ""),
                last_event_at_ms=int(last_event_ms or 0),
                is_stale=age_sec > STALE_THRESHOLD_SEC,
            )
        )
    return result


def poll_gateway_liveness() -> tuple[bool, str]:
    """
    Проверяет жив ли gateway: есть ли runs.sqlite + есть ли recent activity.

    Returns: (is_alive, reason)
    Не выполняет HTTP — только локальная проверка БД.
    Используется как быстрый тест без I/O в event loop.
    """
    if not RUNS_DB_PATH.exists():
        return False, "runs.sqlite not found"
    try:
        conn = sqlite3.connect(f"file:{RUNS_DB_PATH}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT MAX(last_event_at) FROM task_runs;"
            ).fetchone()
        finally:
            conn.close()
        if row and row[0]:
            age_sec = (time.time() * 1000 - row[0]) / 1000.0
            if age_sec < 3600:  # активность в последний час
                return True, f"last_activity_{int(age_sec)}s_ago"
            return True, f"idle_{int(age_sec)}s"
        return True, "no_tasks"
    except (sqlite3.Error, OSError) as exc:
        return False, str(exc)


async def check_gateway_http_alive() -> bool:
    """
    Быстрый async HTTP ping gateway health endpoint.

    Returns True если gateway отвечает 200 на /healthz.
    Не бросает исключений — возвращает False при любой ошибке.
    Таймаут 5 сек.
    """
    try:
        import httpx

        async with httpx.AsyncClient(timeout=GATEWAY_HEALTH_TIMEOUT_SEC) as client:
            r = await client.get(GATEWAY_HEALTH_URL)
            return r.status_code == 200
    except Exception as exc:  # noqa: BLE001
        logger.debug("gateway_http_health_check_failed", error=str(exc))
        return False


def format_task_progress_for_telegram(tasks: list[TaskState]) -> str:
    """Форматирует активные задачи для Telegram notice."""
    if not tasks:
        return ""
    parts = []
    for t in tasks[:3]:  # Максимум 3 задачи
        if t.is_stale:
            status_icon = "⚠️"
        elif t.status == "running":
            status_icon = "🔄"
        else:
            status_icon = "⏳"
        line = f"{status_icon} {t.label or 'задача'}"
        if t.progress_summary:
            # Берём первые 80 символов summary
            summary = t.progress_summary[:80]
            if len(t.progress_summary) > 80:
                summary += "…"
            line += f"\n   {summary}"
        if t.is_stale:
            age_sec = (time.time() * 1000 - t.last_event_at_ms) / 1000.0
            line += f"\n   ⚠️ Нет активности {int(age_sec)} сек"
        parts.append(line)
    return "\n".join(parts)


def check_tasks_hung(
    tasks: list[TaskState],
    *,
    hung_threshold_sec: float = 180.0,
) -> Optional[float]:
    """
    Проверяет, все ли running-задачи зависли дольше hung_threshold_sec.

    Returns: максимальный stale_sec если все зависли, иначе None.
    """
    if not tasks:
        return None
    running = [t for t in tasks if t.status == "running"]
    if not running:
        return None
    now_ms = time.time() * 1000
    stale_secs = [(now_ms - t.last_event_at_ms) / 1000.0 for t in running]
    if all(s > hung_threshold_sec for s in stale_secs):
        return max(stale_secs)
    return None


def detect_stagnation(
    tasks: list[TaskState],
    threshold_sec: float = STAGNATION_THRESHOLD_SEC,
) -> list[TaskState]:
    """
    Возвращает running/queued задачи, которые зависли (last_event_at > threshold ago).

    Используется LLM flow как hard-trigger для cancel текущего in-flight call:
    если gateway watchdog видит N секунд без новых событий — значит codex-cli
    subprocess hung после gateway restart, и ждать бесконечно уже бессмысленно.

    Игнорирует:
      - задачи в статусе не running/queued (завершённые/failed)
      - задачи без last_event_at_ms (<=0 → фиктивное значение)
    """
    if not tasks:
        return []
    now_ms = int(time.time() * 1000)
    stagnant: list[TaskState] = []
    for task in tasks:
        if task.status not in ("running", "queued"):
            continue
        if not task.last_event_at_ms or task.last_event_at_ms <= 0:
            continue
        age_sec = (now_ms - task.last_event_at_ms) / 1000.0
        if age_sec > threshold_sec:
            stagnant.append(task)
    return stagnant
