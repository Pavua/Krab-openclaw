# -*- coding: utf-8 -*-
"""
Krab v6.0 — Core Orchestrator (Entry Point)

Тонкий оркестратор. Вся логика обработчиков вынесена в src/handlers/.
Этот файл отвечает только за:
1. Загрузку конфигурации и .env
2. Инициализацию компонентов (Router, Memory, Perceptor, etc.)
3. Регистрацию обработчиков через register_all_handlers()
4. Запуск клиента Pyrogram и graceful shutdown

Предыдущая версия (1661 строка) сохранена в main_legacy.py.
"""

import os
import signal
import asyncio
from datetime import datetime

from dotenv import load_dotenv
from pyrogram import Client, filters, idle
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)

# Core-модули
from src.core.model_manager import ModelRouter
from src.core.context_manager import ContextKeeper
from src.core.error_handler import safe_handler, get_error_stats
from src.core.rate_limiter import RateLimiter
from src.core.config_manager import ConfigManager
from src.core.security_manager import SecurityManager
from src.core.mcp_client import mcp_manager
from src.core.logger_setup import setup_logger, get_last_logs
from src.core.persona_manager import PersonaManager
from src.modules.perceptor import Perceptor
from src.modules.screen_catcher import ScreenCatcher
from src.utils.black_box import BlackBox
from src.utils.web_scout import WebScout
from src.core.scheduler import KrabScheduler
from src.core.agent_manager import AgentWorkflow
from src.core.tool_handler import ToolHandler

# Handler-модули (новая модульная система)
from src.handlers import register_all_handlers
from src.handlers.scheduling import get_active_reminders

# === ИНИЦИАЛИЗАЦИЯ ===

# Логирование
logger = setup_logger()

# Переменные окружения
load_dotenv()

# Telegram-конфигурация
API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")
SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "krab_v2_session")

# --- Компоненты ---

# AI Router (LocalLLM + Gemini)
router = ModelRouter(config=os.environ)

# Контекстная память (JSONL)
memory = ContextKeeper()

# Perceptor: STT (Whisper), Vision (Gemini), TTS
perceptor_config = {"WHISPER_MODEL": "mlx-community/whisper-large-v3-turbo"}
perceptor = Perceptor(config=perceptor_config)

# Screen Awareness (скриншоты + Vision AI)
screen_catcher = ScreenCatcher(perceptor)

# Черный Ящик (SQLite логирование)
black_box = BlackBox()

# Разведчик (Web Search)
scout = WebScout()

# Безопасность (роли, stealth mode)
security = SecurityManager(owner_username=os.getenv("OWNER_USERNAME", "p0lrd"))

# Конфигурация с hot-reload (YAML)
cfg = ConfigManager()

# Персоны (личности бота)
persona_manager = PersonaManager(cfg, black_box)
router.persona = persona_manager

# Агентный воркфлоу (Phase 6)
agent = AgentWorkflow(router, memory, security)

# Инструменты (shell, RAG, MCP)
tools = ToolHandler(router, router.rag, scout, mcp=mcp_manager)
router.tools = tools

# Rate Limiter
rate_limiter = RateLimiter(
    limit=cfg.get("security.rate_limit", 10),
    window=cfg.get("security.rate_window_sec", 60),
)

# Memory Archiver (если доступен)
try:
    from src.core.memory_archiver import MemoryArchiver
    archiver = MemoryArchiver(memory, router)
except ImportError:
    archiver = None

# Планировщик (будет инициализирован в main())
scheduler = None

# === PYROGRAM CLIENT ===
app = Client(SESSION_NAME, api_id=API_ID, api_hash=API_HASH)


# === DEBUG LOGGER (group=-1, срабатывает первым на каждое сообщение) ===
@app.on_message(group=-1)
async def debug_logger(client, message: Message):
    """Глобальный логгер — записывает каждое сообщение в Black Box."""
    sender = message.from_user.username if message.from_user else "Unknown"
    sender_id = message.from_user.id if message.from_user else 0
    name = message.from_user.first_name if message.from_user else "Unknown"
    msg_type = message.media.value if message.media else "Text"
    text = message.text or message.caption or f"[{msg_type}]"
    direction = (
        "OUTGOING" if message.from_user and message.from_user.is_self
        else "INCOMING"
    )

    logger.info(
        f"🔍 DEBUG: {direction} from @{sender} ({message.chat.id}). "
        f"Type: {msg_type}. Text: {text[:50]}..."
    )

    black_box.log_message(
        chat_id=message.chat.id,
        chat_title=message.chat.title or "Private",
        sender_id=sender_id,
        sender_name=name,
        username=sender,
        direction=direction,
        text=text,
        reply_to_id=message.reply_to_message_id,
    )


