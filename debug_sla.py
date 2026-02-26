import asyncio
from unittest.mock import AsyncMock
from src.core.bounded_queue import BoundedRequestQueue

async def slow_coro(d=99):
    try:
        print("slow starting")
        await asyncio.sleep(d)
    except asyncio.CancelledError:
        print("slow cancelled")
        raise
    except Exception as e:
        print("slow exception", e)
    finally:
        print("slow done")

async def main():
    app = AsyncMock()
    app.send_message = AsyncMock()
    q = BoundedRequestQueue(app, {'QUEUE_MAX_SIZE': 5, 'QUEUE_MAX_RUNNING': 2, 'QUEUE_SLA_TIMEOUT': 0.05})
    
    tid = await q.enqueue('test', 1, slow_coro())
    
    for i in range(10):
        await asyncio.sleep(0.1)
        task = q.get_status(tid)
        print(f"[{i}] status:", task.status)
        if task.status == 'FAILED':
            break

asyncio.run(main())
