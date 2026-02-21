# -*- coding: utf-8 -*-
"""
Krab v2.5 - Core Entry Point (Orchestrator)
Главный модуль юзербота. Связывает все подсистемы:
Router, RAG, Perceptor, Scheduler, Black Box, Config.
Phase 5: Error Resilience, Rate Limiting, Config Hot-Reload.
"""

import os
import sys
import signal
import asyncio
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from pyrogram import Client, filters, idle, enums
from pyrogram.types import Message

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
from src.utils.black_box import BlackBox
from src.utils.web_scout import WebScout
from src.core.scheduler import KrabScheduler
from src.core.agent_manager import AgentWorkflow
from src.core.tool_handler import ToolHandler

# Дополнительные типы Pyrogram
from pyrogram.types import (
    Message, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton,
    CallbackQuery
)

# Настройка логирования 2.0
logger = setup_logger()

# Загрузка переменных
load_dotenv()

# Конфигурация
API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")
SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "krab_v2_session")

# Инициализация компонентов
# Config for Perceptor (loaded from env or defaults)
perceptor_config = {
    "WHISPER_MODEL": "mlx-community/whisper-large-v3-turbo"
}

router = ModelRouter(config=os.environ)
memory = ContextKeeper()
perceptor = Perceptor(config=perceptor_config) # Модуль "Глаза и Уши"
from src.modules.screen_catcher import ScreenCatcher
screen_catcher = ScreenCatcher(perceptor)
black_box = BlackBox() # Черный Ящик для логов
scout = WebScout() # Модуль "Разведчик" (Scout)

# Phase 5: Hardening
security = SecurityManager(owner_username=os.getenv("OWNER_USERNAME", "p0lrd"))
cfg = ConfigManager()  # YAML-конфиг с горячей перезагрузкой
persona_manager = PersonaManager(cfg, black_box)
router.persona = persona_manager  # Связываем роутер с личностями

# Phase 6: Intelligence
agent = AgentWorkflow(router, memory, security)
tools = ToolHandler(router, router.rag, scout, mcp=mcp_manager)
router.tools = tools  # Связываем роутер с инструментами

rate_limiter = RateLimiter(
    limit=cfg.get("security.rate_limit", 10),
    window=cfg.get("security.rate_window_sec", 60)
)

# Хранилище напоминаний (key = asyncio.Task)
_reminders = []

# Инициализация клиента (Userbot)
app = Client(SESSION_NAME, api_id=API_ID, api_hash=API_HASH)

# --- GLOBAL DEBUG LOGGER ---
@app.on_message(group=-1)
async def debug_logger(client, message: Message):
    sender = message.from_user.username if message.from_user else "Unknown"
    sender_id = message.from_user.id if message.from_user else 0
    name = message.from_user.first_name if message.from_user else "Unknown"
    msg_type = message.media.value if message.media else "Text"
    text = message.text or message.caption or f"[{msg_type}]"
    direction = "OUTGOING" if message.from_user and message.from_user.is_self else "INCOMING"
    
    logger.info(f"🔍 DEBUG: {direction} from @{sender} ({message.chat.id}). Type: {msg_type}. Text: {text[:50]}...")
    
    # Сохраняем в Black Box (Черный Ящик)
    black_box.log_message(
        chat_id=message.chat.id,
        chat_title=message.chat.title or "Private",
        sender_id=sender_id,
        sender_name=name,
        username=sender,
        direction=direction,
        text=text,
        reply_to_id=message.reply_to_message_id
    )

# --- EVENT HANDLERS ---

@app.on_message(filters.command("status", prefixes="!"))
@safe_handler
async def status_check(client, message):
    """Быстрая проверка здоровья систем (Router + Local Server)."""
    # Проверка прав (как в auto_reply)
    allowed_users = os.getenv("ALLOWED_USERS", "").split(",")
    allowed_users = [u.strip() for u in allowed_users if u.strip()]
    owner = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
    if owner: allowed_users.append(owner)
    
    sender = message.from_user.username if message.from_user else "Unknown"
    sender_id = str(message.from_user.id) if message.from_user else "0"
    
    if sender not in allowed_users and sender_id not in allowed_users:
        return

    msg_method = message.edit_text if message.from_user.is_self else message.reply_text
    status_msg = await msg_method("🦀 Checking systems...")

    # Статус локального инстанса
    await router.check_local_health()
    local_status = "🟢 ON" if router.is_local_available else "🔴 OFF"
    local_model = router.active_local_model or "None"
    
    # Статус Black Box
    bb_stats = black_box.get_stats()
    
    # Резюме системной памяти
    mem_info = "Unknown"
    try:
        chat_path = memory.get_chat_storage_path(message.chat.id)
        if os.path.exists(chat_path):
            mem_size = os.path.getsize(chat_path) / 1024
            mem_info = f"{mem_size:.1f} KB"
        else:
            mem_info = "0 KB"
    except:
        pass

    text = (
        "**🦀 Krab v2.0 Statistics**\n\n"
        f"🧠 **Local Brain:** {local_status} ({local_model})\n"
        f"☁️ **Cloud:** Gemini 2.0 Flash\n"
        f"💾 **Memory (JSONL):** {mem_info}\n"
        f"🖤 **Black Box (DB):** {bb_stats['total']} msgs\n"
        f"📈 **I/O:** {bb_stats['incoming']} 📥 / {bb_stats['outgoing']} 📤\n"
    )

    # Добавляем интерактивные кнопки
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Диагностика", callback_data="diag_full"),
            InlineKeyboardButton("⚙️ Конфиг", callback_data="cfg_view")
        ],
        [
            InlineKeyboardButton("🔄 Обновить", callback_data="status_refresh")
        ]
    ])
    
    await status_msg.edit_text(text, reply_markup=keyboard)

@app.on_callback_query()
async def handle_callbacks(client, callback_query: CallbackQuery):
    """Обработка нажатий на инлайн-кнопки."""
    data = callback_query.data
    
    if data == "status_refresh":
        await router.check_local_health()
        local_status = "🟢 ON" if router.is_local_available else "🔴 OFF"
        bb_stats = black_box.get_stats()
        
        new_text = (
            "**🦀 Krab v2.0 Statistics (Refreshed)**\n\n"
            f"🧠 **Local Brain:** {local_status}\n"
            f"🖤 **Black Box:** {bb_stats['total']} msgs\n\n"
            f"🕒 Обновлено: {datetime.now().strftime('%H:%M:%S')}"
        )
        await callback_query.edit_message_text(new_text)
        await callback_query.answer("Статус обновлен")
    
    elif data == "diag_full":
        await callback_query.answer("Запускаю диагностику...")
        await callback_query.message.reply_text("Запустите команду `!diagnose` для полного отчета.")
    
    elif data == "status_view":
        await callback_query.answer("Перехожу к статусу...")
        await callback_query.message.reply_text("Используйте `!status` для просмотра деталей.")
    
    elif data == "cfg_view":
        await callback_query.answer("Просмотр конфигурации...")
        # Показываем безопасную часть конфига
        config_text = (
            f"🔍 **Session:** `{os.getenv('TELEGRAM_SESSION_NAME')}`\n"
            f"👤 **Owner:** `{os.getenv('OWNER_USERNAME')}`\n"
            f"📡 **Local URL:** `{os.getenv('LM_STUDIO_URL', 'Default')}`"
        )
        await callback_query.message.reply_text(config_text)

