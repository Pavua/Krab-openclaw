# -*- coding: utf-8 -*-
"""
Scheduling Handler v1.0.
Управляет напоминаниями и другими задачами по расписанию.
"""

from pyrogram import filters
from pyrogram.types import Message
import dateparser
from datetime import datetime, strftime, timedelta
import logging

logger = logging.getLogger("SchedulingHandler")

# Глобальный список активных задач для graceful shutdown (если необходимо)
_active_tasks = []

def get_active_reminders():
    return _active_tasks

def register_handlers(app, deps: dict):
    """Регистрирует обработчики для работы с расписанием."""
    scheduler_obj = deps.get("scheduler")
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

        # Пытаемся распарсить время и текст
        # Мы предполагаем, что время идет первым, но оно может состоять из нескольких слов (через 10 минут)
        full_text = message.text.split(" ", 1)[1]
        
        # Интеллектуальный парсинг через dateparser (поддерживает русский)
        # Мы будем пробовать разные префиксы текста как дату
        words = full_text.split()
        due_time = None
        rem_text = ""
        
        for i in range(len(words), 0, -1):
            time_part = " ".join(words[:i])
            parsed = dateparser.parse(time_part, settings={'PREFER_DATES_FROM': 'future'})
            if parsed:
                # Проверяем, что время в будущем
                if parsed < datetime.now():
                    # Попробуем принудительно сдвинуть на завтра если это просто время (например "в 10:00")
                    if parsed.time() and (datetime.now() - parsed).total_seconds() < 86400:
                         parsed += timedelta(days=1)
                
                if parsed > datetime.now():
                    due_time = parsed
                    rem_text = " ".join(words[i:])
                    break
        
        if not due_time:
            await message.reply_text("❌ Не удалось распознать время. Попробуй: `через 5 минут`, `в 15:00`, `завтра в 10 утра`.")
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
        if not reminder_manager: return
        
        reminders = reminder_manager.get_list(message.chat.id)
        if not reminders:
            await message.reply_text("⏰ У тебя нет активных напоминаний.")
            return
            
        text = "⏰ **Твои активные напоминания:**\n\n"
        for i, r in enumerate(reminders, 1):
            dt = datetime.fromisoformat(r["due_time"])
            text += f"{i}. `{dt.strftime('%H:%M')}` — {r['text']} (ID: `{r['id']}`)\n"
            
        await message.reply_text(text)

    @app.on_message(filters.command("rm_remind", prefixes="!"))
    @safe_handler
    async def remove_reminder_command(client, message: Message):
        """Удалить напоминание: !rm_remind <id>"""
        if not reminder_manager: return
        
        if len(message.command) < 2:
            await message.reply_text("🆔 Введи ID напоминания из списка `!reminders`.")
            return
            
        rid = message.command[1]
        reminder_manager.remove_reminder(rid)
        await message.reply_text(f"🗑️ Напоминание `{rid}` удалено.")
