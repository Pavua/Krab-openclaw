import asyncio
import time
from unittest.mock import AsyncMock

from src.core.bounded_queue import BoundedRequestQueue

def make_queue(max_size: int = 5, max_running: int = 2, sla: float = 10) -> BoundedRequestQueue:
    mock_app = AsyncMock()
    mock_app.send_message = AsyncMock()
    config = {
        "QUEUE_MAX_SIZE": max_size,
        "QUEUE_MAX_RUNNING": max_running,
        "QUEUE_SLA_TIMEOUT": sla,
    }
    return BoundedRequestQueue(mock_app, config)

async def slow_coro(delay: float = 999):
    print(" slow_coro started")
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        print(" slow_coro cancelled")
        raise
    finally:
        print(" slow_coro done")

async def test_sla_timeout_aborts_slow_task():
    print("test start")
    queue = make_queue(sla=0.05)
    
    print("queue created")
    task_id = await queue.enqueue("медленная", 1, slow_coro(999))
    print(f"enqueued: {task_id}")
    
    print("sleeping 0.5s")
    await asyncio.sleep(0.5)
    
    print("checking status")
    task = queue.get_status(task_id)
    print("STATUS:", task.status)
    if task.error:
        print("ERROR:", task.error)
    print("metrics:", queue.get_metrics())

if __name__ == "__main__":
    asyncio.run(test_sla_timeout_aborts_slow_task())