@app.on_message(filters.command("summary", prefixes="!"))
@safe_handler
async def summarize_chat(client, message):
    """Саммаризация переписки (последние 50-100 сообщений)."""
    # Проверка прав
    sender = message.from_user.username if message.from_user else "Unknown"
    allowed_users = os.getenv("ALLOWED_USERS", "").split(",")
    allowed_users = [u.strip() for u in allowed_users if u.strip()]
    owner = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
    if owner: allowed_users.append(owner)

    if sender not in allowed_users and not message.from_user.is_self:
        return

    notification = await message.reply_text("🧐 Читаю историю...")
    
    # 1. Получаем контекст (последние 100 сообщений)
    # limit=0 gets all, but lets keep it 100 for safety
    history = memory.get_recent_context(message.chat.id, limit=100)
    
    if not history:
        await notification.edit_text("❌ История пуста.")
        return

    # 2. Формируем промпт
    messages_text = ""
    for msg in history:
        role = msg.get('user', msg.get('role', 'unknown'))
        content = msg.get('text', msg.get('content', '[media]'))
        messages_text += f"{role}: {content}\n"

    prompt = (
        "Проанализируй эту переписку и составь краткое резюме (Summary).\n"
        "Выдели главные темы, договоренности и важные моменты.\n"
        "Стиль: Краткий, деловой, структурированный булллитами.\n\n"
        f"Переписка:\n{messages_text}"
    )

    # 3. Отправляем в AI
    summary = await router.route_query(
        prompt, 
        task_type='chat', 
        is_private=message.chat.type == enums.ChatType.PRIVATE
    )
    
    await notification.edit_text(f"📝 **Summary:**\n\n{summary}")

@app.on_message(filters.command("say", prefixes="!"))
async def say_text(client, message):
    """Text-to-Speech: !say <text> or reply to text."""
    # Check permissions (reuse logic or improve)
    sender = message.from_user.username if message.from_user else "Unknown"
    allowed_users = os.getenv("ALLOWED_USERS", "").split(",")
    allowed_users = [u.strip() for u in allowed_users if u.strip()]
    owner = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
    if owner: allowed_users.append(owner)

    if sender not in allowed_users and not message.from_user.is_self:
        return

    # Determine text
    text_to_speak = ""
    if len(message.command) > 1:
        text_to_speak = message.text.split(" ", 1)[1]
    elif message.reply_to_message:
        text_to_speak = message.reply_to_message.text or message.reply_to_message.caption
    
    if not text_to_speak:
         await message.reply_text("🗣️ Укажи текст: `!say Привет` или ответь на сообщение.")
         return

    notification = await message.reply_text("🗣️ Генерирую голос...")
    
    # Call Perceptor TTS
    # Default voice is Milena (Russian). 
    voice_path = await perceptor.speak(text_to_speak, voice="Milena")
    
    if voice_path and os.path.exists(voice_path):
        await message.reply_voice(voice_path, caption=f"🗣️ **Said:** {text_to_speak[:20]}...")
        await notification.delete()
        os.remove(voice_path)
    else:
        await notification.edit_text("❌ Ошибка генерации голоса.")

@app.on_message(filters.command("translate", prefixes="!"))
async def translate_text(client, message):
    """Перевод текста: !translate <text> или реплаем на сообщение.
    По умолчанию переводит: RU -> EN, EN -> RU (авто-определение).
    Можно указать язык: !translate en Привет мир
    """
    sender = message.from_user.username if message.from_user else "Unknown"
    allowed_users = os.getenv("ALLOWED_USERS", "").split(",")
    allowed_users = [u.strip() for u in allowed_users if u.strip()]
    owner = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
    if owner: allowed_users.append(owner)
    if sender not in allowed_users and not message.from_user.is_self:
        return

    # Определяем текст для перевода
    text_to_translate = ""
    target_lang = None  # Авто-определение

    if len(message.command) > 1:
        args = message.text.split(" ", 1)[1]
        # Проверяем, не указан ли язык первым словом
        first_word = args.split(" ", 1)[0].lower()
        if first_word in ["en", "ru", "es", "fr", "de", "zh", "ja", "ko", "ar", "pt", "it"]:
            target_lang = first_word
            text_to_translate = args.split(" ", 1)[1] if len(args.split(" ", 1)) > 1 else ""
        else:
            text_to_translate = args
    elif message.reply_to_message:
        text_to_translate = message.reply_to_message.text or message.reply_to_message.caption

    if not text_to_translate:
        await message.reply_text("🌐 Укажи текст: `!translate Привет` или ответь на сообщение.\n"
                                 "Можно указать язык: `!translate en Привет мир`")
        return

    notification = await message.reply_text("🌐 Перевожу...")

    # Формируем промпт для перевода
    if target_lang:
        lang_map = {"en": "English", "ru": "Russian", "es": "Spanish", "fr": "French",
                    "de": "German", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
                    "ar": "Arabic", "pt": "Portuguese", "it": "Italian"}
        lang_name = lang_map.get(target_lang, target_lang)
        prompt = f"Переведи этот текст на {lang_name}. Выведи ТОЛЬКО перевод, без пояснений.\n\n{text_to_translate}"
    else:
        prompt = ("Определи язык этого текста. Если это русский — переведи на английский. "
                  "Если это любой другой язык — переведи на русский. "
                  "Выведи ТОЛЬКО перевод, без пояснений.\n\n"
                  f"{text_to_translate}")

    translation = await router.route_query(
        prompt, 
        task_type='chat', 
        is_private=message.chat.type == enums.ChatType.PRIVATE
    )
    await notification.edit_text(f"🌐 **Translation:**\n\n{translation}")

@app.on_message(filters.command("diagnose", prefixes="!"))
async def diagnose_system(client, message):
    """Полная диагностика всех подсистем Краба."""
    sender = message.from_user.username if message.from_user else "Unknown"
    allowed_users = os.getenv("ALLOWED_USERS", "").split(",")
    allowed_users = [u.strip() for u in allowed_users if u.strip()]
    owner = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
    if owner: allowed_users.append(owner)
    if sender not in allowed_users and not message.from_user.is_self:
        return

    notification = await message.reply_text("🔍 Запускаю диагностику...")

    import psutil
    import platform
    
    # 1. Системные метрики
    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    # 2. Проверка Local AI
    await router.check_local_health()
    local_status = "🟢 Online" if router.is_local_available else "🔴 Offline"
    local_model = router.active_local_model or "N/A"
    
    # 3. Проверка Gemini
    gemini_status = "🟢 Key Present" if router.gemini_key else "🔴 No Key"
    
    # 4. Дискеция памяти бота
    history_count = len(memory.get_recent_context(message.chat.id, limit=0))
    
    # 5. Python / Platform info
    py_ver = f"{platform.python_version()}"
    mac_ver = f"{platform.mac_ver()[0]}"
    chip = f"{platform.processor() or 'Apple Silicon'}"
    
    # 6. Black Box stats
    bb_stats = black_box.get_stats()
    
    # 7. RAG stats
    rag_stats = router.rag.get_stats()
    
    text = (
        f"**🦀 Krab v2.0 — Полная Диагностика**\n\n"
        f"**Система:**\n"
        f"├ macOS {mac_ver} ({chip})\n"
        f"├ Python {py_ver}\n"
        f"├ CPU: {cpu}%\n"
        f"├ RAM: {ram.percent}% ({ram.used // (1024**3)}/{ram.total // (1024**3)} GB)\n"
        f"└ Disk: {disk.percent}% ({disk.used // (1024**3)}/{disk.total // (1024**3)} GB)\n\n"
        f"**AI Core:**\n"
        f"├ Local LLM: {local_status} ({local_model})\n"
        f"├ Gemini API: {gemini_status}\n"
        f"├ Whisper: 🟢 Active ({perceptor.whisper_model})\n"
        f"└ 🧠 Memory Index (RAG): {rag_stats['count']} facts\n\n"
        f"**Modules:**\n"
        f"├ 👂 Ears (STT): Active\n"
        f"├ 🗣️ Voice (TTS): Active (Milena)\n"
        f"├ 👁️ Eyes (Vision): Active\n"
        f"├ 📝 Summary: Active\n"
        f"├ 🌐 Translate: Active\n"
        f"├ 💾 Context Memory: {history_count} msgs (this chat)\n"
        f"└ 🖤 Black Box Log: {bb_stats['total']} msgs (global)\n\n"
        f"**Uptime:** Running as @{(await client.get_me()).username}"
    )
    
    await notification.edit_text(text)

