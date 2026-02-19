# -*- coding: utf-8 -*-
"""
Scheduler Module
Центральный планировщик задач для Краба.
Позволяет выполнять задачи по расписанию (cron/interval/once).
"""

import logging
import asyncio
from typing import Callable, Any
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class KrabScheduler:
    def __init__(self, telegram_client=None):
        self.scheduler = AsyncIOScheduler()
        self.telegram_client = telegram_client
        self._is_started = False

    def start(self):
        """Запуск планировщика."""
        if not self._is_started:
            self.scheduler.start()
            self._is_started = True
            logger.info("📅 Крабо-планировщик запущен.")

    def stop(self):
        """Остановка планировщика."""
        if self._is_started:
            self.scheduler.shutdown()
            self._is_started = False
            logger.info("🛑 Крабо-планировщик остановлен.")

    def add_once_task(self, func: Callable, delay_seconds: int, args: list = None, task_id: str = None):
        """Выполнить задачу один раз через X секунд."""
        run_time = datetime.now() + timedelta(seconds=delay_seconds)
        job = self.scheduler.add_job(
            func, 
            'date', 
            run_date=run_time, 
            args=args or [], 
            id=task_id
        )
        logger.info(f"⏳ Задача {task_id or job.id} запланирована на {run_time}")
        return job.id

    def add_cron_task(self, func: Callable, cron_string: str, args: list = None, task_id: str = None):
        """Выполнить задачу по расписанию (cron)."""
        job = self.scheduler.add_job(
            func,
            CronTrigger.from_crontab(cron_string),
            args=args or [],
            id=task_id
        )
        logger.info(f"📅 Cron-задача {task_id or job.id} установлена: {cron_string}")
        return job.id

    def add_interval_task(self, func: Callable, minutes: int, args: list = None, task_id: str = None):
        """Выполнить задачу с интервалом."""
        job = self.scheduler.add_job(
            func,
            IntervalTrigger(minutes=minutes),
            args=args or [],
            id=task_id
        )
        logger.info(f"🔄 Интервальная задача {task_id or job.id} установлена: {minutes} мин.")
        return job.id

    def remove_task(self, task_id: str):
        """Удалить задачу по ID."""
        try:
            self.scheduler.remove_job(task_id)
            logger.info(f"🗑️ Задача {task_id} удалена.")
            return True
        except Exception as e:
            logger.error(f"❌ Не удалось удалить задачу {task_id}: {e}")
            return False

    async def _notify_owner(self, message: str):
        """Отправка уведомления владельцу."""
        if self.telegram_client:
            try:
                from src.core.config_manager import config
                owner_id = config.get("OWNER_ID")
                if owner_id:
                    await self.telegram_client.send_message(int(owner_id), message)
            except Exception as e:
                logger.error(f"❌ Ошибка отправки уведомления из планировщика: {e}")

# Синглтон для глобального доступа
krab_scheduler = KrabScheduler()
