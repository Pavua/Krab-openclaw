# -*- coding: utf-8 -*-
"""
Group Management Handler (Phase 12.2).
Управление настройками групп и автоматизацией.
"""

import json
import asyncio
from pyrogram import filters
from pyrogram.types import Message, ChatPrivileges
from .auth import is_owner
import structlog

logger = structlog.get_logger(__name__)

def register_handlers(app, deps: dict):
    """Регистрирует обработчики для управления группами."""
    black_box = deps["black_box"]
    safe_handler = deps["safe_handler"]

    @app.on_message(filters.command("group", prefixes="!"))
    @safe_handler
    async def group_command(client, message: Message):
        """Управление группой: !group <subcommand>"""
        if not is_owner(message): return
        
        if message.chat.type.name.lower() not in ["group", "supergroup"]:
            await message.reply_text("❌ Эта команда работает только в группах.")
            return

        chat_id = message.chat.id
        args = message.command
        
        if len(args) < 2:
            await message.reply_text(
                "🏘 **Управление группой:**\n"
                "- `!group status`: текущие настройки\n"
                "- `!group mod on/off`: авто-модерация\n"
                "- `!group welcome <текст>`: приветствие\n"
                "- `!group on/off`: активность бота"
            )
            return

        sub = args[1].lower()
        settings = black_box.get_group_settings(chat_id)

        if sub == "status":
            is_active = "✅ Активен" if settings.get("is_active", 1) else "❌ Выключен"
            mod = "🛡 ON" if settings.get("auto_moderation", 0) else "🔓 OFF"
            welcome = settings.get("welcome_message", "_Не задано_")
            
            await message.reply_text(
                f"🏘 **Статус группы: {message.chat.title}**\n\n"
                f"🤖 Бот: {is_active}\n"
                f"🛡 Модерация: {mod}\n"
                f"👋 Приветствие: {welcome}\n"
                f"🆔 CID: `{chat_id}`"
            )

        elif sub == "mod":
            if len(args) < 3: return
            val = 1 if args[2].lower() == "on" else 0
            black_box.set_group_setting(chat_id, "auto_moderation", val)
            await message.reply_text(f"🛡 Авто-модерация: {'ВКЛ' if val else 'ВЫКЛ'}")

        elif sub == "welcome":
            text = " ".join(args[2:]) if len(args) > 2 else ""
            black_box.set_group_setting(chat_id, "welcome_message", text)
            await message.reply_text("✅ Приветствие обновлено." if text else "🗑 Приветствие удалено.")

        elif sub == "on":
            black_box.set_group_setting(chat_id, "is_active", 1)
            await message.reply_text("✅ Бот активирован в этой группе.")

        elif sub == "off":
            black_box.set_group_setting(chat_id, "is_active", 0)
            await message.reply_text("💤 Бот теперь игнорирует сообщения в этой группе.")

    # --- Обработка новых участников (Welcome) ---
    @app.on_chat_member_updated()
    async def welcome_new_member(client, cms):
        """Приветствие новых участников."""
        if not cms.new_chat_member or cms.new_chat_member.status != "member":
            return
        
        # Если это старый участник или мы сами — игнорим
        if cms.old_chat_member and cms.old_chat_member.status == "member":
             return

        settings = black_box.get_group_settings(cms.chat.id)
        welcome_text = settings.get("welcome_message")
        
        if welcome_text and settings.get("is_active", 1):
             user = cms.new_chat_member.user
             mention = f"@{user.username}" if user.username else user.first_name
             formatted = welcome_text.replace("{user}", mention).replace("{title}", cms.chat.title)
             await client.send_message(cms.chat.id, formatted)

    # --- Авто-модерация (Phase 12.2) ---
    @app.on_message(filters.group & ~filters.me, group=1)
    async def auto_mod_handler(client, message: Message):
        """Простейшая авто-модерация: ссылки и спам."""
        chat_id = message.chat.id
        settings = black_box.get_group_settings(chat_id)
        
        if not settings.get("auto_moderation", 0) or not settings.get("is_active", 1):
             return

        # Проверка на наличие ссылок (базовая)
        if message.entities:
             for entity in message.entities:
                  if entity.type.name.lower() in ["url", "text_link"]:
                       # Удаляем сообщение и уведомляем (если есть права)
                       try:
                            await message.delete()
                            # Отправляем временное предупреждение
                            warn = await client.send_message(
                                chat_id, 
                                f"🛡 **AutoMod:** Сообщения со ссылками запрещены. (@{message.from_user.username})"
                            )
                            await asyncio.sleep(5)
                            await warn.delete()
                            logger.info(f"🛡 Link deleted in group {chat_id} from @{message.from_user.username}")
                       except Exception as e:
                            logger.warning(f"Could not delete message for moderation: {e}")
                       return
