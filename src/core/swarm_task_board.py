# -*- coding: utf-8 -*-
"""
src/core/swarm_task_board.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Task board для swarm teams — хранит задачи, статусы и артефакты.

Паттерн: singleton, JSON persist в ~/.openclaw/krab_runtime_state/swarm_task_board.json.
FIFO: максимум 200 задач. При переполнении удаляются самые старые completed.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

_STATE_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "swarm_task_board.json"

# Максимум задач в board (FIFO — сначала удаляются старые completed)
_MAX_TASKS = 200

# Порог (мс) для warning-лога при медленной загрузке с диска.
# Wave 22-H: блокирующий _load в async контексте замораживает event-loop,
# поэтому любая медленная загрузка должна быть видна в логах.
_SLOW_LOAD_WARN_MS = 500.0

# Допустимые значения статуса
VALID_STATUSES = frozenset({"pending", "in_progress", "done", "failed", "blocked"})

# Допустимые приоритеты
VALID_PRIORITIES = frozenset({"low", "medium", "high", "critical"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SwarmTask:
    """Одна задача на board."""

    task_id: str
    team: str
    title: str
    description: str
    status: str  # pending/in_progress/done/failed/blocked
    created_by: str  # owner / delegation / scheduler
    assigned_to: str  # имя команды
    priority: str  # low/medium/high/critical
    created_at: str
    updated_at: str
    result: str = ""  # результат после done
    artifacts: list[str] = field(default_factory=list)  # пути к файлам
    parent_task_id: str = ""  # для цепочек делегирования

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SwarmTask:
        return cls(
            task_id=d.get("task_id", ""),
            team=d.get("team", ""),
            title=d.get("title", ""),
            description=d.get("description", ""),
            status=d.get("status", "pending"),
            created_by=d.get("created_by", ""),
            assigned_to=d.get("assigned_to", ""),
            priority=d.get("priority", "medium"),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            result=d.get("result", ""),
            artifacts=list(d.get("artifacts", [])),
            parent_task_id=d.get("parent_task_id", ""),
        )


class SwarmTaskBoard:
    """
    Персистентный task board для swarm teams.

    Данные: list[SwarmTask] в JSON.
    При превышении _MAX_TASKS сначала удаляются старые задачи со статусом done,
    затем failed, затем blocked.
    """

    def __init__(self, state_path: Path | None = None) -> None:
        self._path = state_path or _STATE_PATH
        # dict task_id -> dict для быстрого доступа
        self._tasks: dict[str, dict[str, Any]] = {}
        self._load()

    # -- persistence ----------------------------------------------------------

    def _load(self) -> None:
        """
        Синхронная загрузка board с диска.

        Wave 22-H: добавлена инструментация elapsed_ms. Для загрузки из async
        контекста используй load_async(), чтобы не блокировать event-loop.
        """
        t0 = time.monotonic()
        if not self._path.exists():
            self._tasks = {}
            elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
            logger.info(
                "swarm_task_board_loaded",
                total=0,
                elapsed_ms=elapsed_ms,
                path_missing=True,
            )
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
            # поддержка обоих форматов: dict {task_id: {...}} или list [{...}]
            if isinstance(data, list):
                self._tasks = {item["task_id"]: item for item in data if "task_id" in item}
            elif isinstance(data, dict):
                self._tasks = data
            else:
                self._tasks = {}
            elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
            logger.info(
                "swarm_task_board_loaded",
                total=len(self._tasks),
                elapsed_ms=elapsed_ms,
            )
            if elapsed_ms > _SLOW_LOAD_WARN_MS:
                logger.warning(
                    "swarm_task_board_slow_load",
                    total=len(self._tasks),
                    elapsed_ms=elapsed_ms,
                    threshold_ms=_SLOW_LOAD_WARN_MS,
                )
        except (json.JSONDecodeError, OSError) as exc:
            elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
            logger.warning(
                "swarm_task_board_load_failed",
                error=str(exc),
                error_type=type(exc).__name__,
                elapsed_ms=elapsed_ms,
            )
            self._tasks = {}

    async def load_async(self) -> None:
        """
        Асинхронная загрузка — оборачивает sync _load в asyncio.to_thread.

        Использовать в startup path вместо прямого _load(), когда singleton
        уже инициализирован (например, после configure_default_path с диска
        большого размера). Это гарантирует, что event-loop не замораживается
        на время JSON parse.
        """
        await asyncio.to_thread(self._load)

    def _persist(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self._tasks, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except OSError as exc:
            logger.error(
                "swarm_task_board_save_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    def _trim_if_needed(self) -> None:
        """FIFO: удаляет старые задачи при превышении лимита."""
        if len(self._tasks) <= _MAX_TASKS:
            return

        # Очерёдность удаления: done → failed → blocked → oldest any
        for evict_status in ("done", "failed", "blocked"):
            candidates = [t for t in self._tasks.values() if t.get("status") == evict_status]
            # Сортируем по updated_at — самые старые первыми
            candidates.sort(key=lambda t: t.get("updated_at", ""))
            for task in candidates:
                if len(self._tasks) <= _MAX_TASKS:
                    break
                self._tasks.pop(task["task_id"], None)
            if len(self._tasks) <= _MAX_TASKS:
                return

        # Если всё ещё переполнено — удаляем самые старые pending/in_progress
        remaining = sorted(self._tasks.values(), key=lambda t: t.get("created_at", ""))
        while len(self._tasks) > _MAX_TASKS and remaining:
            oldest = remaining.pop(0)
            self._tasks.pop(oldest["task_id"], None)

    # -- public API -----------------------------------------------------------

    def create_task(
        self,
        team: str,
        title: str,
        description: str,
        priority: str = "medium",
        created_by: str = "owner",
        parent_task_id: str = "",
    ) -> SwarmTask:
        """Создаёт новую задачу и добавляет на board."""
        now = _now_iso()
        task_id = f"{team}_{uuid.uuid4().hex[:8]}_{int(time.time())}"

        task = SwarmTask(
            task_id=task_id,
            team=team.lower(),
            title=title[:500],
            description=description[:2000],
            status="pending",
            created_by=created_by,
            assigned_to=team.lower(),
            priority=priority if priority in VALID_PRIORITIES else "medium",
            created_at=now,
            updated_at=now,
            parent_task_id=parent_task_id,
        )

        self._tasks[task_id] = asdict(task)
        self._trim_if_needed()
        self._persist()

        logger.info(
            "swarm_task_created",
            task_id=task_id,
            team=team,
            priority=task.priority,
        )
        return task

    def update_task(self, target_id: str, **changes: Any) -> SwarmTask | None:
        """Обновляет поля задачи. Возвращает None если задача не найдена."""
        raw = self._tasks.get(target_id)
        if raw is None:
            logger.warning("swarm_task_update_not_found", task_id=target_id)
            return None

        # Запрещаем прямое изменение task_id и created_at
        changes.pop("task_id", None)
        changes.pop("created_at", None)

        # Валидация статуса и приоритета
        if "status" in changes and changes["status"] not in VALID_STATUSES:
            logger.warning(
                "swarm_task_invalid_status",
                task_id=target_id,
                status=changes["status"],
            )
            changes.pop("status")
        if "priority" in changes and changes["priority"] not in VALID_PRIORITIES:
            changes.pop("priority")

        raw.update(changes)
        raw["updated_at"] = _now_iso()
        self._persist()

        logger.info("swarm_task_updated", task_id=target_id, fields=list(changes.keys()))
        return SwarmTask.from_dict(raw)

    def complete_task(
        self,
        task_id: str,
        result: str = "",
        artifacts: list[str] | None = None,
    ) -> SwarmTask | None:
        """Переводит задачу в статус done с результатом."""
        return self.update_task(
            task_id,
            status="done",
            result=result,
            artifacts=list(artifacts or []),
        )

    def fail_task(self, task_id: str, reason: str = "") -> SwarmTask | None:
        """Переводит задачу в статус failed."""
        return self.update_task(task_id, status="failed", result=reason)

    def get_task(self, task_id: str) -> SwarmTask | None:
        """Возвращает задачу по ID или None."""
        raw = self._tasks.get(task_id)
        if raw is None:
            return None
        return SwarmTask.from_dict(raw)

    def list_tasks(
        self,
        team: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[SwarmTask]:
        """
        Список задач с фильтрацией.

        Возвращает копию (не ссылку на внутреннее состояние).
        Сортировка: новые первыми (по created_at desc).
        """
        tasks = list(self._tasks.values())

        if team is not None:
            tasks = [t for t in tasks if t.get("team") == team.lower()]
        if status is not None:
            tasks = [t for t in tasks if t.get("status") == status]

        tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)
        return [SwarmTask.from_dict(t) for t in tasks[:limit]]

    def get_board_summary(self) -> dict[str, Any]:
        """Возвращает сводку: кол-во задач по статусу и команде."""
        by_status: dict[str, int] = {}
        by_team: dict[str, int] = {}

        for raw in self._tasks.values():
            st = raw.get("status", "unknown")
            tm = raw.get("team", "unknown")
            by_status[st] = by_status.get(st, 0) + 1
            by_team[tm] = by_team.get(tm, 0) + 1

        return {
            "total": len(self._tasks),
            "by_status": dict(by_status),
            "by_team": dict(by_team),
        }

    def configure_default_path(self, path: Path) -> None:
        """Переинициализирует board с новым путём (используется в bootstrap)."""
        self._path = path
        self._tasks = {}
        self._load()

    async def configure_default_path_async(self, path: Path) -> None:
        """
        Async-вариант configure_default_path: оборачивает блокирующий _load
        в asyncio.to_thread. Использовать в startup path, чтобы не тормозить
        event-loop при чтении большого state-файла.
        """
        self._path = path
        self._tasks = {}
        await asyncio.to_thread(self._load)

    def cleanup_old(self, keep_done: int = 0) -> int:
        """
        Удаляет все done/failed задачи, оставляя только keep_done самых новых.

        Возвращает число удалённых задач.
        """
        removed = 0
        terminal = [
            t for t in self._tasks.values()
            if t.get("status") in {"done", "failed"}
        ]
        terminal.sort(key=lambda t: t.get("updated_at", ""), reverse=True)
        for task in terminal[keep_done:]:
            self._tasks.pop(task["task_id"], None)
            removed += 1
        if removed:
            self._persist()
            logger.info("swarm_task_board_cleanup_old", removed=removed, kept=keep_done)
        return removed


# Singleton
swarm_task_board = SwarmTaskBoard()
