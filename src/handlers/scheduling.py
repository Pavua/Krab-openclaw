# -*- coding: utf-8 -*-
"""
Scheduling Handler — Обработчики планирования: напоминания, таймеры, Screen Awareness.

Извлечён из main.py. Включает:
- !remind: установка напоминаний с гибким парсингом (5m, 2h, 1d)
- !timer: простой таймер
- !see / !screen: скриншот экрана и AI-анализ
- _parse_duration: утилита парсинга времени
"""

import re
import asyncio
from datetime import datetime, timedelta

from pyrogram import filters
from pyrogram.types import Message

from .auth import is_owner

import structlog
logger = structlog.get_logger(__name__)

# Список активных задач-напоминаний (для graceful shutdown)
_reminders: list[asyncio.Task] = []


def _parse_duration(text: str) -> int:
    """
    Парсинг длительности из строки.
    Форматы: 5m, 10min, 2h, 1d, 30s, 90 (секунды по умолчанию).
    Возвращает количество секунд (0 если не распознано).
    """
    text = text.strip().lower()
    match = re.match(r'^(\d+)\s*(s|sec|m|min|h|hour|d|day)?$', text)
    if not match:
        return 0

    amount = int(match.group(1))
    unit = match.group(2) or 's'

    if unit in ('m', 'min'):
        return amount * 60
    elif unit in ('h', 'hour'):
        return amount * 3600
    elif unit in ('d', 'day'):
        return amount * 86400
    else:
        return amount


def register_handlers(app, deps: dict):
    """Регистрирует обработчики планирования."""
    router = deps["router"]
    security = deps["security"]
    safe_handler = deps["safe_handler"]

    # --- !remind: Напоминания ---
    @app.on_message(filters.command("remind", prefixes="!"))
    @safe_handler
    async def remind_command(client, message: Message):
        """Напоминание: !remind 30m Позвонить врачу"""
        if len(message.command) < 3:
            await message.reply_text(
                "⏰ **Использование:** `!remind <время> <текст>`\n"
                "Примеры: `!remind 30m Обед`, `!remind 2h Встреча`, `!remind 1d Дедлайн`"
            )
            return

        duration_str = message.command[1]
        seconds = _parse_duration(duration_str)

        if seconds <= 0:
            await message.reply_text(
                "❌ Не могу распознать время. Используй: `5m`, `2h`, `30s`, `1d`"
            )
            return

        reminder_text = message.text.split(maxsplit=2)[2]
        chat_id = message.chat.id

        fire_time = datetime.now() + timedelta(seconds=seconds)

        await message.reply_text(
            f"⏰ **Напоминание установлено!**\n"
            f"📝 `{reminder_text}`\n"
            f"🕐 Через {duration_str} (в {fire_time.strftime('%H:%M')})"
        )

        async def _fire_reminder():
            await asyncio.sleep(seconds)
            await client.send_message(
                chat_id,
                f"🔔 **НАПОМИНАНИЕ:**\n\n{reminder_text}\n\n"
                f"_Установлено {duration_str} назад_",
            )

        task = asyncio.create_task(_fire_reminder())
        _reminders.append(task)

    # --- !timer: Простой таймер ---
    @app.on_message(filters.command("timer", prefixes="!"))
    @safe_handler
    async def timer_command(client, message: Message):
        """Таймер: !timer 5m"""
        if len(message.command) < 2:
            await message.reply_text(
                "⏱ **Использование:** `!timer <время>`\n"
                "Примеры: `!timer 5m`, `!timer 30s`, `!timer 1h`"
            )
            return

        duration_str = message.command[1]
        seconds = _parse_duration(duration_str)

        if seconds <= 0:
            await message.reply_text("❌ Не могу распознать время.")
            return

        notification = await message.reply_text(
            f"⏱ **Таймер запущен:** {duration_str}"
        )

        async def _fire_timer():
            await asyncio.sleep(seconds)
            await notification.reply(
                f"🔔 **Таймер {duration_str} завершён!** ⏱✅"
            )

        task = asyncio.create_task(_fire_timer())
        _reminders.append(task)

    # --- !see: Screen Awareness ---
    @app.on_message(filters.command("see", prefixes="!"))
    async def see_command(client, message: Message):
        """Screen Awareness: !see [вопрос]"""
        if not security.is_owner(message):
            return

        query = (
            " ".join(message.command[1:])
            or "Опиши, что происходит на моем экране."
        )
        status_msg = await message.reply_text("👀 Смотрю на экран...")

        try:
            screen_catcher = deps.get("screen_catcher")
            if screen_catcher:
                report = await screen_catcher.analyze_screen(query)
                await status_msg.edit_text(report)
            else:
                await status_msg.edit_text("❌ Screen Awareness не инициализирован.")
        except Exception as e:
            await status_msg.edit_text(f"❌ Ошибка зрения: {e}")


def get_active_reminders() -> list[asyncio.Task]:
    """Возвращает список активных задач (для graceful shutdown)."""
    return _reminders
