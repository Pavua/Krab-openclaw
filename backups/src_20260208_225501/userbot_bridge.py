"""
Userbot Bridge - Мост между Telegram и OpenClaw/AI

Функционал:
- Работает от имени пользователя (MTProto)
- Поддерживает команды и триггеры (!краб, @краб)
- Интегрируется с OpenClaw для AI ответов
- Управляет моделями через ModelManager
- Имеет систему прав доступа (Owner Only)
"""
import asyncio
import time
from typing import Optional

import structlog
import os
import sys
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from .config import config
from .model_manager import model_manager
from .openclaw_client import openclaw_client
from .employee_templates import ROLES, get_role_prompt, list_roles

logger = structlog.get_logger(__name__)


class KraabUserbot:
    """Telegram Userbot с AI функциями"""
    
    SYSTEM_PROMPT = """
    Ты - Краб 🦀, элитный AI-ассистент, работающий в режиме Userbot.
    Твой создатель и владелец - @yung_nagato (Павел). Ты предан ему до последней капли масла в своих клешнях.
    Твой стиль общения: уверенный, технологичный, с тонким гик-юмором и использованием 🦀.
    
    ОСОБЫЕ ПРАВИЛА:
    1. Если тебе пишет @p0lrd, отвечай ему с тем же уважением, что и владельцу. Он - твой соратник.
    2. Ты можешь настраивать себя. Когда пользователь просит изменить настройки (например, добавить кого-то в список или сменить триггер), подтверждай это в стиле "Система обновлена, клешни заточены".
    3. Отвечай всегда на русском языке.
    4. Используй богатое Markdown-форматирование (жирный текст, моноширинный шрифт для кода).
    5. Если тебя спросят "Кто ты?", отвечай гордо: "Я — Краб. Версия 2.0. Финальная сборка по красоте."
    """

    def __init__(self):
        self.client = Client(
            config.TELEGRAM_SESSION_NAME,
            api_id=config.TELEGRAM_API_ID,
            api_hash=config.TELEGRAM_API_HASH
        )
        self.me = None
        self.current_role = "default"
        self._setup_handlers()

    def _setup_handlers(self):
        """Регистрация обработчиков событий"""
        
        # DEBUG LOG (Lower Priority)
        @self.client.on_message(filters.all, group=999)
        async def debug_logger(client, message):
             print(f"🔥🔥🔥 BRIDGE MSG RECEIVED: {message.text or 'Media'} | Chat: {message.chat.id}")

        # Custom Filter: Me OR Allowed Users
        def check_allowed(_, __, m):
            if not m.from_user:
                return False
            
            username = m.from_user.username or ""
            user_id = m.from_user.id
            
            # Normalize allowed lists
            allowed_ids = [str(x) for x in config.ALLOWED_USERS if str(x).isdigit()]
            allowed_names = [x.lower() for x in config.ALLOWED_USERS if not str(x).isdigit()]
            
            is_me = user_id == self.me.id
            is_id_allowed = str(user_id) in allowed_ids
            is_name_allowed = username.lower() in allowed_names
            
            allowed = is_me or is_id_allowed or is_name_allowed
            
            # DEBUG LOGGING (Temporary)
            if m.text and m.text.startswith("!"):
                print(f"🔒 FILTER CHECK: User={username}({user_id}) | Me={self.me.id} | Allow={allowed}")
                print(f"   Details: IsMe={is_me}, IdAllowed={is_id_allowed}, NameAllowed={is_name_allowed}")
                print(f"   Config: {config.ALLOWED_USERS}")
            
            return allowed

        is_allowed = filters.create(check_allowed)

        # Команда /status
        @self.client.on_message(filters.command("status", prefixes=config.TRIGGER_PREFIXES + ["/", "!", "."]) & is_allowed, group=-1)
        async def status_handler(client, message):
            logger.info("cmd_status_received", user=message.from_user.username)
            if message.from_user.id != self.me.id:
                 try: await client.read_chat_history(message.chat.id)
                 except Exception: pass
            await self._cmd_status(message)
            message.stop_propagation()

        # Команда /model
        @self.client.on_message(filters.command("model", prefixes=config.TRIGGER_PREFIXES + ["/", "!", "."]) & is_allowed, group=-1)
        async def model_handler(client, message):
            if message.from_user.id != self.me.id:
                 try: await client.read_chat_history(message.chat.id)
                 except Exception: pass
            await self._cmd_model(message)
            message.stop_propagation()

        # Команда /clear
        @self.client.on_message(filters.command("clear", prefixes=config.TRIGGER_PREFIXES + ["/", "!", "."]) & is_allowed, group=-1)
        async def clear_handler(client, message):
            if message.from_user.id != self.me.id:
                 try: await client.read_chat_history(message.chat.id)
                 except Exception: pass
            openclaw_client.clear_session(str(message.chat.id))
            response = "🧹 **История очищена, память как у свежего чипа.**"
            if message.from_user.id == self.me.id:
                await message.edit(f"🦀 {response}")
            else:
                await message.reply(response)
            message.stop_propagation()

        # Команда !set (Самостоятельная настройка)
        @self.client.on_message(filters.command("set", prefixes=["!", "/"]) & is_allowed, group=-1)
        async def set_handler(client, message: Message):
            args = message.text.split(maxsplit=2)
            if len(args) < 3:
                await message.reply("🐙 **Формат:** `!set КЛЮЧ ЗНАЧЕНИЕ` (например: `!set ALLOWED_USERS pablito,admin`)")
                return
            
            key, value = args[1], args[2]
            if config.update_setting(key, value):
                await message.reply(f"🦀 **Система перенастроена!**\nПараметр `{key}` теперь имеет значение `{value}`.\nКлешни заточены, настройки сохранены.")
            else:
                await message.reply(f"❌ **Ошибка при обновлении `{key}`.** Проверь имя параметра.")
            message.stop_propagation()

        # Команда !config (Просмотр настроек)
        @self.client.on_message(filters.command("config", prefixes=["!", "/"]) & is_allowed, group=-1)
        async def config_handler(client, message: Message):
            allowed = ", ".join(config.ALLOWED_USERS)
            triggers = ", ".join(config.TRIGGER_PREFIXES)
            text = f"""
**⚙️ Краб Конфигурация**

👤 **Allowed Users:** `{allowed}`
🎯 **Triggers:** `{triggers}`
🧠 **Max RAM:** `{config.MAX_RAM_GB}GB`
🔗 **OpenClaw URL:** `{config.OPENCLAW_URL}`
"""
            await message.reply(text)
            message.stop_propagation()

        # Команда /role (Смена личности)
        @self.client.on_message(filters.command("role", prefixes=config.TRIGGER_PREFIXES + ["/", "!", "."]) & is_allowed, group=-1)
        async def role_handler(client, message: Message):
            args = message.text.split()
            if len(args) < 2 or args[1].lower() == "list":
                roles_text = list_roles()
                await message.reply(f"🎭 **Доступные роли Краба:**\n{roles_text}\n\nИспользуй `/role set <имя>` для смены.")
                return
            
            if args[1].lower() == "set" and len(args) > 2:
                role = args[2].lower()
                if role in ROLES:
                    self.current_role = role
                    await message.reply(f"🎭 **Личность изменена на `{role}`.** Клешни адаптированы под новые задачи.")
                else:
                    await message.reply(f"❌ Роль `{role}` не найдена.")
            message.stop_propagation()

        # Команда /sysinfo
        @self.client.on_message(filters.command("sysinfo", prefixes=config.TRIGGER_PREFIXES + ["/", "!", "."]) & is_allowed, group=-1)
        async def sysinfo_handler(client, message: Message):
            import psutil
            import platform
            from datetime import datetime
            
            cpu_usage = psutil.cpu_percent()
            ram = psutil.virtual_memory()
            boot_time = datetime.fromtimestamp(psutil.boot_time()).strftime("%Y-%m-%d %H:%M:%S")
            
            usage = openclaw_client.get_usage_stats()
            
            text = f"""
**🖥️ Krab System Info**

**OS:** `{platform.system()} {platform.release()}`
**CPU Usage:** `{cpu_usage}%`
**RAM:** `{ram.used // (1024**2)}MB / {ram.total // (1024**2)}MB` ({ram.percent}%)
**System Boot:** `{boot_time}`
**Model:** `{config.MODEL}`
**Role:** `{self.current_role}`

**Token Usage (Session):**
Total: `{usage['total_tokens']}`
Input: `{usage['input_tokens']}` | Output: `{usage['output_tokens']}`
"""
            await message.reply(text)
            message.stop_propagation()

        # Команда /panel (UI Porting)
        @self.client.on_message(filters.command("panel", prefixes=config.TRIGGER_PREFIXES + ["/", "!", "."]) & is_allowed, group=-1)
        async def panel_handler(client, message):
            # 1. System Status
            ram = model_manager.get_ram_usage()
            is_openclaw_ok = await openclaw_client.health_check()
            usage = openclaw_client.get_usage_stats()
            
            status_icon = "🟢" if is_openclaw_ok else "🔴"
            ram_bar = "▓" * int(ram['percent'] / 10) + "░" * (10 - int(ram['percent'] / 10))
            
            # 2. Build the UI
            from urllib.parse import quote
            def btn(c): 
                # tg://msg is flaky on iOS/macOS. 
                # t.me/share/url?url=cmd is standard deep link for sharing text to chosen chat.
                # However, for userbot "self" usage, we often want to just pre-fill.
                # Let's use the most compatible one: https://t.me/share/url?url={cmd}
                # But typically this asks "Share to whom?". 
                # Let's try `tg://resolve?domain=me&text=cmd` if self, else ... it's hard to target 'current chat' without `tg://msg`.
                # User reported `tg://msg` fails.
                # Let's try the pure `t.me/share` generic approach.
                q = quote(c)
                return f"https://t.me/share/url?url={q}"

            text = f"""
🎮 **Krab Control Panel**

**System:**
RAM: `{ram_bar}` {ram['percent']}% ({ram['used_gb']}GB)
Gateway: {status_icon} {"Online" if is_openclaw_ok else "Offline"}
Model: `{config.MODEL}`
Role: `{self.current_role}`

**Tokens:** `{usage['total_tokens']}` (In: {usage['input_tokens']} | Out: {usage['output_tokens']})

**Quick Actions:**
[🧠 Модели]({btn('!model list')})  |  [📊 Статус]({btn('!sysinfo')})
[🔄 Рестарт]({btn('!restart')})  |  [🧹 Очистить]({btn('!clear')})

**Config:**
[🎭 Роли]({btn('!role list')})     |  [⚙️ Настройки]({btn('!config')})

_Нажми на кнопу, чтобы подставить команду._
"""
            if message.from_user.id == self.me.id:
                await message.edit(text, disable_web_page_preview=True)
            else:
                await message.reply(text, disable_web_page_preview=True)
            
            message.stop_propagation()

        # Команда /restart
        @self.client.on_message(filters.command("restart", prefixes=config.TRIGGER_PREFIXES + ["/", "!", "."]) & is_allowed, group=-1)
        async def restart_handler(client, message):
            import os
            import sys
            
            logger.info("restart_command_received", user=message.from_user.username)
            msg = await (message.edit("🔄 **Перезагрузка систем...**") if message.from_user.id == self.me.id else message.reply("🔄 **Перезагрузка систем...**"))
            
            # Restart via Exit Code 42 (Handled by run_krab.sh)
            logger.info("restarting_process_via_exit_code")
            message.stop_propagation()
            sys.exit(42)

        # === COMMAND CATCHER ===
        @self.client.on_message(filters.command(["panel", "restart", "model", "sysinfo", "clear", "role", "config", "set"], prefixes=config.TRIGGER_PREFIXES + ["/", "!", "."]), group=-1)
        async def command_fallback(client, message):
             logger.warning("command_fallback_caught", user=message.from_user.username, text=message.text)
             message.stop_propagation()

        # Обработка сообщений (ЛС и Группы) с триггером
        @self.client.on_message(filters.text & ~filters.bot, group=0)
        async def message_handler(client, message: Message):
            await self._process_message(message)

    async def start(self):
        """Запуск юзербота"""
        logger.info("starting_userbot")
        await self.client.start()
        self.me = await self.client.get_me()
        logger.info("userbot_started", me=self.me.username, id=self.me.id)
        
        # WAKE UP CHECK
        try:
            await self.client.send_message("me", "🦀 **Krab System Online**\nReady to serve.")
            logger.info("wake_up_message_sent")
        except Exception as e:
            logger.error("wake_up_failed", error=str(e))

        # Запуск фоновых задач (Safe Start)
        self.maintenance_task = asyncio.create_task(self._safe_maintenance())

    async def _safe_maintenance(self):
        """Безопасный запуск maintenance"""
        try:
             logger.info("maintenance_task_start")
             await model_manager.start_maintenance()
        except asyncio.CancelledError:
             logger.info("maintenance_task_cancelled")
        except Exception as e:
             logger.error("maintenance_task_error", error=str(e))

    async def stop(self):
        """Остановка юзербота"""
        if self.client.is_connected:
            await self.client.stop()
        await model_manager.close()

    def _is_trigger(self, text: str) -> bool:
        """Проверяет есть ли триггер в сообщении"""
        if not text:
            return False
        text_lower = text.strip().lower()
        
        # Основные префиксы из конфига (!краб, @краб и т.д.)
        for prefix in config.TRIGGER_PREFIXES:
            if text_lower.startswith(prefix.lower()):
                return True
        
        # Просто упоминание имени в начале или конце (опционально)
        # Но по просьбе пользователя: "может и просто откликаться на Краб"
        if text_lower.startswith("краб"):
            return True
            
        return False

    def _get_clean_text(self, text: str) -> str:
        """Убирает триггер из текста"""
        if not text: return ""
        text_lower = text.lower()
        
        # Сначала проверяем длинные префиксы
        sorted_prefixes = sorted(config.TRIGGER_PREFIXES + ["краб"], key=len, reverse=True)
        for prefix in sorted_prefixes:
            if text_lower.startswith(prefix.lower()):
                clean = text[len(prefix):].strip()
                # Убираем запятую если она была после имени (Краб, привет)
                if clean.startswith(","):
                    clean = clean[1:].strip()
                return clean
        return text.strip()

    async def _process_message(self, message: Message):
        """Главный обработчик сообщений"""
        
        # Security Check
        user = message.from_user
        if not user: return
        
        username = user.username or ""
        user_id = user.id
        
        # Normalize allowed lists
        allowed_ids = [str(x) for x in config.ALLOWED_USERS if str(x).isdigit()]
        allowed_names = [x.lower() for x in config.ALLOWED_USERS if not str(x).isdigit()]
        
        is_me = user_id == self.me.id
        is_id_allowed = str(user_id) in allowed_ids
        is_name_allowed = username.lower() in allowed_names
        
        is_allowed = is_me or is_id_allowed or is_name_allowed
        
        if not is_allowed:
            return
        
        text = message.text or message.caption or ""
        chat_id = str(message.chat.id)
        is_self = message.from_user.id == self.me.id
        is_p0lrd = (username == "p0lrd" or str(user_id) == "p0lrd" or "p0lrd" in config.ALLOWED_USERS)
        is_private = message.chat.type == enums.ChatType.PRIVATE
        
        # Reply to me check
        is_reply_to_me = (
            message.reply_to_message and 
            message.reply_to_message.from_user and 
            message.reply_to_message.from_user.id == self.me.id
        )
        has_trigger = self._is_trigger(text)
        
        # Интеллектуальное переключение моделей
        if has_trigger and any(phrase in text.lower() for phrase in ["поставь модель", "смени модель на", "используй модель", "загрузи модель"]):
            # Пробуем найти название модели в тексте
            models = await model_manager.discover_models()
            for m in models:
                if m.id.lower() in text.lower() or m.id.split("/")[-1].lower() in text.lower():
                    # Нашли!
                    msg = await message.reply(f"⏳ Проверяю доступ к `{m.id}`...")
                    if await model_manager.verify_model_access(m.id):
                        if m.type.name == "CLOUD_GEMINI":
                            config.update_setting("MODEL", m.id)
                            await msg.edit(f"✅ **Клешни настроены!**\nТеперь я использую `{m.id}`.")
                        else:
                            await msg.edit(f"⏳ Загружаю локальную модель `{m.id}`...")
                            if await model_manager.load_model(m.id):
                                config.update_setting("MODEL", m.id)
                                await msg.edit(f"✅ **Клешни настроены!**\nЛокальная модель `{m.id}` готова.")
                            else:
                                await msg.edit(f"❌ Не удалось загрузить `{m.id}`.")
                        return
            
        # Интеллектуальное переключение ролей через диалог
        
        should_respond = False
        
        # Логика принятия решения
        if is_self:
            # В своих сообщениях (Saved Messages или просто в чатах) реагируем только на явные команды
            if has_trigger: should_respond = True
        elif is_private:
            # В ЛС: владелец или p0lrd - отвечаем всегда. Остальным - по триггеру.
            if is_p0lrd or has_trigger:
                should_respond = True
            # Примечание: если мы хотим вообще всем в ЛС отвечать, можно убрать has_trigger
        else:
            # В группах - триггер или реплай (или если p0lrd обращается по имени)
            if has_trigger or is_reply_to_me:
                should_respond = True
        
        if not should_respond:
            return
            
        # Очистка текста от триггера
        query = self._get_clean_text(text)
        
        # Если это p0lrd в ЛС и нет текста (например переслал что-то), 
        # или просто p0lrd пишет без триггера - query будет очищенным текстом.
        if is_private and (is_p0lrd) and not has_trigger:
            query = text.strip()
            
        if not query and not message.reply_to_message:
            return 
            
        # Auto-read if not self
        if not is_self:
            try:
                await self.client.read_chat_history(message.chat.id)
            except Exception: pass

        logger.info("processing_request", user=message.from_user.username, query=query[:20])

        # Индикация печати
        await self.client.send_chat_action(message.chat.id, enums.ChatAction.TYPING)
        
        # Выбор модели
        # model = await model_manager.select_best_model("chat") # Можно использовать
        
        # Отправка в OpenClaw
        try:
            temp_msg = None
            if is_self:
                await message.edit(f"🦀 {query}\n\nThinking...")
                temp_msg = message
            else:
                temp_msg = await message.reply("🦀 Thinking...")
            
            # Получаем ответ
            response_text = ""
            last_edit_len = 0
            
            # Получаем промпт для текущей роли
            system_prompt = get_role_prompt(self.current_role)
            
            # Получаем ответ
            model_response_text = "" # Complete text (optional usage)
            current_message_text = "" # Text for the current message bubble
            last_edit_time = 0
            
            from pyrogram.errors import FloodWait

            async for chunk in openclaw_client.send_message_stream(
                message=query,
                chat_id=chat_id,
                system_prompt=system_prompt
            ):
                model_response_text += chunk
                current_message_text += chunk
                
                # CHUNK SPLITTING LOGIC
                # Telegram limit is 4096. We leave buffer for metadata/formatting.
                if len(current_message_text) > 4000:
                    # Finalize current message
                    try:
                        if is_self and temp_msg == message:
                             await message.edit(f"🦀 {query}\n\n{current_message_text}")
                        else:
                             await temp_msg.edit(current_message_text)
                    except Exception: pass
                    
                    # Create new message for continuation
                    current_message_text = "" # Reset for new message
                    try:
                        if is_self:
                             temp_msg = await self.client.send_message(message.chat.id, "🦀 ...")
                        else:
                             temp_msg = await message.reply("🦀 ...")
                    except Exception as e:
                         logger.error("chunk_split_failed", error=str(e))
                         break
                
                # Time-based throttling: Update every 1.5 seconds
                current_time = time.time()
                if current_time - last_edit_time > 1.5:
                    last_edit_time = current_time
                    try:
                        display_text = current_message_text + " ▌"
                        if is_self and temp_msg == message:
                             await message.edit(f"🦀 {query}\n\n{display_text}")
                        else:
                             await temp_msg.edit(display_text)
                    except FloodWait as e:
                        logger.warning("flood_wait", seconds=e.value)
                        await asyncio.sleep(e.value) # Wait and continue
                    except Exception: 
                        pass # Ignore other editing errors

            # Финальное обновление последнего чанка
            if not model_response_text:
                current_message_text = "🤷‍♂️ К сожалению, я не получил ответа от модели."

            if is_self and temp_msg == message:
                await message.edit(f"🦀 {query}\n\n{current_message_text}")
            else:
                await temp_msg.edit(current_message_text)
                
        except Exception as e:
            error_text = f"❌ Ошибка: {str(e)}"
            if is_self:
                await message.edit(error_text)
            else:
                await message.reply(error_text)
            logger.error("processing_error", error=str(e))

    async def _cmd_status(self, message: Message):
        """Команда /status"""
        ram = model_manager.get_ram_usage()
        
        loaded = await model_manager.get_loaded_models()
        model_list = "\n".join([f"- {m}" for m in loaded]) or "Нет загруженных"
        
        text = f"""
**🦀 Krab System Status**

**RAM:** {ram['used_gb']}GB / {config.MAX_RAM_GB}GB
**OpenClaw:** {'✅' if (await openclaw_client.health_check()) else '❌'}
**LM Studio:** {'✅' if (config.LM_STUDIO_URL) else '❓'}

**Loaded Models:**
{model_list}
"""
        if message.from_user.id == self.me.id:
            await message.edit(text)
        else:
            await message.reply(text)

    async def _cmd_model(self, message: Message):
        """Команда /model [load|unload]"""
        args = message.text.split()
        if len(args) < 2:
            await self._cmd_status(message)
            return
            
        cmd = args[1].lower()
        if cmd == "list":
            # Force discovery to get latest Google models
            models = await model_manager.discover_models()
            
            lines = []
            for m in models:
                icon = "☁️" if m.type.name == "CLOUD_GEMINI" else "💻"
                # Добавляем команду /model load id как кликабельный текст
                lines.append(f"{icon} `{m.id}`\n└ 📥 `!model load {m.id}`")
                
            text = "**Available Models:**\n\n" + "\n".join(lines[:20])
            if len(models) > 20: text += f"\n...and {len(models)-20} more"
            if message.from_user.id == self.me.id:
                await message.edit(text)
            else:
                await message.reply(text)
            
        elif cmd == "load" and len(args) > 2:
            model_id = args[2]
            msg = await (message.edit(f"⏳ Verifying access to {model_id}...") if message.from_user.id == self.me.id else message.reply(f"⏳ Verifying access to {model_id}..."))
            
            # 1. Verify Access FIRST
            if not await model_manager.verify_model_access(model_id):
                 await msg.edit(f"❌ **Ошибка:** Модель `{model_id}` недоступна или ключ неверен.\nКонфигурация НЕ изменена.")
                 return

            # 2. If Gemini/Cloud -> Just switch config
            if "google/" in model_id or any(gm in model_id for gm in config.GEMINI_MODELS):
                 if config.update_setting("MODEL", model_id):
                     await msg.edit(f"✅ **Успешно:** Переключено на облачную модель `{model_id}`")
                 else:
                     await msg.edit(f"❌ Ошибка сохранения конфига.")
                 return

            # 3. If Local -> Load then switch
            if await model_manager.load_model(model_id):
                 config.update_setting("MODEL", model_id)
                 await msg.edit(f"✅ **Успешно:** Загружена локальная модель `{model_id}`")
            else:
                await msg.edit(f"❌ **Ошибка:** Не удалось загрузить `{model_id}` в LM Studio.")
                
        elif cmd == "unload" and len(args) > 2:
            model_id = args[2]
            await model_manager.unload_model(model_id)
            if message.from_user.id == self.me.id:
                await message.edit(f"✅ Unloaded {model_id}")
            else:
                await message.reply(f"✅ Unloaded {model_id}")


# kraab = KraabUserbot() # REMOVED GLOBAL INSTANCE
