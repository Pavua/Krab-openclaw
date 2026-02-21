# -*- coding: utf-8 -*-
"""
Persona Handler — Личности и саммаризация.

Извлечён из main.py. Включает:
- !personality: переключение личности бота
- !summary: саммаризация истории чата
"""

from pyrogram import filters, enums
from pyrogram.types import Message

from .auth import is_owner

import structlog
logger = structlog.get_logger(__name__)


def register_handlers(app, deps: dict):
    """Регистрирует обработчики персоны."""
    router = deps["router"]
    memory = deps["memory"]
    persona_manager = deps["persona_manager"]
    safe_handler = deps["safe_handler"]

    # --- !personality: Смена личности ---
    @app.on_message(filters.command("personality", prefixes="!"))
    @safe_handler
    async def change_personality(client, message: Message):
        """Смена личности: !personality coder / !personality pirate"""
        if not is_owner(message):
            return

        args = message.command
        if len(args) == 1:
            # Показать список доступных личностей
            personas = persona_manager.get_persona_list()
            text = "👤 **Доступные личности Krab v3.0:**\n\n"
            for pid, info in personas.items():
                active = "✅ " if pid == persona_manager.active_persona else "▫️ "
                text += (
                    f"{active}**{pid}**: {info['name']} — "
                    f"_{info['description']}_\n"
                )

            text += "\nИспользуй: `!personality <id>` для переключения."
            await message.reply_text(text)
            return

        target = args[1].lower()
        if persona_manager.set_persona(target):
            info = persona_manager.get_persona_info(target)
            await message.reply_text(
                f"🎭 **Личность изменена на: {info['name']}**\n"
                f"_{info['description']}_"
            )
        else:
            await message.reply_text(f"❌ Личность `{target}` не найдена.")

    # --- !summary: Саммари контекста ---
    @app.on_message(filters.command("summary", prefixes="!"))
    @safe_handler
    async def summary_command(client, message: Message):
        """Summarize Chat: !summary"""
        security = deps["security"]
        if not security.can_execute_command(
            message.from_user.username, message.from_user.id, "admin"
        ):
            return

        notification = await message.reply_text("📝 **Анализирую историю чата...**")

        # Берём всю историю
        history = memory.get_recent_context(message.chat.id, limit=0)
        if not history:
            await notification.edit_text("❌ История этого чата пуста.")
            return

        # Форматируем для AI
        history_str = "\n".join([
            f"{m.get('user', m.get('role', 'Unknown'))}: "
            f"{m.get('text', m.get('content', ''))}"
            for m in history
        ])

        summary_prompt = (
            f"### ИСТОРИЯ ЧАТА:\n{history_str[-15000:]}\n\n"
            "### ИНСТРУКЦИЯ:\n"
            "Сделай краткое, но емкое саммари этого диалога. "
            "Выдели ключевые темы, принятые решения и текущее состояние. "
            "Пиши на русском."
        )

        summary_text = await router.route_query(summary_prompt, task_type="reasoning")

        # Сохраняем
        memory.save_summary(message.chat.id, summary_text)

        await notification.edit_text(
            f"📝 **Саммари сохранено!**\n\n{summary_text}"
        )
