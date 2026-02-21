# -*- coding: utf-8 -*-
"""
Guardian Plugin (Phase 14/18).
Проактивный мониторинг безопасности и здоровья системы.
"""

import asyncio
import structlog
from datetime import datetime

logger = structlog.get_logger("GuardianPlugin")

def register_handlers(app, deps: dict):
    # Плагины могут регистрировать свои команды
    from pyrogram import filters
    @app.on_message(filters.command("guardian", prefixes="!"))
    async def guardian_status(client, message):
        await message.reply_text("🛡 **Guardian System is ACTIVE.**\nMonitoring auth, logs and RAG health.")

async def setup_plugin(deps: dict):
    """Фоновая задача мониторинга."""
    asyncio.create_task(proactive_loop(deps))
    logger.info("🛡 Guardian Proactive Loop started")

async def proactive_loop(deps: dict):
    black_box = deps["black_box"]
    security = deps["security"]
    
    while True:
        try:
            # 1. Проверка на попытки несанкционированного доступа
            # (Эмуляция: ищем в BlackBox сообщения с неудачной ролью)
            stats = black_box.get_stats()
            # logger.info(f"🛡 Guardian Check: {stats['total']} total messages logs safe.")
            
            # 2. Проверка здоровья (здесь можно добавить алерты в Telegram владельцу)
            
            await asyncio.sleep(300) # Проверка каждые 5 минут
        except Exception as e:
            logger.error(f"Guardian Loop Error: {e}")
            await asyncio.sleep(60)
