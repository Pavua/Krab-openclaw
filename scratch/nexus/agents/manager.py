import os
import logging
import asyncio
from typing import Dict, Any
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from dotenv import load_dotenv

# Import teammates
from agents.scout import ScoutAgent
from agents.analyst import AnalystAgent
from agents.coder import CoderAgent

# Load env handling (redundant if called from main, but good for standalone)
load_dotenv(dotenv_path="../.env")

# Configure logging
from utils.logger import setup_logger
logger = setup_logger("Manager")

class ManagerAgent:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.name = config.get("name", "Менеджер")
        self.token = os.getenv("TELEGRAM_TOKEN")
        
        if not self.token:
            logger.error("TELEGRAM_TOKEN не найден в .env")
            raise ValueError("TELEGRAM_TOKEN отсутствует")

        # Инициализация команды
        self.scout = ScoutAgent(config.get("team", {}).get("scout", {}))
        self.analyst = AnalystAgent(config.get("team", {}).get("analyst", {}))
        self.coder = CoderAgent(config.get("team", {}).get("coder", {}))
        
        logger.info(f"{self.name} инициализирован. Команда в сборе.")

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Nexus онлайн. Я ваш Менеджер. Используйте /report <токен> для начала анализа."
        )

    async def report_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Использование: /report <токен_или_тема>")
            return

        topic = " ".join(context.args)
        chat_id = update.effective_chat.id
        
        await context.bot.send_message(chat_id=chat_id, text=f"🤖 **Скаут** ищет данные по '{topic}'...")
        
        # 1. Скаут
        raw_data = await self.scout.gather_intel(topic)
        
        await context.bot.send_message(chat_id=chat_id, text=f"🕵️ **Скаут**: Данные получены. Передаю Аналитику...")
        
        # 2. Аналитик
        analysis = await self.analyst.analyze(raw_data, focus=topic)
        
        # 3. Отчет
        final_report = f"📊 **Отчет Nexus: {topic}**\n\n{analysis}"
        
        # Log to DB
        if hasattr(self, 'db'):
            self.db.log_interaction(chat_id, "report", topic, final_report)
            
        await context.bot.send_message(chat_id=chat_id, text=final_report, parse_mode='Markdown')

    async def code_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Использование: /code <описание_задачи>")
            return

        task = " ".join(context.args)
        chat_id = update.effective_chat.id
        
        await context.bot.send_message(chat_id=chat_id, text=f"👨‍💻 **Кодер** думает над задачей: '{task}'...")
        
        # Call Coder
        code_result = await self.coder.generate_code(task)
        
        # Log to DB
        if hasattr(self, 'db'):
            self.db.log_interaction(chat_id, "code", task, code_result)
            
        await context.bot.send_message(chat_id=chat_id, text=code_result, parse_mode='Markdown')

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Обрабатывает обычные текстовые сообщения.
        """
        user_msg = update.message.text
        chat_id = update.effective_chat.id
        
        # Log incoming
        logger.info(f"Сообщение от {chat_id}: {user_msg}")
        
        # Отправляем "печатает..."
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        # Для начала просто используем Аналитика как "Мозг" для общения
        # Можно улучшить: классифицировать намерение (Код vs Анализ vs Болтовня)
        
        response = await self.analyst.analyze(
            raw_data=f"Сообщение пользователя: {user_msg}", 
            focus="Ответ на вопрос пользователя в свободной форме"
        )
        
        # Log to DB
        if hasattr(self, 'db'):
            self.db.log_interaction(chat_id, "chat", user_msg, response)
            
        await context.bot.send_message(chat_id=chat_id, text=response, parse_mode='Markdown')

    def run(self):
        """
        Starts the Telegram Bot polling loop.
        """
        # Init DB
        from database.db import Database
        self.db = Database()
        self.db.init_db()
        
        application = ApplicationBuilder().token(self.token).build()
        
        start_handler = CommandHandler('start', self.start_command)
        report_handler = CommandHandler('report', self.report_command)
        code_handler = CommandHandler('code', self.code_command)
        
        # Chat Handler (Text only, no commands)
        chat_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), self.handle_message)
        
        application.add_handler(start_handler)
        application.add_handler(report_handler)
        application.add_handler(code_handler)
        application.add_handler(chat_handler)
        
        logger.info("Менеджер слушает команды...")
        application.run_polling()

if __name__ == "__main__":
    # Test config
    dummy_config = {
        "name": "Manager", 
        "team": {
            "scout": {"name": "Scout"}, 
            "analyst": {"name": "Analyst", "model": "gemini-pro"}
        }
    }
    manager = ManagerAgent(dummy_config)
    manager.run()
