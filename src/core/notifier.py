# -*- coding: utf-8 -*-
"""
Notification Engine
Отвечает за отправку асинхронных уведомлений владельцу.
"""

import logging
import asyncio
import psutil
import os
from typing import Optional

logger = logging.getLogger(__name__)

class KrabNotifier:
    def __init__(self, client=None, owner_id: int = None):
        self.client = client
        self.owner_id = owner_id

    def set_client(self, client, owner_id: int):
        """Привязка Pyrogram клиента и ID владельца."""
        self.client = client
        self.owner_id = owner_id
        logger.info(f"🔔 Notifier linked to owner: {owner_id}")

    async def notify(self, text: str, parse_mode=None):
        """
        Отправить сообщение владельцу.
        parse_mode=None → Pyrogram использует дефолт (Markdown).
        Строка "markdown" не подходит — Pyrogram ожидает enums.ParseMode.
        """
        if not self.client or not self.owner_id:
            logger.warning(f"⚠️ Notifier not ready. Text: {text}")
            return False
        
        try:
            # Если parse_mode не задан — не передаём его, Pyrogram сам выберет
            kwargs = {"chat_id": self.owner_id, "text": text}
            if parse_mode is not None:
                kwargs["parse_mode"] = parse_mode
            await self.client.send_message(**kwargs)
            return True
        except Exception as e:
            logger.error(f"❌ Notification failed: {e}")
            return False

    async def notify_system(self, event: str, details: str = ""):
        """Системное уведомление."""
        msg = f"🖥️ **System Alert: {event}**\n\n{details}"
        return await self.notify(msg)

    async def notify_task(self, task_name: str, status: str):
        """Уведомление о задаче."""
        msg = f"⏳ **Task Update: {task_name}**\nStatus: `{status}`"
        return await self.notify(msg)

    async def check_resources(self, cpu_threshold: int = 90, ram_threshold: int = 90):
        """Проверка ресурсов системы и уведомление при превышении."""
        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory().percent
        
        if cpu > cpu_threshold or ram > ram_threshold:
            msg = f"🛰️ **Resource Warning**\n\n🔥 CPU: `{cpu}%`\n🧠 RAM: `{ram}%`"
            await self.notify(msg)
            logger.warning(f"Resource alert sent: CPU {cpu}%, RAM {ram}%")
            return True
        return False

# Синглтон
krab_notifier = KrabNotifier()
