# -*- coding: utf-8 -*-
"""
Test Reminders Service.
Проверка функционала напоминаний.
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta

# Добавляем корень проекта в путь
sys.path.append(os.getcwd())

from src.modules.reminder_pro import ReminderManager

class MockScheduler:
    def __init__(self):
        class MockAPScheduler:
            def add_job(self, *args, **kwargs):
                print(f"DEBUG: Job added to APScheduler: {kwargs.get('id')}")
            def get_job(self, *args, **kwargs): return None
            def remove_job(self, *args, **kwargs): pass
        self.scheduler = MockAPScheduler()
        self.client = type('MockClient', (), {'send_message': self.mock_send})()

    async def mock_send(self, chat_id, text):
        print(f"DEBUG: Message sent to {chat_id}: {text}")

async def test_reminders():
    print("🧪 Starting Reminder Pro Test...")
    
    scheduler = MockScheduler()
    manager = ReminderManager(scheduler, storage_path="data/test_reminders.json")
    
    # 1. Добавление напоминания
    chat_id = 12345
    text = "Проверить тесты"
    due = datetime.now() + timedelta(seconds=5)
    
    print(f"Adding reminder for {due}...")
    rid = manager.add_reminder(chat_id, text, due)
    print(f"✅ Reminder added ID: {rid}")
    
    # 2. Проверка списка
    reminders = manager.get_list(chat_id)
    assert len(reminders) == 1
    assert reminders[0]['text'] == text
    print("✅ List check passed")
    
    # 3. Эмуляция срабатывания
    print("Triggering reminder manually...")
    await manager.trigger_reminder(rid)
    
    # 4. Проверка удаления
    reminders = manager.get_list(chat_id)
    assert len(reminders) == 0
    print("✅ Cleanup check passed")
    
    # Cleanup file
    if os.path.exists("data/test_reminders.json"):
        os.remove("data/test_reminders.json")
        
    print("\n🎉 All internal reminder tests passed!")

if __name__ == "__main__":
    asyncio.run(test_reminders())
