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
import base64
import os
import textwrap
import time
from pathlib import Path

from pyrogram import Client, enums, filters
from pyrogram.types import Message

from .config import config
from .core.exceptions import KrabError, UserInputError
from .core.logger import get_logger
from .core.routing_errors import RouterError, user_message_for_surface
from .employee_templates import ROLES, get_role_prompt
from .handlers import (
    handle_agent,
    handle_clear,
    handle_config,
    handle_diagnose,
    handle_help,
    handle_ls,
    handle_model,
    handle_panel,
    handle_read,
    handle_recall,
    handle_remember,
    handle_restart,
    handle_role,
    handle_search,
    handle_set,
    handle_status,
    handle_sysinfo,
    handle_voice,
    handle_web,
    handle_write,
)
from .model_manager import model_manager
from .openclaw_client import openclaw_client
from .search_engine import close_search
from .voice_engine import text_to_speech

logger = get_logger(__name__)


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

    _known_commands: set[str] = set()

    def __init__(self):
        """Инициализация юзербота и клиента Pyrogram"""
        self.client = Client(
            config.TELEGRAM_SESSION_NAME,
            api_id=config.TELEGRAM_API_ID,
            api_hash=config.TELEGRAM_API_HASH,
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

        self._known_commands = {
            "status", "model", "clear", "config", "set", "role",
            "voice", "web", "sysinfo", "panel", "restart", "search",
            "remember", "recall", "ls", "read", "write", "agent",
            "diagnose", "help",
        }

        async def run_cmd(handler, m):
            try:
                await handler(self, m)
            except UserInputError as e:
                await m.reply(e.user_message or str(e))
            except Exception as e:
                logger.error("command_error", handler=handler.__name__, error=str(e))
                await m.reply(f"Ошибка: {str(e)[:200]}")
            finally:
                m.stop_propagation()

        # Регистрация командных оберток (Фаза 4.4: модульные хендлеры)
        @self.client.on_message(filters.command("status", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_status(c, m):
            await run_cmd(handle_status, m)

        @self.client.on_message(filters.command("model", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_model(c, m):
            await run_cmd(handle_model, m)

        @self.client.on_message(filters.command("clear", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_clear(c, m):
            await run_cmd(handle_clear, m)

        @self.client.on_message(filters.command("config", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_config(c, m):
            await run_cmd(handle_config, m)

        @self.client.on_message(filters.command("set", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_set(c, m):
            await run_cmd(handle_set, m)

        @self.client.on_message(filters.command("role", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_role(c, m):
            await run_cmd(handle_role, m)

        @self.client.on_message(filters.command("voice", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_voice(c, m):
            await run_cmd(handle_voice, m)

        @self.client.on_message(filters.command("web", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_web(c, m):
            await run_cmd(handle_web, m)

        @self.client.on_message(
            filters.command("sysinfo", prefixes=prefixes) & is_allowed, group=-1
        )
        async def wrap_sysinfo(c, m):
            await run_cmd(handle_sysinfo, m)

        @self.client.on_message(filters.command("panel", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_panel(c, m):
            await run_cmd(handle_panel, m)

        @self.client.on_message(
            filters.command("restart", prefixes=prefixes) & is_allowed, group=-1
        )
        async def wrap_restart(c, m):
            await run_cmd(handle_restart, m)

        @self.client.on_message(filters.command("search", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_search(c, m):
            await run_cmd(handle_search, m)

        @self.client.on_message(
            filters.command("remember", prefixes=prefixes) & is_allowed, group=-1
        )
        async def wrap_remember(c, m):
            await run_cmd(handle_remember, m)

        @self.client.on_message(filters.command("recall", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_recall(c, m):
            await run_cmd(handle_recall, m)

        @self.client.on_message(filters.command("ls", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_ls(c, m):
            await run_cmd(handle_ls, m)

        @self.client.on_message(filters.command("read", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_read(c, m):
            await run_cmd(handle_read, m)

        @self.client.on_message(filters.command("write", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_write(c, m):
            await run_cmd(handle_write, m)

        @self.client.on_message(filters.command("agent", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_agent(c, m):
            await run_cmd(handle_agent, m)

        @self.client.on_message(
            filters.command("diagnose", prefixes=prefixes) & is_allowed, group=-1
        )
        async def wrap_diagnose(c, m):
            await run_cmd(handle_diagnose, m)

        @self.client.on_message(filters.command("help", prefixes=prefixes) & is_allowed, group=-1)
        async def wrap_help(c, m):
            await run_cmd(handle_help, m)

        # Обработка обычных сообщений и медиа
        @self.client.on_message((filters.text | filters.photo) & ~filters.bot, group=0)
        async def wrap_message(c, m):
            await self._process_message(m)

    async def start(self):
        """Запуск юзербота"""
        logger.info("starting_userbot")
        try:
            await self.client.start()
        except Exception as exc:  # noqa: BLE001
            error_text = str(exc).lower()
            if "auth key not found" in error_text or "auth_key_unregistered" in error_text:
                removed_files = self._purge_telegram_session_files()
                logger.warning(
                    "telegram_session_invalid_auto_purge",
                    removed_files=removed_files,
                    error=str(exc),
                )
                # Повторяем старт один раз: Pyrogram запросит интерактивный логин.
                await self.client.start()
            else:
                raise
        self.me = await self.client.get_me()
        logger.info("userbot_started", me=self.me.username, id=self.me.id)

        # WAKE UP CHECK
        try:
            # Wait for OpenClaw to spin up (up to 10s)
            logger.info("waiting_for_openclaw")
            is_claw_ready = await openclaw_client.wait_for_healthy(timeout=10)

            status_emoji = "✅" if is_claw_ready else "⚠️"
            status_text = "Online" if is_claw_ready else "Gateway Unreachable (Check logs)"

            await self.client.send_message(
                "me",
                f"🦀 **Krab System Online**\nGateway: {status_emoji} {status_text}\nReady to serve.",
            )
            logger.info("wake_up_message_sent", gateway_ready=is_claw_ready)
        except Exception as e:
            logger.error("wake_up_failed", error=str(e))

        # Запуск фоновых задач (Safe Start)
        self.maintenance_task = asyncio.create_task(self._safe_maintenance())

    def _purge_telegram_session_files(self) -> list[str]:
        """
        Удаляет локальные файлы сессии Pyrogram.

        Почему:
        - После ошибки `auth key not found` сессия в SQLite обычно уже невалидна.
        - Очистка позволяет получить чистый интерактивный relogin без ручного поиска файлов.
        """
        session_name = str(config.TELEGRAM_SESSION_NAME or "kraab").strip() or "kraab"
        base_dir = Path.cwd()
        removed: list[str] = []
        for suffix in (".session", ".session-journal", ".session-shm", ".session-wal"):
            target = base_dir / f"{session_name}{suffix}"
            if target.exists():
                try:
                    target.unlink()
                    removed.append(str(target))
                except OSError as exc:
                    logger.warning("telegram_session_purge_failed", file=str(target), error=str(exc))
        return removed

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
        if not text:
            return ""
        text_lower = text.lower()

        # Сначала проверяем длинные префиксы
        sorted_prefixes = sorted(config.TRIGGER_PREFIXES + ["краб"], key=len, reverse=True)
        for prefix in sorted_prefixes:
            if text_lower.startswith(prefix.lower()):
                clean = text[len(prefix) :].strip()
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

    @staticmethod
    def _is_message_not_modified_error(exc: Exception) -> bool:
        """Определяет типичную ошибку Telegram при повторном edit того же текста."""
        text = str(exc).upper()
        return "MESSAGE_NOT_MODIFIED" in text

    async def _safe_edit(self, msg: Message, text: str) -> bool:
        """
        Безопасно редактирует сообщение.
        Возвращает True, если edit выполнен; False, если текст уже идентичен.
        """
        current_text = (getattr(msg, "text", None) or getattr(msg, "caption", None) or "").strip()
        target_text = (text or "").strip()
        if current_text == target_text:
            return False
        try:
            await msg.edit(text)
            return True
        except Exception as exc:  # noqa: BLE001 - фильтруем MESSAGE_NOT_MODIFIED
            if self._is_message_not_modified_error(exc):
                return False
            raise

    def _get_command_args(self, message: Message) -> str:
        """Извлекает аргументы команды, убирая саму команду"""
        if not message.text:
            return ""

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
            if not user or user.is_bot:
                return

            text = message.text or message.caption or ""

            if text and text.lstrip()[:1] in ("!", "/", "."):
                cmd_word = text.lstrip().split()[0].lstrip("!/.").lower()
                if cmd_word in self._known_commands:
                    return

            if not text and not message.photo:
                return

            chat_id = str(message.chat.id)
            is_self = user.id == self.me.id
            has_trigger = self._is_trigger(text)

            is_reply_to_me = (
                message.reply_to_message
                and message.reply_to_message.from_user
                and message.reply_to_message.from_user.id == self.me.id
            )

            if not (has_trigger or message.chat.type == enums.ChatType.PRIVATE or is_reply_to_me):
                return

            query = self._get_clean_text(text)
            if not query and not message.photo and not is_reply_to_me:
                return

            logger.info(
                "processing_ai_request",
                chat_id=chat_id,
                user=user.username,
                has_photo=bool(message.photo),
            )
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
                await self._safe_edit(message, f"🦀 {query}\n\n⏳ *Думаю...*")

            # VISION: Обработка фото
            images = []
            if message.photo:
                try:
                    if is_self:
                        await self._safe_edit(message, f"🦀 {query}\n\n👀 *Разглядываю фото...*")
                    else:
                        await self._safe_edit(temp_msg, "👀 *Разглядываю фото...*")

                    # in_memory=True returns BytesIO
                    photo_obj = await self.client.download_media(message, in_memory=True)
                    if photo_obj:
                        img_bytes = photo_obj.getvalue()
                        b64_img = base64.b64encode(img_bytes).decode("utf-8")
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

            chunk_timeout_sec = float(getattr(config, "OPENCLAW_CHUNK_TIMEOUT_SEC", 120.0))
            stream = openclaw_client.send_message_stream(
                message=query or ("(Image sent)" if images else ""),
                chat_id=chat_id,
                system_prompt=system_prompt,
                images=images,
                force_cloud=getattr(config, "FORCE_CLOUD", False),
            )
            stream_iter = stream.__aiter__()

            while True:
                try:
                    chunk = await asyncio.wait_for(
                        stream_iter.__anext__(),
                        timeout=chunk_timeout_sec,
                    )
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    logger.error(
                        "openclaw_stream_chunk_timeout",
                        chat_id=chat_id,
                        timeout_sec=chunk_timeout_sec,
                        has_photo=bool(images),
                    )
                    full_response = (
                        "❌ Таймаут ответа модели. Попробуй ещё раз или переключись на `!model cloud` / `!model local`."
                    )
                    try:
                        await stream.aclose()
                    except Exception:
                        pass
                    break

                full_response += chunk
                current_chunk += chunk

                if time.time() - last_edit_time > 1.5:
                    last_edit_time = time.time()
                    try:
                        display = current_chunk + " ▌"
                        if is_self:
                            await self._safe_edit(message, f"🦀 {query}\n\n{display}")
                        else:
                            await self._safe_edit(temp_msg, display)
                    except Exception:
                        pass

            if not full_response:
                full_response = "❌ Модель не вернула ответ."

            if not full_response:
                full_response = "❌ Модель не вернула ответ."

            # SPLIT LOGIC: Отправка длинных сообщений частями
            parts = self._split_message(
                f"🦀 {query}\n\n{full_response}" if is_self else full_response
            )

            if is_self:
                # Первую часть редактируем (чтобы заменить "думаю...")
                await self._safe_edit(message, parts[0])
                # Остальные отправляем следом
                for part in parts[1:]:
                    await message.reply(part)
            else:
                # Первую часть редактируем
                await self._safe_edit(temp_msg, parts[0])
                # Остальные отправляем
                for part in parts[1:]:
                    await message.reply(part)

            if self.voice_mode:
                voice_path = await text_to_speech(full_response)
                if voice_path:
                    await self.client.send_voice(message.chat.id, voice_path)
                    if os.path.exists(voice_path):
                        os.remove(voice_path)

        except KrabError as e:
            logger.warning("provider_error", error=str(e), retryable=e.retryable)
            await message.reply(e.user_message or str(e))
        except RouterError as e:
            logger.warning("routing_error", code=e.code, error=str(e))
            await message.reply(user_message_for_surface(e, telegram=True))
        except Exception as e:
            logger.error("process_message_error", error=str(e))
            await message.reply(f"🦀❌ **Ошибка в клешнях:** `{str(e)}`")

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

    async def _get_chat_context(self, chat_id: int, limit: int = 20, max_chars: int = 8000) -> str:
        """
        Получает контекст чата (последние сообщения) для групп.
        Скользящее окно: не более limit сообщений и не более max_chars символов.
        """
        try:
            messages = []
            total_chars = 0
            async for m in self.client.get_chat_history(chat_id, limit=limit):
                if m.text and len(messages) < limit:
                    sender = m.from_user.first_name if m.from_user else "Unknown"
                    line = f"{sender}: {m.text}"
                    if total_chars + len(line) > max_chars:
                        logger.debug(
                            "chat_context_trimmed",
                            chat_id=chat_id,
                            reason="max_chars",
                            total_chars=total_chars,
                            max_chars=max_chars,
                        )
                        break
                    messages.append(line)
                    total_chars += len(line)

            return "\n".join(reversed(messages))
        except Exception as e:
            logger.warning("chat_context_error", chat_id=chat_id, error=str(e))
            return ""
