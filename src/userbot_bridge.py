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
import base64
import textwrap
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from .config import config
from .core.routing_errors import RouterError, user_message_for_surface
from .model_manager import model_manager
from .openclaw_client import openclaw_client
from .employee_templates import ROLES, get_role_prompt, list_roles, save_role
from .voice_engine import text_to_speech
from .employee_templates import ROLES, get_role_prompt, list_roles
from .voice_engine import text_to_speech
from .search_engine import search_brave, close_search
from .memory_engine import memory_manager
from .mcp_client import mcp_manager

logger = structlog.get_logger(__name__)


class KraabUserbot:
    """
    Класс KraabUserbot.
    Основной мост между Telegram и AI-движком OpenClaw.
    Управляет сессией, обрабатывает команды и генерирует ответы.
    """
    
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
    6. Ты умеешь запоминать факты (!remember) и работать с файлами (!ls, !read). Ищи информацию в памяти, если пользователь спрашивает о прошлом.
    """

    def __init__(self):
        """Инициализация юзербота и клиента Pyrogram"""
        self.client = Client(
            config.TELEGRAM_SESSION_NAME,
            api_id=config.TELEGRAM_API_ID,
            api_hash=config.TELEGRAM_API_HASH
        )
        self.me = None
        self.current_role = "default"
        self.voice_mode = False
        self._setup_handlers()

    def _setup_handlers(self):
        """Регистрация обработчиков событий и команд"""
        
        # Custom Filter: Владелец или разрешенные пользователи
        def check_allowed(_, __, m):
            if not m.from_user:
                return False
            
            username = (m.from_user.username or "").lower()
            user_id = str(m.from_user.id)
            
            allowed_ids = [str(x) for x in config.ALLOWED_USERS if str(x).isdigit()]
            allowed_names = [str(x).lower() for x in config.ALLOWED_USERS if not str(x).isdigit()]
            
            is_me = m.from_user.id == self.me.id
            is_id_allowed = user_id in allowed_ids
            is_name_allowed = username in allowed_names
            
            is_me = m.from_user.id == self.me.id
            is_id_allowed = user_id in allowed_ids
            is_name_allowed = username in allowed_names
            
            result = is_me or is_id_allowed or is_name_allowed
            if not result:
                logger.warning("access_denied", user=username, id=user_id, chat=m.chat.id)
            return result

        is_allowed = filters.create(check_allowed)
        prefixes = config.TRIGGER_PREFIXES + ["/", "!", "."]

        # Регистрация командных оберток
        @self.client.on_message(filters.command("status", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_status(c, m): await self._handle_status(m)

        @self.client.on_message(filters.command("model", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_model(c, m): await self._handle_model(m)

        @self.client.on_message(filters.command("clear", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_clear(c, m): await self._handle_clear(m)
            
        @self.client.on_message(filters.command("config", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_config(c, m): await self._handle_config(m)

        @self.client.on_message(filters.command("set", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_set(c, m): await self._handle_set(m)

        @self.client.on_message(filters.command("role", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_role(c, m): await self._handle_role(m)

        @self.client.on_message(filters.command("voice", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_voice(c, m): await self._handle_voice(m)

        @self.client.on_message(filters.command("web", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_web(c, m): await self._handle_web(m)

        @self.client.on_message(filters.command("sysinfo", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_sysinfo(c, m): await self._handle_sysinfo(m)

        @self.client.on_message(filters.command("panel", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_panel(c, m): await self._handle_panel(m)

        @self.client.on_message(filters.command("restart", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_restart(c, m): await self._handle_restart(m)

        @self.client.on_message(filters.command("search", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_search(c, m): await self._handle_search(m)

        @self.client.on_message(filters.command("remember", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_remember(c, m): await self._handle_remember(m)

        @self.client.on_message(filters.command("recall", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_recall(c, m): await self._handle_recall(m)

        @self.client.on_message(filters.command("ls", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_ls(c, m): await self._handle_ls(m)

        @self.client.on_message(filters.command("read", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_read(c, m): await self._handle_read(m)

        @self.client.on_message(filters.command("write", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_write(c, m): await self._handle_write(m)

        @self.client.on_message(filters.command("agent", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_agent(c, m): await self._handle_agent(m)

        @self.client.on_message(filters.command("diagnose", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_diagnose(c, m): await self._handle_diagnose(m)

        # Обработка обычных сообщений и медиа
        @self.client.on_message((filters.text | filters.photo) & ~filters.bot, group=0)
        async def wrap_message(c, m): await self._process_message(m)

    async def start(self):
        """Запуск юзербота"""
        logger.info("starting_userbot")
        await self.client.start()
        self.me = await self.client.get_me()
        logger.info("userbot_started", me=self.me.username, id=self.me.id)
        
        # WAKE UP CHECK
        try:
             # Wait for OpenClaw to spin up (up to 10s)
             logger.info("waiting_for_openclaw")
             is_claw_ready = await openclaw_client.wait_for_healthy(timeout=10)
             
             status_emoji = "✅" if is_claw_ready else "⚠️"
             status_text = "Online" if is_claw_ready else "Gateway Unreachable (Check logs)"
             
             await self.client.send_message("me", f"🦀 **Krab System Online**\nGateway: {status_emoji} {status_text}\nReady to serve.")
             logger.info("wake_up_message_sent", gateway_ready=is_claw_ready)
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
        await close_search()

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

    def _split_message(self, text: str, limit: int = 4000) -> list[str]:
        """
        Разбивает сообщение на части, если оно превышает лимит Telegram (4096).
        Оставляет запас символов (limit=4000) для безопасности.
        """
        if len(text) <= limit:
            return [text]
        return textwrap.wrap(text, width=limit, replace_whitespace=False)

    def _get_command_args(self, message: Message) -> str:
        """Извлекает аргументы команды, убирая саму команду"""
        if not message.text: return ""
        
        # Если это не команда (нет префикса), возвращаем весь текст через clean_text
        # Но здесь мы знаем, что это хендлер команды
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            return parts[1].strip()
        return ""

    async def _process_message(self, message: Message):
        """Главный обработчик входящих сообщений"""
        try:
            user = message.from_user
            if not user or user.is_bot: return

            text = message.text or message.caption or ""
            # Если нет текста и нет фото - игнорируем
            if not text and not message.photo: return

            chat_id = str(message.chat.id)
            is_self = user.id == self.me.id
            has_trigger = self._is_trigger(text)
            
            is_reply_to_me = (
                message.reply_to_message and 
                message.reply_to_message.from_user and 
                message.reply_to_message.from_user.id == self.me.id
            )
            
            if not (has_trigger or message.chat.type == enums.ChatType.PRIVATE or is_reply_to_me):
                return

            query = self._get_clean_text(text)
            if not query and not message.photo and not is_reply_to_me: return

            logger.info("processing_ai_request", chat_id=chat_id, user=user.username, has_photo=bool(message.photo))
            action = enums.ChatAction.RECORD_AUDIO if self.voice_mode else enums.ChatAction.TYPING
            await self.client.send_chat_action(message.chat.id, action)

            # Переключение ролей
            if has_trigger and any(p in text.lower() for p in ["стань", "будь", "как"]):
                for role in ROLES:
                    if role in text.lower():
                        self.current_role = role
                        await message.reply(f"🎭 **Режим изменен:** `{role}`. Слушаю.")
                        return

            temp_msg = message
            if not is_self:
                temp_msg = await message.reply("🦀 ...")
            else:
                await message.edit(f"🦀 {query}\n\n⏳ *Думаю...*")

            # VISION: Обработка фото
            images = []
            if message.photo:
                try:
                    if is_self: await message.edit(f"🦀 {query}\n\n👀 *Разглядываю фото...*")
                    else: await temp_msg.edit("👀 *Разглядываю фото...*")
                    
                    # in_memory=True returns BytesIO
                    photo_obj = await self.client.download_media(message, in_memory=True)
                    if photo_obj:
                         img_bytes = photo_obj.getvalue()
                         b64_img = base64.b64encode(img_bytes).decode('utf-8')
                         images.append(b64_img)
                except Exception as e:
                    logger.error("photo_processing_error", error=str(e))

            full_response = ""
            current_chunk = ""
            last_edit_time = 0
            
            system_prompt = get_role_prompt(self.current_role)
            
            # CONTEXT: Добавляем контекст чата для групп
            if message.chat.type != enums.ChatType.PRIVATE:
                context = await self._get_chat_context(message.chat.id)
                if context:
                    system_prompt += f"\n\n[CONTEXT OF LAST MESSAGES]\n{context}\n[END CONTEXT]\n\nReply to the user request taking into account the context above."

            async for chunk in openclaw_client.send_message_stream(
                message=query or ("(Image sent)" if images else ""),
                chat_id=chat_id,
                system_prompt=system_prompt,
                images=images,
                force_cloud=getattr(config, "FORCE_CLOUD", False),
            ):
                full_response += chunk
                current_chunk += chunk
                
                if time.time() - last_edit_time > 1.5:
                    last_edit_time = time.time()
                    try:
                        display = current_chunk + " ▌"
                        if is_self:
                            await message.edit(f"🦀 {query}\n\n{display}")
                        else:
                            await temp_msg.edit(display)
                    except Exception: pass

            if not full_response:
                full_response = "❌ Модель не вернула ответ."
            
            if not full_response:
                full_response = "❌ Модель не вернула ответ."
            
            # SPLIT LOGIC: Отправка длинных сообщений частями
            parts = self._split_message(f"🦀 {query}\n\n{full_response}" if is_self else full_response)
            
            if is_self:
                # Первую часть редактируем (чтобы заменить "думаю...")
                await message.edit(parts[0])
                # Остальные отправляем следом
                for part in parts[1:]:
                     await message.reply(part)
            else:
                # Первую часть редактируем
                await temp_msg.edit(parts[0])
                # Остальные отправляем
                for part in parts[1:]:
                     await message.reply(part)

            if self.voice_mode:
                voice_path = await text_to_speech(full_response)
                if voice_path:
                    await self.client.send_voice(message.chat.id, voice_path)
                    if os.path.exists(voice_path): os.remove(voice_path)

        except RouterError as e:
            logger.warning("routing_error", code=e.code, error=str(e))
            await message.reply(user_message_for_surface(e, telegram=True))
        except Exception as e:
            logger.error("process_message_error", error=str(e))
            await message.reply(f"🦀❌ **Ошибка в клешнях:** `{str(e)}`")

    async def _handle_search(self, message: Message):
        """Ручной веб-поиск через Brave"""
        query = self._get_command_args(message)
        if not query or query.lower() in ["search", "!search"]:
            await message.reply("🔍 Что ищем? Напиши: `!search <запрос>`")
            message.stop_propagation()
            return
            
        msg = await message.reply(f"🔍 **Краб ищет в сети:** `{query}`...")
        try:
            results = await search_brave(query)
            
            # Если ответ слишком длинный, режем его
            if len(results) > 4000:
                results = results[:3900] + "..."
                
            await msg.edit(f"🔍 **Результаты поиска:**\n\n{results}")
        except Exception as e:
             await msg.edit(f"❌ Ошибка поиска: {e}")
        
        message.stop_propagation()

    async def _handle_remember(self, message: Message):
        """Запомнить факт"""
        text = self._get_command_args(message)
        if not text:
            await message.reply("🧠 Что запомнить? Напиши: `!remember <текст>`")
            return
            
        try:
            success = memory_manager.save_fact(text)
            if success:
                await message.reply(f"🧠 **Запомнил:** `{text}`")
            else:
                await message.reply("❌ Ошибка памяти.")
        except Exception as e:
            await message.reply(f"❌ Critical Memory Error: {e}")
        
        message.stop_propagation()

    async def _handle_recall(self, message: Message):
        """Вспомнить факт"""
        text = self._get_command_args(message)
        if not text:
            await message.reply("🧠 Что вспомнить? Напиши: `!recall <запрос>`")
            return
            
        try:
            facts = memory_manager.recall(text)
            if facts:
                await message.reply(f"🧠 **Вспомнил:**\n\n{facts}")
            else:
                await message.reply("🧠 Ничего не нашел по этому запросу.")
        except Exception as e:
            await message.reply(f"❌ Recalling Error: {e}")

        message.stop_propagation()

    async def _handle_ls(self, message: Message):
        """Список файлов"""
        path = self._get_command_args(message) or str(config.BASE_DIR)
        
        # Защита от выхода выше (хотя MCP тоже защищает, но добавим)
        if ".." in path and not config.is_valid(): # Просто заглушка, лучше довериться MCP
            pass

        msg = await message.reply("📂 Scanning...")
        try:
            result = await mcp_manager.list_directory(path)
            await msg.edit(f"📂 **Files in {path}:**\n\n`{result[:3900]}`")
        except Exception as e:
            await msg.edit(f"❌ Error listing: {e}")
            
        message.stop_propagation()

    async def _handle_read(self, message: Message):
        """Чтение файла"""
        path = self._get_command_args(message)
        if not path:
            await message.reply("📂 Какой файл читать? `!read <path>`")
            return

        # Если путь относительный, добавляем BASE_DIR
        if not path.startswith("/"):
             path = os.path.join(config.BASE_DIR, path)

        msg = await message.reply("📂 Reading...")
        try:
             content = await mcp_manager.read_file(path)
             
             if len(content) > 4000:
                 filename = os.path.basename(path)
                 content = content[:1000] + "\n... [truncated]"
                 
             await msg.edit(f"📂 **Content of {os.path.basename(path)}:**\n\n```\n{content}\n```")
        except Exception as e:
             await msg.edit(f"❌ Reading error: {e}")
        
        message.stop_propagation()

    async def _handle_write(self, message: Message):
        """Запись файла (опасно!)"""
        # Формат: !write filename [new line] content
        text = self._get_command_args(message)
        if not text: 
            await message.reply("📂 Формат: `!write <filename> <content>`")
            return
            
        parts = text.split("\n", 1)
        if len(parts) < 2:
            # Попробуем разделить по пробелу если одна строка
            parts = text.split(" ", 1)
            if len(parts) < 2:
                await message.reply("📂 Нет контента для записи.")
                return

        path = parts[0].strip()
        content = parts[1]
        
        if not path.startswith("/"):
             path = os.path.join(config.BASE_DIR, path)
             
        # Простая защита: не перезаписывать .py файлы без подтверждения (пока без подтверждения)
        # if path.endswith(".py"): ...
        
        result = await mcp_manager.write_file(path, content)
        await message.reply(result)
        
        message.stop_propagation()

    async def _handle_status(self, message: Message):
        """Статус системы и ресурсов"""
        ram = model_manager.get_ram_usage()
        is_ok = await openclaw_client.health_check()
        text = f"""
