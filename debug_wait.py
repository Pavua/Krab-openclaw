import asyncio
async def slow_coro():
    try:
        print("start sleep")
        await asyncio.sleep(99)
    except asyncio.CancelledError:
        print("cancelled inner")
        raise
async def wrapper():
    try:
        task = asyncio.create_task(slow_coro())
        done, pending = await asyncio.wait([task], timeout=0.05)
        if pending:
            print("cancelling task")
            task.cancel()
            await task
    except asyncio.CancelledError:
        print("cancelled outer")
        raise
    except Exception as e:
        print("exception outer", e)

async def main():
    asyncio.create_task(wrapper())
    print("sleeping 1s")
    for i in range(10):
        await asyncio.sleep(0.1)
        print("waking", i)

asyncio.run(main())
