# -*- coding: utf-8 -*-
"""
Task Queue Manager v1.0 (Phase 8.3).
Управляет фоновыми задачами, чтобы не блокировать основной поток AI.
"""

import asyncio
import structlog
import uuid
from datetime import datetime
from typing import Dict, Any, Callable, Coroutine, Optional, List

logger = structlog.get_logger("TaskQueue")

class BackgroundTask:
    def __init__(self, task_id: str, name: str, chat_id: int):
        self.id = task_id
        self.name = name
        self.chat_id = chat_id
        self.status = "PENDING" # PENDING, RUNNING, COMPLETED, FAILED
        self.start_time = datetime.now()
        self.end_time = None
        self.result = None
        self.error = None

class TaskQueue:
    def __init__(self, app):
        self.app = app # Pyrogram Client for notifications
        self.tasks: Dict[str, BackgroundTask] = {}
        self._running_count = 0
        
        # R15: Метрики рантайма
        self._total_completed = 0
        self._total_failed = 0
        self._total_execution_time = 0.0

    async def enqueue(self, name: str, chat_id: int, coro: Coroutine) -> str:
        """Добавляет задачу в очередь на выполнение."""
        task_id = str(uuid.uuid4())[:8]
        bt = BackgroundTask(task_id, name, chat_id)
        self.tasks[task_id] = bt
        
        # Запускаем в фоне
        asyncio.create_task(self._run_wrapper(bt, coro))
        
        return task_id

    async def _run_wrapper(self, bt: BackgroundTask, coro: Coroutine):
        """Обертка для выполнения и уведомления."""
        bt.status = "RUNNING"
        self._running_count += 1
        start_ts = datetime.now()
        logger.info(f"🚀 Background Task Starter: {bt.name}", id=bt.id)
        
        try:
            bt.result = await coro
            bt.status = "COMPLETED"
            self._total_completed += 1
            
            # Уведомляем пользователя
            await self.app.send_message(
                bt.chat_id,
                f"✅ **Задача завершена!**\nID: `{bt.id}`\nРезультат: {str(bt.result)[:500]}"
            )
        except Exception as e:
            bt.status = "FAILED"
            bt.error = str(e)
            self._total_failed += 1
            logger.error(f"❌ Task {bt.id} Failed: {e}")
            await self.app.send_message(
                bt.chat_id,
                f"❌ **Задача провалена!**\nID: `{bt.id}`\nОшибка: {e}"
            )
        finally:
            bt.end_time = datetime.now()
            duration = (bt.end_time - start_ts).total_seconds()
            self._total_execution_time += duration
            self._running_count -= 1

    def get_metrics(self) -> Dict[str, Any]:
        """Возвращает метрики очереди для Dashboard (R15)."""
        avg_time = 0.0
        total_finished = self._total_completed + self._total_failed
        if total_finished > 0:
            avg_time = round(self._total_execution_time / total_finished, 2)
            
        return {
            "active_tasks": self._running_count,
            "waiting_tasks": 0, # В текущей архитектуре задачи не ждут
            "completed_count": self._total_completed,
            "failed_count": self._total_failed,
            "avg_task_seconds": avg_time,
            "total_tasks_ever": total_finished + self._running_count
        }

    def get_status(self, task_id: str) -> Optional[BackgroundTask]:
        return self.tasks.get(task_id)

    def list_active(self) -> List[BackgroundTask]:
        return [t for t in self.tasks.values() if t.status in ["PENDING", "RUNNING"]]
