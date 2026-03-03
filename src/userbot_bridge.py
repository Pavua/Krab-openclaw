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
import signal
import sqlite3
import sys
import textwrap
import time
from pathlib import Path
from typing import Optional

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
        self.client: Client | None = None
        self.me = None
        self.current_role = "default"
        self.voice_mode = False
        self.maintenance_task: Optional[asyncio.Task] = None
        self._telegram_watchdog_task: Optional[asyncio.Task] = None
        self._session_recovery_lock = asyncio.Lock()
        self._client_lifecycle_lock = asyncio.Lock()
        self._session_workdir = config.BASE_DIR / "data" / "sessions"
        self._disclosure_sent_for_chat_ids: set[str] = set()
        # Runtime-состояние старта userbot для health/handoff и контролируемой деградации.
        self._startup_state = "initializing"
        self._startup_error_code = ""
        self._startup_error = ""
        self._recreate_client()

    def _get_session_dirs(self) -> list[Path]:
        """
        Возвращает список каталогов, где могли лежать session-файлы.
        Порядок важен: сначала новый канонический путь, затем legacy.
        """
        dirs = [
            self._session_workdir,
            config.BASE_DIR,
            config.BASE_DIR / "src",
            Path.cwd(),
        ]
        unique: list[Path] = []
        seen: set[str] = set()
        for item in dirs:
            key = str(item.resolve()) if item.exists() else str(item)
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique

    def _recreate_client(self) -> None:
        """
        Полностью пересоздает экземпляр Pyrogram Client и регистрирует хендлеры заново.
        Нужен для recovery после протухшей/битой сессии.
        """
        self.client = Client(
            config.TELEGRAM_SESSION_NAME,
            api_id=config.TELEGRAM_API_ID,
            api_hash=config.TELEGRAM_API_HASH,
            workdir=str(self._session_workdir),
        )
        self._session_workdir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "telegram_client_created",
            session_name=config.TELEGRAM_SESSION_NAME,
            workdir=str(self._session_workdir),
        )
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
        @self.client.on_message((filters.text | filters.photo | filters.voice) & ~filters.bot, group=0)
        async def wrap_message(c, m):
            await self._process_message(m)

    @staticmethod
    def _is_sqlite_io_error(exc: Exception) -> bool:
        """Определяет non-fatal ошибки sqlite при сохранении сессии Telegram."""
        if isinstance(exc, sqlite3.OperationalError):
            low = str(exc).lower()
            return "disk i/o error" in low or "database is locked" in low
        low = str(exc).lower()
        return "disk i/o error" in low or "database is locked" in low

    async def _start_client_serialized(self) -> None:
        """
        Сериализованный client.start(), чтобы избежать гонки start/stop над одним sqlite session-файлом.
        """
        async with self._client_lifecycle_lock:
            assert self.client is not None
            await self.client.start()

    async def _safe_stop_client(self, *, reason: str) -> None:
        """
        Безопасный stop Telegram-клиента.

        Почему:
        - во время shutdown pyrogram может падать на сохранении sqlite-сессии;
        - такие ошибки должны считаться non-fatal и не валить весь runtime.
        """
        async with self._client_lifecycle_lock:
            if not self.client:
                return
            if not self.client.is_connected:
                return
            try:
                await self.client.stop()
            except Exception as exc:  # noqa: BLE001
                if self._is_sqlite_io_error(exc):
                    logger.warning(
                        "telegram_session_save_failed",
                        reason=reason,
                        error=str(exc),
                        non_fatal=True,
                    )
                    return
                logger.warning(
                    "telegram_client_stop_failed",
                    reason=reason,
                    error=str(exc),
                    non_fatal=False,
                )
                raise

    @staticmethod
    def _is_interactive_login_required_error(exc: Exception) -> bool:
        """
        True, если ошибка указывает, что Pyrogram запросил интерактивный ввод
        (номер телефона/код), но консоль недоступна.
        """
        if isinstance(exc, EOFError):
            return True
        text = str(exc).lower()
        return (
            "eof when reading a line" in text
            or "phone number or bot token" in text
            or "enter phone number" in text
            or "please enter" in text
        )

    def _set_startup_state(self, *, state: str, error_code: str = "", error: str = "") -> None:
        """Обновляет внутреннее состояние старта userbot."""
        self._startup_state = str(state or "unknown")
        self._startup_error_code = str(error_code or "")
        self._startup_error = str(error or "")

    def _mark_manual_relogin_required(self, *, reason: str, error: str) -> None:
        """
        Переводит userbot в контролируемый режим `login_required` без падения процесса.
        """
        self._set_startup_state(
            state="login_required",
            error_code="telegram_session_login_required",
            error=error,
        )
        logger.warning(
            "telegram_manual_relogin_required",
            reason=reason,
            error=error,
            session_name=config.TELEGRAM_SESSION_NAME,
            next_action="run_telegram_relogin_command",
        )

    def _ensure_maintenance_started(self) -> None:
        """Запускает maintenance-задачу model_manager, если она еще не активна."""
        if self.maintenance_task and not self.maintenance_task.done():
            return
        self.maintenance_task = asyncio.create_task(self._safe_maintenance())

    def get_runtime_state(self) -> dict:
        """
        Возвращает runtime-состояние userbot для health/lite и handoff.
        """
        client_connected = bool(self.client and self.client.is_connected)
        me_username = getattr(self.me, "username", None) if self.me else None
        me_id = getattr(self.me, "id", None) if self.me else None
        return {
            "startup_state": self._startup_state,
            "startup_error_code": self._startup_error_code,
            "startup_error": self._startup_error,
            "client_connected": client_connected,
            "authorized_user": me_username,
            "authorized_user_id": me_id,
        }

    async def start(self):
        """Запуск юзербота"""
        self._set_startup_state(state="starting")
        logger.info("starting_userbot")
        start_timeout_sec = int(getattr(config, "TELEGRAM_START_TIMEOUT_SEC", 35))
        max_attempts = int(getattr(config, "TELEGRAM_START_ATTEMPTS", 3))
        relogin_timeout_sec = int(getattr(config, "TELEGRAM_RELOGIN_TIMEOUT_SEC", 300))
        purged_for_relogin = False
        is_interactive_terminal = bool(getattr(sys.stdin, "isatty", lambda: False)())

        # В non-interactive запуске запрещаем провоцировать pyrogram на input().
        if not is_interactive_terminal and (not self._session_file_exists()):
            self._mark_manual_relogin_required(
                reason="session_missing_non_interactive",
                error="Telegram session отсутствует, интерактивный вход недоступен",
            )
            self._ensure_maintenance_started()
            return

        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                assert self.client is not None
                # Перед каждой попыткой мягко чистим sqlite lock-артефакты.
                self._cleanup_telegram_session_locks()
                needs_interactive_login = purged_for_relogin or (not self._session_file_exists())
                attempt_timeout = max(10, start_timeout_sec)
                if needs_interactive_login or is_interactive_terminal:
                    # В интерактивном терминале пользователь может вводить номер/код вручную,
                    # поэтому short-timeout приводит к ложным отменам и lock sqlite сессии.
                    attempt_timeout = max(attempt_timeout, relogin_timeout_sec)
                    logger.info(
                        "telegram_interactive_login_mode",
                        attempt=attempt,
                        timeout_sec=attempt_timeout,
                    )

                await asyncio.wait_for(self._start_client_serialized(), timeout=attempt_timeout)
                break
            except asyncio.TimeoutError as exc:
                last_error = exc
                logger.warning(
                    "telegram_start_timeout",
                    attempt=attempt,
                    timeout_sec=attempt_timeout,
                    session_name=config.TELEGRAM_SESSION_NAME,
                )
                # Важно: аккуратно закрываем клиент перед пересозданием, чтобы снять sqlite lock.
                try:
                    await self._safe_stop_client(reason="start_timeout")
                except Exception as stop_exc:  # noqa: BLE001
                    logger.debug("telegram_client_stop_after_timeout_failed", error=str(stop_exc))
                # На таймауте транспорт часто застревает. Пересоздаем клиента.
                self._recreate_client()
                # Если старт завис/протух, делаем controlled relogin через purge session.
                # В интерактивном режиме лучше очищать уже после первого таймаута,
                # иначе пользователь видит бесконечные `auth key not found`.
                should_purge_for_relogin = (attempt >= 2) or is_interactive_terminal
                if should_purge_for_relogin and not purged_for_relogin:
                    removed_files = self._purge_telegram_session_files()
                    purged_for_relogin = True
                    logger.warning(
                        "telegram_session_purged_for_relogin",
                        removed_files=removed_files,
                        next_attempt_timeout_sec=max(start_timeout_sec, relogin_timeout_sec),
                    )
                continue
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if self._is_db_locked_error(exc):
                    # После прерванного интерактивного старта sqlite может остаться заблокирован.
                    # Чистим lock/журналы и делаем повторную попытку без удаления основной session.
                    stale_files = self._cleanup_telegram_session_locks()
                    logger.warning(
                        "telegram_session_db_locked_retry",
                        stale_files=stale_files,
                        error=str(exc),
                        attempt=attempt,
                    )
                    try:
                        await self._safe_stop_client(reason="start_db_locked")
                    except Exception as stop_exc:  # noqa: BLE001
                        logger.debug("telegram_client_stop_after_dblock_failed", error=str(stop_exc))
                    self._recreate_client()
                    await asyncio.sleep(1.0)
                    continue
                if self._is_auth_key_invalid(exc):
                    removed_files = self._purge_telegram_session_files()
                    logger.warning(
                        "telegram_session_invalid_auto_purge",
                        removed_files=removed_files,
                        error=str(exc),
                        attempt=attempt,
                    )
                    if not is_interactive_terminal:
                        self._mark_manual_relogin_required(
                            reason="auth_key_invalid_non_interactive",
                            error=str(exc),
                        )
                        self._ensure_maintenance_started()
                        return
                    self._recreate_client()
                    continue
                if (not is_interactive_terminal) and self._is_interactive_login_required_error(exc):
                    try:
                        await self._safe_stop_client(reason="non_interactive_login_required")
                    except Exception as stop_exc:  # noqa: BLE001
                        logger.debug("telegram_stop_after_login_required_failed", error=str(stop_exc))
                    self._mark_manual_relogin_required(
                        reason="interactive_prompt_in_non_tty",
                        error=str(exc),
                    )
                    self._ensure_maintenance_started()
                    return
                raise
        else:
            raise RuntimeError(
                f"Не удалось запустить Telegram client за {max_attempts} попыток: {last_error}"
            )

        self.me = await self.client.get_me()
        self._set_startup_state(state="running")
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
        self._ensure_maintenance_started()
        self._telegram_watchdog_task = asyncio.create_task(self._telegram_session_watchdog())

    @staticmethod
    def _is_auth_key_invalid(exc: Exception) -> bool:
        """True, если исключение связано с протухшей Telegram auth key."""
        text = str(exc).lower()
        return "auth key not found" in text or "auth_key_unregistered" in text

    async def _recover_telegram_session(self, reason: str) -> None:
        """
        Автовосстановление протухшей Telegram-сессии:
        stop -> purge session files -> start (интерактивный логин при необходимости).
        """
        if self._session_recovery_lock.locked():
            return
        async with self._session_recovery_lock:
            logger.warning("telegram_session_recovery_started", reason=reason)
            try:
                await self._safe_stop_client(reason="session_recovery")
            except Exception as exc:  # noqa: BLE001
                logger.warning("telegram_session_recovery_stop_failed", error=str(exc))

            removed_files = self._purge_telegram_session_files()
            logger.warning("telegram_session_files_purged", removed_files=removed_files)

            try:
                await self._start_client_serialized()
                self.me = await self.client.get_me()
                logger.info(
                    "telegram_session_recovered",
                    me=(self.me.username if self.me else None),
                    id=(self.me.id if self.me else None),
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("telegram_session_recovery_failed", error=str(exc))
                # Передаем управление авто-рестартеру launcher-скрипта.
                os.kill(os.getpid(), signal.SIGTERM)

    async def _telegram_session_watchdog(self) -> None:
        """
        Периодически проверяет валидность Telegram-сессии.
        Если auth key протухла, запускает auto-recovery без ручного удаления файлов.
        """
        interval_sec = int(getattr(config, "TELEGRAM_SESSION_HEARTBEAT_SEC", 45))
        while True:
            try:
                await asyncio.sleep(max(15, interval_sec))
                if not self.client.is_connected:
                    continue
                await self.client.get_me()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                if self._is_auth_key_invalid(exc):
                    await self._recover_telegram_session(reason=str(exc))
                else:
                    logger.warning("telegram_watchdog_probe_failed", error=str(exc))

    def _purge_telegram_session_files(self) -> list[str]:
        """
        Удаляет локальные файлы сессии Pyrogram.

        Почему:
        - После ошибки `auth key not found` сессия в SQLite обычно уже невалидна.
        - Очистка позволяет получить чистый интерактивный relogin без ручного поиска файлов.
        """
        session_name = str(config.TELEGRAM_SESSION_NAME or "kraab").strip() or "kraab"
        removed: list[str] = []
        for base_dir in self._get_session_dirs():
            for suffix in (".session", ".session-journal", ".session-shm", ".session-wal"):
                target = base_dir / f"{session_name}{suffix}"
                if target.exists():
                    try:
                        target.unlink()
                        removed.append(str(target))
                    except OSError as exc:
                        logger.warning("telegram_session_purge_failed", file=str(target), error=str(exc))
        return removed

    @staticmethod
    def _is_db_locked_error(exc: Exception) -> bool:
        """True, если ошибка связана с блокировкой sqlite session-файла."""
        return "database is locked" in str(exc).lower()

    def _cleanup_telegram_session_locks(self) -> list[str]:
        """
        Удаляет только lock/journal файлы sqlite-сессии.
        Основной `.session` файл не трогаем.
        """
        session_name = str(config.TELEGRAM_SESSION_NAME or "kraab").strip() or "kraab"
        removed: list[str] = []
        for base_dir in self._get_session_dirs():
            for suffix in (".session-journal", ".session-shm", ".session-wal"):
                target = base_dir / f"{session_name}{suffix}"
                if target.exists():
                    try:
                        target.unlink()
                        removed.append(str(target))
                    except OSError as exc:
                        logger.warning("telegram_session_lock_cleanup_failed", file=str(target), error=str(exc))
        return removed

    def _session_file_exists(self) -> bool:
        """Проверяет наличие основного session-файла (`*.session`)."""
        session_name = str(config.TELEGRAM_SESSION_NAME or "kraab").strip() or "kraab"
        for base_dir in self._get_session_dirs():
            target = base_dir / f"{session_name}.session"
            if target.exists():
                return True
        return False

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
        self._set_startup_state(state="stopping")
        if self._telegram_watchdog_task:
            self._telegram_watchdog_task.cancel()
        try:
            await self._safe_stop_client(reason="runtime_stop")
        except Exception as exc:  # noqa: BLE001
            logger.warning("telegram_stop_failed", error=str(exc), non_fatal=True)
        await model_manager.close()
        await close_search()
        self._set_startup_state(state="stopped")

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
    def _looks_like_model_status_question(text: str) -> bool:
        """Эвристика: пользователь спрашивает, на какой модели сейчас ответ."""
        low = str(text or "").strip().lower()
        if not low:
            return False
        patterns = [
            "на какой модел",
            "какой моделью",
            "какая модель",
            "на чем работаешь",
            "через какую модель",
            "какой модель",
        ]
        return any(p in low for p in patterns)

    @staticmethod
    def _build_runtime_model_status(route: dict) -> str:
        """Формирует детерминированный статус маршрута по фактическим runtime-метаданным."""
        channel = str(route.get("channel", "unknown"))
        model = str(route.get("model", "unknown"))
        provider = str(route.get("provider", "unknown"))
        tier = str(route.get("active_tier", "-"))
        if channel == "local_direct":
            mode = "local_direct (LM Studio)"
        elif channel == "openclaw_local":
            mode = "openclaw_local"
        elif channel == "openclaw_cloud":
            mode = "openclaw_cloud"
        else:
            mode = channel
        return (
            "🧭 Фактический runtime-маршрут:\n"
            f"- Канал: `{mode}`\n"
            f"- Модель: `{model}`\n"
            f"- Провайдер: `{provider}`\n"
            f"- Cloud tier: `{tier}`"
        )

    def _apply_optional_disclosure(self, *, chat_id: str, text: str) -> str:
        """
        Опционально добавляет дисклеймер в первый ответ для конкретного чата.
        Это снижает риск «неожиданности» для новых собеседников и остается честным.
        """
        if not bool(getattr(config, "AI_DISCLOSURE_ENABLED", False)):
            return text
        chat_key = str(chat_id or "").strip()
        if not chat_key:
            return text
        if chat_key in self._disclosure_sent_for_chat_ids:
            return text
        disclosure = str(getattr(config, "AI_DISCLOSURE_TEXT", "") or "").strip()
        if not disclosure:
            return text
        self._disclosure_sent_for_chat_ids.add(chat_key)
        body = str(text or "").strip()
        if not body:
            return disclosure
        return f"{disclosure}\n\n{body}"

    @staticmethod
    def _is_message_not_modified_error(exc: Exception) -> bool:
        """Определяет типичную ошибку Telegram при повторном edit того же текста."""
        text = str(exc).upper()
        return "MESSAGE_NOT_MODIFIED" in text

    @staticmethod
    def _is_message_id_invalid_error(exc: Exception) -> bool:
        """Определяет ошибку Telegram при попытке edit невалидного message id."""
        return "MESSAGE_ID_INVALID" in str(exc).upper()

    @staticmethod
    def _is_message_empty_error(exc: Exception) -> bool:
        """Определяет ошибку Telegram при попытке отправить/отредактировать пустой текст."""
        return "MESSAGE_EMPTY" in str(exc).upper()

    async def _safe_edit(self, msg: Message, text: str) -> Message:
        """
        Безопасно редактирует сообщение.
        Возвращает актуальный Message:
        - исходный, если edit не потребовался;
        - результат edit;
        - новый message при fallback на send_message.
        """
        current_text = (getattr(msg, "text", None) or getattr(msg, "caption", None) or "").strip()
        target_text = (text or "").strip()
        # Telegram EditMessage не принимает пустой/невидимый текст.
        if not target_text:
            target_text = "…"
        if current_text == target_text:
            return msg
        try:
            edited = await msg.edit(target_text)
            return edited or msg
        except Exception as exc:  # noqa: BLE001 - фильтруем MESSAGE_NOT_MODIFIED
            if self._is_message_not_modified_error(exc):
                return msg
            if self._is_message_id_invalid_error(exc) or self._is_message_empty_error(exc):
                logger.warning("telegram_edit_fallback_send_new", error=str(exc))
                return await self.client.send_message(msg.chat.id, target_text)
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
            has_voice = bool(getattr(message, "voice", None))

            if text and text.lstrip()[:1] in ("!", "/", "."):
                cmd_word = text.lstrip().split()[0].lstrip("!/.").lower()
                if cmd_word in self._known_commands:
                    return

            if not text and not message.photo and not has_voice:
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
            if not query and has_voice:
                query = "(Голосовое сообщение)"
            if not query and not message.photo and not has_voice and not is_reply_to_me:
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
                message = await self._safe_edit(message, f"🦀 {query}\n\n⏳ *Думаю...*")

            # VISION: Обработка фото
            images = []
            if message.photo:
                try:
                    if is_self:
                        message = await self._safe_edit(message, f"🦀 {query}\n\n👀 *Разглядываю фото...*")
                    else:
                        temp_msg = await self._safe_edit(temp_msg, "👀 *Разглядываю фото...*")

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
                            message = await self._safe_edit(message, f"🦀 {query}\n\n{display}")
                        else:
                            temp_msg = await self._safe_edit(temp_msg, display)
                    except Exception:
                        pass

            if not full_response:
                full_response = "❌ Модель не вернула ответ."

            # Нормализация: защита от пустого/невидимого вывода модели.
            if not str(full_response).strip():
                full_response = "❌ Модель вернула пустой ответ. Попробуй повторить запрос."

            # Если пользователь спрашивает именно о модели, отвечаем по фактическому маршруту,
            # а не доверяем декларативному тексту самой LLM.
            if self._looks_like_model_status_question(query):
                route_meta = {}
                if hasattr(openclaw_client, "get_last_runtime_route"):
                    try:
                        route_meta = openclaw_client.get_last_runtime_route() or {}
                    except Exception:
                        route_meta = {}
                if route_meta:
                    full_response = self._build_runtime_model_status(route_meta)

            full_response = self._apply_optional_disclosure(
                chat_id=chat_id,
                text=full_response,
            )

            # SPLIT LOGIC: Отправка длинных сообщений частями
            parts = self._split_message(
                f"🦀 {query}\n\n{full_response}" if is_self else full_response
            )

            if is_self:
                # Первую часть редактируем (чтобы заменить "думаю...")
                message = await self._safe_edit(message, parts[0])
                # Остальные отправляем следом
                for part in parts[1:]:
                    await message.reply(part)
            else:
                # Первую часть редактируем
                temp_msg = await self._safe_edit(temp_msg, parts[0])
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
            if self._is_auth_key_invalid(e):
                logger.error("telegram_session_invalid_in_handler", error=str(e))
                await self._recover_telegram_session(reason=str(e))
                return
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