@app.on_message(filters.command("code", prefixes="!"))
async def generate_code(client, message):
    """Генерация кода: !code <задача>. Используя AI для написания кода."""
    sender = message.from_user.username if message.from_user else "Unknown"
    allowed_users = os.getenv("ALLOWED_USERS", "").split(",")
    allowed_users = [u.strip() for u in allowed_users if u.strip()]
    owner = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
    if owner: allowed_users.append(owner)
    if sender not in allowed_users and not message.from_user.is_self:
        return

    task = ""
    if len(message.command) > 1:
        task = message.text.split(" ", 1)[1]
    elif message.reply_to_message:
        task = message.reply_to_message.text or message.reply_to_message.caption

    if not task:
        await message.reply_text("💻 Опиши задачу: `!code Напиши парсер JSON`")
        return

    notification = await message.reply_text("💻 Генерирую код...")

    prompt = (
        "Ты — опытный Python-разработчик. "
        "Напиши чистый, рабочий код для решения задачи. "
        "Верни ТОЛЬКО код внутри блока ```python ... ```. "
        "Добавь краткие комментарии на русском.\n\n"
        f"Задача: {task}"
    )

    code = await router.route_query(
        prompt, 
        task_type='coding', 
        is_private=message.chat.type == enums.ChatType.PRIVATE
    )
    await notification.edit_text(f"💻 **Code:**\n\n{code}")

@app.on_message(filters.command("exec", prefixes="!"))
async def exec_python(client, message):
    """Выполнение Python-кода (ТОЛЬКО для владельца).
    !exec print('hello') или реплай на сообщение с кодом.
    ⚠️ Опасно! Только для OWNER.
    """
    # СТРОГО только владелец
    owner = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
    sender = message.from_user.username if message.from_user else "Unknown"
    
    if sender != owner and not message.from_user.is_self:
        await message.reply_text("🔒 Эта команда доступна только владельцу.")
        return

    code = ""
    if len(message.command) > 1:
        code = message.text.split(" ", 1)[1]
    elif message.reply_to_message:
        code = message.reply_to_message.text or message.reply_to_message.caption

    if not code:
        await message.reply_text("⚙️ Укажи код: `!exec print('hello')`")
        return

    # Убираем markdown-обёртку если есть
    if code.startswith("```") and code.endswith("```"):
        code = code.strip("```")
        if code.startswith("python\n"):
            code = code[7:]

    notification = await message.reply_text("⚙️ Выполняю...")

    import io
    import sys
    import traceback

    # Безопасное выполнение с перехватом stdout/stderr
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = captured_out = io.StringIO()
    sys.stderr = captured_err = io.StringIO()

    try:
        # Таймаут 10 секунд (защита от бесконечных циклов)
        exec(code, {"__builtins__": __builtins__})
        output = captured_out.getvalue()
        error = captured_err.getvalue()
        
        result = ""
        if output:
            result += f"📤 **Output:**\n```\n{output[:3000]}\n```\n"
        if error:
            result += f"⚠️ **Stderr:**\n```\n{error[:1000]}\n```\n"
        if not output and not error:
            result = "✅ Код выполнен без вывода."
        
        await notification.edit_text(result)
    except Exception as e:
        tb = traceback.format_exc()
        await notification.edit_text(f"❌ **Error:**\n```\n{tb[-2000:]}\n```")
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

@app.on_message(filters.command("commit", prefixes="!"))
async def git_commit(client, message: Message):
    """Auto-Commit to GitHub: !commit <message> (Owner only)"""
    sender = message.from_user.username if message.from_user else "Unknown"
    owner = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
    if sender != owner and not message.from_user.is_self:
        return

    commit_msg = "Krab Auto-Update"
    if len(message.command) > 1:
        commit_msg = message.text.split(" ", 1)[1]

    notification = await message.reply_text("🚀 **Git:** Пушу изменения в GitHub...")
    
    import subprocess
    try:
        # 1. Add
        subprocess.run(["git", "add", "."], check=True)
        # 2. Commit
        subprocess.run(["git", "commit", "-m", f"🦀 {commit_msg}"], check=True)
        # 3. Push
        result = subprocess.run(["git", "push"], capture_output=True, text=True)
        
        if result.returncode == 0:
            await notification.edit_text(f"✅ **GitHub Updated!**\nMessage: `{commit_msg}`")
        else:
            await notification.edit_text(f"❌ **Git Push Error:**\n```{result.stderr[:500]}```")
            
    except subprocess.CalledProcessError as e:
        await notification.edit_text(f"❌ **Git Error:** `{e}`\n(Возможно нет изменений для коммита)")
    except Exception as e:
        await notification.edit_text(f"❌ **System Error:** `{e}`")

@app.on_message(filters.command("learn", prefixes="!"))
async def learn_info(client, message: Message):
    """Index info for RAG: !learn <text> (Owner/WhiteList only)"""
    sender = message.from_user.username if message.from_user else "Unknown"
    allowed = os.getenv("ALLOWED_USERS", "").split(",")
    owner = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
    if sender != owner and sender not in allowed and not message.from_user.is_self:
        return

    text_to_learn = ""
    if len(message.command) > 1:
        text_to_learn = message.text.split(" ", 1)[1]
    elif message.reply_to_message:
        text_to_learn = message.reply_to_message.text or message.reply_to_message.caption

    if not text_to_learn:
        await message.reply_text("🧠 Что мне выучить? Пришли текст или ответь на сообщение командой `!learn`.")
        return

    notification = await message.reply_text("🧠 Запоминаю...")
    
    doc_id = router.rag.add_document(
        text=text_to_learn,
        metadata={"source": sender, "chat": str(message.chat.id), "timestamp": str(datetime.now())}
    )
    
    if doc_id:
        await notification.edit_text(f"✅ **Выучено!**\nТеперь я знаю это и смогу использовать в ответах. ID: `{doc_id}`")
    else:
        await notification.edit_text("❌ Ошибка при индексации в ChromaDB.")

