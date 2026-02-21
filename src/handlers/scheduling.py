# -*- coding: utf-8 -*-
"""
Scheduling Handler v1.1.
Управляет напоминаниями и расписанием, включая fallback-парсинг времени без внешних зависимостей.
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from pyrogram import filters
from pyrogram.types import Message

try:
    import dateparser
except ImportError:  # pragma: no cover - зависит от окружения
    dateparser = None

logger = logging.getLogger("SchedulingHandler")

# Глобальный список активных задач для graceful shutdown (если необходимо)
_active_tasks = []

_DURATION_UNITS = {
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "сек": 1,
    "секунда": 1,
    "секунд": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "мин": 60,
    "минута": 60,
    "минут": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "ч": 3600,
    "час": 3600,
    "часа": 3600,
    "часов": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
    "д": 86400,
    "день": 86400,
    "дня": 86400,
    "дней": 86400,
}


def _parse_duration(raw_value: str) -> int:
    """
    Парсит длительность в секундах.
    Поддерживает форматы: 90, 30s, 5m, 2h, 1d, 10min, 1hour, 2day.
    """
    if not raw_value:
        return 0

    value = raw_value.strip().lower()
    if not value:
        return 0

    match = re.match(r"^(\d+)\s*([a-zа-я]*)$", value)
    if not match:
        return 0

    amount = int(match.group(1))
    unit = match.group(2) or "s"
    multiplier = _DURATION_UNITS.get(unit)
    if multiplier is None:
        return 0

    return amount * multiplier


def _try_parse_time_prefix(raw_text: str) -> Optional[datetime]:
    """
    Пытается распарсить время по префиксу.
    Приоритет:
    1) dateparser (если установлен),
    2) fallback для "через N[unit]" и "in N[unit]".
    """
    if dateparser is not None:
        parsed = dateparser.parse(raw_text, settings={"PREFER_DATES_FROM": "future"})
        if parsed:
            if parsed < datetime.now():
                if parsed.time() and (datetime.now() - parsed).total_seconds() < 86400:
                    parsed += timedelta(days=1)
            if parsed > datetime.now():
                return parsed

    normalized = raw_text.strip().lower()
    if normalized.startswith("через "):
        normalized = normalized.replace("через ", "", 1)
    elif normalized.startswith("in "):
        normalized = normalized.replace("in ", "", 1)
    else:
        return None

    seconds = _parse_duration(normalized)
    if seconds <= 0:
        return None

    return datetime.now() + timedelta(seconds=seconds)


def get_active_reminders():
    return _active_tasks


def register_handlers(app, deps: dict):
    """Регистрирует обработчики для работы с расписанием."""
    reminder_manager = deps.get("reminder_manager")
    safe_handler = deps.get("safe_handler")

    @app.on_message(filters.command("remind", prefixes="!"))
    @safe_handler
    async def remind_command(client: Message, message: Message):
        """
        Установка напоминания: !remind <время> <текст>
        Пример: !remind через 5 минут купить хлеб
        !remind в 18:00 созвон
        """
        if not reminder_manager:
            await message.reply_text("❌ Менеджер напоминаний не инициализирован.")
            return

        if len(message.command) < 3:
            await message.reply_text(
                "⏰ **Как использовать:**\n`!remind <время> <текст>`\n\n"
                "Примеры:\n"
                "- `!remind через 10 минут выпить воды`\n"
                "- `!remind завтра в 9:00 проверить почту`"
            )
            return

        # Пытаемся распарсить время и текст:
        # время может состоять из нескольких слов, поэтому перебираем префиксы.
        full_text = message.text.split(" ", 1)[1]
        words = full_text.split()
        due_time = None
        rem_text = ""

        for i in range(len(words), 0, -1):
            time_part = " ".join(words[:i])
            parsed = _try_parse_time_prefix(time_part)
            if parsed:
                due_time = parsed
                rem_text = " ".join(words[i:])
                break

        if not due_time:
            await message.reply_text(
                "❌ Не удалось распознать время. Попробуй: `через 5 минут`, `в 15:00`, `завтра в 10 утра`."
            )
            return

        if not rem_text:
            rem_text = "Без названия"

        reminder_id = reminder_manager.add_reminder(message.chat.id, rem_text, due_time)

        time_str = due_time.strftime("%d.%m %H:%M:%S")
        await message.reply_text(
            f"✅ **Напоминание установлено!**\n"
            f"📅 Время: `{time_str}`\n"
            f"📝 Текст: `{rem_text}`\n"
            f"🆔 ID: `{reminder_id}`"
        )

    @app.on_message(filters.command("reminders", prefixes="!"))
    @safe_handler
    async def list_reminders_command(client, message: Message):
        """Список моих напоминаний."""
        if not reminder_manager:
            return

        reminders = reminder_manager.get_list(message.chat.id)
        if not reminders:
            await message.reply_text("⏰ У тебя нет активных напоминаний.")
            return

        text = "⏰ **Твои активные напоминания:**\n\n"
        for i, reminder in enumerate(reminders, 1):
            due_dt = datetime.fromisoformat(reminder["due_time"])
            text += f"{i}. `{due_dt.strftime('%H:%M')}` — {reminder['text']} (ID: `{reminder['id']}`)\n"

        await message.reply_text(text)

    @app.on_message(filters.command("rm_remind", prefixes="!"))
    @safe_handler
    async def remove_reminder_command(client, message: Message):
        """Удалить напоминание: !rm_remind <id>."""
        if not reminder_manager:
            return

        if len(message.command) < 2:
            await message.reply_text("🆔 Введи ID напоминания из списка `!reminders`.")
            return

        reminder_id = message.command[1]
        reminder_manager.remove_reminder(reminder_id)
        await message.reply_text(f"🗑️ Напоминание `{reminder_id}` удалено.")
