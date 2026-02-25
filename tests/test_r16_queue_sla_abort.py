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
async def test_queue_sla_abort(mock_app):
    queue = TaskQueue(mock_app)
    queue.sla_timeout = 0.5  # Уменьшаем таймаут для теста (актуальное поле R24)
    
    # Задача, которая точно не успеет
    async def stuck_coro():
        await asyncio.sleep(2.0)
        return "too slow"

    await queue.enqueue("stuck_task", 123, stuck_coro())
    
    # Ждем срабатывания таймаута
    await asyncio.sleep(1.0)
    
    metrics = queue.get_metrics()
    assert metrics["failed_count"] == 1
    assert metrics["sla_aborts"] == 1
    assert metrics["active_tasks"] == 0
    
    # Проверяем, что пользователю ушло уведомление о прерывании по SLA
    found = False
    for call in mock_app.send_message.call_args_list:
        args, kwargs = call
        if len(args) > 1 and "прервана по SLA" in args[1]:
            found = True
            break
    assert found, f"Notification about SLA abort not found in {mock_app.send_message.call_args_list}"

@pytest.mark.asyncio
async def test_queue_normal_task_no_abort(mock_app):
    queue = TaskQueue(mock_app)
    queue.sla_timeout = 1.0
    
    async def normal_coro():
        await asyncio.sleep(0.2)
        return "ok"

    await queue.enqueue("normal_task", 123, normal_coro())
    await asyncio.sleep(0.5)
    
    metrics = queue.get_metrics()
    assert metrics["completed_count"] == 1
    assert metrics["failed_count"] == 0
    assert metrics["sla_aborts"] == 0