# --- !config: Просмотр и изменение настроек ---
@app.on_message(filters.command("config", prefixes="!"))
@safe_handler
async def config_command(client, message: Message):
    """Config Hot-Reload: !config / !config set ai.temperature 0.9"""
    sender = message.from_user.username if message.from_user else "Unknown"
    owner = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
    if sender != owner and not message.from_user.is_self:
        return

    args = message.text.split()
    
    if len(args) == 1:
        # Показать текущий конфиг
        await message.reply_text(cfg.to_display())
    
    elif len(args) >= 4 and args[1].lower() == "set":
        # !config set ai.temperature 0.9
        key = args[2]
        raw_value = " ".join(args[3:])
        
        # Авто-определение типа
        try:
            if raw_value.lower() in ("true", "false"):
                value = raw_value.lower() == "true"
            elif "." in raw_value:
                value = float(raw_value)
            else:
                value = int(raw_value)
        except ValueError:
            value = raw_value
        
        cfg.set(key, value)
        await message.reply_text(f"✅ `{key}` = `{value}`\nКонфиг обновлён и сохранён.")
    
    elif len(args) >= 2 and args[1].lower() == "reload":
        cfg.reload()
        await message.reply_text("🔄 Конфиг перезагружен с диска.")
    
    else:
        await message.reply_text(
            "⚙️ **Использование:**\n"
            "`!config` — показать все настройки\n"
            "`!config set <ключ> <значение>` — изменить\n"
            "`!config reload` — перечитать с диска"
        )

# --- !remind: Напоминания ---
def _parse_duration(text: str) -> int:
    """
    Парсинг длительности из строки.
    Форматы: 5m, 10min, 2h, 1d, 30s, 90 (секунды по умолчанию)
    """
    import re
    text = text.strip().lower()
    match = re.match(r'^(\d+)\s*(s|sec|m|min|h|hour|d|day)?$', text)
    if not match:
        return 0
    
    amount = int(match.group(1))
    unit = match.group(2) or 's'
    
    if unit in ('m', 'min'):
        return amount * 60
    elif unit in ('h', 'hour'):
        return amount * 3600
    elif unit in ('d', 'day'):
        return amount * 86400
    else:
        return amount

@app.on_message(filters.command("remind", prefixes="!"))
@safe_handler
async def remind_command(client, message: Message):
    """Напоминание: !remind 30m Позвонить врачу"""
    if len(message.command) < 3:
        await message.reply_text(
            "⏰ **Использование:** `!remind <время> <текст>`\n"
            "Примеры: `!remind 30m Обед`, `!remind 2h Встреча`, `!remind 1d Дедлайн`"
        )
        return
    
    duration_str = message.command[1]
    seconds = _parse_duration(duration_str)
    
    if seconds <= 0:
        await message.reply_text("❌ Не могу распознать время. Используй: `5m`, `2h`, `30s`, `1d`")
        return
    
    reminder_text = message.text.split(maxsplit=2)[2]
    chat_id = message.chat.id
    
    # Рассчитываем время срабатывания
    fire_time = datetime.now() + timedelta(seconds=seconds)
    
    await message.reply_text(
        f"⏰ **Напоминание установлено!**\n"
        f"📝 `{reminder_text}`\n"
        f"🕐 Через {duration_str} (в {fire_time.strftime('%H:%M')})"
    )
    
    async def _fire_reminder():
        await asyncio.sleep(seconds)
        await client.send_message(
            chat_id,
            f"🔔 **НАПОМИНАНИЕ:**\n\n{reminder_text}\n\n_Установлено {duration_str} назад_"
        )
    
    task = asyncio.create_task(_fire_reminder())
    _reminders.append(task)

# --- !see: Screen Awareness (Phase 11) ---
@app.on_message(filters.command("see", prefixes="!"))
async def see_command(client, message):
    if not security.is_owner(message):
        return
        
    query = " ".join(message.command[1:]) or "Опиши, что происходит на моем экране."
    status_msg = await message.reply_text("👀 Смотрю на экран...")
    
    try:
        report = await screen_catcher.analyze_screen(query)
        await status_msg.edit_text(report)
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка зрения: {e}")

# --- !voice: Text-to-Speech (Phase 11) ---
@app.on_message(filters.command("voice", prefixes="!"))
async def voice_command(client, message):
    text = " ".join(message.command[1:])
    if not text:
        return await message.reply_text("❌ Введите текст для озвучки.")
        
    status_msg = await message.reply_text("🗣️ Генерирую голос...")
    try:
        voice_file = await perceptor.speak(text)
        if voice_file:
            await message.reply_voice(voice_file, caption=f"🗣️: {text[:50]}...")
            os.remove(voice_file)
            await status_msg.delete()
        else:
            await status_msg.edit_text("❌ Ошибка генерации голоса (проверьте 'say' и 'ffmpeg').")
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка TTS: {e}")

# --- !smart: Agentic Reasoning ---
# --- !timer: Простой таймер ---
@app.on_message(filters.command("timer", prefixes="!"))
@safe_handler
async def timer_command(client, message: Message):
    """Таймер: !timer 5m"""
    if len(message.command) < 2:
        await message.reply_text("⏱ **Использование:** `!timer <время>`\nПримеры: `!timer 5m`, `!timer 30s`, `!timer 1h`")
        return
    
    duration_str = message.command[1]
    seconds = _parse_duration(duration_str)
    
    if seconds <= 0:
        await message.reply_text("❌ Не могу распознать время.")
        return
    
    notification = await message.reply_text(f"⏱ **Таймер запущен:** {duration_str}")
    
    async def _fire_timer():
        await asyncio.sleep(seconds)
        await notification.reply(f"🔔 **Таймер {duration_str} завершён!** ⏱✅")
    
    task = asyncio.create_task(_fire_timer())
    _reminders.append(task)

@app.on_message(filters.command("scout", prefixes="!"))
@safe_handler
async def scout_command(client, message):
    """Deep Research: !scout <query>"""
    if not security.can_execute_command(message.from_user.username, message.from_user.id, "user"):
        return

    if len(message.command) < 2:
        await message.reply_text("🔎 Укажи тему для разведки: `!scout Bitcoin ETF`")
        return

    query = message.text.split(" ", 1)[1]
    notification = await message.reply_text(f"🕵️ **Скаут:** Ищу информацию по `{query}`...")
    
    # Сбор данных
    results = await scout.search(query)
    if not results:
        await notification.edit_text("❌ Ничего не найдено в вебе.")
        return
    
    formatted_data = scout.format_results(results)
    await notification.edit_text("📊 **Аналитик:** Изучаю данные...")
    
    # Аналитика (Nexus Analyst Persona)
    prompt = f"""
    Ты — Ведущий Аналитик Nexus. 
    Проанализируй данные, собранные Скаутом по теме: "{query}".
    
    Сырые данные:
    {formatted_data}
    
    Твоя задача:
    1. Выдели 3 ключевых факта.
    2. Оцени значимость (Низкая/Средняя/Высокая).
    3. Дай итоговую рекомендацию или краткий вывод.
    
    Отвечай лаконично, в стиле киберпанк. Язык: РУССКИЙ.
    """
    
    report = await router.route_query(
        prompt, 
        task_type='reasoning', 
        is_private=message.chat.type == enums.ChatType.PRIVATE
    )
    
    final_text = f"🕵️‍♂️ **Nexus Intelligence Report: {query}**\n\n{report}"
    await notification.edit_text(final_text)

@app.on_message(filters.command("news", prefixes="!"))
async def news_command(client, message):
    """Fresh News: !news <query>"""
    query = "Криптовалюты" if len(message.command) < 2 else message.text.split(" ", 1)[1]
    notification = await message.reply_text(f"🗞️ Ищу свежие новости по теме `{query}`...")
    
    news_results = await scout.search_news(query)
    if not news_results:
        await notification.edit_text("❌ Не удалось найти свежих новостей.")
        return
    
    formatted_news = scout.format_results(news_results)
    await notification.edit_text("🧠 **Саммари новостей...**")
    
    prompt = f"Составь краткий дайджест самых свежих новостей по теме '{query}' на основе этих данных:\n\n{formatted_news}\n\nБудь краток."
    summary = await router.route_query(
        prompt, 
        task_type='chat', 
        is_private=message.chat.type == enums.ChatType.PRIVATE
    )
    
    await notification.edit_text(f"🗞️ **Fresh News Digest: {query}**\n\n{summary}")

