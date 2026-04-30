# -*- coding: utf-8 -*-
"""
src/core/swarm_auto_executor.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Автоматический экзекутор задач swarm task board.

Фоновый воркер периодически проверяет задачи с флагом auto_execute=True
и запускает для них swarm round (аналог !swarm <team> <title>).

Гейт: KRAB_SWARM_AUTO_EXECUTE_ENABLED=1 (дефолт выключено).
Rate limit: максимум KRAB_SWARM_AUTO_EXECUTE_MAX_PER_HOUR выполнений в час,
            максимум 1 авто-выполнение за цикл проверки.

Связь:
- swarm_task_board.py — источник задач и обновление статусов
- swarm.py / swarm_bus.py — фактическое выполнение через AgentRoom
- swarm_artifact_store.py — сохранение результатов
- swarm_scheduler.py — аналогичный паттерн bind/start/stop
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from ..config import config
from .logger import get_logger
from .swarm_task_board import SwarmTask, swarm_task_board

logger = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SwarmAutoExecutor:
    """
    Фоновый воркер автоматического выполнения задач с auto_execute=True.

    Алгоритм одного цикла:
    1. Получить pending-задачи с auto_execute=True (не более 1)
    2. Проверить rate limit (max_per_hour)
    3. Запустить AgentRoom.run_round для выбранной задачи
    4. Обновить статус задачи (in_progress → done/failed) + сохранить результат
    """

    def __init__(self) -> None:
        # Привязываемые зависимости (аналог SwarmScheduler.bind)
        self._sender: Callable[[str, str], Awaitable[None]] | None = None
        self._router_factory: Any = None
        self._owner_chat_id: str = ""

        # Фоновая asyncio-задача
        self._task: asyncio.Task | None = None
        self._started = False

        # Rate limit: хранит unix-timestamp каждого успешного выполнения за последний час
        self._executions_history: deque[float] = deque()

    # -- lifecycle ------------------------------------------------------------

    def bind(
        self,
        *,
        sender: Callable[[str, str], Awaitable[None]],
        router_factory: Any,
        owner_chat_id: str,
    ) -> None:
        """Привязывает sender и router factory (те же, что у SwarmScheduler)."""
        self._sender = sender
        self._router_factory = router_factory
        self._owner_chat_id = owner_chat_id

    def start(self) -> None:
        """Запускает фоновый цикл проверки задач."""
        if self._started:
            return
        if not config.KRAB_SWARM_AUTO_EXECUTE_ENABLED:
            logger.info(
                "swarm_auto_executor_disabled",
                reason="KRAB_SWARM_AUTO_EXECUTE_ENABLED=false",
            )
            return
        self._started = True
        loop = asyncio.get_event_loop()
        self._task = loop.create_task(self._run_loop())
        logger.info(
            "swarm_auto_executor_started",
            interval_sec=config.KRAB_SWARM_AUTO_EXECUTE_INTERVAL,
            max_per_hour=config.KRAB_SWARM_AUTO_EXECUTE_MAX_PER_HOUR,
        )

    def stop(self) -> None:
        """Останавливает фоновый цикл."""
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self._started = False
        logger.info("swarm_auto_executor_stopped")

    def get_status(self) -> dict[str, Any]:
        """Статус для owner panel / diagnostics."""
        # Подсчёт выполнений за последний час
        now_ts = time.time()
        recent = [t for t in self._executions_history if now_ts - t < 3600]
        return {
            "enabled": config.KRAB_SWARM_AUTO_EXECUTE_ENABLED,
            "started": self._started,
            "interval_sec": config.KRAB_SWARM_AUTO_EXECUTE_INTERVAL,
            "max_per_hour": config.KRAB_SWARM_AUTO_EXECUTE_MAX_PER_HOUR,
            "executions_last_hour": len(recent),
        }

    # -- internal loop --------------------------------------------------------

    async def _run_loop(self) -> None:
        """Бесконечный цикл: sleep → проверить задачи → выполнить 1 задачу."""
        try:
            while True:
                await asyncio.sleep(float(config.KRAB_SWARM_AUTO_EXECUTE_INTERVAL))
                try:
                    await self._tick()
                except Exception as exc:  # noqa: BLE001
                    logger.error("swarm_auto_executor_tick_error", error=str(exc))
        except asyncio.CancelledError:
            return

    async def _tick(self) -> None:
        """Один цикл проверки: ищет pending auto_execute задачи, запускает одну."""
        if not self._router_factory:
            logger.debug("swarm_auto_executor_no_router")
            return

        # Проверить rate limit
        if not self._check_rate_limit():
            logger.info(
                "swarm_auto_executor_rate_limit",
                max_per_hour=config.KRAB_SWARM_AUTO_EXECUTE_MAX_PER_HOUR,
            )
            return

        # Найти pending задачу с auto_execute=True
        task = self._pick_next_task()
        if task is None:
            return

        logger.info(
            "swarm_auto_executor_executing",
            task_id=task.task_id,
            team=task.team,
            title=task.title[:80],
        )

        # Перевести в in_progress
        swarm_task_board.update_task(task.task_id, status="in_progress")

        try:
            result = await self._run_swarm_for_task(task)

            # Завершить задачу с результатом
            swarm_task_board.complete_task(task.task_id, result=result[:2000])

            # Сохранить артефакт
            try:
                from .swarm_artifact_store import swarm_artifact_store

                swarm_artifact_store.save_round_artifact(
                    team=task.team,
                    topic=f"[auto] {task.title}",
                    result=result,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("swarm_auto_executor_artifact_save_failed", error=str(exc))

            # Записать в rate limit history
            self._executions_history.append(time.time())

            # Уведомить owner-а
            await self._notify_owner(task, result, success=True)

            logger.info(
                "swarm_auto_executor_task_done",
                task_id=task.task_id,
                team=task.team,
            )

        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)[:500]
            swarm_task_board.fail_task(task.task_id, reason=error_msg)
            await self._notify_owner(task, error_msg, success=False)
            logger.error(
                "swarm_auto_executor_task_failed",
                task_id=task.task_id,
                error=error_msg,
            )

    def _check_rate_limit(self) -> bool:
        """Проверяет не превышен ли лимит выполнений в час."""
        now_ts = time.time()
        # Чистим старые записи (> 1 часа)
        while self._executions_history and now_ts - self._executions_history[0] > 3600:
            self._executions_history.popleft()
        return len(self._executions_history) < config.KRAB_SWARM_AUTO_EXECUTE_MAX_PER_HOUR

    def _pick_next_task(self) -> SwarmTask | None:
        """
        Выбирает следующую задачу для авто-выполнения.

        Приоритет: critical → high → medium → low.
        Берёт только pending задачи с auto_execute=True.
        """
        pending = [
            t for t in swarm_task_board.list_tasks(status="pending", limit=200) if t.auto_execute
        ]
        if not pending:
            return None

        # Сортируем по приоритету (critical первым), затем по времени создания
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        pending.sort(key=lambda t: (priority_order.get(t.priority, 2), t.created_at))
        return pending[0]

    async def _run_swarm_for_task(self, task: SwarmTask) -> str:
        """Запускает AgentRoom.run_round для задачи — аналог !swarm <team> <title>."""
        from .swarm import AgentRoom
        from .swarm_bus import TEAM_REGISTRY, swarm_bus

        roles = TEAM_REGISTRY.get(task.team)
        if not roles:
            raise RuntimeError(f"team_not_found:{task.team}")

        # Формируем топик: title + description если есть
        topic = task.title
        if task.description:
            topic = f"{task.title}\n\nКонтекст: {task.description[:500]}"

        room = AgentRoom(roles=roles)
        router = self._router_factory(task.team)
        return await room.run_round(
            topic,
            router,
            _bus=swarm_bus,
            _router_factory=self._router_factory,
            _team_name=task.team,
        )

    async def _notify_owner(self, task: SwarmTask, result: str, *, success: bool) -> None:
        """Отправляет уведомление owner-у о завершении авто-задачи."""
        if not self._sender or not self._owner_chat_id:
            return
        try:
            if success:
                header = (
                    f"🤖 **Авто-задача выполнена**\n"
                    f"Task: `{task.task_id[:8]}` | Team: {task.team}\n"
                    f"Тема: {task.title[:100]}\n\n"
                )
                body = result
            else:
                header = (
                    f"❌ **Авто-задача провалилась**\n"
                    f"Task: `{task.task_id[:8]}` | Team: {task.team}\n"
                    f"Тема: {task.title[:100]}\n\n"
                    f"Ошибка: "
                )
                body = result

            msg = header + body
            # Лимит Telegram
            if len(msg) > 4000:
                msg = msg[:3950] + "\n\n[...обрезано]"
            await self._sender(self._owner_chat_id, msg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("swarm_auto_executor_notify_failed", error=str(exc))


# Singleton
swarm_auto_executor = SwarmAutoExecutor()
