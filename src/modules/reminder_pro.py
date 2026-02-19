# -*- coding: utf-8 -*-
"""
Krab Reminder Pro v1.0.
Управляет напоминаниями пользователя с поддержкой уведомлений через планировщик.

Сохранение напоминаний в JSON для персистентности.
"""

import json
import os
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any

logger = logging.getLogger("ReminderPro")

class ReminderManager:
    """
    Класс для управления напоминаниями.
    """
    def __init__(self, scheduler_obj, storage_path: str = "data/reminders.json"):
        self.scheduler = scheduler_obj
        self.storage_path = storage_path
        self.reminders: List[Dict[str, Any]] = []
        self._load()

    def _load(self):
        """Загрузка напоминаний из файла."""
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    self.reminders = json.load(f)
                logger.info(f"Loaded {len(self.reminders)} reminders")
            except Exception as e:
                logger.error(f"Failed to load reminders: {e}")
                self.reminders = []
        else:
            os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
            self.reminders = []

    def _save(self):
        """Сохранение напоминаний в файл."""
        try:
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self.reminders, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save reminders: {e}")

    def add_reminder(self, chat_id: int, text: str, due_time: datetime):
        """
        Добавить новое напоминание.
        due_time: объект datetime (UTC или локальное время, согласованное с планировщиком).
        """
        reminder_id = f"rem_{int(due_time.timestamp())}_{chat_id}"
        
        reminder = {
            "id": reminder_id,
            "chat_id": chat_id,
            "text": text,
            "due_time": due_time.isoformat(),
            "created_at": datetime.now().isoformat()
        }
        
        self.reminders.append(reminder)
        self._save()
        
        # Планируем задачу в APScheduler (через KrabScheduler)
        self.scheduler.scheduler.add_job(
            self.trigger_reminder,
            'date',
            run_date=due_time,
            args=[reminder_id],
            id=reminder_id
        )
        
        logger.info(f"Reminder added: {reminder_id} for {due_time}")
        return reminder_id

    async def trigger_reminder(self, reminder_id: str):
        """Срабатывание напоминания."""
        reminder = next((r for r in self.reminders if r["id"] == reminder_id), None)
        if not reminder:
            return

        try:
            await self.scheduler.telegram_client.send_message(
                reminder["chat_id"],
                f"⏰ **НАПОМИНАНИЕ**\n\n{reminder['text']}"
            )
            logger.info(f"Reminder triggered: {reminder_id}")
        except Exception as e:
            logger.error(f"Failed to trigger reminder {reminder_id}: {e}")
        finally:
            self.remove_reminder(reminder_id)

    def remove_reminder(self, reminder_id: str):
        """Удалить напоминание (после срабатывания или вручную)."""
        self.reminders = [r for r in self.reminders if r["id"] != reminder_id]
        self._save()
        
        # Также удаляем из планировщика если оно там еще есть
        try:
            if self.scheduler.scheduler.get_job(reminder_id):
                self.scheduler.scheduler.remove_job(reminder_id)
        except Exception:
            pass

    def get_list(self, chat_id: int) -> List[Dict[str, Any]]:
        """Получить список активных напоминаний для чата."""
        return [r for r in self.reminders if r["chat_id"] == chat_id]