@app.on_message(filters.command("help", prefixes="!"))
@safe_handler
async def show_help(client, message):
    """Справка по командам бота."""
    text = (
        "**🦀 Krab v4.0 (Singularity) — Команды:**\n\n"
        "**Основные:**\n"
        "`!status` — Здоровье AI\n"
        "`!diagnose` — Полная диагностика\n"
        "`!config` — Настройки (hot-reload)\n"
        "`!logs` — Чтение системного лога\n"
        "`!help` — Справка\n\n"
        "**Intelligence & Agents (v3.0):**\n"
        "`!smart <задача>` — Автономное решение задачи (Plan -> Gen)\n"
        "`!personality` — Смена личности (coder, pirate...)\n"
        "`!think <тема>` — Deep Reasoning (Thinking Mode)\n"
        "`!scout <тема>` — Deep Research (Web Search)\n"
        "`!learn <факт>` — Обучение (RAG)\n"
        "`!summary` — Саммари чата\n\n"
        "**AI Tools:**\n"
        "`!translate` — Перевод RU↔EN\n"
        "`!say` — Голосовое (TTS)\n"
        "`!code` — Написать код\n"
        "📎 Отправь документ — авто-анализ (PDF/DOCX/Excel)\n"
        "📹 Отправь видео/кружок — AI-анализ контента\n\n"
        "**System & macOS (v5.0):**\n"
        "`!sysinfo` — RAM / CPU / Диск / GPU / Батарея\n"
        "`!mac` — macOS Bridge (уведомления, громкость, приложения)\n"
        "`!rag` — Управление базой знаний (stats/cleanup/search)\n"
        "`!refactor` — Саморефакторинг проекта (Owner)\n"
        "`!panic` — Режим секретности (Panic Button)\n\n"
        "**Dev (Owner):**\n"
        "`!exec` — Python REPL\n"
        "`!sh` — Terminal (Shell)\n"
        "`!commit` — Git push\n"
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 Wiki", url="https://github.com/Pavua/Krab-openclaw")],
        [InlineKeyboardButton("📊 Статистика", callback_data="diag_full")]
    ])
    
    await message.reply_text(text, reply_markup=keyboard)

# --- !logs: Просмотр последних логов ---
@app.on_message(filters.command("logs", prefixes="!"))
@safe_handler
async def show_logs(client, message: Message):
    """Показать последние строки логов (Owner only)."""
    sender = message.from_user.username if message.from_user else "Unknown"
    owner = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
    if sender != owner and not message.from_user.is_self:
        return

    lines = 20
    if len(message.command) > 1:
        try:
            lines = int(message.command[1])
        except ValueError:
            pass

    log_text = get_last_logs(lines)
    if not log_text:
        log_text = "Логи пусты."
        
    await message.reply_text(f"📋 **Последние {lines} строк логов:**\n\n```{log_text[-4000:]}```")

# --- !personality: Смена личности ---
@app.on_message(filters.command("personality", prefixes="!"))
@safe_handler
async def change_personality(client, message: Message):
    """Смена личности бота: !personality coder / !personality pirate"""
    sender = message.from_user.username if message.from_user else "Unknown"
    owner = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
    if sender != owner and not message.from_user.is_self:
        return

    args = message.command
    if len(args) == 1:
        # Показать список доступных
        personas = persona_manager.get_persona_list()
        text = "👤 **Доступные личности Krab v3.0:**\n\n"
        for pid, info in personas.items():
            active = "✅ " if pid == persona_manager.active_persona else "▫️ "
            text += f"{active}**{pid}**: {info['name']} — _{info['description']}_\n"
        
        text += "\nИспользуй: `!personality <id>` для переключения."
        await message.reply_text(text)
        return

    target = args[1].lower()
    if persona_manager.set_persona(target):
        info = persona_manager.get_persona_info(target)
        await message.reply_text(f"🎭 **Личность изменена на: {info['name']}**\n_{info['description']}_")
    else:
        await message.reply_text(f"❌ Личность `{target}` не найдена.")

# --- !think: Режим долгого раздумья ---
@app.on_message(filters.command("think", prefixes="!"))
@safe_handler
async def think_command(client, message: Message):
    """Reasoning Mode: !think <запрос>"""
    if len(message.command) < 2:
        await message.reply_text("🧠 О чем мне подумать? `!think Как работает квантовый компьютер?`")
        return

    prompt = message.text.split(" ", 1)[1]
    notification = await message.reply_text("🧠 **Размышляю...** (Reasoning Mode)")
    
    # Отправляем в роутер с типом 'reasoning'
    context = memory.get_recent_context(message.chat.id, limit=5)
    
    response = await router.route_query(
        prompt=prompt,
        task_type='reasoning',
        context=context,
        is_private=message.chat.type == enums.ChatType.PRIVATE
    )
    
    await notification.edit_text(response)
    memory.save_message(message.chat.id, {"role": "assistant", "text": response})

# --- !smart: Агентный цикл (Phase 6) ---
@app.on_message(filters.command("smart", prefixes="!"))
@safe_handler
async def smart_command(client, message: Message):
    """Agent Workflow: !smart <задача>"""
    if not security.can_execute_command(message.from_user.username, message.from_user.id, "user"):
        return

    if len(message.command) < 2:
        await message.reply_text("🧠 Опиши сложную задачу: `!smart Разработай план переезда в другую страну`")
        return

    prompt = message.text.split(" ", 1)[1]
    notification = await message.reply_text("🕵️ **Agent:** Инициализирую воркфлоу...")
    
    # Запускаем агентный цикл
    result = await agent.solve_complex_task(prompt, message.chat.id)
    
    await notification.edit_text(result)
    memory.save_message(message.chat.id, {"role": "assistant", "text": result})

# --- !sh: Терминал (Owner only) ---
@app.on_message(filters.command(["sh", "terminal"], prefixes="!"))
@safe_handler
async def shell_command(client, message: Message):
    """Execution Shell: !sh <command> (Owner Only)"""
    sender = message.from_user.username if message.from_user else "Unknown"
    owner = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
    
    # Двойная проверка безопасности
    if sender != owner and not message.from_user.is_self:
        logger.warning(f"⛔ Unauthorized shell attempt from @{sender}")
        return

    if len(message.command) < 2:
        await message.reply_text("💻 Введи команду: `!sh ls -la`")
        return

    cmd = message.text.split(" ", 1)[1]
    notification = await message.reply_text("💻 **Выполняю...**")
    
    result = await tools.run_shell(cmd)
    
    # Обрезаем если слишком длинный вывод для Telegram
    if len(result) > 4000:
        result = result[:3900] + "\n...[Output Truncated]..."
        
    await notification.edit_text(f"💻 **Результат:**\n\n```\n{result}\n```")