# === CALLBACK HANDLER (инлайн-кнопки) ===
@app.on_callback_query()
async def handle_callbacks(client, callback_query: CallbackQuery):
    """Маршрутизация нажатий на inline-кнопки."""
    data = callback_query.data

    if data == "status_refresh":
        await router.check_local_health()
        local_status = "🟢 ON" if router.is_local_available else "🔴 OFF"
        bb_stats = black_box.get_stats()

        new_text = (
            "**🦀 Krab v6.0 Statistics (Refreshed)**\n\n"
            f"🧠 **Local Brain:** {local_status}\n"
            f"🖤 **Black Box:** {bb_stats['total']} msgs\n\n"
            f"🕒 Обновлено: {datetime.now().strftime('%H:%M:%S')}"
        )
        await callback_query.edit_message_text(new_text)
        await callback_query.answer("Статус обновлен")

    elif data == "diag_full":
        await callback_query.answer("Запускаю диагностику...")
        await callback_query.message.reply_text(
            "Запустите команду `!diagnose` для полного отчета."
        )

    elif data == "cfg_view":
        await callback_query.answer("Просмотр конфигурации...")
        config_text = (
            f"🔍 **Session:** `{os.getenv('TELEGRAM_SESSION_NAME')}`\n"
            f"👤 **Owner:** `{os.getenv('OWNER_USERNAME')}`\n"
            f"📡 **Local URL:** `{os.getenv('LM_STUDIO_URL', 'Default')}`"
        )
        await callback_query.message.reply_text(config_text)


# === РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ ===

# Формируем словарь зависимостей для handler-модулей
# Причина: обработчики не должны импортировать глобальные переменные напрямую,
# чтобы их было легко тестировать и переиспользовать.
_deps = {
    "router": router,
    "memory": memory,
    "perceptor": perceptor,
    "screen_catcher": screen_catcher,
    "black_box": black_box,
    "scout": scout,
    "security": security,
    "config_manager": cfg,
    "persona_manager": persona_manager,
    "agent": agent,
    "tools": tools,
    "rate_limiter": rate_limiter,
    "safe_handler": safe_handler,
    "get_last_logs": get_last_logs,
}

# Регистрируем все обработчики из src/handlers/
register_all_handlers(app, _deps)


# === MAIN LOOP ===

async def main():
    """Точка входа: запуск клиента, MCP, планировщика."""
    global scheduler

    logger.info("🦀 Starting Krab v6.0 (Modular Architecture)...")
    await app.start()

    # MCP Initialization
    logger.info("🔌 Initializing MCP Servers...")
    await mcp_manager.connect_all()

    # Проверка роутера
    await router.check_local_health()
    me = await app.get_me()
    logger.info(f"Logged in as {me.first_name} (@{me.username})")

    # Планировщик
    scheduler = KrabScheduler(app, router, black_box, archiver=archiver)
    scheduler.start()

    # Graceful shutdown по SIGTERM/SIGINT
    def handle_signal(sig, frame):
        logger.info(f"⚡ Received signal {sig}, shutting down gracefully...")
        asyncio.get_event_loop().create_task(graceful_shutdown())

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    async def graceful_shutdown():
        logger.info("🛑 Graceful shutdown in progress...")
        if scheduler:
            scheduler.shutdown()
        # Отменяем активные напоминания
        for task in get_active_reminders():
            task.cancel()

        await mcp_manager.shutdown()
        await app.stop()
        logger.info("✅ Krab stopped cleanly.")

    # Уведомление владельца о запуске
    try:
        await app.send_message("p0lrd", (
            "🦀 **Krab v6.0 Modular Architecture Online.**\n"
            "📦 Handlers: 9 modules loaded\n"
            "🧠 Swarm Intelligence: Active\n"
            "🔌 MCP Singularity: Active\n"
            "👀 Screen Awareness: Ready (!see)\n"
            "🗣️ Neural Voice: Ready (!voice)\n"
            "🛡️ Stealth Mode: Ready (!panic)\n"
            "✅ RAG Memory v2.0: Ready"
        ))
    except Exception as e:
        logger.warning(f"Could not send startup notification: {e}")

    await idle()
    await graceful_shutdown()


if __name__ == "__main__":
    app.run(main())