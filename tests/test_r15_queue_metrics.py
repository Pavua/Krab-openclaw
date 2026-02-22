import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from src.core.task_queue import TaskQueue

@pytest.fixture
def mock_app():
    app = MagicMock()
    app.send_message = AsyncMock()
    return app

@pytest.mark.asyncio
async def test_queue_metrics_increments(mock_app):
    queue = TaskQueue(mock_app)
    
    # Мокаем корутину, которая выполняется успешно
    async def success_coro():
        await asyncio.sleep(0.1)
        return "success"
    
    # Мокаем корутину, которая падает
    async def fail_coro():
        await asyncio.sleep(0.1)
        raise ValueError("boom")

    # 1. Запускаем успешную задачу
    await queue.enqueue("success_task", 123, success_coro())
    
    # 2. Запускаем провальную задачу
    await queue.enqueue("fail_task", 123, fail_coro())
    
    # Ждем завершения фоновых задач
    await asyncio.sleep(0.3)
    
    metrics = queue.get_metrics()
    assert metrics["completed_count"] == 1
    assert metrics["failed_count"] == 1
    assert metrics["active_tasks"] == 0
    assert metrics["avg_task_seconds"] >= 0.1

@pytest.mark.asyncio
async def test_queue_metrics_avg_time(mock_app):
    queue = TaskQueue(mock_app)
    
    async def fast_coro():
        await asyncio.sleep(0.1)
        return "fast"
        
    async def slow_coro():
        await asyncio.sleep(0.3)
        return "slow"

    await queue.enqueue("fast", 123, fast_coro())
    await queue.enqueue("slow", 123, slow_coro())
    
    await asyncio.sleep(0.5)
    metrics = queue.get_metrics()
    
    # (0.1 + 0.3) / 2 = 0.2
    assert 0.15 <= metrics["avg_task_seconds"] <= 0.25