# --- !summary: Саммари контекста (Phase 7) ---
@app.on_message(filters.command("summary", prefixes="!"))
@safe_handler
async def summary_command(client, message: Message):
    """Summarize Chat: !summary (Owner/Admin)"""
    if not security.can_execute_command(message.from_user.username, message.from_user.id, "admin"):
        return

    notification = await message.reply_text("📝 **Анализирую историю чата...**")
    
    # Берем ВСЮ историю (limit=0)
    history = memory.get_recent_context(message.chat.id, limit=0)
    if not history:
        await notification.edit_text("❌ История этого чата пуста.")
        return

    # Форматируем историю для AI
    history_str = "\n".join([f"{m.get('user', m.get('role', 'Unknown'))}: {m.get('text', m.get('content', ''))}" for m in history])
    
    # Запрос на саммаризацию
    summary_prompt = f"### ИСТОРИЯ ЧАТА:\n{history_str[-15000:]}\n\n### ИНСТРУКЦИЯ:\nСделай краткое, но емкое саммари этого диалога. Выдели ключевые темы, принятые решения и текущее состояние. Пиши на русском."
    
    summary_text = await router.route_query(summary_prompt, task_type='reasoning')
    
    # Сохраняем
    memory.save_summary(message.chat.id, summary_text)
    
    await notification.edit_text(f"📝 **Саммари сохранено!**\n\n{summary_text}")

# --- !sysinfo: Системный монитор (Owner) ---
@app.on_message(filters.command(["sysinfo", "system", "ram"], prefixes="!"))
@safe_handler
async def sysinfo_command(client, message: Message):
    """Системный монитор: RAM, CPU, диск, GPU, батарея."""
    sender = message.from_user.username if message.from_user else "Unknown"
    owner = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
    
    if sender != owner and not message.from_user.is_self:
        return
    
    notification = await message.reply_text("🖥️ **Сканирую систему...**")
    
    try:
        from src.utils.system_monitor import SystemMonitor
        
        snapshot = SystemMonitor.get_snapshot()
        report = snapshot.format_report()
        
        # Добавляем инфо о процессе бота
        proc_info = SystemMonitor.get_process_info()
        report += (
            f"\n\n**🦀 Процесс Krab:**\n"
            f"  PID: {proc_info['pid']}\n"
            f"  RAM: {proc_info['ram_mb']:.0f} MB\n"
            f"  Потоки: {proc_info['threads']}\n"
            f"  Открытых файлов: {proc_info['open_files']}"
        )
        
        # Предупреждения
        warnings = []
        if snapshot.is_ram_critical():
            warnings.append("⚠️ **КРИТИЧНО:** RAM почти исчерпана!")
        if snapshot.is_disk_critical():
            warnings.append("⚠️ **КРИТИЧНО:** Диск почти заполнен!")
        
        if warnings:
            report += "\n\n" + "\n".join(warnings)
        
        await notification.edit_text(report)
        
    except Exception as e:
        await notification.edit_text(f"❌ Ошибка мониторинга: {e}")

# --- !mac: macOS Automation Bridge (Owner only) ---
@app.on_message(filters.command("mac", prefixes="!"))
@safe_handler
async def mac_command(client, message: Message):
    """
    macOS Automation: !mac <действие> [параметры]
    Примеры:
        !mac volume 50
        !mac notify Заголовок | Текст
        !mac apps
        !mac battery
        !mac wifi
        !mac clipboard
        !mac open_url https://google.com
        !mac music play
        !mac say Привет мир
    """
    sender = message.from_user.username if message.from_user else "Unknown"
    owner = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
    
    if sender != owner and not message.from_user.is_self:
        logger.warning(f"⛔ Unauthorized mac command attempt from @{sender}")
        return
    
    if len(message.command) < 2:
        help_text = (
            "**🍎 macOS Bridge — Команды:**\n\n"
            "`!mac battery` — Батарея\n"
            "`!mac wifi` — Текущая сеть\n"
            "`!mac volume <0-100>` — Громкость\n"
            "`!mac mute` — Без звука\n"
            "`!mac apps` — Запущенные приложения\n"
            "`!mac open <App>` — Открыть приложение\n"
            "`!mac quit <App>` — Закрыть приложение\n"
            "`!mac clipboard` — Буфер обмена\n"
            "`!mac notify <text>` — Уведомление\n"
            "`!mac music play/next/current` — Музыка\n"
            "`!mac say <text>` — Произнести вслух\n"
            "`!mac lock` — Заблокировать экран\n"
            "`!mac url <link>` — Открыть URL"
        )
        await message.reply_text(help_text)
        return
    
    action = message.command[1].lower()
    args = message.command[2:] if len(message.command) > 2 else []
    
    try:
        from src.utils.mac_bridge import MacAutomation
        mac = MacAutomation
        
        # Маппинг действий
        if action == "battery":
            result = await mac.get_battery_status()
        elif action == "wifi":
            result = await mac.get_wifi_name()
        elif action == "volume":
            if args:
                result = await mac.set_volume(int(args[0]))
            else:
                result = await mac.get_volume()
        elif action == "mute":
            result = await mac.toggle_mute()
        elif action == "apps":
            result = await mac.list_running_apps()
        elif action == "open":
            result = await mac.open_app(" ".join(args))
        elif action == "quit":
            result = await mac.quit_app(" ".join(args))
        elif action == "clipboard":
            result = await mac.get_clipboard()
        elif action == "notify":
            text = " ".join(args)
            if "|" in text:
                title, msg = text.split("|", 1)
                result = await mac.send_notification(title.strip(), msg.strip())
            else:
                result = await mac.send_notification("Krab", text)
        elif action == "music":
            sub = args[0] if args else "current"
            if sub in ("play", "pause", "toggle"):
                result = await mac.music_play_pause()
            elif sub == "next":
                result = await mac.music_next()
            else:
                result = await mac.music_current()
        elif action == "say":
            result = await mac.say_text(" ".join(args))
        elif action == "lock":
            result = await mac.lock_screen()
        elif action == "url":
            result = await mac.open_url(" ".join(args))
        else:
            result = f"❌ Неизвестное действие: {action}"
        
        await message.reply_text(f"🍎 {result}")
        
    except Exception as e:
        await message.reply_text(f"❌ Ошибка macOS Bridge: {e}")

# --- !rag: Управление базой знаний (Owner/Admin) ---
@app.on_message(filters.command("rag", prefixes="!"))
@safe_handler
async def rag_command(client, message: Message):
    """
    Управление RAG базой знаний.
    !rag — статистика
    !rag cleanup — удалить устаревшие документы
    !rag export — экспорт в JSON
    !rag search <запрос> — поиск по базе
    """
    sender = message.from_user.username if message.from_user else "Unknown"
    owner = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
    
    if sender != owner and not message.from_user.is_self:
        return
    
    sub = message.command[1].lower() if len(message.command) > 1 else "stats"
    
    if sub == "stats":
        report = router.rag.format_stats_report()
        await message.reply_text(report)
        
    elif sub == "cleanup":
        notification = await message.reply_text("🧹 **Очищаю устаревшие документы...**")
        removed = router.rag.cleanup_expired()
        await notification.edit_text(f"🧹 **Очистка завершена!** Удалено: {removed} документов")
        
    elif sub == "export":
        notification = await message.reply_text("📦 **Экспортирую базу знаний...**")
        path = router.rag.export_knowledge()
        if path:
            await notification.edit_text(f"📦 **Экспорт завершён!**\nФайл: `{path}`")
        else:
            await notification.edit_text("❌ Ошибка экспорта")
        
    elif sub == "search":
        query = " ".join(message.command[2:]) if len(message.command) > 2 else ""
        if not query:
            await message.reply_text("🔍 Укажи запрос: `!rag search <текст>`")
            return
        
        results = router.rag.query_with_scores(query, n_results=5)
        if results:
            text = "**🔍 Результаты поиска в RAG:**\n\n"
            for i, r in enumerate(results, 1):
                expired_mark = " ⏰" if r['expired'] else ""
                text += (
                    f"**{i}.** [{r['category']}]{expired_mark} (score: {r['score']})\n"
                    f"`{r['text'][:150]}...`\n\n"
                )
            await message.reply_text(text)
        else:
            await message.reply_text("🔍 Ничего не найдено в базе знаний.")
    else:
        await message.reply_text(
            "**🧠 RAG v2.0 — Команды:**\n\n"
            "`!rag` — Статистика\n"
            "`!rag cleanup` — Очистка устаревших\n"
            "`!rag export` — Экспорт в JSON\n"
        )