🦀 **Системный статус Краба**
---------------------------
📡 **Gateway (OpenClaw):** {'✅ Online' if is_ok else '❌ Offline'}
🧠 **Модель:** `{config.MODEL}`
🎭 **Роль:** `{self.current_role}`
🎙️ **Голос:** `{'ВКЛ' if self.voice_mode else 'ВЫКЛ'}`
💻 **RAM:** [{ "▓" * int(ram['percent']/10) + "░" * (10-int(ram['percent']/10)) }] {ram['percent']}%
"""
        await (message.edit(text) if message.from_user.id == self.me.id else message.reply(text))

    async def _handle_model(self, message: Message):
        """Управление загрузкой AI моделей"""
        args = message.text.split()
        if len(args) < 2:
            await self._handle_status(message)
            return
            
        cmd = args[1].lower()
        if cmd == "list":
            models = await model_manager.discover_models()
            lines = [f"{('☁️' if m.type.name == 'CLOUD_GEMINI' else '💻')} `{m.id}`" for m in models]
            await message.reply("**Доступные модели:**\n\n" + "\n".join(lines[:15]))
        elif cmd == "load" and len(args) > 2:
            mid = args[2]
            msg = await message.reply(f"⏳ Переключаюсь на `{mid}`...")
            if await model_manager.load_model(mid):
                config.update_setting("MODEL", mid)
                await msg.edit(f"✅ Успешно! Текущая модель: `{mid}`")
            else:
                await msg.edit(f"❌ Не удалось загрузить `{mid}`")

    async def _handle_clear(self, message: Message):
        """Очистка истории диалога"""
        openclaw_client.clear_session(str(message.chat.id))
        res = "🧹 **Память очищена. Клешни как новые!**"
        await (message.edit(res) if message.from_user.id == self.me.id else message.reply(res))

    async def _handle_config(self, message: Message):
        """Просмотр текущих настроек"""
        text = f"""
