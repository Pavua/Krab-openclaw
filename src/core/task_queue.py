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
        logger.info(f"🚀 Background Task Starter: {bt.name}", id=bt.id)
        
        try:
            bt.result = await coro
            bt.status = "COMPLETED"
            
            # Уведомляем пользователя
            await self.app.send_message(
                bt.chat_id,
                f"✅ **Задача завершена!**\nID: `{bt.id}`\nРезультат: {str(bt.result)[:500]}"
            )
        except Exception as e:
            bt.status = "FAILED"
            bt.error = str(e)
            logger.error(f"❌ Task {bt.id} Failed: {e}")
            await self.app.send_message(
                bt.chat_id,
                f"❌ **Задача провалена!**\nID: `{bt.id}`\nОшибка: {e}"
            )
        finally:
            bt.end_time = datetime.now()
            self._running_count -= 1

    def get_status(self, task_id: str) -> Optional[BackgroundTask]:
        return self.tasks.get(task_id)

    def list_active(self) -> List[BackgroundTask]:
        return [t for t in self.tasks.values() if t.status in ["PENDING", "RUNNING"]]
