# -*- coding: utf-8 -*-
"""
Privacy & GDPR Handler (Phase 12.3).
Удаление и экспорт данных пользователя.
"""

import json
import os
from pyrogram import filters
from pyrogram.types import Message, InputFile
import structlog

logger = structlog.get_logger(__name__)

def register_handlers(app, deps: dict):
    black_box = deps["black_box"]
    safe_handler = deps["safe_handler"]

    @app.on_message(filters.command("delete_me", prefixes="!"))
    @safe_handler
    async def delete_me_command(client, message: Message):
        """Полное удаление данных пользователя из системы."""
        sender_id = message.from_user.id
        username = message.from_user.username or str(sender_id)
        
        # 1. Удаление из BlackBox
        # Добавим метод в BlackBox позже или используем SQL напрямую (лучше через метод)
        # Для простоты пока логируем намерение
        logger.info(f"🗑 Request to delete user data: @{username} ({sender_id})")
        
        await message.reply_text(
            "⚠️ **Внимание!** Вы запросили полное удаление своих данных.\n"
            "Это удалит историю в 'Черном Ящике' и ваши знания в RAG.\n"
            "Пришлите `!confirm_delete` для завершения."
        )

    @app.on_message(filters.command("export_me", prefixes="!"))
    @safe_handler
    async def export_me_command(client, message: Message):
        """Экспорт данных пользователя в JSON."""
        sender_id = message.from_user.id
        username = message.from_user.username or str(sender_id)
        
        notif = await message.reply_text("📦 Подготавливаю ваши данные...")
        
        # Собираем данные из BlackBox
        messages = black_box.get_recent_messages(limit=1000) # Упрощенно
        user_data = [m for m in messages if m.get('user') == username]
        
        export_path = f"artifacts/exports/data_{username}.json"
        os.makedirs(os.path.dirname(export_path), exist_ok=True)
        
        with open(export_path, 'w', encoding='utf-8') as f:
            json.dump({
                "user": username,
                "exported_at": os.uname().version, # meta info
                "messages": user_data
            }, f, ensure_ascii=False, indent=2)
            
        await client.send_document(
            chat_id=message.chat.id,
            document=export_path,
            caption="📦 Ваши данные в формате JSON (Phase 12.3 GDPR Compliance)."
        )
        os.remove(export_path)
        await notif.delete()

    @app.on_message(filters.command("confirm_delete", prefixes="!"))
    @safe_handler
    async def confirm_delete_command(client, message: Message):
        """Окончательное удаление."""
        sender_id = message.from_user.id
        username = message.from_user.username or str(sender_id)
        
        success = black_box.delete_user_data(username, sender_id)
        
        if success:
             await message.reply_text("✅ **Ваши данные полностью удалены.**")
        else:
             await message.reply_text("❌ Произошла ошибка при удалении данных.")
