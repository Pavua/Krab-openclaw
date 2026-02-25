# -*- coding: utf-8 -*-
"""
Task Queue Manager v2.0 (Phase R24).
Использует BoundedRequestQueue для обеспечения стабильности при перегрузках.
"""

from typing import Dict, Any, List, Optional, Coroutine
from src.core.bounded_queue import BoundedRequestQueue, QueueTask

# Псевдоним для совместимости (если кто-то импортирует BackgroundTask)
BackgroundTask = QueueTask

class TaskQueue(BoundedRequestQueue):
    """
    Улучшенная очередь задач с backpressure и приоритетами.
    Наследуется от BoundedRequestQueue для переиспользования логики R24.
    """
    
    def __init__(self, app, config: Optional[Dict[str, Any]] = None):
        # В Krab TaskQueue обычно инициализируется с app (Pyrogram Client)
        # BoundedRequestQueue ожидает app и config.
        super().__init__(app, config or {})

    # Метод enqueue в BoundedRequestQueue уже поддерживает нужные аргументы.
    # Если в старом коде вызывали без именованных аргументов:
    # async def enqueue(self, name: str, chat_id: int, coro: Coroutine)
    # то super().enqueue(name, chat_id, coro) сработает корректно.

    def get_status(self, task_id: str) -> Optional[QueueTask]:
        """Для обратной совместимости с v1.0."""
        return self.tasks.get(task_id)

    def list_active(self) -> List[QueueTask]:
        """Для обратной совместимости с v1.0."""
        return [t for t in self.tasks.values() if t.status in ("PENDING", "RUNNING")]

