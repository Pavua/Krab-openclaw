# -*- coding: utf-8 -*-
import asyncio

from src.core.notifier import krab_notifier
from src.core.watchdog import krab_watchdog

from src.core.scheduler import krab_scheduler


async def test_autonomous_systems():
    print("🧪 Testing Autonomous Systems...")

    # 1. Скедулер
    krab_scheduler.start()
    job_id = krab_scheduler.add_once_task(lambda: print("✅ Task executed!"), delay_seconds=1)
    print(f"Job {job_id} scheduled.")
    await asyncio.sleep(2)

    # 2. Нотифаер (Mock client)
    class MockClient:
        async def send_message(self, chat_id, text, parse_mode):
            print(f"📩 Notification to {chat_id}: {text}")
            return True

    krab_notifier.set_client(MockClient(), owner_id=12345)
    await krab_notifier.notify_system("Test Event", "This is a verification alert.")

    # 3. Watchdog (Pulse)
    krab_watchdog.update_pulse("TestCore")
    print(f"Pulse update: {krab_watchdog.components_pulse}")

    krab_scheduler.stop()
    print("✅ All systems verified.")


if __name__ == "__main__":
    asyncio.run(test_autonomous_systems())