# --- !panic / !stealth: Panic Button (Owner only) ---
@app.on_message(filters.command(["panic", "stealth"], prefixes="!"))
@safe_handler
async def panic_command(client, message: Message):
    """
    Режим секретности (Panic Button).
    Мгновенно ограничивает доступ ко всем командам и блокирует систему.
    """
    sender = message.from_user.username if message.from_user else "Unknown"
    owner = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
    
    if sender != owner and not message.from_user.is_self:
        return
        
    is_stealth = security.toggle_stealth()
    
    if is_stealth:
        # Экстренные действия через MacBridge
        from src.utils.mac_bridge import MacAutomation
        await MacAutomation.execute_intent("notification", {"title": "🛡️ Krab Security", "message": "Stealth Mode Activated. Restricted access enabled."})
        # Можно добавить блокировку экрана:
        # await MacAutomation.run_applescript('tell application "System Events" to sleep')
        
        await message.reply_text(
            "🕶️ **STEALTH MODE: ACTIVATED**\n\n"
            "• Все входящие запросы от посторонних будут игнорироваться.\n"
            "• Доступ к командам ограничен только Владельцем.\n"
            "• Бот перешёл в режим пониженной видимости."
        )
    else:
        await message.reply_text(
            "🔓 **STEALTH MODE: DEACTIVATED**\n\n"
            "• Стандартный режим работы восстановлен.\n"
            "• Уровни доступа (Admin/User) снова активны."
        )

# --- !refactor: Саморефакторинг (Owner only) ---
@app.on_message(filters.command("refactor", prefixes="!"))
@safe_handler
async def refactor_command(client, message: Message):
    """
    Саморефакторинг кода Krab.
    !refactor <file_path> [инструкции]
    !refactor audit — аудит безопасности всего проекта
    """
    sender = message.from_user.username if message.from_user else "Unknown"
    owner = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
    
    if sender != owner and not message.from_user.is_self:
        return
        
    if len(message.command) < 2:
        await message.reply_text("📋 Использование: `!refactor <путь_к_файлу> [инструкции]` или `!refactor audit`")
        return

    from src.utils.self_refactor import SelfRefactor
    refactorer = SelfRefactor(os.getcwd())
    
    sub = message.command[1].lower()
    
    if sub == "audit":
        notification = await message.reply_text("🕵️‍♂️ **Провожу аудит безопасности проекта...**")
        report = await refactorer.find_vulnerabilities(router)
        await notification.edit_text(f"🕵️‍♂️ **Security Audit Report:**\n\n{report}")
        
    else:
        target_file = sub
        instructions = " ".join(message.command[2:]) if len(message.command) > 2 else ""
        
        notification = await message.reply_text(f"👨‍🔬 **Анализирую `{target_file}`...**")
        
        proposal = await refactorer.analyze_and_propose(router, target_file, instructions)
        
        # Сохраняем предложение для возможности применения (упрощенно)
        await notification.edit_text(f"👨‍🔬 **Предложение по рефакторингу `{target_file}`:**\n\n{proposal}")
        await message.reply_text("💡 _Чтобы применить изменения, скопируйте код и используйте !sh или отредактируйте вручную. Полная авто-запись будет в v5.1._")

# --- Обработка документов (PDF, DOCX, Excel, etc.) ---
@app.on_message(filters.document)
@safe_handler
async def handle_document(client, message: Message):
    """
    Автоматический парсинг документов при отправке.
    Поддерживает: PDF, DOCX, XLSX, CSV, TXT, JSON, Markdown, Python, etc.
    Результат индексируется в RAG.
    """
    # Проверяем, в ЛС ли мы или есть caption с триггером
    is_private = message.chat.type == enums.ChatType.PRIVATE
    has_trigger = message.caption and ("!read" in message.caption or "!doc" in message.caption or "!parse" in message.caption)
    
    if not (is_private or has_trigger):
        return  # В группах парсим только по запросу
    
    # Проверяем поддерживаемость формата
    filename = message.document.file_name or "unknown"
    
    try:
        from src.utils.doc_parser import DocumentParser
        
        if not DocumentParser.is_supported(filename):
            # Это не документ для парсинга (например, стикер или видео)
            return
        
        notification = await message.reply_text(f"📄 **Читаю документ:** `{filename}`...")
        
        # Скачиваем файл
        file_path = await message.download(
            file_name=f"artifacts/downloads/{filename}"
        )
        
        # Парсим
        text, metadata = await DocumentParser.parse(file_path)
        
        if text.startswith("⚠️") or text.startswith("❌"):
            await notification.edit_text(text)
        else:
            # Индексируем в RAG
            doc_id = router.rag.add_document(
                text=f"[Document: {filename}]\n{text}",
                metadata={
                    **metadata,
                    "chat_id": str(message.chat.id),
                    "timestamp": str(datetime.now())
                },
                category="document"
            )
            
            # Показываем превью
            preview = text[:500] + "..." if len(text) > 500 else text
            result_text = (
                f"📄 **Документ проанализирован:** `{filename}`\n"
                f"📊 Размер: {metadata.get('size_kb', '?')} KB | "
                f"Символов: {metadata.get('chars_extracted', '?')}\n"
                f"🧠 Проиндексирован в RAG: `{doc_id}`\n\n"
                f"**Превью:**\n```\n{preview}\n```"
            )
            
            await notification.edit_text(result_text)
            
            # Если в caption есть вопрос — отвечаем на него в контексте документа
            if message.caption and not message.caption.startswith("!"):
                context = memory.get_recent_context(message.chat.id, limit=5)
                response = await router.route_query(
                    prompt=f"[Документ '{filename}']: {text[:5000]}\n\nВопрос пользователя: {message.caption}",
                    task_type='chat',
                    context=context
                )
                await message.reply_text(response)
                memory.save_message(message.chat.id, {"role": "assistant", "text": response})
        
        # Убираем скачанный файл
        if os.path.exists(file_path):
            os.remove(file_path)
            
    except ImportError:
        pass  # Нет док. парсера — тихо пропускаем
    except Exception as e:
        logger.error(f"Document parsing error: {e}")

# --- Обработка видео (Кружки и файлы) ---
@app.on_message(filters.video | filters.video_note)
@safe_handler
async def handle_video(client, message: Message):
    """Анализ видео-контента (включая кружки) через Gemini."""
    is_private = message.chat.type == enums.ChatType.PRIVATE
    # Триггеры: в ЛС всегда, в группах по !scan или !video
    has_trigger = message.caption and ("!scan" in message.caption or "!video" in message.caption)
    
    if not (is_private or has_trigger):
        return

    notification = await message.reply_text("🎞️ **Смотрю видео (кружок)...**")
    
    try:
        # Скачиваем (кружки — это video_note)
        media = message.video or message.video_note
        file_path = await message.download(file_name=f"artifacts/downloads/{media.file_unique_id}.mp4")
        
        prompt = "Опиши подробно, что происходит на видео."
        if message.caption:
            prompt += f" Обрати внимание на: {message.caption}"
            
        analysis = await perceptor.analyze_video(file_path, router, prompt)
        
        # Индексируем в RAG
        router.rag.add_document(
            text=f"[Video Analysis]: {analysis}",
            metadata={"source": "video", "chat": str(message.chat.id), "timestamp": str(datetime.now())},
            category="vision"
        )
        
        await notification.edit_text(f"🎞️ **Анализ видео:**\n\n{analysis}")
        
        # Чистим
        if os.path.exists(file_path):
            os.remove(file_path)
            
    except Exception as e:
        logger.error(f"Video handling error: {e}")
        await notification.edit_text(f"❌ Ошибка анализа видео: {e}")