⚙️ **Конфигурация Краба**
----------------------
👤 **Владелец:** `{config.OWNER_USERNAME}`
🎯 **Триггеры:** `{', '.join(config.TRIGGER_PREFIXES)}`
🧠 **Память (RAM):** `{config.MAX_RAM_GB}GB`
"""
        await message.reply(text)

    async def _handle_set(self, message: Message):
        """Изменение настроек на лету"""
        args = message.text.split(maxsplit=2)
        if len(args) < 3:
            await message.reply("⚙️ `!set <KEY> <VAL>`")
            return
        if config.update_setting(args[1], args[2]):
            await message.reply(f"✅ `{args[1]}` обновлено!")
        else:
            await message.reply("❌ Ошибка обновления.")

    async def _handle_role(self, message: Message):
        """Смена системного промпта (личности)"""
        args = message.text.split()
        if len(args) < 2 or args[1] == "list":
            await message.reply(f"🎭 **Роли:**\n{list_roles()}")
        else:
            role = args[1] if len(args) == 2 else args[2]
            if role in ROLES:
                self.current_role = role
                await message.reply(f"🎭 Теперь я: `{role}`")
            else:
                await message.reply("❌ Роль не найдена.")

    async def _handle_voice(self, message: Message):
        """Переключение голосовых ответов"""
        self.voice_mode = not self.voice_mode
        await message.reply(f"🎙️ Голосовой режим: `{'ВКЛ' if self.voice_mode else 'ВЫКЛ'}`")

    async def _handle_web(self, message: Message):
        """Автоматизация браузера"""
        from .web_session import web_manager
        args = message.text.split()
        if len(args) < 2:
            from urllib.parse import quote
            link = lambda c: f"https://t.me/share/url?url={quote(c)}"
            await message.reply(f"🌏 **Web Control**\n\n[🔑 Login]({link('!web login')}) | [📸 Screen]({link('!web screen')})\n[🤖 GPT]({link('!web gpt привет')})", disable_web_page_preview=True)
            return
        
        sub = args[1].lower()
        if sub == "login":
            await message.reply(await web_manager.login_mode())
        elif sub == "screen":
            path = await web_manager.take_screenshot()
            if path:
                await message.reply_photo(path)
                os.remove(path)
        elif sub == "stop":
            await web_manager.stop()
            await message.reply("🛑 Web остановлен.")
        elif sub == "self-test":
             await self._run_self_test(message)

    async def _handle_sysinfo(self, message: Message):
        """Расширенная информация о хосте"""
        import psutil, platform
        text = f"🖥️ **System:** `{platform.system()}`\n🔥 **CPU:** `{psutil.cpu_percent()}%`"
        await message.reply(text)

    async def _handle_panel(self, message: Message):
        """Графическая панель управления"""
        await self._handle_status(message)

    async def _handle_restart(self, message: Message):
        """Мягкая перезагрузка процесса"""
        await message.reply("🔄 Перезапускаюсь...")
        import sys
        sys.exit(42)

    async def _run_self_test(self, message: Message):
        """Вызов внешнего теста здоровья"""
        await message.reply("🧪 Запуск теста...")
        proc = await asyncio.create_subprocess_exec(
            "python3",
            "tests/autonomous_test.py",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        asyncio.create_task(proc.wait())  # reap in background
        await message.reply("✅ Тест запущен в фоне. Проверьте `health_check.log`.")


# kraab = KraabUserbot() # REMOVED GLOBAL INSTANCE
    async def _handle_agent(self, message: Message):
        """Управление агентами: !agent new <name> <prompt>"""
        # !agent new python_expert "Ты эксперт по Python..."
        text = self._get_command_args(message)
        if not text:
            await message.reply("🕵️‍♂️ Использование: `!agent new <имя> <промпт>`\nИли: `!agent list`")
            return
            
        if text.startswith("list"):
            await message.reply(f"🕵️‍♂️ **Доступные агенты:**\n\n{list_roles()}")
            return
            
        if text.startswith("new"):
            parts = text[3:].strip().split(" ", 1)
            if len(parts) < 2:
                 await message.reply("❌ Ошибка: укажите имя и промпт.")
                 return
                 
            name = parts[0].strip()
            prompt = parts[1].strip().strip('"').strip("'")
            
            if save_role(name, prompt):
                await message.reply(f"🕵️‍♂️ **Агент создан:** `{name}`\n\nТеперь можно использовать: `стань {name}`")
            else:
                 await message.reply("❌ Ошибка при сохранении агента.")
        
        message.stop_propagation()

    async def _get_chat_context(self, chat_id: int, limit: int = 10) -> str:
        """Получает контекст чата (последние сообщения)"""
        try:
            messages = []
            async for m in self.client.get_chat_history(chat_id, limit=limit):
                if m.text:
                    sender = m.from_user.first_name if m.from_user else "Unknown"
                    messages.append(f"{sender}: {m.text}")
            
            # Reverse to chronological order
            return "\n".join(reversed(messages))
        except Exception:
            return ""

    async def _handle_diagnose(self, message: Message):
        """Диагностика системы (!diagnose)"""
        msg = await message.reply("🏥 **Запускаю диагностику системы...**")
        
        report = []
        
        # 1. Config Check
        report.append(f"**Config:**")
        report.append(f"- OPENCLAW_URL: `{config.OPENCLAW_URL}`")
        report.append(f"- LM_STUDIO_URL: `{config.LM_STUDIO_URL}`")
        
        # 2. LM Studio Check
        try:
            import httpx
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{config.LM_STUDIO_URL}/v1/models")
                if resp.status_code == 200:
                    report.append(f"- LM Studio: ✅ OK (Available)")
                else:
                    report.append(f"- LM Studio: ⚠️ Error ({resp.status_code})")
        except Exception as e:
            report.append(f"- LM Studio: ❌ Offline ({str(e)})")
            
        # 3. OpenClaw Check
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{config.OPENCLAW_URL}/health")
                if resp.status_code == 200:
                    report.append(f"- OpenClaw: ✅ OK (Healthy)")
                else:
                    report.append(f"- OpenClaw: ⚠️ Error ({resp.status_code})")
        except Exception as e:
            report.append(f"- OpenClaw: ❌ Unreachable ({str(e)})")
            report.append(f"  _Совет: Проверьте, запущен ли Gateway и совпадает ли порт (обычно 18792)_")

        await msg.edit("\n".join(report))
