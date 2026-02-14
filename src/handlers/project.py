# -*- coding: utf-8 -*-
"""
Project Handler — Команды для управления автономными проектами: !project, !status, !stop.
Фаза 16: Turnkey Architect.
"""

from pyrogram import filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from .auth import is_owner
import structlog
import asyncio

logger = structlog.get_logger(__name__)

def register_handlers(app, deps: dict):
    """Регистрирует обработчики команд управления проектами."""
    agent = deps["project_agent"]
    safe_handler = deps["safe_handler"]

    @app.on_message(filters.command("project", prefixes="!"))
    @safe_handler
    async def project_start_command(client, message: Message):
        """Запуск нового автономного проекта."""
        if not is_owner(message):
            return

        goal = " ".join(message.command[1:])
        if not goal:
            await message.reply_text("❌ **Укажите цель проекта.**\nПример: `!project Создай игру Змейка на Python`")
            return

        notification = await message.reply_text("🚀 **Инициализация автономного проекта...**")
        
        project_id = await agent.create_project(goal, message.chat.id)
        
        # Запускаем планирование
        await notification.edit_text(f"📝 **ID: `{project_id}`**\n\n**Цель:** {goal}\n\n⚙️ *Формирую план работ...*")
        
        step_result = await agent.run_step(project_id)
        
        if step_result.get("status") == "planned":
            plan_text = "\n".join([f"{t['id']}. {t['title']}" for t in step_result['plan']])
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Начать выполнение", callback_data=f"proj_exec_{project_id}")]
            ])
            await notification.edit_text(
                f"✅ **План сформирован!**\n\n{plan_text}\n\nНачать выполнение?",
                reply_markup=keyboard
            )
        else:
            await notification.edit_text(f"❌ **Ошибка при планировании:** {step_result.get('error', 'Unknown error')}")

    @app.on_message(filters.command("project_status", prefixes="!"))
    @safe_handler
    async def project_status_all_command(client, message: Message):
        """Показать статус всех активных проектов."""
        if not is_owner(message):
            return
            
        active = agent.active_projects
        if not active:
            await message.reply_text("📭 **Нет активных проектов.**")
            return
            
        res = "📋 **Активные проекты:**\n\n"
        for pid, state in active.items():
            res += f"- `{pid}`: {state.goal[:30]}... ({state.status})\n"
        
        await message.reply_text(res)

async def run_project_loop(client, message, project_id, agent):
    """Фоновый цикл выполнения проекта."""
    while True:
        try:
            step_result = await agent.run_step(project_id)
            
            if step_result.get("status") == "executing":
                await message.edit_text(
                    f"🛠 **Выполняю задачу:**\n`{step_result['task']}`\n\nРезультат: {step_result['result'][:200]}..."
                )
            elif step_result.get("status") == "completed":
                await message.edit_text(f"🏁 **Проект завершен!**\n\n{step_result['summary']}")
                break
            elif "error" in step_result:
                await message.edit_text(f"❌ **Ошибка выполнения:** {step_result['error']}")
                break
            
            await asyncio.sleep(2) # Пауза между шагами
        except Exception as e:
            logger.error("Project loop failed", error=str(e))
            await message.edit_text(f"❌ **Критический сбой цикла проекта:** {e}")
            break