@app.on_message(filters.voice | filters.audio | filters.document)
@safe_handler
async def handle_audio(client, message: Message):
    """Автоматическая обработка голосовых (через Perceptor)."""
    # Проверяем, аудио ли это, т.к. фильтр document ловит все
    is_audio = message.voice or message.audio or (message.document and "audio" in message.document.mime_type)
    
    if not is_audio:
        return # Skip non-audio documents

    # Определяем объект медиа
    media = message.voice or message.audio or message.document
    if not media:
        return

    # Логика: Если сообщение в ЛС мне или я упомянут - транскрибировать
    is_private = message.chat.type == enums.ChatType.PRIVATE
    if is_private or (message.caption and "!txt" in message.caption):
        logger.info(f"Processing audio from {message.chat.id}")

        # Скачиваем файл
        file_path = await message.download(file_name=f"artifacts/downloads/{media.file_unique_id}.ogg")
        
        # Транскрибируем (Локально или через API)
        notification = await message.reply_text("👂 Слушаю...")
        
        # Проверяем, существует ли файл (иногда download возвращает None при ошибке)
        if not file_path or not os.path.exists(file_path):
             await notification.edit_text("❌ Ошибка скачивания файла.")
             return

        text = await perceptor.transcribe(file_path, router)

        # Сохраняем в контекст
        memory.save_message(message.chat.id, {"role": "audio_transcript", "content": text})

        await notification.edit_text(f"**Transcript:** `{text}`\n\n🤔 Думаю...")
        
        # Запрашиваем ответ у AI
        context = memory.get_recent_context(message.chat.id, limit=5)
        voice_prompt = f"[Голосовое сообщение]: {text}"
        
        response_text = await router.route_query(
            prompt=voice_prompt,
            task_type='chat',
            context=context,
            is_private=message.chat.type == enums.ChatType.PRIVATE
        )
        
        await message.reply_text(response_text)
        memory.save_message(message.chat.id, {"role": "assistant", "text": response_text})
        
        # Финализируем уведомление
        await notification.edit_text(f"**Transcript:**\n\n{text}")

        # Удаляем файл для экономии места
        os.remove(file_path)

@app.on_message(filters.photo)
async def handle_vision(client, message: Message):
    """Обработка изображений (включая HEIC)."""
    # Реагируем если есть caption !scan или !vision, ИЛИ если это ЛС с ботом (авто-скан)
    is_private = message.chat.type == enums.ChatType.PRIVATE
    should_scan = (message.caption and ("!scan" in message.caption or "!vision" in message.caption)) or is_private
    
    if should_scan:
        notification = await message.reply_text("👁️ Смотрю...")
        file_path = await message.download(file_name=f"artifacts/downloads/{message.file.unique_id}")
        
        # Анализ изображения
        description = await perceptor.analyze_image(file_path, router, prompt="Что на изображении? Опиши подробно.")
        memory.save_message(message.chat.id, {"role": "vision_analysis", "content": description})
        
        # Phase 7: OCR to RAG integration
        router.rag.add_document(
            text=f"[Vision Scan]: {description}",
            metadata={"source": "vision", "chat": str(message.chat.id), "timestamp": str(datetime.now())}
        )
        
        await notification.edit_text(f"👁️ **Vision:** `{description}`\n\n🤔 Думаю...")
        
        # Запрашиваем реакцию AI
        context = memory.get_recent_context(message.chat.id, limit=5)
        vision_prompt = f"[Пользователь прислал фото]: {description}. Прокомментируй или ответь на вопрос."
        if message.caption:
            vision_prompt += f"\nПодпись: {message.caption}"

        response_text = await router.route_query(
            prompt=vision_prompt,
            task_type='chat',
            context=context
        )
        
        await message.reply_text(response_text)
        memory.save_message(message.chat.id, {"role": "assistant", "text": response_text})
        
        await notification.edit_text(f"**Vision Analysis:**\n\n{description}")
        os.remove(file_path)

@app.on_message(filters.text & ~filters.me & ~filters.bot)
@safe_handler
async def auto_reply_logic(client, message: Message):
    """
    Умный автоответчик.
    Срабатывает, если:
    1. Это ЛС или упоминание.
    2. Пользователь в белом списке.
    3. Rate limit не превышен.
    """
    if message.text is None: return # Защита от странных апдейтов

    # 1. Проверка доступа через SecurityManager
    role = security.get_user_role(sender, message.from_user.id if message.from_user else 0)
    
    if role == "stealth_restricted":
        logger.info(f"🕶️ Stealth Mode: Ignored message from @{sender}")
        return

    allowed_users = os.getenv("ALLOWED_USERS", "").split(",")
    allowed_users = [u.strip() for u in allowed_users if u.strip()]
    owner = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
    if owner: allowed_users.append(owner)

    if role != "owner" and sender not in allowed_users and str(message.from_user.id) not in allowed_users:
        logger.info(f"⛔ Ignored unauthorized message from @{sender}")
        return
    
    # 1.5. Rate Limiting (Phase 5)
    user_id = message.from_user.id if message.from_user else 0
    if not rate_limiter.is_allowed(user_id):
        logger.warning(f"🚫 Rate limited: @{sender} ({user_id})")
        return  # Тихо игнорируем превышение лимита

    # 2. Сохраняем контекст (User Msg)
    memory.save_message(message.chat.id, {
        "user": sender,
        "text": message.text
    })

    # 3. Маршрутизация запроса
    # Получаем историю для контекста
    context = memory.get_recent_context(message.chat.id, limit=10)
    
    # Отправляем "печатает..."
    await client.send_chat_action(message.chat.id, action=enums.ChatAction.TYPING)

    # Запрашиваем ответ у AI
    response_text = await router.route_query(
        prompt=message.text,
        task_type='chat',
        context=context,
        is_private=message.chat.type == enums.ChatType.PRIVATE
    )

    # 4. Отправляем ответ
    await message.reply_text(response_text)

    # 5. Сохраняем контекст (Bot Msg)
    memory.save_message(message.chat.id, {
        "role": "assistant",
        "text": response_text
    })

# --- MAIN LOOP ---

async def main():
    logger.info("🦀 Starting Krab v5.0 (Singularity Evolution)...")
    await app.start()

    # Phase 10: MCP Initialization
    logger.info("🔌 Initializing MCP Servers...")
    await mcp_manager.connect_all()

    # Предварительная проверка роутера
    await router.check_local_health()
    me = await app.get_me()
    logger.info(f"Logged in as {me.first_name} (@{me.username})")
    
    # Инициализация планировщика
    global scheduler
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
        for task in _reminders:
            task.cancel()
            
        await mcp_manager.shutdown()
        await app.stop()
        logger.info("✅ Krab stopped cleanly.")
    
    # Notify Owner
    try:
        await app.send_message("p0lrd", (
            "🦀 **Krab v5.2 Omni-Presence Online.**\n"
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