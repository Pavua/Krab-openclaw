"""
Userbot Bridge - Мост между Telegram и OpenClaw/AI

Функционал:
- Работает от имени пользователя (MTProto)
- Поддерживает команды и триггеры (!краб, @краб)
- Интегрируется с OpenClaw для AI ответов
- Управляет моделями через ModelManager
- Имеет систему прав доступа owner/full/partial/guest
"""

import asyncio
import base64
import json
import os
import re
import shutil
import sqlite3
import sys
import textwrap
import time
from pathlib import Path
from typing import Optional

from pyrogram import Client, enums, filters
from pyrogram.types import Message

from .config import config
from .core.access_control import AccessLevel, AccessProfile, resolve_access_profile
from .core.exceptions import KrabError, UserInputError
from .core.logger import get_logger
from .core.mcp_registry import resolve_managed_server_launch
from .core.openclaw_workspace import load_workspace_prompt_bundle
from .core.openclaw_runtime_models import get_runtime_primary_model
from .core.routing_errors import RouterError, user_message_for_surface
from .core.scheduler import krab_scheduler
from .employee_templates import ROLES, get_role_prompt
from .handlers import (
    handle_agent,
    handle_acl,
    handle_clear,
    handle_config,
    handle_cronstatus,
    handle_diagnose,
    handle_help,
    handle_inbox,
    handle_ls,
    handle_model,
    handle_panel,
    handle_read,
    handle_recall,
    handle_remind,
    handle_reminders,
    handle_remember,
    handle_restart,
    handle_role,
    handle_rm_remind,
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


def _current_runtime_primary_model() -> str:
    """
    Возвращает primary-модель из живого OpenClaw runtime.

    Почему helper нужен здесь:
    - truthful self-check не должен опираться на stale `.env` значение;
    - owner userbot должен видеть тот же primary, что реально выставлен в
      `~/.openclaw/openclaw.json`, даже если в этом канале ещё не было
      подтверждённого LLM-маршрута.
    """
    return str(get_runtime_primary_model() or "").strip()


def _resolve_openclaw_stream_timeouts(*, has_photo: bool) -> tuple[float, float]:
    """
    Возвращает (first_chunk_timeout_sec, chunk_timeout_sec) для OpenClaw stream.

    Почему отдельный таймаут первого чанка:
    - тяжёлые локальные модели (например Qwen 27B) могут долго выдавать первый токен;
    - после старта стрима интервалы между чанками обычно заметно меньше.
    """
    chunk_timeout_sec = float(getattr(config, "OPENCLAW_CHUNK_TIMEOUT_SEC", 180.0))
    default_first = 540.0 if has_photo else 420.0
    # Для фото-разбора допускаем отдельный override первого чанка:
    # vision-модели/большие контексты стабильно дольше выходят на первый токен.
    if has_photo:
        first_key = "OPENCLAW_PHOTO_FIRST_CHUNK_TIMEOUT_SEC"
    else:
        first_key = "OPENCLAW_FIRST_CHUNK_TIMEOUT_SEC"
    first_chunk_timeout_sec = float(
        getattr(
            config,
            first_key,
            max(chunk_timeout_sec, default_first),
        )
    )

    # Нижние границы для защиты от слишком маленьких env-значений.
    chunk_timeout_sec = max(15.0, chunk_timeout_sec)
    first_chunk_timeout_sec = max(chunk_timeout_sec, 30.0, first_chunk_timeout_sec)
    return first_chunk_timeout_sec, chunk_timeout_sec


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
    _partial_commands: set[str] = {"help", "search", "status"}
    _reply_to_tag_pattern = re.compile(
        r"\[\[\s*(?:reply_to_current|reply_to\s*:[^\]]+|reply_to_[^\]]+)\s*\]\]\s*",
        re.IGNORECASE,
    )
    _tool_response_block_pattern = re.compile(
        r"(?is)<tool_response>.*?(?:<\|im_end\|>|$)"
    )
    _llm_transport_tokens_pattern = re.compile(
        r"(?i)<\|[^|>]+?\|>|</?tool_response>"
    )
    _think_block_pattern = re.compile(r"(?is)<think>.*?</think>")
    _final_block_pattern = re.compile(r"(?is)<final>(.*?)</final>")
    _think_final_tag_pattern = re.compile(r"(?i)</?(?:think|final)>")
    _deferred_intent_pattern = re.compile(
        r"(?is)\b(напомню|сделаю|выполню|запланирую|отправлю)\b.{0,80}\b(позже|через|завтра|утром|вечером|по таймеру|по расписанию)\b"
    )

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

    def _session_name(self) -> str:
        """Нормализованное имя Telegram session-файла."""
        return str(config.TELEGRAM_SESSION_NAME or "kraab").strip() or "kraab"

    def _primary_session_file(self) -> Path:
        """Канонический session-файл, который использует текущий Pyrogram client."""
        return self._session_workdir / f"{self._session_name()}.session"

    def _inspect_session_file(self, session_file: Path) -> dict:
        """
        Легковесная диагностика sqlite session-файла:
        - есть ли auth key;
        - есть ли user binding (user_id > 0), т.е. завершенный логин.
        """
        snapshot = {
            "path": str(session_file),
            "exists": session_file.exists(),
            "has_auth_key": False,
            "has_user_binding": False,
            "user_id": 0,
            "is_bot": None,
            "error": "",
        }
        if not session_file.exists():
            return snapshot
        try:
            with sqlite3.connect(str(session_file), timeout=0.7) as conn:
                row = conn.execute(
                    "SELECT length(auth_key), coalesce(user_id,0), is_bot FROM sessions LIMIT 1"
                ).fetchone()
            if row:
                auth_len = int(row[0] or 0)
                user_id = int(row[1] or 0)
                is_bot = row[2]
                snapshot["has_auth_key"] = auth_len > 0
                snapshot["has_user_binding"] = user_id > 0
                snapshot["user_id"] = user_id
                snapshot["is_bot"] = None if is_bot is None else int(is_bot)
        except Exception as exc:  # noqa: BLE001
            snapshot["error"] = str(exc)
        return snapshot

    def _primary_session_snapshot(self) -> dict:
        """Snapshot канонического session-файла (из рабочего каталога клиента)."""
        return self._inspect_session_file(self._primary_session_file())

    def _restore_primary_session_from_legacy(self) -> bool:
        """
        Восстанавливает канонический session-файл из legacy-пути, если:
        - в рабочем пути сессия отсутствует или неавторизована;
        - в одном из legacy-путей найдена валидная авторизованная сессия.

        Это устраняет ложные relogin после миграций путей session-файла.
        """
        primary_file = self._primary_session_file()
        primary_snapshot = self._inspect_session_file(primary_file)
        if primary_snapshot["has_user_binding"]:
            return False

        session_name = self._session_name()
        for base_dir in self._get_session_dirs():
            if base_dir == self._session_workdir:
                continue
            candidate = base_dir / f"{session_name}.session"
            candidate_snapshot = self._inspect_session_file(candidate)
            if not candidate_snapshot["has_user_binding"]:
                continue
            try:
                self._session_workdir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(candidate, primary_file)
                for suffix in (".session-shm", ".session-wal", ".session-journal"):
                    sidecar = base_dir / f"{session_name}{suffix}"
                    if sidecar.exists():
                        shutil.copy2(sidecar, self._session_workdir / sidecar.name)
                logger.info(
                    "telegram_session_restored_from_legacy",
                    source=str(candidate),
                    target=str(primary_file),
                    user_id=candidate_snapshot["user_id"],
                )
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "telegram_session_restore_from_legacy_failed",
                    source=str(candidate),
                    target=str(primary_file),
                    error=str(exc),
                )
        return False

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

        prefixes = config.TRIGGER_PREFIXES + ["/", "!", "."]

        self._known_commands = {
            "status", "model", "clear", "config", "set", "role",
            "voice", "web", "sysinfo", "panel", "restart", "search",
            "inbox", "remember", "recall", "ls", "read", "write", "agent",
            "diagnose", "help", "remind", "reminders", "rm_remind", "cronstatus",
            "acl", "access",
        }

        def _make_command_filter(command_name: str):
            """Создаёт per-command ACL-фильтр без дублирования правил в декораторах."""

            def check_access(_, __, m):
                if not m.from_user:
                    return False
                result = self._has_command_access(m.from_user, command_name)
                if not result:
                    access_profile = self._get_access_profile(m.from_user)
                    logger.warning(
                        "command_access_denied",
                        command=command_name,
                        access_level=access_profile.level.value,
                        user=(m.from_user.username or "").lower(),
                        id=str(m.from_user.id),
                        chat=m.chat.id,
                    )
                return result

            return filters.create(check_access)

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
        @self.client.on_message(filters.command("status", prefixes=prefixes) & _make_command_filter("status"), group=-1)
        async def wrap_status(c, m):
            await run_cmd(handle_status, m)

        @self.client.on_message(filters.command("model", prefixes=prefixes) & _make_command_filter("model"), group=-1)
        async def wrap_model(c, m):
            await run_cmd(handle_model, m)

        @self.client.on_message(filters.command("clear", prefixes=prefixes) & _make_command_filter("clear"), group=-1)
        async def wrap_clear(c, m):
            await run_cmd(handle_clear, m)

        @self.client.on_message(filters.command("config", prefixes=prefixes) & _make_command_filter("config"), group=-1)
        async def wrap_config(c, m):
            await run_cmd(handle_config, m)

        @self.client.on_message(filters.command("set", prefixes=prefixes) & _make_command_filter("set"), group=-1)
        async def wrap_set(c, m):
            await run_cmd(handle_set, m)

        @self.client.on_message(filters.command("role", prefixes=prefixes) & _make_command_filter("role"), group=-1)
        async def wrap_role(c, m):
            await run_cmd(handle_role, m)

        @self.client.on_message(filters.command("voice", prefixes=prefixes) & _make_command_filter("voice"), group=-1)
        async def wrap_voice(c, m):
            await run_cmd(handle_voice, m)

        @self.client.on_message(filters.command("web", prefixes=prefixes) & _make_command_filter("web"), group=-1)
        async def wrap_web(c, m):
            await run_cmd(handle_web, m)

        @self.client.on_message(filters.command("inbox", prefixes=prefixes) & _make_command_filter("inbox"), group=-1)
        async def wrap_inbox(c, m):
            await run_cmd(handle_inbox, m)

        @self.client.on_message(
            filters.command("sysinfo", prefixes=prefixes) & _make_command_filter("sysinfo"), group=-1
        )
        async def wrap_sysinfo(c, m):
            await run_cmd(handle_sysinfo, m)

        @self.client.on_message(filters.command("panel", prefixes=prefixes) & _make_command_filter("panel"), group=-1)
        async def wrap_panel(c, m):
            await run_cmd(handle_panel, m)

        @self.client.on_message(
            filters.command("restart", prefixes=prefixes) & _make_command_filter("restart"), group=-1
        )
        async def wrap_restart(c, m):
            await run_cmd(handle_restart, m)

        @self.client.on_message(filters.command("search", prefixes=prefixes) & _make_command_filter("search"), group=-1)
        async def wrap_search(c, m):
            await run_cmd(handle_search, m)

        @self.client.on_message(
            filters.command("remember", prefixes=prefixes) & _make_command_filter("remember"), group=-1
        )
        async def wrap_remember(c, m):
            await run_cmd(handle_remember, m)

        @self.client.on_message(filters.command("recall", prefixes=prefixes) & _make_command_filter("recall"), group=-1)
        async def wrap_recall(c, m):
            await run_cmd(handle_recall, m)

        @self.client.on_message(filters.command("ls", prefixes=prefixes) & _make_command_filter("ls"), group=-1)
        async def wrap_ls(c, m):
            await run_cmd(handle_ls, m)

        @self.client.on_message(filters.command("read", prefixes=prefixes) & _make_command_filter("read"), group=-1)
        async def wrap_read(c, m):
            await run_cmd(handle_read, m)

        @self.client.on_message(filters.command("write", prefixes=prefixes) & _make_command_filter("write"), group=-1)
        async def wrap_write(c, m):
            await run_cmd(handle_write, m)

        @self.client.on_message(filters.command("agent", prefixes=prefixes) & _make_command_filter("agent"), group=-1)
        async def wrap_agent(c, m):
            await run_cmd(handle_agent, m)

        @self.client.on_message(filters.command("acl", prefixes=prefixes) & _make_command_filter("acl"), group=-1)
        async def wrap_acl(c, m):
            await run_cmd(handle_acl, m)

        @self.client.on_message(filters.command("access", prefixes=prefixes) & _make_command_filter("access"), group=-1)
        async def wrap_access(c, m):
            # Alias для тех, кто интуитивно ищет именно access-management.
            await run_cmd(handle_acl, m)

        @self.client.on_message(
            filters.command("diagnose", prefixes=prefixes) & _make_command_filter("diagnose"), group=-1
        )
        async def wrap_diagnose(c, m):
            await run_cmd(handle_diagnose, m)

        @self.client.on_message(filters.command("help", prefixes=prefixes) & _make_command_filter("help"), group=-1)
        async def wrap_help(c, m):
            await run_cmd(handle_help, m)

        @self.client.on_message(filters.command("remind", prefixes=prefixes) & _make_command_filter("remind"), group=-1)
        async def wrap_remind(c, m):
            await run_cmd(handle_remind, m)

        @self.client.on_message(filters.command("reminders", prefixes=prefixes) & _make_command_filter("reminders"), group=-1)
        async def wrap_reminders(c, m):
            await run_cmd(handle_reminders, m)

        @self.client.on_message(filters.command("rm_remind", prefixes=prefixes) & _make_command_filter("rm_remind"), group=-1)
        async def wrap_rm_remind(c, m):
            await run_cmd(handle_rm_remind, m)

        @self.client.on_message(filters.command("cronstatus", prefixes=prefixes) & _make_command_filter("cronstatus"), group=-1)
        async def wrap_cronstatus(c, m):
            await run_cmd(handle_cronstatus, m)

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

    async def _send_scheduled_message(self, chat_id: str, text: str) -> None:
        """
        Отправляет сообщение из scheduler в Telegram-чат.

        Почему отдельный метод:
        - scheduler должен быть изолирован от деталей Telegram API;
        - здесь централизуем валидацию и безопасную нарезку длинных сообщений.
        """
        if not self.client or not self.client.is_connected:
            raise RuntimeError("telegram_client_not_ready")

        payload = str(text or "").strip()
        if not payload:
            raise ValueError("scheduled_message_empty")

        target_chat: int | str = str(chat_id or "").strip()
        if re.fullmatch(r"-?\d+", str(target_chat)):
            target_chat = int(str(target_chat))

        for part in self._split_message(payload):
            await self.client.send_message(target_chat, part)

    def _sync_scheduler_runtime(self) -> None:
        """
        Синхронизирует состояние scheduler с runtime:
        - при enabled + connected: bind sender и старт;
        - иначе: безопасная остановка.
        """
        scheduler_enabled = bool(getattr(config, "SCHEDULER_ENABLED", False))
        client_connected = bool(self.client and self.client.is_connected)

        if scheduler_enabled and client_connected:
            krab_scheduler.bind_sender(self._send_scheduled_message)
            if not krab_scheduler.is_started:
                krab_scheduler.start()
                logger.info("scheduler_runtime_started")
            return

        if krab_scheduler.is_started:
            krab_scheduler.stop()
            logger.info(
                "scheduler_runtime_stopped",
                scheduler_enabled=scheduler_enabled,
                client_connected=client_connected,
            )

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
        allow_interactive_login = bool(getattr(config, "TELEGRAM_ALLOW_INTERACTIVE_LOGIN", False))
        is_interactive_terminal = bool(getattr(sys.stdin, "isatty", lambda: False)())
        self._restore_primary_session_from_legacy()
        session_snapshot = self._primary_session_snapshot()
        needs_interactive_login = not bool(session_snapshot.get("has_user_binding"))

        if needs_interactive_login and is_interactive_terminal and (not allow_interactive_login):
            self._mark_manual_relogin_required(
                reason="session_invalid_manual_relogin_required",
                error=(
                    "Сессия Telegram не авторизована. "
                    "Запусти telegram_relogin.command для одноразового входа."
                ),
            )
            self._ensure_maintenance_started()
            return

        # В non-interactive запуске запрещаем провоцировать pyrogram на input().
        if not is_interactive_terminal and needs_interactive_login:
            self._mark_manual_relogin_required(
                reason="session_missing_non_interactive",
                error="Telegram session отсутствует или не авторизована, интерактивный вход недоступен",
            )
            self._ensure_maintenance_started()
            return

        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                assert self.client is not None
                # Перед каждой попыткой мягко чистим sqlite lock-артефакты.
                self._cleanup_telegram_session_locks()
                attempt_timeout = max(10, start_timeout_sec)
                if needs_interactive_login and allow_interactive_login:
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
                    logger.warning(
                        "telegram_session_invalid_manual_relogin",
                        error=str(exc),
                        attempt=attempt,
                    )
                    try:
                        await self._safe_stop_client(reason="auth_key_invalid")
                    except Exception as stop_exc:  # noqa: BLE001
                        logger.debug("telegram_stop_after_auth_invalid_failed", error=str(stop_exc))
                    self._mark_manual_relogin_required(
                        reason="auth_key_invalid",
                        error=str(exc),
                    )
                    self._ensure_maintenance_started()
                    return
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
        try:
            self._sync_scheduler_runtime()
        except Exception as exc:  # noqa: BLE001
            logger.warning("scheduler_runtime_sync_failed", error=str(exc))

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
        Контролируемая деградация при невалидной Telegram-сессии:
        - останавливаем текущий клиент;
        - НЕ удаляем session-файл автоматически;
        - переводим runtime в `login_required`.
        """
        if self._session_recovery_lock.locked():
            return
        async with self._session_recovery_lock:
            logger.warning("telegram_session_recovery_started", reason=reason)
            try:
                await self._safe_stop_client(reason="session_recovery")
            except Exception as exc:  # noqa: BLE001
                logger.warning("telegram_session_recovery_stop_failed", error=str(exc))
            self._mark_manual_relogin_required(
                reason="session_recovery_manual_relogin",
                error=str(reason),
            )
            self._ensure_maintenance_started()
            logger.warning(
                "telegram_session_recovery_requires_manual_relogin",
                reason=reason,
            )

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
        if krab_scheduler.is_started:
            try:
                krab_scheduler.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("scheduler_stop_failed", error=str(exc), non_fatal=True)
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

    @staticmethod
    def _normalize_username(value: str) -> str:
        """Нормализует username для сравнений ACL."""
        return str(value or "").strip().lstrip("@").lower()

    def _get_access_profile(self, user: object) -> AccessProfile:
        """Возвращает ACL-профиль отправителя."""
        if not user:
            return AccessProfile(level=AccessLevel.GUEST, source="missing_user", matched_subject="")
        return resolve_access_profile(
            user_id=getattr(user, "id", ""),
            username=getattr(user, "username", ""),
            self_user_id=getattr(self.me, "id", None),
        )

    def _is_allowed_sender(self, user: object) -> bool:
        """
        Проверяет, является ли отправитель доверенным участником owner/full контура.
        """
        return self._get_access_profile(user).is_trusted

    def _has_command_access(self, user: object, command_name: str) -> bool:
        """Проверяет доступ пользователя к конкретной Telegram-команде."""
        access_profile = self._get_access_profile(user)
        return access_profile.can_execute_command(command_name, self._known_commands)

    def _build_runtime_chat_scope_id(
        self,
        *,
        chat_id: str,
        user_id: int,
        is_allowed_sender: bool,
        access_level: str | AccessLevel | None = None,
    ) -> str:
        """
        Возвращает ключ сессии для LLM-контекста.

        Для неавторизованных пользователей включаем изоляцию, чтобы исключить
        смешивание истории с owner-контекстом и риск утечки персональных данных.
        """
        resolved_level = str(access_level.value if isinstance(access_level, AccessLevel) else access_level or "").strip().lower()
        if is_allowed_sender or not bool(getattr(config, "NON_OWNER_SAFE_MODE_ENABLED", True)):
            return str(chat_id)
        isolated_level = resolved_level or AccessLevel.GUEST.value
        return f"{isolated_level}:{chat_id}:{user_id}"

    def _build_system_prompt_for_sender(
        self,
        *,
        is_allowed_sender: bool,
        access_level: str | AccessLevel | None = None,
    ) -> str:
        """
        Возвращает системный промпт в зависимости от доверия к отправителю.
        """
        resolved_level = str(access_level.value if isinstance(access_level, AccessLevel) else access_level or "").strip().lower()
        if is_allowed_sender or not bool(getattr(config, "NON_OWNER_SAFE_MODE_ENABLED", True)):
            base_prompt = get_role_prompt(self.current_role)
            workspace_bundle = load_workspace_prompt_bundle()
            if workspace_bundle:
                base_prompt = (
                    f"{base_prompt}\n\n"
                    "Ниже канонический OpenClaw workspace для внешнего messaging-контура. "
                    "Это источник истины для Краба; придерживайся его, а не устаревших локальных копий.\n\n"
                    f"{workspace_bundle}"
                ).strip()
        elif resolved_level == AccessLevel.PARTIAL.value:
            partial_prompt = str(getattr(config, "PARTIAL_ACCESS_PROMPT", "") or "").strip()
            base_prompt = partial_prompt or str(getattr(config, "NON_OWNER_SAFE_PROMPT", "") or "").strip()
        else:
            safe_prompt = str(getattr(config, "NON_OWNER_SAFE_PROMPT", "") or "").strip()
            if safe_prompt:
                base_prompt = safe_prompt
            else:
                base_prompt = (
                    "Ты — нейтральный автоассистент. Не раскрывай персональные данные владельца "
                    "и внутренние рабочие сведения."
                )
        return self._append_runtime_constraints(base_prompt)

    @staticmethod
    def _append_runtime_constraints(prompt: str) -> str:
        """
        Добавляет runtime-ограничения, которые не должны теряться между ролями.
        """
        base = str(prompt or "").strip()
        if not bool(getattr(config, "SCHEDULER_ENABLED", False)):
            guard = (
                "Важное ограничение runtime: фоновый scheduler/cron сейчас выключен. "
                "Не обещай, что что-то будет выполнено позже автоматически. "
                "Вместо этого честно предлагай выполнить действие сейчас или напомнить пользователю вручную при следующем сообщении."
            )
            if guard not in base:
                base = f"{base}\n\n{guard}".strip()
        return base

    @classmethod
    def _strip_transport_markup(cls, text: str) -> str:
        """
        Удаляет служебные транспортные теги из пользовательского текста.
        Примеры:
        - `[[reply_to:12345]]`
        - `[[reply_to_current]]`
        - `<|im_start|>...<|im_end|>`
        - `<tool_response>{...}</tool_response>`
        - `<think>...</think>` / `<final>...</final>`
        """
        raw = str(text or "")
        if not raw:
            return ""
        cleaned = cls._reply_to_tag_pattern.sub("", raw)
        cleaned = cls._think_block_pattern.sub("", cleaned)
        cleaned = cls._final_block_pattern.sub(lambda match: str(match.group(1) or ""), cleaned)
        cleaned = cls._think_final_tag_pattern.sub("", cleaned)
        cleaned = cls._tool_response_block_pattern.sub("", cleaned)
        cleaned = cls._llm_transport_tokens_pattern.sub("", cleaned)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"(?mi)^\s*(assistant|user|system)\s*$", "", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    @staticmethod
    def _should_force_cloud_for_photo_route(*, has_images: bool) -> bool:
        """
        Жёстко уводит фото userbot в cloud по умолчанию.

        Почему это нужно:
        - пользователь не ждёт, что текстовый Nemotron будет выгружен ради
          случайной маленькой VL-модели;
        - для userbot важнее предсказуемая доставка и язык ответа, чем локальный
          vision-эксперимент с автопереключением.
        Локальный vision остаётся только как явный opt-in через конфиг.
        """
        if not has_images:
            return False
        if not bool(getattr(config, "USERBOT_FORCE_CLOUD_FOR_PHOTO", True)):
            return False
        return True

    @classmethod
    def _apply_deferred_action_guard(cls, text: str) -> str:
        """
        Защищает от ложных обещаний "сделаю позже", когда scheduler выключен.
        """
        raw = str(text or "").strip()
        if not raw:
            return raw
        if bool(getattr(config, "SCHEDULER_ENABLED", False)):
            return raw
        if not bool(getattr(config, "DEFERRED_ACTION_GUARD_ENABLED", True)):
            return raw
        if not cls._deferred_intent_pattern.search(raw):
            return raw
        note = (
            "⚠️ Важно: фоновый cron/таймер сейчас не активен, "
            "поэтому отложенная задача автоматически не запустится."
        )
        if note in raw:
            return raw
        return f"{raw}\n\n{note}"

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
        Разбивает длинный ответ на Telegram-friendly части.

        Почему не обычный `textwrap.wrap`:
        - длинный ответ в Telegram визуально выглядит «оборванным», если следующая
          часть приходит отдельным сообщением без явного маркера;
        - для списков и markdown-ответов важно по возможности сохранять границы строк;
        - нам нужен запас до лимита Telegram (4096), поэтому `limit=4000` сохраняем.
        """
        normalized = str(text or "")
        if len(normalized) <= limit:
            return [normalized]

        # Резерв под префикс вида `[Часть 2/3]`, чтобы не выйти за safe-limit.
        marker_reserve = 48
        body_limit = max(32, limit - marker_reserve)

        chunks: list[str] = []
        current = ""

        def _flush_current() -> None:
            nonlocal current
            if current:
                chunks.append(current)
                current = ""

        for line in normalized.splitlines():
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) <= body_limit:
                current = candidate
                continue

            _flush_current()
            if len(line) <= body_limit:
                current = line
                continue

            # Для сверхдлинной строки режем мягко, не схлопывая пробелы.
            wrapped = textwrap.wrap(
                line,
                width=body_limit,
                replace_whitespace=False,
                drop_whitespace=False,
                break_long_words=True,
                break_on_hyphens=False,
            )
            if not wrapped:
                continue
            chunks.extend(wrapped[:-1])
            current = wrapped[-1]

        _flush_current()

        if len(chunks) <= 1:
            return chunks or [normalized[:limit]]

        total = len(chunks)
        decorated: list[str] = []
        for index, chunk in enumerate(chunks, start=1):
            prefix = f"[Часть {index}/{total}]\n"
            payload = f"{prefix}{chunk}"
            if len(payload) > limit:
                payload = f"{prefix}{chunk[: max(0, limit - len(prefix))]}"
            decorated.append(payload)
        return decorated

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
    def _looks_like_capability_status_question(text: str) -> bool:
        """
        Эвристика: пользователь спрашивает о том, что Краб уже умеет и что пока ограничено.

        Почему отдельный fast-path:
        - модель часто отвечает устаревшим шаблоном с ложными "не умею";
        - такой вопрос лучше отдавать из runtime truth, а не из галлюцинирующего
          narrative-ответа.
        """
        low = str(text or "").strip().lower()
        if not low:
            return False
        patterns = [
            "что ты уме",
            "что уже уме",
            "что ты уже уме",
            "что ты ещё уме",
            "что ты еще уме",
            "что ты не уме",
            "что еще не уме",
            "что ещё не уме",
            "что уже можешь",
            "что можешь",
            "какие у тебя возможности",
            "что умеет краб",
            "что краб умеет",
        ]
        return any(pattern in low for pattern in patterns)

    @staticmethod
    def _looks_like_commands_question(text: str) -> bool:
        """Эвристика: пользователь спрашивает о доступных командах userbot."""
        low = str(text or "").strip().lower()
        if not low:
            return False
        patterns = [
            "какие команды",
            "список команд",
            "что есть из команд",
            "какие у тебя команды",
            "что умеешь по командам",
            "какие у тебя есть команды",
            "что можно через команды",
        ]
        return any(pattern in low for pattern in patterns)

    @staticmethod
    def _looks_like_integrations_question(text: str) -> bool:
        """Эвристика: пользователь спрашивает про инструменты, MCP и интеграции."""
        low = str(text or "").strip().lower()
        if not low:
            return False
        patterns = [
            "какие интеграции",
            "что подключено",
            "какие инструменты",
            "какие сервисы",
            "какие mcp",
            "какие у тебя mcp",
            "какие у тебя интеграции",
            "чем ты подключен",
            "что у тебя подключено",
        ]
        return any(pattern in low for pattern in patterns)

    @staticmethod
    def _looks_like_runtime_truth_question(text: str) -> bool:
        """
        Эвристика: пользователь просит реальный runtime/self-check, а не общую болтовню.

        Почему это отдельный fast-path:
        - такие вопросы особенно вредно отдавать на свободную генерацию;
        - пользователю нужен фактический статус транспорта/модели/интеграций;
        - это экономит токены и снижает ложные claims про cron, браузер и интернет.
        """
        low = str(text or "").strip().lower()
        if not low:
            return False
        # Живой кейс из owner-чата: запросы вида "проведи полную диагностику"
        # раньше не попадали в truthful fast-path и уходили в свободную LLM-
        # генерацию, из-за чего пользователь видел мусор вроде "контекст потерян"
        # вместо реального self-check. Поэтому явно считаем диагностические
        # формулировки runtime-вопросом.
        patterns = [
            "проверка связи",
            "проверь связь",
            "что работает",
            "что у тебя работает",
            "что работает, а что нет",
            "проверь что работает",
            "проверь все",
            "проверь всё",
            "проведи диагностику",
            "полную диагностику",
            "диагностику рантайма",
            "диагностику runtime",
            "runtime self-check",
            "сделай self-check",
            "самопровер",
            "работает ли cron",
            "работает ли крон",
            "cron у тебя уже работает",
            "крон у тебя уже работает",
            "доступ к браузеру",
            "есть ли браузер",
            "можешь использовать браузер",
            "есть ли интернет",
            "доступ к интернету",
        ]
        return any(pattern in low for pattern in patterns)

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

    @staticmethod
    def _resolve_runtime_access_mode(
        *,
        is_allowed_sender: bool,
        access_level: str | AccessLevel | None,
    ) -> str:
        """Нормализует access_level для truthful runtime-summary."""
        if isinstance(access_level, AccessLevel):
            return access_level.value
        normalized = str(access_level or "").strip().lower()
        if normalized in {
            AccessLevel.OWNER.value,
            AccessLevel.FULL.value,
            AccessLevel.PARTIAL.value,
            AccessLevel.GUEST.value,
        }:
            return normalized
        return AccessLevel.FULL.value if is_allowed_sender else AccessLevel.GUEST.value

    def _build_runtime_capability_status(
        self,
        *,
        is_allowed_sender: bool,
        access_level: str | AccessLevel | None = None,
    ) -> str:
        """
        Возвращает детерминированный capability-отчёт по реальному runtime.

        Принципы:
        - не обещаем то, чего реально нет;
        - не отдаём опасные owner-only возможности посторонним чатам;
        - не строим "roadmap", а описываем текущее состояние.
        """
        current_model = str(model_manager.get_current_model() or "").strip()
        route_meta = {}
        if hasattr(openclaw_client, "get_last_runtime_route"):
            try:
                route_meta = openclaw_client.get_last_runtime_route() or {}
            except Exception:
                route_meta = {}

        route_channel = str(route_meta.get("channel", "") or "").strip()
        route_model = str(route_meta.get("model", "") or "").strip()
        active_model = current_model or route_model or str(getattr(config, "LOCAL_PREFERRED_MODEL", "") or "").strip()
        access_mode = self._resolve_runtime_access_mode(
            is_allowed_sender=is_allowed_sender,
            access_level=access_level,
        )

        abilities: list[str] = [
            "- Отвечать на вопросы, объяснять сложные темы, писать тексты и помогать с кодом.",
            f"- Работать локально через LM Studio. Сейчас активная локальная модель: `{active_model or 'не определена'}`.",
            "- Поддерживать контекст диалога в текущей сессии и держать историю разговора.",
            "- Разбирать фото и скриншоты, когда доступен vision-маршрут.",
        ]

        if bool(getattr(config, "SCHEDULER_ENABLED", False)):
            abilities.append("- Ставить напоминания и отложенные задачи через `!remind`, `!reminders`, `!rm_remind`.")
        if access_mode in {AccessLevel.OWNER.value, AccessLevel.FULL.value}:
            abilities.extend(
                [
                    "- Искать информацию в вебе по команде `!search`.",
                    "- Запоминать и вспоминать факты по командам `!remember` и `!recall`.",
                    "- Работать с файлами по путям через `!ls`, `!read`, `!write`.",
                    "- Управлять браузерным/веб-контуром через `!web` и открывать панель через `!panel`.",
                    "- Отправлять голосовой ответ в режиме `!voice`.",
                ]
            )
        elif access_mode == AccessLevel.PARTIAL.value:
            abilities.extend(
                [
                    "- Искать информацию в вебе по команде `!search`.",
                    "- Показывать truthful runtime-статус и безопасные help-команды.",
                    "- Работать в изолированном контуре без owner-only инструментов.",
                ]
            )
        else:
            abilities.extend(
                [
                    "- Давать структурированные ответы в виде списков, планов, кратких инструкций и пояснений.",
                    "- Работать как текстовый ассистент без раскрытия внутренних owner-инструментов.",
                ]
            )

        limitations: list[str] = [
            "- Актуальные данные из интернета подтягиваю не автоматически в каждом ответе, а через явный инструментальный маршрут или команду.",
            "- Не выполняю физические действия в реальном мире, только даю текстовые инструкции и результаты.",
            "- Не запоминаю всю переписку навсегда автоматически: долговременная память у меня точечная и управляется отдельно.",
            "- Качество анализа фото зависит от того, какая модель и какой маршрут сейчас доступны.",
        ]
        if access_mode in {AccessLevel.OWNER.value, AccessLevel.FULL.value}:
            limitations.append(
                "- Голосовой ответ есть, но полноценное понимание входящих голосовых сообщений всё ещё ограничено текущим контуром."
            )
            limitations.append(
                "- Работа с файлами идёт через команды и пути, а не как полностью бесшовная загрузка любых вложений в обычном диалоге."
            )
        elif access_mode == AccessLevel.PARTIAL.value:
            limitations.append(
                "- Частичный доступ не открывает файловый контур, браузерное управление, панель, конфиги и admin-команды."
            )
        else:
            limitations.append(
                "- Системные инструменты вроде файлов, браузера и admin-команд доступны только доверенному контуру владельца."
            )

        route_note = ""
        if route_channel or route_model:
            route_note = (
                "\n\n🧭 **Текущий runtime-статус**\n"
                f"- Канал: `{route_channel or 'unknown'}`\n"
                f"- Модель: `{route_model or active_model or 'unknown'}`"
            )

        return (
            "🦀 **Что я уже умею сейчас**\n"
            + "\n".join(abilities)
            + "\n\n🧩 **Что пока ограничено**\n"
            + "\n".join(limitations)
            + route_note
            + "\n\nЕсли хочешь, я могу отдельно показать список **команд**, **инструментов владельца** или **реальных активных интеграций** в этом runtime."
        )

    def _build_runtime_commands_status(
        self,
        *,
        is_allowed_sender: bool,
        access_level: str | AccessLevel | None = None,
    ) -> str:
        """
        Возвращает truth-summary по доступным Telegram-командам.

        Для гостевого контура не раскрываем owner-only/admin команды.
        """
        access_mode = self._resolve_runtime_access_mode(
            is_allowed_sender=is_allowed_sender,
            access_level=access_level,
        )
        if access_mode == AccessLevel.PARTIAL.value:
            return (
                "🧭 **Команды частичного доступа**\n"
                "- `!status`\n"
                "- `!help`\n"
                "- `!search <запрос>`\n\n"
                "🔒 **Что недоступно в этом контуре**\n"
                "- Управление моделями, памятью, файлами, браузером, панелью и runtime-конфигом.\n"
                "- Owner/full-команды для диагностики, записи файлов и глобальных изменений."
            )
        if access_mode not in {AccessLevel.OWNER.value, AccessLevel.FULL.value}:
            return (
                "🦀 **Что доступно в обычном диалоге**\n"
                "- Свободные текстовые запросы без спецкоманд.\n"
                "- Вопросы, объяснения, помощь с текстом и кодом.\n"
                "- Уточняющие запросы по текущему диалогу.\n\n"
                "🔒 **Что скрыто в этом контуре**\n"
                "- Служебные команды владельца для управления моделями, файлами, вебом и панелью.\n"
                "- Внутренние admin-инструменты и файловый доступ.\n\n"
                "Если нужен именно список owner-команд, его можно показать только в доверенном чате."
            )

        core_commands = [
            "`!status`, `!clear`, `!config`, `!set`, `!restart`, `!help`, `!acl ...`",
        ]
        model_commands = [
            "`!model`, `!model local`, `!model cloud`, `!model auto`, `!model set <model_id>`, `!model load <name>`, `!model unload`, `!model scan`",
        ]
        tool_commands = [
            "`!search <запрос>`, `!remember <текст>`, `!recall <запрос>`, `!inbox [list|status|ack|done|cancel]`, `!role`, `!agent ...`",
        ]
        system_commands = [
            "`!ls [path]`, `!read <path>`, `!write <file> <content>`, `!sysinfo`, `!diagnose`, `!web`, `!panel`, `!voice`",
        ]
        if bool(getattr(config, "SCHEDULER_ENABLED", False)):
            tool_commands.append("`!remind <время> | <текст>`, `!reminders`, `!rm_remind <id>`, `!cronstatus`")

        return (
            "🧭 **Команды, которые реально доступны сейчас**\n"
            "\n**Core**\n- " + "\n- ".join(core_commands)
            + "\n\n**AI / Model**\n- " + "\n- ".join(model_commands)
            + "\n\n**Tools**\n- " + "\n- ".join(tool_commands)
            + "\n\n**System / Dev**\n- " + "\n- ".join(system_commands)
            + "\n\nЕсли хочешь, я могу следующим сообщением показать короткую шпаргалку **по каждой команде с примерами**."
        )

    async def _build_runtime_integrations_status(
        self,
        *,
        is_allowed_sender: bool,
        access_level: str | AccessLevel | None = None,
    ) -> str:
        """
        Возвращает truth-summary по активным интеграциям и инструментам runtime.

        Здесь избегаем ложных обещаний:
        - MCP считаем "configured", если у managed-launch нет missing env;
        - внешние инструменты, требующие owner-доступ, не раскрываем в гостевом контуре.
        """
        local_model = str(model_manager.get_current_model() or "").strip()
        openclaw_ok = await openclaw_client.health_check()
        scheduler_on = bool(getattr(config, "SCHEDULER_ENABLED", False))
        brave_ready = not bool(resolve_managed_server_launch("brave-search").get("missing_env"))
        context7_ready = not bool(resolve_managed_server_launch("context7").get("missing_env"))
        firecrawl_ready = not bool(resolve_managed_server_launch("firecrawl").get("missing_env"))
        browser_ready = not bool(resolve_managed_server_launch("openclaw-browser").get("missing_env"))
        chrome_profile_ready = not bool(resolve_managed_server_launch("chrome-profile").get("missing_env"))
        access_mode = self._resolve_runtime_access_mode(
            is_allowed_sender=is_allowed_sender,
            access_level=access_level,
        )

        public_lines = [
            f"- OpenClaw Gateway: {'ON' if openclaw_ok else 'OFF'}",
            f"- LM Studio local: {'ON' if local_model else 'IDLE'}" + (f" (`{local_model}`)" if local_model else ""),
            f"- Scheduler / reminders: {'ON' if scheduler_on else 'OFF'}",
            "- Голосовой TTS-ответ: ON",
        ]

        if access_mode == AccessLevel.PARTIAL.value:
            return (
                "🔌 **Текущие интеграции Краба**\n"
                + "\n".join(public_lines)
                + f"\n- Web search (Brave): {'configured' if brave_ready else 'missing key'}"
                + "\n- Owner-only MCP, браузерный контроль, файловый доступ и расширенный tool-контур скрыты в этом чате."
            )
        if access_mode not in {AccessLevel.OWNER.value, AccessLevel.FULL.value}:
            return (
                "🔌 **Текущие интеграции Краба**\n"
                + "\n".join(public_lines)
                + "\n- Внешние owner-инструменты и расширенный tool-контур скрыты в этом чате."
            )

        owner_lines = [
            f"- Web search (Brave): {'configured' if brave_ready else 'missing key'}",
            f"- Context7 docs: {'configured' if context7_ready else 'missing key'}",
            f"- Firecrawl: {'configured' if firecrawl_ready else 'missing key / credits'}",
            f"- Browser relay MCP: {'configured' if browser_ready else 'missing config'}",
            f"- Chrome profile DevTools: {'configured' if chrome_profile_ready else 'missing config'}",
            "- Memory engine: ON",
            "- Файловый MCP-контур: ON",
        ]
        return (
            "🔌 **Реальные интеграции и инструменты runtime**\n"
            + "\n".join(public_lines + owner_lines)
            + "\n\nЕсли хочешь, я могу отдельно показать статус в формате **что работает / что требует ключ / что требует баланс**."
        )

    async def _build_runtime_truth_status(
        self,
        *,
        is_allowed_sender: bool,
        access_level: str | AccessLevel | None = None,
    ) -> str:
        """
        Собирает короткий truthful self-check без вызова LLM.

        Это сводка по самым важным для пользователя вещам:
        - отвечает ли транспорт;
        - какой фактический маршрут/модель были последними;
        - включён ли scheduler;
        - что можно утверждать про браузер и интернет без фантазий.
        """
        route_meta = {}
        if hasattr(openclaw_client, "get_last_runtime_route"):
            try:
                route_meta = openclaw_client.get_last_runtime_route() or {}
            except Exception:
                route_meta = {}

        openclaw_ok = await openclaw_client.health_check()
        local_model = str(model_manager.get_current_model() or "").strip()
        route_channel = str(route_meta.get("channel", "") or "").strip()
        route_model = str(route_meta.get("model", "") or "").strip()
        route_provider = str(route_meta.get("provider", "") or "").strip()
        scheduler_on = bool(getattr(config, "SCHEDULER_ENABLED", False))
        scheduler_started = bool(getattr(krab_scheduler, "is_started", False))
        browser_ready = not bool(resolve_managed_server_launch("openclaw-browser").get("missing_env"))
        chrome_profile_ready = not bool(resolve_managed_server_launch("chrome-profile").get("missing_env"))
        brave_ready = not bool(resolve_managed_server_launch("brave-search").get("missing_env"))
        access_mode = self._resolve_runtime_access_mode(
            is_allowed_sender=is_allowed_sender,
            access_level=access_level,
        )

        route_line = (
            f"`{route_channel}`"
            if route_channel
            else "ещё не подтверждён в этом канале (self-check не гоняет LLM-маршрут)"
        )
        model_line = f"`{route_model or local_model}`" if (route_model or local_model) else "ещё не подтверждена"
        primary_hint = ""
        try:
            model_info = self.router.get_model_info() if hasattr(self, "router") and self.router else {}
        except Exception:
            model_info = {}
        if isinstance(model_info, dict):
            primary_hint = str(model_info.get("current_model", "") or "").strip()
        if not primary_hint:
            primary_hint = _current_runtime_primary_model()

        lines: list[str] = [
            "🧭 **Фактический runtime self-check**",
            f"- Gateway / transport: {'ON' if openclaw_ok else 'OFF'}",
            "- Текущий канал: Python Telegram userbot (primary transport)",
            f"- Последний маршрут: {route_line}",
            f"- Последняя модель: {model_line}",
        ]
        if route_provider:
            lines.append(f"- Провайдер: `{route_provider}`")
        if primary_hint and not route_model:
            lines.append(f"- Primary по runtime: `{primary_hint}`")
        if scheduler_on and scheduler_started:
            lines.append("- Scheduler / reminders: включён и подтверждён runtime-стартом")
        elif scheduler_on:
            lines.append("- Scheduler / reminders: включён, но runtime-старт ещё не подтверждён")
        else:
            lines.append("- Scheduler / reminders: выключен")
        lines.append(
            "- Браузерный контур: "
            + (
                "сконфигурирован, но доступ к конкретной вкладке надо подтверждать отдельным действием"
                if browser_ready or chrome_profile_ready
                else "не подтверждён"
            )
        )
        lines.append(
            "- Интернет / веб-поиск: "
            + (
                "доступен через инструментальный маршрут по явному запросу"
                if access_mode in {AccessLevel.OWNER.value, AccessLevel.FULL.value, AccessLevel.PARTIAL.value} and brave_ready
                else "не подтверждается как постоянный фоновой доступ"
            )
        )
        if scheduler_on and scheduler_started and openclaw_ok:
            lines.append("- Cron / heartbeat: scheduler активен, transport живой.")
        elif scheduler_on and scheduler_started:
            lines.append("- Cron / heartbeat: scheduler активен, но transport сейчас не подтверждён.")
        else:
            lines.append("- Cron / heartbeat: без подтверждённого scheduler runtime не считаю их рабочими.")

        return "\n".join(lines)

    @staticmethod
    def _build_command_access_denied_text(command_name: str, access_profile: AccessProfile) -> str:
        """Возвращает понятное сообщение при попытке вызвать недоступную команду."""
        command = str(command_name or "").strip().lower()
        if access_profile.level == AccessLevel.PARTIAL:
            return (
                f"🔒 Команда `!{command}` недоступна в режиме частичного доступа.\n"
                "Сейчас доступны: `!status`, `!help`, `!search <запрос>`.\n"
                "Для расширения прав владелец должен перевести контакт в full-доступ."
            )
        return (
            f"🔒 Команда `!{command}` доступна только доверенному контуру Краба.\n"
            "В обычном диалоге доступны свободные сообщения, а служебные команды скрыты."
        )

    async def _deliver_response_parts(
        self,
        *,
        source_message: Message,
        temp_message: Message,
        is_self: bool,
        query: str,
        full_response: str,
    ) -> None:
        """
        Доставляет готовый ответ в Telegram с безопасным split.

        Почему отдельный helper:
        - capability/status fast-path должен использовать ту же доставку, что и
          обычный AI-ответ;
        - так не дублируем логику split/edit/reply в нескольких ветках.
        """
        parts = self._split_message(
            f"🦀 {query}\n\n{full_response}" if is_self else full_response
        )

        if is_self:
            source_message = await self._safe_edit(source_message, parts[0])
            for part in parts[1:]:
                await source_message.reply(part)
            return

        temp_message = await self._safe_edit(temp_message, parts[0])
        for part in parts[1:]:
            await source_message.reply(part)

    @staticmethod
    def _build_effective_user_query(*, query: str, has_images: bool) -> str:
        """
        Нормализует текст пользовательского запроса перед отправкой в модель.

        Почему отдельный helper:
        - раньше фото без подписи уходило как английское `(Image sent)`;
        - маленькие vision-модели цеплялись за этот placeholder и начинали
          описывать картинку по-английски, игнорируя тон чата;
        - для user-facing канала безопаснее отправить явный русский запрос.
        """
        normalized = str(query or "").strip()
        if normalized:
            return normalized
        if has_images:
            return "Опиши присланное изображение на русском языке."
        return ""

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
            access_profile = self._get_access_profile(user)
            is_allowed_sender = self._is_allowed_sender(user)
            if is_allowed_sender and not access_profile.is_trusted:
                access_profile = AccessProfile(
                    level=AccessLevel.FULL,
                    source="legacy_allowed_sender_override",
                    matched_subject=str(getattr(user, "username", "") or getattr(user, "id", "")),
                )

            text = message.text or message.caption or ""
            has_voice = bool(getattr(message, "voice", None))

            if text and text.lstrip()[:1] in ("!", "/", "."):
                cmd_word = text.lstrip().split()[0].lstrip("!/.").lower()
                if cmd_word in self._known_commands:
                    if not access_profile.can_execute_command(cmd_word, self._known_commands):
                        await message.reply(self._build_command_access_denied_text(cmd_word, access_profile))
                    return

            if not text and not message.photo and not has_voice:
                return

            chat_id = str(message.chat.id)
            runtime_chat_id = self._build_runtime_chat_scope_id(
                chat_id=chat_id,
                user_id=int(user.id),
                is_allowed_sender=is_allowed_sender,
                access_level=access_profile.level,
            )
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

            if self._looks_like_runtime_truth_question(query) or self._looks_like_model_status_question(query):
                runtime_text = await self._build_runtime_truth_status(
                    is_allowed_sender=is_allowed_sender,
                    access_level=access_profile.level,
                )
                runtime_text = self._apply_optional_disclosure(
                    chat_id=chat_id,
                    text=runtime_text,
                )
                await self._deliver_response_parts(
                    source_message=message,
                    temp_message=temp_msg,
                    is_self=is_self,
                    query=query,
                    full_response=runtime_text,
                )
                return

            if self._looks_like_capability_status_question(query):
                capability_text = self._build_runtime_capability_status(
                    is_allowed_sender=is_allowed_sender,
                    access_level=access_profile.level,
                )
                capability_text = self._apply_optional_disclosure(
                    chat_id=chat_id,
                    text=capability_text,
                )
                await self._deliver_response_parts(
                    source_message=message,
                    temp_message=temp_msg,
                    is_self=is_self,
                    query=query,
                    full_response=capability_text,
                )
                return

            if self._looks_like_commands_question(query):
                commands_text = self._build_runtime_commands_status(
                    is_allowed_sender=is_allowed_sender,
                    access_level=access_profile.level,
                )
                commands_text = self._apply_optional_disclosure(
                    chat_id=chat_id,
                    text=commands_text,
                )
                await self._deliver_response_parts(
                    source_message=message,
                    temp_message=temp_msg,
                    is_self=is_self,
                    query=query,
                    full_response=commands_text,
                )
                return

            if self._looks_like_integrations_question(query):
                integrations_text = await self._build_runtime_integrations_status(
                    is_allowed_sender=is_allowed_sender,
                    access_level=access_profile.level,
                )
                integrations_text = self._apply_optional_disclosure(
                    chat_id=chat_id,
                    text=integrations_text,
                )
                await self._deliver_response_parts(
                    source_message=message,
                    temp_message=temp_msg,
                    is_self=is_self,
                    query=query,
                    full_response=integrations_text,
                )
                return

            # VISION: Обработка фото
            images = []
            photo_error = ""
            if message.photo:
                try:
                    if is_self:
                        message = await self._safe_edit(message, f"🦀 {query}\n\n👀 *Разглядываю фото...*")
                    else:
                        temp_msg = await self._safe_edit(temp_msg, "👀 *Разглядываю фото...*")

                    # Защита от зависания media-path: ограничиваем download timeout.
                    photo_timeout_sec = float(getattr(config, "PHOTO_DOWNLOAD_TIMEOUT_SEC", 40.0))
                    photo_obj = await asyncio.wait_for(
                        self.client.download_media(message, in_memory=True),
                        timeout=max(5.0, photo_timeout_sec),
                    )
                    if photo_obj:
                        img_bytes = photo_obj.getvalue()
                        b64_img = base64.b64encode(img_bytes).decode("utf-8")
                        images.append(b64_img)
                    else:
                        photo_error = "❌ Не удалось прочитать фото. Отправь изображение повторно."
                except asyncio.TimeoutError:
                    photo_error = "❌ Таймаут загрузки фото. Повтори отправку изображения."
                    logger.error(
                        "photo_processing_timeout",
                        chat_id=chat_id,
                        timeout_sec=float(getattr(config, "PHOTO_DOWNLOAD_TIMEOUT_SEC", 40.0)),
                    )
                except Exception as e:
                    logger.error("photo_processing_error", error=str(e))
                    photo_error = "❌ Ошибка обработки фото. Попробуй отправить его ещё раз."

            # Для фото-пути не продолжаем в AI-stream без успешно загруженного изображения:
            # это исключает зависание на «Разглядываю фото...» и пустые/необъяснимые ответы.
            if message.photo and not images:
                safe_query = (query or "(Фото)").strip()
                safe_error = photo_error or "❌ Фото не удалось обработать. Отправь изображение повторно."
                if is_self:
                    message = await self._safe_edit(message, f"🦀 {safe_query}\n\n{safe_error}")
                else:
                    temp_msg = await self._safe_edit(temp_msg, safe_error)
                return

            full_response = ""
            full_response_raw = ""
            last_edit_time = 0

            system_prompt = self._build_system_prompt_for_sender(
                is_allowed_sender=is_allowed_sender,
                access_level=access_profile.level,
            )

            # CONTEXT: Добавляем контекст чата для групп
            if is_allowed_sender and message.chat.type != enums.ChatType.PRIVATE:
                context = await self._get_chat_context(message.chat.id)
                if context:
                    system_prompt += f"\n\n[CONTEXT OF LAST MESSAGES]\n{context}\n[END CONTEXT]\n\nReply to the user request taking into account the context above."

            first_chunk_timeout_sec, chunk_timeout_sec = _resolve_openclaw_stream_timeouts(
                has_photo=bool(images)
            )
            max_output_tokens = int(
                getattr(
                    config,
                    "USERBOT_PHOTO_MAX_OUTPUT_TOKENS" if images else "USERBOT_MAX_OUTPUT_TOKENS",
                    0,
                )
                or 0
            )
            effective_query = self._build_effective_user_query(
                query=query,
                has_images=bool(images),
            )
            force_cloud = bool(getattr(config, "FORCE_CLOUD", False))
            if self._should_force_cloud_for_photo_route(has_images=bool(images)):
                logger.info(
                    "userbot_photo_route_forced_to_cloud",
                    chat_id=chat_id,
                    preferred_vision=str(getattr(config, "LOCAL_PREFERRED_VISION_MODEL", "") or ""),
                )
                force_cloud = True

            stream = openclaw_client.send_message_stream(
                message=effective_query,
                chat_id=runtime_chat_id,
                system_prompt=system_prompt,
                images=images,
                force_cloud=force_cloud,
                max_output_tokens=max_output_tokens if max_output_tokens > 0 else None,
            )
            stream_iter = stream.__aiter__()
            received_any_chunk = False

            while True:
                wait_timeout = chunk_timeout_sec if received_any_chunk else first_chunk_timeout_sec
                try:
                    chunk = await asyncio.wait_for(
                        stream_iter.__anext__(),
                        timeout=wait_timeout,
                    )
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    logger.error(
                        "openclaw_stream_chunk_timeout",
                        chat_id=chat_id,
                        timeout_sec=wait_timeout,
                        first_chunk=not received_any_chunk,
                        has_photo=bool(images),
                    )
                    full_response = (
                        "❌ Модель отвечает слишком долго. Попробуй ещё раз или переключись на `!model cloud` / `!model local`."
                    )
                    try:
                        await stream.aclose()
                    except Exception:
                        pass
                    break

                full_response_raw += chunk
                received_any_chunk = True
                full_response = (
                    self._strip_transport_markup(full_response_raw)
                    if bool(getattr(config, "STRIP_REPLY_TO_TAGS", True))
                    else full_response_raw
                )

                if time.time() - last_edit_time > 1.5:
                    last_edit_time = time.time()
                    try:
                        display = (full_response or "…") + " ▌"
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

            if bool(getattr(config, "STRIP_REPLY_TO_TAGS", True)):
                full_response = self._strip_transport_markup(full_response)
                if not full_response:
                    full_response = "❌ Модель вернула пустой ответ. Попробуй повторить запрос."
            full_response = self._apply_deferred_action_guard(full_response)

            # Если пользователь спрашивает именно о модели, отвечаем по фактическому маршруту,
            # а не доверяем декларативному тексту самой LLM.
            if is_allowed_sender and self._looks_like_model_status_question(query):
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

            await self._deliver_response_parts(
                source_message=message,
                temp_message=temp_msg,
                is_self=is_self,
                query=query,
                full_response=full_response,
            )

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
