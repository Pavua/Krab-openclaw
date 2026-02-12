# -*- coding: utf-8 -*-
"""
Communication Handler — Управление электронной почтой (Phase 9.3).
Включает:
- !email: просмотр последних писем
- !send_email: отправка письма
"""

from pyrogram import filters
from pyrogram.types import Message
from .auth import is_owner
import structlog
import asyncio

logger = structlog.get_logger(__name__)

def register_handlers(app, deps: dict):
    """Регистрирует коммуникационные обработчики."""
    email_manager = deps.get("email_manager")
    safe_handler = deps["safe_handler"]

    @app.on_message(filters.command("email", prefixes="!"))
    @safe_handler
    async def list_emails_command(client, message: Message):
        """Просмотр последних писем: !email [limit]"""
        if not is_owner(message): return

        if not email_manager:
            await message.reply_text("❌ Email Manager не инициализирован. Проверьте настройки в .env")
            return

        limit = 5
        if len(message.command) > 1:
            try:
                limit = int(message.command[1])
            except ValueError:
                pass

        notification = await message.reply_text("📧 **Загружаю письма...**")
        
        # Выполняем синхронные операции imaplib в потоке
        emails = await asyncio.to_thread(asyncio.run, email_manager.get_latest_emails(limit))
        
        if not emails:
            await notification.edit_text("📧 Писем нет или произошла ошибка подключения.")
            return

        if "error" in emails[0]:
            await notification.edit_text(f"❌ Ошибка: {emails[0]['error']}")
            return

        text = f"📧 **Последние {len(emails)} писем:**\n\n"
        for i, mail in enumerate(emails, 1):
            text += f"{i}. **{mail['subject']}**\n   От: `{mail['from']}`\n   Дата: `{mail['date']}`\n\n"
        
        text += "💡 Используйте `!email_read <ID>` для чтения (скоро)."
        await notification.edit_text(text)

    @app.on_message(filters.command("send_email", prefixes="!"))
    @safe_handler
    async def send_email_command(client, message: Message):
        """Отправка письма: !send_email <to> <subject> | <content>"""
        if not is_owner(message): return

        if not email_manager:
            await message.reply_text("❌ Email Manager не инициализирован.")
            return

        if len(message.command) < 2:
            await message.reply_text("📧 Usage: `!send_email user@example.com Тема | Текст письма`")
            return

        full_text = message.text.split(" ", 1)[1]
        try:
            target_part, content_part = full_text.split("|", 1)
            target_info = target_part.strip().split(" ", 1)
            to_email = target_info[0]
            subject = target_info[1] if len(target_info) > 1 else "No Subject"
            content = content_part.strip()
        except ValueError:
            await message.reply_text("❌ Ошибка формата. Используйте разделитель `|` для текста.")
            return

        notification = await message.reply_text(f"📧 **Отправляю письмо на {to_email}...**")
        
        success = await asyncio.to_thread(asyncio.run, email_manager.send_email(to_email, subject, content))
        
        if success:
            await notification.edit_text(f"✅ Письмо успешно отправлено на `{to_email}`")
        else:
            await notification.edit_text("❌ Не удалось отправить письмо. Проверьте логи.")
