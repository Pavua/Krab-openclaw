# -*- coding: utf-8 -*-
"""
Trading Handler — Обработка торговых команд через Swarm Intelligence.

Команда: !trade <рыночные данные или описание>
Запускает последовательную работу Analyst -> Strategist -> RiskManager -> Executor.
"""

import structlog
from pyrogram import filters
from pyrogram.types import Message
from src.core.agent_swarm import SwarmManager

logger = structlog.get_logger(__name__)

def register_trading_handlers(app, deps: dict):
    """Регистрирует обработчики торговых команд."""
    router = deps["router"]
    swarm_manager = deps.get("swarm_manager") or SwarmManager(router)
    safe_handler = deps["safe_handler"]
    
    # Импортируем проверку владельца, если она нужна (обычно в auth.py)
    try:
        from .auth import is_owner
    except ImportError:
        def is_owner(m): return True # Fallback

    @app.on_message(filters.command("trade", prefixes="!"))
    @safe_handler
    async def trade_command(client, message: Message):
        """Запуск торгового роя."""
        if not is_owner(message):
            return

        if len(message.command) < 2:
            await message.reply_text("❌ Укажите данные для анализа. Пример: `!trade BTC/USD bullish trend`")
            return

        task_description = " ".join(message.command[1:])
        notification = await message.reply_text("🚀 **Запуск торгового роя Краба...**\n_Аналитики приступают к работе._")

        try:
            # Получаем торговую команду
            agents = swarm_manager.get_trading_team()
            
            # Выполняем задачу последовательно
            results = await swarm_manager.execute_task(
                task_description=task_description,
                agents=agents,
                mode="sequential"
            )

            # Формируем красивый отчет
            report = "**📊 Результаты Торгового Роя:**\n\n"
            
            # Добавляем результаты каждого агента
            report += f"🧐 **Анализ:**\n{results.get('Analyst', 'Ошибка')[:300]}...\n\n"
            report += f"📈 **Стратегия:**\n{results.get('Strategist', 'Ошибка')[:300]}...\n\n"
            report += f"🛡️ **Риски:**\n{results.get('RiskManager', 'Ошибка')[:300]}...\n\n"
            report += f"📂 **Итог (JSON):**\n`{results.get('Executor', 'Ошибка')}`"

            await notification.edit_text(report)
            
        except Exception as e:
            logger.error("Swarm trading task failed", error=str(e))
            await notification.edit_text(f"❌ Ошибка при работе роя: `{str(e)}`")
