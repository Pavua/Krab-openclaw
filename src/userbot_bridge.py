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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pyrogram import Client, enums, filters
from pyrogram.types import Message

from .config import config
from .core.access_control import (
    AccessLevel,
    AccessProfile,
    OWNER_ONLY_COMMANDS,
    USERBOT_KNOWN_COMMANDS,
    resolve_access_profile,
)
from .core.capability_registry import resolve_access_mode
from .core.exceptions import KrabError, UserInputError
from .core.inbox_service import inbox_service
from .core.logger import get_logger
from .core.mcp_registry import resolve_managed_server_launch
from .core.proactive_watch import proactive_watch
from .core.openclaw_workspace import load_workspace_prompt_bundle
from .core.openclaw_runtime_models import get_runtime_primary_model
from .core.routing_errors import RouterError, user_message_for_surface
from .core.scheduler import krab_scheduler
from .core.translator_runtime_profile import (
    default_translator_runtime_profile,
    load_translator_runtime_profile,
    normalize_translator_runtime_profile,
    save_translator_runtime_profile,
)
from .core.translator_session_state import (
    apply_translator_session_update,
    default_translator_session_state,
    load_translator_session_state,
    save_translator_session_state,
)
from .employee_templates import ROLES, get_role_prompt
from .integrations.macos_automation import macos_automation
from .handlers import (
    handle_agent,
    handle_acl,
    handle_browser,
    handle_claude_cli,
    handle_clear,
    handle_codex,
    handle_gemini_cli,
    handle_hs,
    handle_opencode,
    handle_config,
    handle_cronstatus,
    handle_diagnose,
    handle_help,
    handle_inbox,
    handle_ls,
    handle_macos,
    handle_memory,
    handle_model,
    handle_panel,
    handle_read,
    handle_reasoning,
    handle_recall,
    handle_remind,
    handle_reminders,
    handle_remember,
    handle_restart,
    handle_role,
    handle_rm_remind,
    handle_search,
    handle_set,
    handle_shop,
    handle_status,
    handle_swarm,
    handle_sysinfo,
    handle_translator,
    handle_voice,
    handle_watch,
    handle_web,
    handle_write,
)
from .model_manager import model_manager
from .openclaw_client import openclaw_client
from .search_engine import close_search
from .voice_engine import text_to_speech

logger = get_logger(__name__)


_RELAY_INTENT_KEYWORDS: frozenset[str] = frozenset({
    "передай", "передайте", "передать", "перешли", "переслать",
    "скажи", "скажите", "сообщи", "сообщите",
    "расскажи", "расскажите",
    "передайте ему", "передай ему",
    "let know", "tell him", "tell her", "notify",
    "pass along", "pass it on",
})


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
    default_first = 720.0 if has_photo else 600.0
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


def _resolve_openclaw_buffered_response_timeout(
    *,
    has_photo: bool,
    first_chunk_timeout_sec: float,
) -> float:
    """
    Возвращает верхнюю границу ожидания buffered-ответа OpenClaw.

    Почему нужен отдельный hard-timeout:
    - в текущем контуре `send_message_stream()` буферизует `stream=False` ответ,
      поэтому первый Telegram chunk приходит только после полного completion;
    - soft-timeout первого чанка полезен как сигнал "ответ идёт слишком долго",
      но не должен рубить ещё живую fallback-цепочку OpenClaw раньше gateway timeout;
    - даём разумный запас сверх первого ожидания, чтобы не зависать бесконечно.
    """
    default_total_timeout_sec = 1020.0 if has_photo else 900.0
    return max(default_total_timeout_sec, float(first_chunk_timeout_sec or 0.0) + 60.0)


def _resolve_openclaw_progress_notice_schedule(
    *,
    has_photo: bool,
    first_chunk_timeout_sec: float,
) -> tuple[float, float]:
    """
    Возвращает (initial_sec, repeat_sec) для ранних тех-уведомлений userbot.

    Почему это вынесено отдельно:
    - hard/soft-timeout отвечают за устойчивость транспорта;
    - progress-notice отвечает за UX ожидания и не должен зависеть от 7-минутного окна.
    """
    if has_photo:
        initial_key = "OPENCLAW_PHOTO_PROGRESS_NOTICE_INITIAL_SEC"
        repeat_key = "OPENCLAW_PHOTO_PROGRESS_NOTICE_REPEAT_SEC"
        default_initial_sec = 30.0
        default_repeat_sec = 60.0
    else:
        initial_key = "OPENCLAW_PROGRESS_NOTICE_INITIAL_SEC"
        repeat_key = "OPENCLAW_PROGRESS_NOTICE_REPEAT_SEC"
        default_initial_sec = 20.0
        default_repeat_sec = 45.0
    initial_sec = float(getattr(config, initial_key, default_initial_sec))
    repeat_sec = float(getattr(config, repeat_key, default_repeat_sec))
    initial_sec = max(5.0, min(float(first_chunk_timeout_sec or 0.0), initial_sec))
    repeat_sec = max(15.0, repeat_sec)
    return initial_sec, repeat_sec


def _build_openclaw_progress_wait_notice(
    *,
    route_model: str,
    attempt: int | None,
    elapsed_sec: float,
    notice_index: int,
    tool_calls_summary: str = "",
) -> str:
    """
    Формирует раннее тех-уведомление о том, что buffered-запрос всё ещё жив.

    Текст честный: показывает текущий инструмент и стадию работы.
    Эмодзи выбирается по типу активного инструмента для быстрого визуального понимания.
    """
    route_line = _build_openclaw_route_notice_line(
        route_model=route_model,
        attempt=attempt,
    )
    elapsed_f = float(elapsed_sec or 0.0)
    # Форматируем время: секунды до 60 сек, потом — минуты
    if elapsed_f < 60:
        elapsed_label = f"~{max(1, int(round(elapsed_f)))} сек"
    else:
        mins = int(elapsed_f // 60)
        secs = int(elapsed_f % 60)
        elapsed_label = f"~{mins} мин {secs:02d} сек"

    # Определяем эмодзи и описание по типу активного инструмента
    TOOL_EMOJIS: dict[str, str] = {
        "search": "🔍",
        "web": "🌐",
        "browser": "🌐",
        "file": "📁",
        "read": "📖",
        "write": "✏️",
        "code": "💻",
        "python": "🐍",
        "bash": "⚙️",
        "shell": "⚙️",
        "memory": "🧠",
        "recall": "🧠",
        "screenshot": "📸",
        "vision": "👁️",
        "telegram": "📱",
        "mcp": "🔌",
        "api": "🔗",
        "fetch": "📡",
        "http": "📡",
        "think": "💭",
        "reason": "💭",
        "plan": "📋",
    }

    tool_emoji = "🛠️"
    tool_name_display = ""
    if tool_calls_summary:
        summary_lower = tool_calls_summary.lower()
        for key, emoji in TOOL_EMOJIS.items():
            if key in summary_lower:
                tool_emoji = emoji
                break
        # Пытаемся извлечь имя инструмента из summary
        import re
        m = re.search(r"(?:выполняется|вызываю|running)[:\s]+([^\n,;]{1,40})", tool_calls_summary, re.I)
        if m:
            tool_name_display = m.group(1).strip()

    if tool_calls_summary and "🔧 Выполняется:" in tool_calls_summary:
        if tool_name_display:
            lead = f"{tool_emoji} Использую инструмент: **{tool_name_display}**"
        else:
            lead = f"{tool_emoji} Вызов инструмента — жду результат."
    elif tool_calls_summary:
        lead = f"✅ Инструменты отработали — {tool_emoji} собираю итоговый ответ."
    elif notice_index <= 1:
        lead = "🧩 Запрос принят, собираю контекст и жду первый ответ модели."
    else:
        lead = f"⏳ Запрос всё ещё в работе ({elapsed_label}). Маршрут жив, не дублируй."

    result = (
        f"{lead}\n"
        f"⏱ Прошло: {elapsed_label}" + route_line
    )
    if tool_calls_summary:
        result += f"\n\n{tool_calls_summary}"
    return result


def _build_openclaw_slow_wait_notice(*, route_model: str, attempt: int | None) -> str:
    """
    Формирует честное уведомление о долгом buffered-ожидании.

    Сообщение намеренно объясняет, что запрос ещё жив, а userbot не завис навсегда.
    """
    route_line = _build_openclaw_route_notice_line(
        route_model=route_model,
        attempt=attempt,
    )
    return (
        "⏳ Ответ собирается дольше обычного. Продолжаю ждать fallback-цепочку OpenClaw,"
        " не дублируй сообщение." + route_line
    )


def _build_openclaw_route_notice_line(*, route_model: str, attempt: int | None) -> str:
    """
    Формирует truthful-строку о текущем маршруте buffered-запроса.

    Почему это отдельно:
    - Telegram notice должен показывать не только стартовую модель, но и
      фактическую текущую попытку fallback-цепочки;
    - одна и та же логика нужна и для ранних progress-notice, и для slow-wait notice.
    """
    normalized_model = str(route_model or "").strip()
    normalized_attempt = int(attempt or 0) or None
    parts: list[str] = []
    if normalized_model:
        parts.append(f"Текущий маршрут: `{normalized_model}`")
    if normalized_attempt:
        parts.append(f"попытка `{normalized_attempt}`")
        if normalized_attempt > 1:
            parts.append("fallback активен")
    if not parts:
        return ""
    return "\n" + " · ".join(parts) + "."


def _message_unix_ts(message: Message | Any) -> float | None:
    """
    Возвращает unix-timestamp сообщения, если дата доступна.

    Helper живёт на module-level, чтобы его было удобно использовать и в runtime,
    и в unit-тестах без тяжёлой инициализации класса.
    """
    value = getattr(message, "date", None)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return float(value.timestamp())
    return None


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
    _think_capture_pattern = re.compile(r"(?is)<think>(.*?)</think>")
    _final_block_pattern = re.compile(r"(?is)<final>(.*?)</final>")
    _think_final_tag_pattern = re.compile(r"(?i)</?(?:think|final)>")
    _plaintext_reasoning_intro_pattern = re.compile(
        r"(?i)^(?:think|thinking|thinking process|reasoning|analysis)\s*:?\s*$"
    )
    _plaintext_reasoning_step_pattern = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+")
    _plaintext_reasoning_meta_pattern = re.compile(
        r"(?i)^(?:step\s*\d+|thinking process|analysis|reasoning|analyze(?: the)? user(?:'s)? request|draft the response)\b"
    )
    _voice_delivery_modes = {"text+voice", "voice-only"}
    _deferred_intent_pattern = re.compile(
        r"(?is)\b(напомню|сделаю|выполню|запланирую|отправлю)\b.{0,80}\b(позже|через|завтра|утром|вечером|по таймеру|по расписанию)\b"
    )

    def __init__(self, *, perceptor: object | None = None):
        """Инициализация юзербота и клиента Pyrogram"""
        self.client: Client | None = None
        self.me = None
        self.current_role = "default"
        self.voice_mode = bool(getattr(config, "VOICE_MODE_DEFAULT", False))
        self.voice_reply_speed = self._normalize_voice_reply_speed(
            getattr(config, "VOICE_REPLY_SPEED", 1.5)
        )
        self.voice_reply_voice = self._normalize_voice_reply_voice(
            getattr(config, "VOICE_REPLY_VOICE", "ru-RU-DmitryNeural")
        )
        self.voice_reply_delivery = self._normalize_voice_reply_delivery(
            getattr(config, "VOICE_REPLY_DELIVERY", "text+voice")
        )
        self.perceptor = perceptor
        self.maintenance_task: Optional[asyncio.Task] = None
        self._telegram_watchdog_task: Optional[asyncio.Task] = None
        self._proactive_watch_task: Optional[asyncio.Task] = None
        self._session_recovery_lock = asyncio.Lock()
        self._client_lifecycle_lock = asyncio.Lock()
        self._chat_processing_locks: dict[str, asyncio.Lock] = {}
        self._chat_background_tasks: dict[str, asyncio.Task] = {}
        self._batched_followup_message_ids: dict[str, dict[str, float]] = {}
        self._hidden_reasoning_traces: dict[str, dict[str, Any]] = {}
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

        self._known_commands = set(USERBOT_KNOWN_COMMANDS)

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

        @self.client.on_message(filters.command("swarm", prefixes=prefixes) & _make_command_filter("swarm"), group=-1)
        async def wrap_swarm(c, m):
            await run_cmd(handle_swarm, m)

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

        @self.client.on_message(filters.command("translator", prefixes=prefixes) & _make_command_filter("translator"), group=-1)
        async def wrap_translator(c, m):
            await run_cmd(handle_translator, m)

        @self.client.on_message(filters.command("web", prefixes=prefixes) & _make_command_filter("web"), group=-1)
        async def wrap_web(c, m):
            await run_cmd(handle_web, m)

        @self.client.on_message(filters.command("mac", prefixes=prefixes) & _make_command_filter("mac"), group=-1)
        async def wrap_mac(c, m):
            await run_cmd(handle_macos, m)

        @self.client.on_message(filters.command("watch", prefixes=prefixes) & _make_command_filter("watch"), group=-1)
        async def wrap_watch(c, m):
            await run_cmd(handle_watch, m)

        @self.client.on_message(filters.command("memory", prefixes=prefixes) & _make_command_filter("memory"), group=-1)
        async def wrap_memory(c, m):
            await run_cmd(handle_memory, m)

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

        @self.client.on_message(filters.command("shop", prefixes=prefixes) & _make_command_filter("shop"), group=-1)
        async def wrap_shop(c, m):
            await run_cmd(handle_shop, m)

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

        # CLI runner команды
        @self.client.on_message(filters.command("codex", prefixes=prefixes) & _make_command_filter("codex"), group=-1)
        async def wrap_codex(c, m):
            await run_cmd(handle_codex, m)

        @self.client.on_message(filters.command("gemini", prefixes=prefixes) & _make_command_filter("gemini"), group=-1)
        async def wrap_gemini_cli(c, m):
            await run_cmd(handle_gemini_cli, m)

        @self.client.on_message(
            filters.command("claude_cli", prefixes=prefixes) & _make_command_filter("claude_cli"), group=-1
        )
        async def wrap_claude_cli(c, m):
            await run_cmd(handle_claude_cli, m)

        @self.client.on_message(
            filters.command("opencode", prefixes=prefixes) & _make_command_filter("opencode"), group=-1
        )
        async def wrap_opencode(c, m):
            await run_cmd(handle_opencode, m)

        @self.client.on_message(
            filters.command("hs", prefixes=prefixes) & _make_command_filter("hs"), group=-1
        )
        async def wrap_hs(c, m):
            await run_cmd(handle_hs, m)

        @self.client.on_message(filters.command("acl", prefixes=prefixes) & _make_command_filter("acl"), group=-1)
        async def wrap_acl(c, m):
            await run_cmd(handle_acl, m)

        @self.client.on_message(filters.command("access", prefixes=prefixes) & _make_command_filter("access"), group=-1)
        async def wrap_access(c, m):
            # Alias для тех, кто интуитивно ищет именно access-management.
            await run_cmd(handle_acl, m)

        @self.client.on_message(
            filters.command("reasoning", prefixes=prefixes) & _make_command_filter("reasoning"),
            group=-1,
        )
        async def wrap_reasoning(c, m):
            await run_cmd(handle_reasoning, m)

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

        @self.client.on_message(filters.command("browser", prefixes=prefixes) & _make_command_filter("browser"), group=-1)
        async def wrap_browser(c, m):
            await run_cmd(handle_browser, m)

        # Обработка обычных сообщений, медиа, голосовых и документов.
        # Voice/audio проходят в _process_message → _transcribe_audio_message
        # (устаревший wrap_audio с stop_propagation() удалён — он блокировал AI pipeline).
        @self.client.on_message((filters.text | filters.photo | filters.voice | filters.audio | filters.document) & ~filters.bot, group=0)
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

    async def _auto_export_handoff_snapshot(self, *, reason: str) -> dict[str, Any]:
        """
        Auto-export handoff snapshot before shutdown or session change (Phase 2.2).
        
        Never raises — if export fails, logs warning and returns exported=False.
        """
        import urllib.request
        from datetime import datetime, timezone
        
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        artifacts_dir = config.BASE_DIR / "artifacts"
        dest = artifacts_dir / f"auto_handoff_{timestamp}.json"
        
        try:
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            handoff_url = "http://127.0.0.1:8080/api/runtime/handoff"
            req = urllib.request.Request(handoff_url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                raw = resp.read()
            data = json.loads(raw)
            dest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            logger.info(
                "auto_handoff_export_success",
                reason=reason,
                path=str(dest),
                size_bytes=len(raw),
            )
            return {"exported": True, "path": str(dest), "error": None, "reason": reason}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "auto_handoff_export_failed",
                reason=reason,
                error=str(exc),
                non_fatal=True,
            )
            return {"exported": False, "path": str(dest), "error": str(exc), "reason": reason}

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

    async def _send_proactive_watch_alert(self, text: str) -> None:
        """
        Отправляет watch-alert в Saved Messages владельца.

        Это даёт владельцу фоновую проактивность без шума в рабочих чатах.
        """
        if not self.client or not self.client.is_connected:
            raise RuntimeError("telegram_client_not_ready")
        for part in self._split_message(str(text or "").strip()):
            await self.client.send_message("me", part)

    def _ensure_proactive_watch_started(self) -> None:
        """Запускает фоновый proactive watch, если он включён конфигом."""
        if not bool(getattr(config, "PROACTIVE_WATCH_ENABLED", False)):
            return
        if self._proactive_watch_task and not self._proactive_watch_task.done():
            return
        self._proactive_watch_task = asyncio.create_task(self._run_proactive_watch_loop())

    async def _run_proactive_watch_loop(self) -> None:
        """
        Периодически снимает owner-oriented runtime digest.

        Первый проход строит baseline без alert.
        Следующие проходы сообщают только про реальные переходы состояния.
        """
        interval_sec = max(60, int(getattr(config, "PROACTIVE_WATCH_INTERVAL_SEC", 900) or 900))
        baseline_ready = False
        try:
            while True:
                if self.client and self.client.is_connected:
                    # Даём runtime прогреться: scheduler, wake-up и route warmup должны
                    # успеть стабилизироваться до baseline, иначе первый snapshot
                    # получается правдивым, но слишком "холодным" и мало полезным.
                    if not baseline_ready:
                        await asyncio.sleep(min(20, max(5, interval_sec // 6)))
                    result = await proactive_watch.capture(
                        manual=False,
                        persist_memory=True,
                        notify=baseline_ready,
                        notifier=self._send_proactive_watch_alert,
                    )
                    baseline_ready = True
                    if result.get("reason"):
                        logger.info(
                            "proactive_watch_transition_captured",
                            reason=result.get("reason"),
                            alerted=bool(result.get("alerted")),
                            wrote_memory=bool(result.get("wrote_memory")),
                        )
                await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            logger.info("proactive_watch_task_cancelled")
        except Exception as exc:  # noqa: BLE001
            logger.warning("proactive_watch_loop_failed", error=str(exc), non_fatal=True)

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
            "voice_profile": self.get_voice_runtime_profile(),
            "translator_profile": self.get_translator_runtime_profile(),
            "translator_session": self.get_translator_session_state(),
        }

    @classmethod
    def _normalize_voice_reply_speed(cls, value: Any) -> float:
        """
        Нормализует коэффициент скорости TTS.

        Почему clamp здесь:
        - команда `!voice speed` не должна ломать TTS мусорным значением;
        - сохраняем предсказуемый диапазон и для runtime, и для .env.
        """
        del cls
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = 1.5
        return max(0.75, min(2.5, round(numeric, 2)))

    @classmethod
    def _normalize_voice_reply_voice(cls, value: Any) -> str:
        """Возвращает непустой voice-id для edge-tts."""
        del cls
        normalized = str(value or "").strip()
        return normalized or "ru-RU-DmitryNeural"

    @classmethod
    def _normalize_voice_reply_delivery(cls, value: Any) -> str:
        """Нормализует режим доставки voice-ответа."""
        normalized = str(value or "").strip().lower()
        if normalized in cls._voice_delivery_modes:
            return normalized
        return "text+voice"

    def get_voice_runtime_profile(self) -> dict[str, Any]:
        """
        Возвращает живой профиль voice-runtime userbot.

        Это source-of-truth для команд, web API и handoff:
        - включена ли озвучка ответов;
        - какой голос/скорость/режим доставки активны;
        - готов ли входящий voice ingress через perceptor.
        """
        perceptor = getattr(self, "perceptor", None)
        perceptor_ready = bool(perceptor) and hasattr(perceptor, "transcribe")
        return {
            "enabled": bool(getattr(self, "voice_mode", False)),
            "delivery": self._normalize_voice_reply_delivery(
                getattr(self, "voice_reply_delivery", "text+voice")
            ),
            "speed": self._normalize_voice_reply_speed(
                getattr(self, "voice_reply_speed", 1.5)
            ),
            "voice": self._normalize_voice_reply_voice(
                getattr(self, "voice_reply_voice", "ru-RU-DmitryNeural")
            ),
            "input_transcription_ready": perceptor_ready,
            "output_tts_ready": True,
            "live_voice_foundation": bool(perceptor_ready),
        }

    def update_voice_runtime_profile(
        self,
        *,
        enabled: Any | None = None,
        speed: Any | None = None,
        voice: Any | None = None,
        delivery: Any | None = None,
        persist: bool = False,
    ) -> dict[str, Any]:
        """
        Обновляет voice-профиль userbot и при необходимости сохраняет его в `.env`.

        Держим это в runtime-классе, а не в command handler:
        - web API и Telegram команды используют одну и ту же логику;
        - handoff/runtime-status не расходятся с фактическим поведением доставки.
        """
        if enabled is not None:
            self.voice_mode = bool(enabled)
            if persist:
                config.update_setting("VOICE_MODE_DEFAULT", "1" if self.voice_mode else "0")
        if speed is not None:
            self.voice_reply_speed = self._normalize_voice_reply_speed(speed)
            if persist:
                config.update_setting("VOICE_REPLY_SPEED", str(self.voice_reply_speed))
        if voice is not None:
            self.voice_reply_voice = self._normalize_voice_reply_voice(voice)
            if persist:
                config.update_setting("VOICE_REPLY_VOICE", self.voice_reply_voice)
        if delivery is not None:
            self.voice_reply_delivery = self._normalize_voice_reply_delivery(delivery)
            if persist:
                config.update_setting("VOICE_REPLY_DELIVERY", self.voice_reply_delivery)
        return self.get_voice_runtime_profile()

    @classmethod
    def _repo_root(cls) -> Path:
        """
        Возвращает корень текущего репозитория Краба.

        Нужен единый helper, чтобы userbot, web API и тесты ссылались на один и тот же
        repo-level persisted translator profile, а не расходились по рабочим каталогам.
        """
        del cls
        return Path(__file__).resolve().parent.parent

    @classmethod
    def _translator_runtime_profile_path(cls) -> Path:
        """Возвращает repo-level путь persisted translator runtime profile."""
        return cls._repo_root() / "data" / "translator" / "runtime_profile.json"

    def get_translator_runtime_profile(self) -> dict[str, Any]:
        """
        Возвращает persisted translator runtime profile с короткой runtime truth-добавкой.

        Это не live session-state переводчика звонков. Здесь лежит именно product/runtime
        профиль owner-уровня, который используется командами, web-панелью и handoff.
        """
        profile = load_translator_runtime_profile(self._translator_runtime_profile_path())
        voice_profile = self.get_voice_runtime_profile()
        result = dict(profile)
        result["quick_phrase_count"] = len(profile.get("quick_phrases") or [])
        result["voice_foundation_ready"] = bool(voice_profile.get("live_voice_foundation"))
        result["voice_runtime_enabled"] = bool(voice_profile.get("enabled"))
        return result

    @classmethod
    def _translator_session_state_path(cls) -> Path:
        """Возвращает repo-level путь persisted translator session state."""
        return cls._repo_root() / "data" / "translator" / "session_state.json"

    def get_translator_session_state(self) -> dict[str, Any]:
        """
        Возвращает persisted translator session state с короткой runtime truth-добавкой.

        Это product-level control state, а не финальный source-of-truth live звонка.
        Но именно он нужен owner-командам и UI до подключения полноценного session feed.
        """
        state = load_translator_session_state(self._translator_session_state_path())
        profile = self.get_translator_runtime_profile()
        result = dict(state)
        result["language_pair"] = str(profile.get("language_pair") or "")
        result["target_device"] = str(profile.get("target_device") or "iphone_companion")
        return result

    def update_translator_runtime_profile(
        self,
        *,
        persist: bool = True,
        **changes: Any,
    ) -> dict[str, Any]:
        """
        Обновляет persisted translator runtime profile и возвращает нормализованный срез.

        Почему логика здесь:
        - Telegram-команда `!translator` и owner web UI должны опираться на одну модель данных;
        - тесты и runtime-status не должны зависеть от разнородной ad-hoc сериализации.
        """
        path = self._translator_runtime_profile_path()
        current = load_translator_runtime_profile(path)
        normalized = normalize_translator_runtime_profile(changes, base=current)
        if persist:
            save_translator_runtime_profile(path, normalized)
        return self.get_translator_runtime_profile() if persist else normalized

    def update_translator_session_state(
        self,
        *,
        persist: bool = True,
        **changes: Any,
    ) -> dict[str, Any]:
        """
        Обновляет persisted translator session state и возвращает нормализованный срез.

        Почему логика здесь:
        - web UI, userbot-команды и handoff должны видеть один session-control слой;
        - до live feed Voice Gateway нам нужен честный persisted placeholder, а не ad-hoc state в памяти процесса.
        """
        path = self._translator_session_state_path()
        current = load_translator_session_state(path)
        normalized = apply_translator_session_update(changes, base=current)
        if persist:
            save_translator_session_state(path, normalized)
        return self.get_translator_session_state() if persist else normalized

    def reset_translator_session_state(self, *, persist: bool = True) -> dict[str, Any]:
        """Сбрасывает translator session state к каноническому idle-срезу."""
        state = default_translator_session_state()
        if persist:
            save_translator_session_state(self._translator_session_state_path(), state)
            return self.get_translator_session_state()
        return state

    def _should_send_voice_reply(self) -> bool:
        """Определяет, нужно ли вообще генерировать TTS для текущего ответа."""
        return bool(self.voice_mode)

    def _should_send_full_text_reply(self) -> bool:
        """
        Определяет, нужен ли полный текстовый дубль вместе с voice.

        `voice-only` полезен для будущего live-режима и для чатов, где длинные
        текстовые полотна мешают. По умолчанию остаёмся в безопасном `text+voice`.
        """
        if not self._should_send_voice_reply():
            return True
        return self._normalize_voice_reply_delivery(self.voice_reply_delivery) != "voice-only"

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
        self._ensure_proactive_watch_started()

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
        """Безопасный запуск maintenance с периодическим handoff export (Phase 2.2)"""
        try:
            logger.info("maintenance_task_start")
            
            # Периодический handoff export (каждые 4 часа)
            last_export_time = time.time()
            export_interval_sec = 4 * 3600  # 4 hours
            
            while True:
                # Запускаем model_manager maintenance
                try:
                    await model_manager.start_maintenance()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("model_manager_maintenance_error", error=str(exc))
                
                # Проверяем нужен ли периодический экспорт
                current_time = time.time()
                if current_time - last_export_time >= export_interval_sec:
                    try:
                        await self._auto_export_handoff_snapshot(reason="periodic_maintenance")
                        last_export_time = current_time
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("periodic_handoff_export_failed", error=str(exc))
                
                # Ждем перед следующей итерацией
                await asyncio.sleep(300)  # 5 минут между проверками
                
        except asyncio.CancelledError:
            logger.info("maintenance_task_cancelled")
        except Exception as e:
            logger.error("maintenance_task_error", error=str(e))

    async def stop(self):
        """Остановка юзербота"""
        self._set_startup_state(state="stopping")
        
        # Auto-export handoff snapshot before shutdown (Phase 2.2)
        try:
            await self._auto_export_handoff_snapshot(reason="userbot_stop")
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto_handoff_export_failed", error=str(exc), non_fatal=True)
        
        if krab_scheduler.is_started:
            try:
                krab_scheduler.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("scheduler_stop_failed", error=str(exc), non_fatal=True)
        if self._telegram_watchdog_task:
            self._telegram_watchdog_task.cancel()
        if self._proactive_watch_task:
            self._proactive_watch_task.cancel()
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
        cleaned = cls._strip_plaintext_reasoning_prefix(cleaned)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"(?mi)^\s*(assistant|user|system)\s*$", "", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    @classmethod
    def _split_plaintext_reasoning_and_answer(cls, text: str) -> tuple[str, str]:
        """
        Разделяет plain-text reasoning и итоговый ответ, если провайдер прислал
        мысли без `<think>`.

        Почему нужен отдельный guard:
        - часть маршрутов может вернуть reasoning в свободном тексте вида
          `think\\nThinking Process: ...`, не используя transport-теги;
        - основной пользовательский ответ не должен смешиваться с цепочкой мыслей;
        - reasoning позже можно вернуть owner-only режимом отдельно, но не внутри
          обычного ответа.
        """
        raw = str(text or "")
        if not raw.strip():
            return "", ""

        lines = raw.splitlines()
        non_empty_indexes = [idx for idx, line in enumerate(lines) if line.strip()]
        if not non_empty_indexes:
            return "", raw.strip()

        intro_hits = 0
        for idx in non_empty_indexes[:3]:
            stripped = lines[idx].strip()
            if cls._plaintext_reasoning_intro_pattern.match(stripped):
                intro_hits += 1
                continue
            if idx == non_empty_indexes[0] and stripped.lower().startswith("thinking process:"):
                intro_hits += 1
                continue
        if intro_hits == 0:
            return "", raw.strip()

        def _is_reasoning_line(candidate: str) -> bool:
            stripped = candidate.strip()
            if not stripped:
                return False
            if cls._plaintext_reasoning_intro_pattern.match(stripped):
                return True
            if cls._plaintext_reasoning_step_pattern.match(stripped):
                return True
            if cls._plaintext_reasoning_meta_pattern.match(stripped):
                return True
            return False

        last_content_idx: int | None = None
        for idx in range(len(lines) - 1, -1, -1):
            if lines[idx].strip():
                last_content_idx = idx
                break
        if last_content_idx is None:
            return "", ""

        answer_end = last_content_idx
        answer_start: int | None = None
        for idx in range(last_content_idx, -1, -1):
            current = lines[idx]
            if not current.strip():
                if answer_start is not None:
                    break
                continue
            if _is_reasoning_line(current):
                if answer_start is not None:
                    break
                continue
            answer_start = idx

        if answer_start is None:
            return raw.strip(), ""

        reasoning = "\n".join(lines[:answer_start]).strip()
        extracted = "\n".join(lines[answer_start : answer_end + 1]).strip()
        if not reasoning:
            return "", raw.strip()
        return reasoning, extracted or raw.strip()

    @classmethod
    def _strip_plaintext_reasoning_prefix(cls, text: str) -> str:
        """
        Убирает plain-text reasoning, если провайдер прислал мысли без `<think>`.
        """
        _, answer = cls._split_plaintext_reasoning_and_answer(text)
        return answer

    @classmethod
    def _extract_reasoning_trace(cls, text: str) -> str:
        """
        Возвращает reasoning trace отдельно от основного ответа.

        Почему нужен отдельный helper:
        - пользователь попросил не смешивать мысли с финальным ответом;
        - owner/debug-контур иногда всё же хочет посмотреть reasoning отдельно;
        - часть провайдеров шлёт мысли внутри `<think>`, часть — plain-text префиксом.
        """
        raw = str(text or "")
        if not raw.strip():
            return ""

        fragments = [str(match.group(1) or "").strip() for match in cls._think_capture_pattern.finditer(raw)]
        if not fragments and "<think>" in raw.lower():
            start = raw.lower().rfind("<think>")
            partial = raw[start + len("<think>") :]
            end = partial.lower().find("</think>")
            if end >= 0:
                partial = partial[:end]
            if partial.strip():
                fragments = [partial.strip()]

        if not fragments:
            reasoning_prefix, _ = cls._split_plaintext_reasoning_and_answer(raw)
            if reasoning_prefix.strip():
                fragments = [reasoning_prefix.strip()]

        if not fragments:
            return ""

        normalized_lines: list[str] = []
        for fragment in fragments:
            cleaned = cls._reply_to_tag_pattern.sub("", fragment)
            cleaned = cls._tool_response_block_pattern.sub("", cleaned)
            cleaned = cls._llm_transport_tokens_pattern.sub("", cleaned)
            cleaned = cls._think_final_tag_pattern.sub("", cleaned)
            cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
            for line in cleaned.splitlines():
                stripped = line.strip()
                if not stripped:
                    if normalized_lines and normalized_lines[-1] != "":
                        normalized_lines.append("")
                    continue
                if cls._plaintext_reasoning_intro_pattern.match(stripped):
                    continue
                if stripped.lower().startswith("thinking process:"):
                    stripped = stripped.split(":", 1)[1].strip()
                    if not stripped:
                        continue
                normalized_lines.append(stripped)

        reasoning = "\n".join(normalized_lines).strip()
        return reasoning

    def _remember_hidden_reasoning_trace(
        self,
        *,
        chat_id: str,
        query: str,
        raw_response: str,
        final_response: str,
        access_level: AccessLevel | str | None = None,
    ) -> None:
        """
        Сохраняет reasoning trace отдельно от пользовательского ответа.

        Почему in-memory:
        - trace нужен как owner-only debug-слой "на сейчас", а не как долговременная память;
        - не хочется писать потенциально чувствительные рассуждения в обычную память Краба;
        - при перезапуске runtime trace может честно пропасть без риска для source-of-truth.
        """
        level = str(access_level.value if isinstance(access_level, AccessLevel) else access_level or "").strip().lower()
        if level not in {AccessLevel.OWNER.value, AccessLevel.FULL.value}:
            return

        route_meta = {}
        if hasattr(openclaw_client, "get_last_runtime_route"):
            try:
                route_meta = openclaw_client.get_last_runtime_route() or {}
            except Exception:
                route_meta = {}

        trace_text = self._extract_reasoning_trace(raw_response)
        traces = getattr(self, "_hidden_reasoning_traces", None)
        if traces is None:
            traces = {}
            self._hidden_reasoning_traces = traces
        traces[str(chat_id or "unknown")] = {
            "available": bool(trace_text),
            "query": str(query or "").strip(),
            "reasoning": trace_text,
            "answer_preview": textwrap.shorten(str(final_response or "").strip(), width=400, placeholder="..."),
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "transport_mode": "buffered_edit_loop",
            "route_channel": str(route_meta.get("channel") or "").strip(),
            "route_model": str(route_meta.get("model") or "").strip(),
        }

    def get_hidden_reasoning_trace_snapshot(self, chat_id: str | int) -> dict[str, Any]:
        """Возвращает последний скрытый reasoning trace для конкретного чата."""
        traces = getattr(self, "_hidden_reasoning_traces", None)
        if not isinstance(traces, dict):
            return {}
        trace = traces.get(str(chat_id or "unknown"))
        return dict(trace) if isinstance(trace, dict) else {}

    def clear_hidden_reasoning_trace_snapshot(self, chat_id: str | int) -> bool:
        """Очищает последний reasoning trace для конкретного чата."""
        traces = getattr(self, "_hidden_reasoning_traces", None)
        if not isinstance(traces, dict):
            return False
        return traces.pop(str(chat_id or "unknown"), None) is not None

    @classmethod
    def _extract_live_stream_text(cls, text: str, *, allow_reasoning: bool = False) -> str:
        """
        Возвращает лучший доступный текст для промежуточного live-stream отображения.

        Почему это отдельный helper:
        - часть провайдеров стримит ответ внутри `<final>` и закрывает тег только
          в самом конце; старое поведение из-за этого показывало почти пустой draft
          до финального чанка;
        - reasoning полезно держать отдельным опциональным режимом, а не мешать в
          обычный пользовательский текст.
        """
        raw = str(text or "")
        if not raw:
            return ""

        if "<final>" in raw.lower():
            lower_raw = raw.lower()
            start = lower_raw.rfind("<final>")
            if start >= 0:
                partial_final = raw[start + len("<final>") :]
                end = partial_final.lower().find("</final>")
                if end >= 0:
                    partial_final = partial_final[:end]
                partial_final = cls._reply_to_tag_pattern.sub("", partial_final)
                partial_final = cls._tool_response_block_pattern.sub("", partial_final)
                partial_final = cls._llm_transport_tokens_pattern.sub("", partial_final)
                partial_final = cls._think_final_tag_pattern.sub("", partial_final)
                partial_final = re.sub(r"[ \t]{2,}", " ", partial_final)
                partial_final = re.sub(r"\n{3,}", "\n\n", partial_final).strip()
                if partial_final:
                    return partial_final

        lower_raw = raw.lower()
        if allow_reasoning and "<think>" in lower_raw:
            start = lower_raw.rfind("<think>")
            partial_think = raw[start + len("<think>") :]
            end = partial_think.lower().find("</think>")
            if end >= 0:
                partial_think = partial_think[:end]
            partial_think = cls._reply_to_tag_pattern.sub("", partial_think)
            partial_think = cls._tool_response_block_pattern.sub("", partial_think)
            partial_think = cls._llm_transport_tokens_pattern.sub("", partial_think)
            partial_think = cls._think_final_tag_pattern.sub("", partial_think)
            partial_think = re.sub(r"[ \t]{2,}", " ", partial_think)
            partial_think = re.sub(r"\n{3,}", "\n\n", partial_think).strip()
            if partial_think:
                return f"🧠 {partial_think}"

        cleaned = cls._strip_transport_markup(raw)
        if cleaned:
            return cleaned

        return ""

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
        """Отключено по просьбе пользователя (все вопросы уходят в LLM)."""
        return False

        low = str(text or "").strip().lower()  # noqa: unreachable
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
        """Отключено по просьбе пользователя (все вопросы уходят в LLM)."""
        return False

        low = str(text or "").strip().lower()  # noqa: unreachable
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
        """Отключено по просьбе пользователя (все вопросы уходят в LLM)."""
        return False

        low = str(text or "").strip().lower()  # noqa: unreachable
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
        """Отключено по просьбе пользователя (все вопросы уходят в LLM)."""
        return False

        low = str(text or "").strip().lower()  # noqa: unreachable
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
        Отключено по просьбе пользователя (все вопросы уходят в LLM).
        """
        return False
        
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
        return resolve_access_mode(
            is_allowed_sender=is_allowed_sender,
            access_level=access_level,
        )

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
                    "- Снимать owner-digest, читать последние записи общей памяти и вести owner-visible inbox через `!watch`, `!memory recent`, `!inbox`.",
                    "- Работать с файлами по путям через `!ls`, `!read`, `!write`.",
                    "- Выполнять базовые действия в macOS через `!mac` (clipboard, notifications, apps, Finder/open, Notes, Reminders, Calendar).",
                    "- Управлять браузерным/веб-контуром через `!web` и открывать панель через `!panel`.",
                    "- Управлять voice-профилем ответов через `!voice` (вкл/выкл, скорость, голос, delivery).",
                    "- Управлять product-профилем переводчика через `!translator` (языки, mode, strategy, call-flags, quick phrases).",
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
            "- Не выполняю физические действия в реальном мире, но могу делать ограниченные системные действия внутри macOS по явной owner-команде.",
            "- Не запоминаю всю переписку навсегда автоматически: долговременная память у меня точечная и управляется отдельно.",
            "- Качество анализа фото зависит от того, какая модель и какой маршрут сейчас доступны.",
        ]
        if access_mode in {AccessLevel.OWNER.value, AccessLevel.FULL.value}:
            limitations.append(
                "- Голосовой ingress уже работает, но полноценный live-call/WebRTC-контур ещё не доведён до финального режима."
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
                "- `!help`\n"
                "- `!search <запрос>`\n"
                "- `!status`\n\n"
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
            "`!status`, `!clear`, `!config`, `!help`",
        ]
        model_commands = [
            "`!model`, `!model local`, `!model cloud`, `!model auto`, `!model set <model_id>`, `!model load <name>`, `!model unload`, `!model scan`",
        ]
        tool_commands = [
            "`!search <запрос>`, `!remember <текст>`, `!recall <запрос>`, `!watch status|now`, `!memory recent [source]`, `!inbox [list|status|ack|done|cancel]`, `!role`, `!agent ...`",
        ]
        system_commands = [
            "`!ls [path]`, `!read <path>`, `!write <file> <content>`, `!sysinfo`, `!diagnose`, `!web`, `!panel`, `!voice ...`, `!translator ...`, `!mac ...`",
        ]
        if bool(getattr(config, "SCHEDULER_ENABLED", False)):
            tool_commands.append("`!remind <время> | <текст>`, `!reminders`, `!rm_remind <id>`, `!cronstatus`")

        body = (
            "🧭 **Команды, которые реально доступны сейчас**\n"
            "\n**Core**\n- " + "\n- ".join(core_commands)
            + "\n\n**AI / Model**\n- " + "\n- ".join(model_commands)
            + "\n\n**Tools**\n- " + "\n- ".join(tool_commands)
            + "\n\n**System / Dev**\n- " + "\n- ".join(system_commands)
        )
        if access_mode == AccessLevel.OWNER.value:
            body += (
                "\n\n**Owner-only admin**\n"
                "- `!set <KEY> <VAL>`\n"
                "- `!restart`\n"
                "- `!acl ...` / `!access ...`"
            )
        elif OWNER_ONLY_COMMANDS:
            body += (
                "\n\n🔒 **Что оставлено только владельцу**\n"
                "- `!set`, `!restart`, `!acl`, `!access`"
            )
        return body + "\n\nЕсли хочешь, я могу следующим сообщением показать короткую шпаргалку **по каждой команде с примерами**."

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
            f"- macOS automation: {'configured' if macos_automation.is_available() else 'unavailable'}",
            "- Memory engine: ON",
            f"- Proactive watch: {'ON' if bool(getattr(config, 'PROACTIVE_WATCH_ENABLED', False)) else 'OFF'}",
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
        prefer_send_message_for_background: bool = False,
    ) -> dict[str, Any]:
        """
        Доставляет готовый ответ в Telegram с безопасным split.

        Почему отдельный helper:
        - capability/status fast-path должен использовать ту же доставку, что и
          обычный AI-ответ;
        - так не дублируем логику split/edit/reply в нескольких ветках.
        """
        if not self._should_send_full_text_reply():
            placeholder = "🦀 Голосовой ответ отправлен. Если нужен текстовый дубль, переключи `!voice delivery text+voice`."
            if is_self:
                updated = await self._safe_edit(source_message, placeholder)
                return {
                    "delivery_mode": "placeholder_only",
                    "text_message_ids": [str(getattr(updated, "id", "") or "")] if getattr(updated, "id", None) else [],
                    "parts_count": 1,
                }
            updated = await self._safe_edit(temp_message, placeholder)
            return {
                "delivery_mode": "placeholder_only",
                "text_message_ids": [str(getattr(updated, "id", "") or "")] if getattr(updated, "id", None) else [],
                "parts_count": 1,
            }

        parts = self._split_message(
            f"🦀 {query}\n\n{full_response}" if is_self else full_response
        )
        delivered_ids: list[str] = []

        if is_self:
            source_message = await self._safe_edit(source_message, parts[0])
            if getattr(source_message, "id", None):
                delivered_ids.append(str(source_message.id))
            for part in parts[1:]:
                sent = await source_message.reply(part)
                if getattr(sent, "id", None):
                    delivered_ids.append(str(sent.id))
            return {
                "delivery_mode": "edit_and_reply",
                "text_message_ids": delivered_ids,
                "parts_count": len(parts),
            }

        if self._should_send_voice_reply() or prefer_send_message_for_background:
            # Для связки `text+voice` делаем явную текстовую отправку отдельным
            # сообщением: edit плейсхолдера в некоторых клиентах теряется
            # визуально, а send_message даёт надёжный финальный event доставки.
            # В background-handoff это ещё и разрывает зависимость от старого
            # placeholder-сообщения, которое могло уже устареть к моменту ответа.
            sent = await self.client.send_message(source_message.chat.id, parts[0])
            if getattr(sent, "id", None):
                delivered_ids.append(str(sent.id))
            for part in parts[1:]:
                sent = await self.client.send_message(source_message.chat.id, part)
                if getattr(sent, "id", None):
                    delivered_ids.append(str(sent.id))
            try:
                delete_coro = getattr(temp_message, "delete", None)
                if callable(delete_coro):
                    await delete_coro()
            except Exception:
                pass
            return {
                "delivery_mode": "send_message",
                "text_message_ids": delivered_ids,
                "parts_count": len(parts),
            }

        temp_message = await self._safe_edit(temp_message, parts[0])
        if getattr(temp_message, "id", None):
            delivered_ids.append(str(temp_message.id))
        for part in parts[1:]:
            sent = await source_message.reply(part)
            if getattr(sent, "id", None):
                delivered_ids.append(str(sent.id))
        return {
            "delivery_mode": "edit_and_reply",
            "text_message_ids": delivered_ids,
            "parts_count": len(parts),
        }

    @staticmethod
    def _message_ids_from_delivery(delivery_result: dict[str, Any] | None) -> list[str]:
        """Извлекает список текстовых message-id из delivery summary."""
        if not isinstance(delivery_result, dict):
            return []
        rows = delivery_result.get("text_message_ids")
        if not isinstance(rows, list):
            return []
        return [str(row).strip() for row in rows if str(row).strip()]

    def _record_incoming_reply_to_inbox(
        self,
        *,
        incoming_item_result: dict[str, Any] | None,
        response_text: str,
        delivery_result: dict[str, Any] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """
        Фиксирует outcome для ранее захваченного owner request.

        Важно не гадать по Telegram-логам задним числом: если ответ уже доставлен,
        transport-слой обязан сразу отметить это в persisted inbox.
        """
        if not isinstance(incoming_item_result, dict) or not incoming_item_result.get("ok"):
            return {"ok": False, "skipped": True, "reason": "incoming_item_missing"}
        item = incoming_item_result.get("item")
        metadata = item.get("metadata") if isinstance(item, dict) else {}
        if not isinstance(metadata, dict):
            return {"ok": False, "skipped": True, "reason": "incoming_item_metadata_missing"}
        chat_id = str(metadata.get("chat_id") or "").strip()
        message_id = str(metadata.get("message_id") or "").strip()
        if not chat_id or not message_id:
            return {"ok": False, "skipped": True, "reason": "incoming_item_identity_incomplete"}
        return inbox_service.record_incoming_owner_reply(
            chat_id=chat_id,
            message_id=message_id,
            response_text=response_text,
            delivery_mode=str((delivery_result or {}).get("delivery_mode") or "text").strip().lower() or "text",
            reply_message_ids=self._message_ids_from_delivery(delivery_result),
            actor="kraab",
            note=note,
        )

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

    @staticmethod
    def _message_has_audio(message: Message) -> bool:
        """Определяет voice/audio attachment, который можно отдать в STT."""
        return bool(getattr(message, "voice", None) or getattr(message, "audio", None))

    @staticmethod
    def _should_capture_incoming_owner_item(
        *,
        is_self: bool,
        is_allowed_sender: bool,
        chat_type: object,
        is_reply_to_me: bool,
        has_trigger: bool,
        has_photo: bool,
        has_audio: bool,
        query: str,
    ) -> bool:
        """
        Решает, надо ли складывать входящее сообщение в owner inbox.

        Нам важно не превратить inbox в лог вообще всех сообщений, поэтому
        берём только directed owner traffic:
        - доверенный private chat;
        - trusted group mention/reply;
        - сообщения с вложением, явно адресованные userbot-контуру.
        """
        if is_self or not is_allowed_sender:
            return False
        normalized_chat_type = str(getattr(chat_type, "value", chat_type) or "").strip().lower()
        if normalized_chat_type == "private":
            return bool(str(query or "").strip() or has_photo or has_audio)
        if not (is_reply_to_me or has_trigger):
            return False
        return bool(str(query or "").strip() or has_photo or has_audio or is_reply_to_me)

    def _sync_incoming_message_to_inbox(
        self,
        *,
        message: Message,
        user: Any,
        query: str,
        is_self: bool,
        is_allowed_sender: bool,
        has_trigger: bool,
        is_reply_to_me: bool,
        has_audio_message: bool,
    ) -> dict[str, Any]:
        """
        Публикует directed owner messages в persisted inbox.

        Почему это живёт в userbot_bridge:
        - именно здесь у нас есть truthful signal о том, что сообщение реально
          адресовано userbot-контуру, а не просто проходит мимо в группе;
        - storage и summary остаются в inbox_service, bridge только решает
          capture/no-capture на transport-слое.
        """
        if not self._should_capture_incoming_owner_item(
            is_self=is_self,
            is_allowed_sender=is_allowed_sender,
            chat_type=getattr(getattr(message, "chat", None), "type", ""),
            is_reply_to_me=is_reply_to_me,
            has_trigger=has_trigger,
            has_photo=bool(getattr(message, "photo", None)),
            has_audio=bool(has_audio_message),
            query=query,
        ):
            return {"ok": False, "skipped": True, "reason": "not_directed_owner_traffic"}
        chat_obj = getattr(message, "chat", None)
        chat_type = getattr(chat_obj, "type", "")
        normalized_chat_type = str(getattr(chat_type, "value", chat_type) or "").strip().lower()
        return inbox_service.upsert_incoming_owner_request(
            chat_id=str(getattr(chat_obj, "id", "") or ""),
            message_id=str(getattr(message, "id", "") or ""),
            text=str(query or "").strip(),
            sender_id=str(getattr(user, "id", "") or ""),
            sender_username=str(getattr(user, "username", "") or ""),
            chat_type=normalized_chat_type,
            is_reply_to_me=bool(is_reply_to_me),
            has_trigger=bool(has_trigger),
            has_photo=bool(getattr(message, "photo", None)),
            has_audio=bool(has_audio_message),
        )

    @staticmethod
    def _detect_relay_intent(query: str) -> bool:
        """
        Детектирует намерение передать сообщение владельцу.

        Зачем детерминированный keyword-match, а не LLM:
        - LLM уже обещает передать, но без side-effect;
        - нужна надёжная точка срабатывания независимо от формулировки ответа модели;
        - false-positives лучше чем missed relay — inbox потом закроет владелец.
        """
        normalized = str(query or "").lower()
        return any(kw in normalized for kw in _RELAY_INTENT_KEYWORDS)

    async def _escalate_relay_to_owner(
        self,
        *,
        message: Message,
        user: Any,
        query: str,
        chat_type: str,
    ) -> None:
        """
        Фиксирует relay-запрос в inbox и уведомляет владельца в Saved Messages.

        Почему Saved Messages (send to self):
        - userbot является аккаунтом владельца, поэтому отправка себе = уведомление;
        - это надёжнее любого бота/вебхука и работает без дополнительных токенов;
        - владелец увидит уведомление через обычный Telegram.
        """
        sender_display = f"@{user.username}" if getattr(user, "username", None) else f"id:{getattr(user, 'id', '?')}"
        chat_id_str = str(getattr(getattr(message, "chat", None), "id", "") or "")
        message_id_str = str(getattr(message, "id", "") or "")
        excerpt = str(query or "")[:1500]

        try:
            inbox_service.upsert_item(
                dedupe_key=f"relay:{chat_id_str}:{message_id_str}",
                kind="relay_request",
                source="telegram-userbot",
                title=f"📨 Relay от {sender_display}",
                body=(
                    f"Чат: `{chat_id_str}`\nОт: `{sender_display}`\nТип: `{chat_type}`\n\n"
                    f"Сообщение:\n{excerpt}"
                ),
                severity="warning",
                status="open",
                identity=inbox_service.build_identity(
                    channel_id=chat_id_str,
                    team_id="owner",
                    trace_id=build_trace_id("relay", chat_id_str, message_id_str),
                    approval_scope="owner",
                ),
                metadata={
                    "chat_id": chat_id_str,
                    "message_id": message_id_str,
                    "sender_id": str(getattr(user, "id", "") or ""),
                    "sender_username": str(getattr(user, "username", "") or ""),
                    "chat_type": chat_type,
                    "relay_text": excerpt,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("relay_inbox_escalation_failed", error=str(exc))

        try:
            me = await self.client.get_me()
            notification = (
                f"📨 **Relay-запрос**\n\n"
                f"От: `{sender_display}`\n"
                f"Чат: `{chat_id_str}` ({chat_type})\n\n"
                f"**Сообщение:**\n{excerpt[:800]}"
            )
            await self.client.send_message(me.id, notification)
            logger.info(
                "relay_owner_notified",
                sender=sender_display,
                chat_id=chat_id_str,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("relay_owner_notification_failed", error=str(exc))

    @staticmethod
    def _voice_download_suffix(message: Message) -> str:
        """Подбирает расширение для временного voice/audio файла."""
        voice = getattr(message, "voice", None)
        if voice:
            return ".ogg"
        audio = getattr(message, "audio", None)
        if audio:
            file_name = str(getattr(audio, "file_name", "") or "").strip()
            suffix = Path(file_name).suffix.strip()
            if suffix:
                return suffix if suffix.startswith(".") else f".{suffix}"
        return ".ogg"

    async def _transcribe_audio_message(self, message: Message) -> tuple[str, str]:
        """
        Скачивает входящее аудио и прогоняет его через Perceptor.

        Возвращает `(текст, ошибка)`, чтобы вызывающий код мог честно показать
        пользователю реальную причину сбоя, а не маскировать её placeholder-ом.
        """
        perceptor = getattr(self, "perceptor", None)
        if not perceptor or not hasattr(perceptor, "transcribe"):
            return "", "❌ Голосовой контур сейчас не подключён. Нужен активный perceptor/STT."
        if not self.client:
            return "", "❌ Telegram client не готов к загрузке аудио."

        voice_dir = config.BASE_DIR / "data" / "voice_inbox"
        voice_dir.mkdir(parents=True, exist_ok=True)
        message_id = int(getattr(message, "id", 0) or 0)
        file_path = voice_dir / (
            f"voice_{int(time.time() * 1000)}_{message_id}{self._voice_download_suffix(message)}"
        )
        download_timeout_sec = float(getattr(config, "VOICE_DOWNLOAD_TIMEOUT_SEC", 45.0))
        stt_timeout_sec = float(
            max(
                20.0,
                float(getattr(perceptor, "stt_worker_timeout_seconds", 240) or 240) + 15.0,
            )
        )
        saved_path = file_path

        try:
            downloaded = await asyncio.wait_for(
                self.client.download_media(message, file_name=str(file_path)),
                timeout=max(5.0, download_timeout_sec),
            )
            if downloaded:
                saved_path = Path(str(downloaded))
            transcript = await asyncio.wait_for(
                perceptor.transcribe(str(saved_path), model_manager),
                timeout=stt_timeout_sec,
            )
            normalized = str(transcript or "").strip()
            if not normalized:
                return "", "❌ Не удалось распознать голосовое сообщение."
            if normalized.lower().startswith("ошибка транскрибации"):
                return "", f"❌ {normalized}"
            return normalized, ""
        except asyncio.TimeoutError:
            return "", "❌ Таймаут обработки голосового сообщения. Попробуй отправить его ещё раз."
        except Exception as exc:  # noqa: BLE001
            logger.error("voice_message_transcription_failed", error=str(exc))
            return "", "❌ Ошибка обработки голосового сообщения. Попробуй отправить его ещё раз."
        finally:
            try:
                if saved_path.exists():
                    saved_path.unlink()
            except Exception:
                pass

    # Расширения, которые обрабатываем как plain-text (встраиваем содержимое в запрос).
    _TEXT_EXTENSIONS: frozenset[str] = frozenset({
        ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".yaml", ".yml",
        ".toml", ".ini", ".cfg", ".sh", ".bash", ".zsh", ".log", ".csv", ".xml",
        ".html", ".css", ".scss", ".sql", ".rs", ".go", ".java", ".kt", ".swift",
        ".c", ".cpp", ".h", ".hpp", ".rb", ".php", ".env", ".conf",
    })
    # Максимальный размер файла и инлайн-вставки.
    _DOC_MAX_BYTES: int = 5 * 1024 * 1024   # 5 MB — не скачиваем больше
    _DOC_INLINE_BYTES: int = 80 * 1024       # 80 KB — встраиваем содержимое текстом

    async def _process_document_message(
        self,
        *,
        message: "Message",
        query: str,
        temp_msg: Any,
        is_self: bool,
    ) -> str | None:
        """
        Скачивает документ из Telegram и обогащает query его содержимым.

        Возвращает обновлённый query или None, если нужно прервать обработку.
        Текстовые файлы <= _DOC_INLINE_BYTES вставляются inline; более крупные
        и бинарные — сохраняются в tmp и передаются путём (MCP filesystem может прочесть).
        """
        doc = getattr(message, "document", None)
        if not doc:
            return query

        file_name: str = str(getattr(doc, "file_name", None) or "document").strip() or "document"
        mime_type: str = str(getattr(doc, "mime_type", None) or "").strip()
        file_size: int = int(getattr(doc, "file_size", 0) or 0)

        if file_size > self._DOC_MAX_BYTES:
            size_kb = file_size // 1024
            limit_kb = self._DOC_MAX_BYTES // 1024
            err = f"❌ Файл слишком большой ({size_kb} KB). Максимум {limit_kb} KB."
            if is_self:
                await self._safe_edit(message, f"🦀 {query or file_name}\n\n{err}")
            else:
                await self._safe_edit(temp_msg, err)
            return None

        notice = f"📎 *Загружаю файл {file_name}...*"
        if is_self:
            await self._safe_edit(message, f"🦀 {query or file_name}\n\n{notice}")
        else:
            await self._safe_edit(temp_msg, notice)

        doc_dir = Path(getattr(config, "DOCUMENT_DOWNLOAD_DIR", "/tmp/krab_docs"))
        doc_dir.mkdir(parents=True, exist_ok=True)
        ts_ms = int(time.time() * 1000)
        msg_id = int(getattr(message, "id", 0) or 0)
        safe_name = "".join(c for c in file_name if c.isalnum() or c in "._-")[:64] or "doc"
        doc_path = doc_dir / f"doc_{ts_ms}_{msg_id}_{safe_name}"

        download_timeout = float(getattr(config, "DOCUMENT_DOWNLOAD_TIMEOUT_SEC", 45.0))
        try:
            downloaded = await asyncio.wait_for(
                self.client.download_media(message, file_name=str(doc_path)),
                timeout=max(5.0, download_timeout),
            )
        except asyncio.TimeoutError:
            err = "❌ Таймаут загрузки файла. Попробуй отправить его ещё раз."
            if is_self:
                await self._safe_edit(message, f"🦀 {query or file_name}\n\n{err}")
            else:
                await self._safe_edit(temp_msg, err)
            return None
        except Exception as exc:
            logger.error("document_download_failed", file_name=file_name, error=str(exc))
            err = "❌ Не удалось загрузить файл. Попробуй отправить его ещё раз."
            if is_self:
                await self._safe_edit(message, f"🦀 {query or file_name}\n\n{err}")
            else:
                await self._safe_edit(temp_msg, err)
            return None

        if not downloaded:
            err = "❌ Файл не удалось скачать. Попробуй снова."
            if is_self:
                await self._safe_edit(message, f"🦀 {query or file_name}\n\n{err}")
            else:
                await self._safe_edit(temp_msg, err)
            return None

        _, ext = os.path.splitext(file_name.lower())
        is_text = ext in self._TEXT_EXTENSIONS or mime_type.startswith("text/")
        actual_size = doc_path.stat().st_size if doc_path.exists() else 0

        if is_text and actual_size <= self._DOC_INLINE_BYTES:
            try:
                content = doc_path.read_text(encoding="utf-8", errors="replace")
                doc_context = f"[Файл: {file_name}]\n```\n{content}\n```"
            except Exception as exc:
                logger.warning("document_read_failed", file_name=file_name, error=str(exc))
                doc_context = f"[Файл сохранён: {doc_path}] (mime: {mime_type or 'unknown'}, размер: {actual_size} байт)"
        else:
            doc_context = f"[Файл сохранён: {doc_path}] (mime: {mime_type or 'unknown'}, размер: {actual_size} байт)"

        return f"{doc_context}\n\n{query}".strip() if query else doc_context

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

    def _get_chat_processing_lock(self, chat_id: str) -> asyncio.Lock:
        """
        Возвращает lock на конкретный чат.

        Почему это нужно:
        - без сериализации несколько сообщений из одного Telegram-чата могут
          одновременно зайти в LLM/TTS;
        - в voice-режиме это даёт наложение озвучки, гонки редактирования и
          ситуацию, когда текст/голос приезжают в перепутанном порядке;
        - per-chat lock убирает гонку локально, не запрещая параллельную
          обработку разных чатов.
        """
        chat_key = str(chat_id or "").strip() or "unknown"
        locks = getattr(self, "_chat_processing_locks", None)
        if locks is None:
            locks = {}
            self._chat_processing_locks = locks
        lock = locks.get(chat_key)
        if lock is None:
            lock = asyncio.Lock()
            locks[chat_key] = lock
        return lock

    def _remember_batched_followup_message_ids(
        self,
        *,
        chat_id: str,
        message_ids: list[str],
    ) -> None:
        """
        Запоминает message-id, уже поглощённые более ранним batch-запросом.

        Это защищает от двойной обработки: follower handlers всё равно дойдут до
        per-chat lock, но после этого должны тихо завершиться.
        """
        chat_key = str(chat_id or "").strip() or "unknown"
        rows = getattr(self, "_batched_followup_message_ids", None)
        if rows is None:
            rows = {}
            self._batched_followup_message_ids = rows
        bucket = rows.setdefault(chat_key, {})
        now = time.monotonic()
        for message_id in message_ids:
            normalized = str(message_id or "").strip()
            if normalized:
                bucket[normalized] = now

    def _consume_batched_followup_message_id(self, *, chat_id: str, message_id: str) -> bool:
        """
        Возвращает True, если сообщение уже было включено в предыдущий batch.

        Храним id недолго: этого достаточно, чтобы отфильтровать уже стоящие в
        очереди handler-вызовы и не раздувать состояние бесконечно.
        """
        chat_key = str(chat_id or "").strip() or "unknown"
        normalized_id = str(message_id or "").strip()
        if not normalized_id:
            return False
        rows = getattr(self, "_batched_followup_message_ids", None) or {}
        bucket = rows.get(chat_key)
        if not bucket:
            return False
        now = time.monotonic()
        ttl_sec = 600.0
        expired = [mid for mid, saved_at in bucket.items() if now - float(saved_at or 0.0) > ttl_sec]
        for expired_id in expired:
            bucket.pop(expired_id, None)
        if not bucket:
            rows.pop(chat_key, None)
            return False
        matched = normalized_id in bucket
        if matched:
            bucket.pop(normalized_id, None)
        if not bucket:
            rows.pop(chat_key, None)
        return matched

    @staticmethod
    def _extract_message_text(message: Message | Any) -> str:
        """Возвращает текст или подпись сообщения единым способом."""
        return str(getattr(message, "text", None) or getattr(message, "caption", None) or "")

    @staticmethod
    def _is_command_like_text(text: str) -> bool:
        """Определяет служебные команды, которые нельзя склеивать с обычным текстом."""
        normalized = str(text or "").lstrip()
        return normalized[:1] in {"!", "/", "."}

    def _is_private_text_batch_candidate(
        self,
        *,
        message: Message | Any,
        sender_id: int,
    ) -> bool:
        """
        Решает, можно ли включать сообщение в private text-burst batch.

        Склеиваем только plain-text сообщения того же отправителя:
        команды, фото и аудио должны идти отдельным путём, иначе потеряем
        ожидаемую семантику и управляемость.
        """
        message_sender_id = int(getattr(getattr(message, "from_user", None), "id", 0) or 0)
        if sender_id and message_sender_id != sender_id:
            return False
        if getattr(message, "photo", None) or self._message_has_audio(message):
            return False
        text = self._get_clean_text(self._extract_message_text(message))
        if not text:
            return False
        return not self._is_command_like_text(text)

    async def _coalesce_private_text_burst(
        self,
        *,
        message: Message,
        user: Any,
        query: str,
    ) -> tuple[Message, str]:
        """
        Склеивает короткую пачку private-сообщений одного отправителя в один query.

        Зачем это нужно:
        - после `!clear` пользователь часто заново передаёт контекст несколькими
          Telegram-сообщениями из-за лимита длины;
        - без склейки каждое сообщение уходит отдельным AI-запросом и вся очередь
          начинает жить своей жизнью;
        - выбираем последнюю user-message как anchor для ответа, чтобы в клиенте
          это выглядело естественно.
        """
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return message, normalized_query
        chat_type = getattr(getattr(message, "chat", None), "type", None)
        if chat_type != enums.ChatType.PRIVATE:
            return message, normalized_query
        if self._is_command_like_text(normalized_query):
            return message, normalized_query
        history_reader = getattr(self.client, "get_chat_history", None)
        if not callable(history_reader):
            return message, normalized_query

        batch_window_sec = float(getattr(config, "TELEGRAM_MESSAGE_BATCH_WINDOW_SEC", 1.4) or 0.0)
        if batch_window_sec <= 0:
            return message, normalized_query
        await asyncio.sleep(max(0.0, batch_window_sec))

        max_messages = max(1, int(getattr(config, "TELEGRAM_MESSAGE_BATCH_MAX_MESSAGES", 6) or 6))
        max_chars = max(1, int(getattr(config, "TELEGRAM_MESSAGE_BATCH_MAX_CHARS", 12000) or 12000))
        history_limit = max(12, max_messages * 4)
        history_rows: list[Message] = []
        async for row in history_reader(message.chat.id, limit=history_limit):
            history_rows.append(row)

        current_message_id = int(getattr(message, "id", 0) or 0)
        sender_id = int(getattr(user, "id", 0) or 0)
        if current_message_id <= 0 or sender_id <= 0:
            return message, normalized_query

        ordered_rows = sorted(
            (
                row
                for row in history_rows
                if int(getattr(row, "id", 0) or 0) >= current_message_id
            ),
            key=lambda row: int(getattr(row, "id", 0) or 0),
        )
        if not ordered_rows:
            return message, normalized_query

        base_ts = _message_unix_ts(message)
        max_gap_sec = max(batch_window_sec + 1.0, 3.0)
        max_span_sec = max(batch_window_sec + 4.0, 6.0)
        combined_messages: list[Message] = []
        combined_parts: list[str] = []
        total_chars = 0
        current_found = False
        previous_ts = base_ts

        for row in ordered_rows:
            row_id = int(getattr(row, "id", 0) or 0)
            if not current_found:
                if row_id != current_message_id:
                    continue
                current_found = True
            elif len(combined_messages) >= max_messages:
                break
            elif not self._is_private_text_batch_candidate(message=row, sender_id=sender_id):
                break

            clean_text = normalized_query if row_id == current_message_id else self._get_clean_text(self._extract_message_text(row))
            if not clean_text:
                if row_id == current_message_id:
                    return message, normalized_query
                break

            row_ts = _message_unix_ts(row)
            if combined_messages:
                if row_ts is not None and previous_ts is not None and (row_ts - previous_ts) > max_gap_sec:
                    break
                if row_ts is not None and base_ts is not None and (row_ts - base_ts) > max_span_sec:
                    break

            projected_chars = total_chars + len(clean_text) + (2 if combined_parts else 0)
            if projected_chars > max_chars:
                break

            combined_messages.append(row if row_id != current_message_id else message)
            combined_parts.append(clean_text)
            total_chars = projected_chars
            previous_ts = row_ts if row_ts is not None else previous_ts

        if len(combined_messages) <= 1:
            return message, normalized_query

        absorbed_ids = [
            str(getattr(row, "id", "") or "").strip()
            for row in combined_messages[1:]
            if str(getattr(row, "id", "") or "").strip()
        ]
        if absorbed_ids:
            self._remember_batched_followup_message_ids(
                chat_id=str(getattr(getattr(message, "chat", None), "id", "") or ""),
                message_ids=absorbed_ids,
            )

        combined_query = "\n\n".join(part for part in combined_parts if part).strip()
        anchor_message = combined_messages[-1]
        logger.info(
            "private_text_burst_coalesced",
            chat_id=str(getattr(getattr(message, "chat", None), "id", "") or ""),
            anchor_message_id=str(getattr(anchor_message, "id", "") or ""),
            absorbed_message_ids=absorbed_ids,
            messages_count=len(combined_messages),
            total_chars=len(combined_query),
        )
        return anchor_message, combined_query

    @staticmethod
    async def _keep_typing_alive(client: Any, chat_id: int, action: Any, stop_event: asyncio.Event) -> None:
        """Фоновая корутина: повторяет send_chat_action каждые 4 секунды, пока не установлен stop_event."""
        while not stop_event.is_set():
            try:
                await client.send_chat_action(chat_id, action)
            except Exception:
                pass
            try:
                await asyncio.wait_for(asyncio.shield(stop_event.wait()), timeout=4.0)
            except asyncio.TimeoutError:
                pass

    def _mark_incoming_item_background_started(
        self,
        *,
        incoming_item_result: dict[str, Any] | None,
        note: str = "background_processing_started",
    ) -> dict[str, Any]:
        """Переводит входящий inbox item в `acked`, если у него уже есть persisted запись."""
        if not isinstance(incoming_item_result, dict) or not incoming_item_result.get("ok"):
            return {"ok": False, "skipped": True, "reason": "incoming_item_missing"}
        item = incoming_item_result.get("item")
        if not isinstance(item, dict):
            return {"ok": False, "skipped": True, "reason": "incoming_item_missing"}
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        chat_id = str((metadata or {}).get("chat_id") or "").strip()
        message_id = str((metadata or {}).get("message_id") or "").strip()
        if not chat_id or not message_id:
            return {"ok": False, "skipped": True, "reason": "incoming_item_identity_incomplete"}
        return inbox_service.set_status_by_dedupe(
            f"incoming:{chat_id}:{message_id}",
            status="acked",
            actor="kraab",
            note=note,
            event_action="background_started",
        )

    def _register_chat_background_task(self, chat_id: str, task: asyncio.Task) -> None:
        """Регистрирует background-task чата и автоматически чистит stale-ссылку после завершения."""
        tasks = getattr(self, "_chat_background_tasks", None)
        if tasks is None:
            tasks = {}
            self._chat_background_tasks = tasks
        chat_key = str(chat_id or "").strip() or "unknown"
        tasks[chat_key] = task

        def _cleanup(_task: asyncio.Task) -> None:
            current = tasks.get(chat_key)
            if current is _task:
                tasks.pop(chat_key, None)

        task.add_done_callback(_cleanup)

    def _get_active_chat_background_task(self, chat_id: str) -> asyncio.Task | None:
        """Возвращает активную background-task чата, если она ещё жива."""
        tasks = getattr(self, "_chat_background_tasks", None) or {}
        chat_key = str(chat_id or "").strip() or "unknown"
        task = tasks.get(chat_key)
        if task and not task.done():
            return task
        return None

    @staticmethod
    def _build_background_handoff_notice(query: str) -> str:
        """
        Возвращает честный текст для момента, когда длинный запрос уходит в фон.

        Это не «готовый ответ», а явное подтверждение, что Краб принял задачу,
        отпустил lock чата и продолжит обработку в background-режиме.
        """
        safe_query = str(query or "").strip() or "запрос"
        return (
            f"🦀 Принял запрос: `{safe_query}`\n\n"
            "⏳ Задача продолжает выполняться в фоне. "
            "Финальный ответ пришлю отдельным сообщением, как только обработка завершится."
        )

    async def _run_llm_request_flow(
        self,
        *,
        message: Message,
        temp_msg: Message,
        is_self: bool,
        query: str,
        chat_id: str,
        runtime_chat_id: str,
        access_profile: AccessProfile,
        is_allowed_sender: bool,
        incoming_item_result: dict[str, Any] | None,
        images: list[str],
        force_cloud: bool,
        system_prompt: str,
        action_stop_event: asyncio.Event,
        action_task: asyncio.Task,
        prefer_send_message_for_background: bool = False,
    ) -> None:
        """Общий long-path LLM/tool flow для inline и background режима."""
        full_response = ""
        full_response_raw = ""
        last_edit_time = 0.0

        first_chunk_timeout_sec, chunk_timeout_sec = _resolve_openclaw_stream_timeouts(
            has_photo=bool(images)
        )
        buffered_response_timeout_sec = _resolve_openclaw_buffered_response_timeout(
            has_photo=bool(images),
            first_chunk_timeout_sec=first_chunk_timeout_sec,
        )
        progress_notice_initial_sec, progress_notice_repeat_sec = (
            _resolve_openclaw_progress_notice_schedule(
                has_photo=bool(images),
                first_chunk_timeout_sec=first_chunk_timeout_sec,
            )
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
        started_wait_at = time.monotonic()
        slow_first_chunk_notice_sent = False
        progress_notice_count = 0
        next_progress_notice_sec = float(progress_notice_initial_sec)
        tool_progress_poll_sec = float(
            getattr(config, "OPENCLAW_TOOL_PROGRESS_POLL_SEC", 4.0) or 4.0
        )
        tool_progress_poll_sec = max(0.01, tool_progress_poll_sec)
        next_tool_progress_sec = tool_progress_poll_sec
        last_tool_summary = ""
        last_progress_notice_text = ""
        startup_route_model = str(
            _current_runtime_primary_model() or getattr(config, "MODEL", "") or ""
        ).strip()
        next_chunk_task = asyncio.create_task(stream_iter.__anext__())

        try:
            while True:
                if received_any_chunk:
                    wait_timeout = chunk_timeout_sec
                elif slow_first_chunk_notice_sent:
                    wait_timeout = chunk_timeout_sec
                else:
                    wait_timeout = first_chunk_timeout_sec
                elapsed_wait_sec = time.monotonic() - started_wait_at
                remaining_total_timeout_sec = max(0.0, buffered_response_timeout_sec - elapsed_wait_sec)
                if not received_any_chunk:
                    if remaining_total_timeout_sec <= 0.0:
                        logger.error(
                            "openclaw_buffered_response_timeout",
                            chat_id=chat_id,
                            elapsed_sec=round(elapsed_wait_sec, 3),
                            hard_timeout_sec=buffered_response_timeout_sec,
                            has_photo=bool(images),
                        )
                        route_meta = {}
                        if hasattr(openclaw_client, "get_last_runtime_route"):
                            try:
                                route_meta = openclaw_client.get_last_runtime_route() or {}
                            except Exception:
                                route_meta = {}
                        route_model = str(
                            route_meta.get("model")
                            or _current_runtime_primary_model()
                            or getattr(config, "MODEL", "")
                            or ""
                        ).strip()
                        if hasattr(openclaw_client, "_set_last_runtime_route"):
                            try:
                                openclaw_client._set_last_runtime_route(  # noqa: SLF001
                                    channel="error",
                                    model=route_model or "unknown",
                                    route_reason="userbot_buffered_wait_timeout",
                                    route_detail="Userbot дождался buffered OpenClaw дольше допустимого окна",
                                    status="error",
                                    error_code="first_chunk_timeout",
                                    force_cloud=force_cloud,
                                )
                            except Exception:
                                pass
                        full_response = (
                            "❌ OpenClaw слишком долго собирает первый ответ. "
                            "Похоже, цепочка fallback зависла или все cloud-кандидаты перегружены. "
                            "Попробуй `!model local` или повтори запрос позже."
                        )
                        if next_chunk_task and not next_chunk_task.done():
                            next_chunk_task.cancel()
                            try:
                                await next_chunk_task
                            except (asyncio.CancelledError, StopAsyncIteration):
                                pass
                            except Exception:
                                pass
                        try:
                            await stream.aclose()
                        except Exception:
                            pass
                        break
                    if not slow_first_chunk_notice_sent:
                        wait_timeout = min(
                            wait_timeout,
                            max(0.0, first_chunk_timeout_sec - elapsed_wait_sec),
                        )
                    if next_progress_notice_sec > 0.0:
                        wait_timeout = min(
                            wait_timeout,
                            max(0.0, next_progress_notice_sec - elapsed_wait_sec),
                        )
                    if next_tool_progress_sec > 0.0:
                        wait_timeout = min(
                            wait_timeout,
                            max(0.0, next_tool_progress_sec - elapsed_wait_sec),
                        )
                    wait_timeout = min(wait_timeout, remaining_total_timeout_sec)
                try:
                    done, _ = await asyncio.wait({next_chunk_task}, timeout=wait_timeout)
                    if not done:
                        raise asyncio.TimeoutError
                    chunk = next_chunk_task.result()
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    elapsed_wait_sec = time.monotonic() - started_wait_at
                    
                    if received_any_chunk:
                        logger.error(
                            "openclaw_stream_chunk_timeout",
                            chat_id=chat_id,
                            timeout_sec=wait_timeout,
                            first_chunk=False,
                            has_photo=bool(images),
                        )
                        full_response = "❌ Модель слишком долго пишет ответ (оборвано на полуслове)."
                        if next_chunk_task and not next_chunk_task.done():
                            next_chunk_task.cancel()
                            try:
                                await next_chunk_task
                            except (asyncio.CancelledError, StopAsyncIteration):
                                pass
                            except Exception:
                                pass
                        try:
                            await stream.aclose()
                        except Exception:
                            pass
                        break

                    # Fetch tool summary if needed for intervals
                    tool_summary = ""
                    if hasattr(openclaw_client, "get_active_tool_calls_summary"):
                        try:
                            tool_summary = openclaw_client.get_active_tool_calls_summary()
                        except Exception:
                            tool_summary = ""

                    handled_interval = False

                    # Handle Tool Progress
                    if elapsed_wait_sec >= next_tool_progress_sec - 1e-6:
                        handled_interval = True
                        if tool_summary:
                            route_meta = {}
                            if hasattr(openclaw_client, "get_last_runtime_route"):
                                try:
                                    route_meta = openclaw_client.get_last_runtime_route() or {}
                                except Exception:
                                    route_meta = {}
                            route_model = str(
                                route_meta.get("model")
                                or startup_route_model
                                or getattr(config, "MODEL", "")
                                or ""
                            ).strip()
                            route_attempt = int(route_meta.get("attempt") or 0) or None
                            progress_notice = _build_openclaw_progress_wait_notice(
                                route_model=route_model,
                                attempt=route_attempt,
                                elapsed_sec=elapsed_wait_sec,
                                notice_index=max(1, progress_notice_count),
                                tool_calls_summary=tool_summary,
                            )
                            if progress_notice != last_progress_notice_text or tool_summary != last_tool_summary:
                                try:
                                    if is_self:
                                        message = await self._safe_edit(message, f"🦀 {query}\n\n{progress_notice}")
                                    else:
                                        temp_msg = await self._safe_edit(temp_msg, progress_notice)
                                    last_progress_notice_text = progress_notice
                                    last_tool_summary = tool_summary
                                except Exception as exc:
                                    logger.warning(
                                        "openclaw_tool_progress_notice_delivery_failed",
                                        chat_id=chat_id,
                                        route_model=route_model,
                                        route_attempt=route_attempt,
                                        error=str(exc),
                                        has_photo=bool(images),
                                    )
                        next_tool_progress_sec = elapsed_wait_sec + tool_progress_poll_sec

                    # Handle Slow First Chunk
                    if not slow_first_chunk_notice_sent and elapsed_wait_sec >= float(first_chunk_timeout_sec) - 1e-6:
                        handled_interval = True
                        slow_first_chunk_notice_sent = True
                        route_meta = {}
                        if hasattr(openclaw_client, "get_last_runtime_route"):
                            try:
                                route_meta = openclaw_client.get_last_runtime_route() or {}
                            except Exception:
                                route_meta = {}
                        route_model = str(
                            route_meta.get("model")
                            or startup_route_model
                            or getattr(config, "MODEL", "")
                            or ""
                        ).strip()
                        route_attempt = int(route_meta.get("attempt") or 0) or None
                        logger.warning(
                            "openclaw_first_chunk_slow_waiting_more",
                            chat_id=chat_id,
                            elapsed_sec=round(elapsed_wait_sec, 3),
                            soft_timeout_sec=first_chunk_timeout_sec,
                            hard_timeout_sec=buffered_response_timeout_sec,
                            route_model=route_model,
                            route_attempt=route_attempt,
                            has_photo=bool(images),
                        )
                        slow_notice = _build_openclaw_slow_wait_notice(
                            route_model=route_model,
                            attempt=route_attempt,
                        )
                        try:
                            if is_self:
                                message = await self._safe_edit(message, f"🦀 {query}\n\n{slow_notice}")
                            else:
                                temp_msg = await self._safe_edit(temp_msg, slow_notice)
                        except Exception as exc:
                            logger.warning(
                                "openclaw_slow_notice_delivery_failed",
                                ...
                            )
                        # We don't continue immediately, we might have progress notice to send

                    # Handle Progress Notice Keepalive
                    if next_progress_notice_sec > 0.0 and elapsed_wait_sec >= next_progress_notice_sec - 1e-6:
                        handled_interval = True
                        route_meta = {}
                        if hasattr(openclaw_client, "get_last_runtime_route"):
                            try:
                                route_meta = openclaw_client.get_last_runtime_route() or {}
                            except Exception:
                                route_meta = {}
                        route_model = str(
                            route_meta.get("model")
                            or startup_route_model
                            or getattr(config, "MODEL", "")
                            or ""
                        ).strip()
                        route_attempt = int(route_meta.get("attempt") or 0) or None
                        progress_notice_count += 1
                        logger.info(
                            "openclaw_first_chunk_progress_notice",
                            chat_id=chat_id,
                            elapsed_sec=round(elapsed_wait_sec, 3),
                            notice_index=progress_notice_count,
                            route_model=route_model,
                            route_attempt=route_attempt,
                            has_photo=bool(images),
                        )
                        progress_notice = _build_openclaw_progress_wait_notice(
                            route_model=route_model,
                            attempt=route_attempt,
                            elapsed_sec=elapsed_wait_sec,
                            notice_index=progress_notice_count,
                            tool_calls_summary=tool_summary,
                        )
                        try:
                            if is_self:
                                message = await self._safe_edit(message, f"🦀 {query}\n\n{progress_notice}")
                            else:
                                temp_msg = await self._safe_edit(temp_msg, progress_notice)
                            last_progress_notice_text = progress_notice
                            last_tool_summary = tool_summary
                        except Exception as exc:
                            logger.warning(
                                "openclaw_progress_notice_delivery_failed",
                                ...
                            )
                        next_progress_notice_sec = elapsed_wait_sec + progress_notice_repeat_sec
                        next_tool_progress_sec = elapsed_wait_sec + tool_progress_poll_sec

                    if handled_interval:
                        continue
                        
                    # If it wasn't an expected interval, and we haven't received a chunk,
                    # AND we have passed the initial slow_first_chunk_notice_sent (so wait_timeout becomes chunk_timeout_sec)
                    # then this is a real timeout:
                    if slow_first_chunk_notice_sent:
                        logger.error(
                            "openclaw_stream_chunk_timeout",
                            chat_id=chat_id,
                            timeout_sec=wait_timeout,
                            first_chunk=True,
                            has_photo=bool(images),
                        )
                        full_response = (
                            "❌ Модель отвечает слишком долго. Попробуй ещё раз или переключись на `!model cloud` / `!model local`."
                        )
                        if next_chunk_task and not next_chunk_task.done():
                            next_chunk_task.cancel()
                            try:
                                await next_chunk_task
                            except (asyncio.CancelledError, StopAsyncIteration):
                                pass
                            except Exception:
                                pass
                        try:
                            await stream.aclose()
                        except Exception:
                            pass
                        break

                    # If none of the conditions triggered an abort, wait for the next timeout
                    # (This shouldn't be reached if timers are exact, but floats are floats)
                    continue

                full_response_raw += chunk
                received_any_chunk = True
                stream_display = (
                    self._extract_live_stream_text(
                        full_response_raw,
                        allow_reasoning=bool(getattr(config, "TELEGRAM_STREAM_SHOW_REASONING", False)),
                    )
                    if bool(getattr(config, "STRIP_REPLY_TO_TAGS", True))
                    else full_response_raw
                )
                if stream_display:
                    full_response = stream_display

                update_interval = float(getattr(config, "TELEGRAM_STREAM_UPDATE_INTERVAL_SEC", 0.75) or 0.75)
                update_interval = max(0.25, update_interval)
                if stream_display and (time.time() - last_edit_time > update_interval):
                    last_edit_time = time.time()
                    try:
                        display = f"{stream_display} ▌"
                        if is_self:
                            message = await self._safe_edit(message, f"🦀 {query}\n\n{display}")
                        else:
                            temp_msg = await self._safe_edit(temp_msg, display)
                    except Exception as exc:
                        logger.warning(
                            "openclaw_stream_edit_delivery_failed",
                            chat_id=chat_id,
                            error=str(exc),
                            has_photo=bool(images),
                        )
                next_chunk_task = asyncio.create_task(stream_iter.__anext__())

            if not full_response:
                full_response = self._extract_live_stream_text(full_response_raw, allow_reasoning=False)
            if not full_response:
                full_response = "❌ Модель не вернула ответ."

            if not str(full_response).strip():
                full_response = "❌ Модель вернула пустой ответ. Попробуй повторить запрос."

            if bool(getattr(config, "STRIP_REPLY_TO_TAGS", True)):
                full_response = self._strip_transport_markup(full_response)
                if not full_response:
                    full_response = "❌ Модель вернула пустой ответ. Попробуй повторить запрос."
            full_response = self._apply_deferred_action_guard(full_response)
            self._remember_hidden_reasoning_trace(
                chat_id=chat_id,
                query=query,
                raw_response=full_response_raw,
                final_response=full_response,
                access_level=access_profile.level,
            )

            full_response = self._apply_optional_disclosure(
                chat_id=chat_id,
                text=full_response,
            )

            delivery_result = await self._deliver_response_parts(
                source_message=message,
                temp_message=temp_msg,
                is_self=is_self,
                query=query,
                full_response=full_response,
                prefer_send_message_for_background=prefer_send_message_for_background,
            )
            self._record_incoming_reply_to_inbox(
                incoming_item_result=incoming_item_result,
                response_text=full_response,
                delivery_result=delivery_result,
                note="llm_response_delivered_background" if prefer_send_message_for_background else "llm_response_delivered",
            )

            if self._should_send_voice_reply():
                voice_path = await text_to_speech(
                    full_response,
                    speed=self.voice_reply_speed,
                    voice=self.voice_reply_voice,
                )
                if voice_path:
                    await self.client.send_voice(message.chat.id, voice_path)
                    if os.path.exists(voice_path):
                        os.remove(voice_path)
        finally:
            action_stop_event.set()
            action_task.cancel()
            await asyncio.gather(action_task, return_exceptions=True)

    async def _finish_ai_request_background(self, **kwargs: Any) -> None:
        """Доводит long LLM/tool path до конца уже после release per-chat lock."""
        chat_id = str(kwargs.get("chat_id") or "").strip()
        incoming_item_result = kwargs.get("incoming_item_result")
        temp_msg = kwargs.get("temp_msg")
        try:
            await self._run_llm_request_flow(**kwargs, prefer_send_message_for_background=True)
        except Exception as exc:  # noqa: BLE001
            logger.error("background_ai_request_failed", chat_id=chat_id, error=str(exc))
            error_text = "❌ Фоновая обработка запроса завершилась ошибкой. Попробуй повторить сообщение."
            try:
                if temp_msg is not None:
                    await self.client.send_message(temp_msg.chat.id, error_text)
            except Exception:
                pass
            self._record_incoming_reply_to_inbox(
                incoming_item_result=incoming_item_result,
                response_text=error_text,
                delivery_result={"delivery_mode": "background_error", "text_message_ids": [], "parts_count": 1},
                note="llm_response_background_error",
            )

    async def _process_message_serialized(
        self,
        *,
        message: Message,
        user: Any,
        access_profile: AccessProfile,
        is_allowed_sender: bool,
        chat_id: str,
    ) -> None:
        """Обрабатывает одно входящее сообщение под эксклюзивным lock чата."""
        text = message.text or message.caption or ""
        has_audio_message = self._message_has_audio(message)

        if text and text.lstrip()[:1] in ("!", "/", "."):
            cmd_word = text.lstrip().split()[0].lstrip("!/.").lower()
            if cmd_word in self._known_commands:
                if not access_profile.can_execute_command(cmd_word, self._known_commands):
                    await message.reply(self._build_command_access_denied_text(cmd_word, access_profile))
                return

        has_document = bool(getattr(message, "document", None))
        if not text and not message.photo and not has_audio_message and not has_document:
            return

        runtime_chat_id = self._build_runtime_chat_scope_id(
            chat_id=chat_id,
            user_id=int(user.id),
            is_allowed_sender=is_allowed_sender,
            access_level=access_profile.level,
        )
        is_self = user.id == self.me.id
        has_trigger = self._is_trigger(text)
        has_group_audio_fallback = (
            has_audio_message
            and is_allowed_sender
            and bool(getattr(config, "GROUP_VOICE_FALLBACK_TRIGGER", True))
        )

        is_reply_to_me = (
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.id == self.me.id
        )

        if not (
            has_trigger
            or message.chat.type == enums.ChatType.PRIVATE
            or is_reply_to_me
            or has_group_audio_fallback
        ):
            return

        query = self._get_clean_text(text)
        if not query and has_audio_message:
            query, voice_error = await self._transcribe_audio_message(message)
            if not query:
                await message.reply(voice_error or "❌ Не удалось распознать голосовое сообщение.")
                return
        elif query and not message.photo and not has_audio_message:
            message, query = await self._coalesce_private_text_burst(
                message=message,
                user=user,
                query=query,
            )
            text = query
        if not query and not message.photo and not has_audio_message and not is_reply_to_me and not has_document:
            return

        incoming_item_result: dict[str, Any] | None = None
        try:
            incoming_item_result = self._sync_incoming_message_to_inbox(
                message=message,
                user=user,
                query=query,
                is_self=is_self,
                is_allowed_sender=is_allowed_sender,
                has_trigger=has_trigger,
                is_reply_to_me=bool(is_reply_to_me),
                has_audio_message=has_audio_message,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("incoming_message_inbox_sync_failed", chat_id=chat_id, error=str(exc))

        if not is_self and query and self._detect_relay_intent(query):
            chat_type_raw = getattr(getattr(message, "chat", None), "type", "")
            asyncio.create_task(
                self._escalate_relay_to_owner(
                    message=message,
                    user=user,
                    query=query,
                    chat_type=str(getattr(chat_type_raw, "value", chat_type_raw) or "").lower(),
                )
            )

        logger.info(
            "processing_ai_request",
            chat_id=chat_id,
            user=user.username,
            has_photo=bool(message.photo),
            has_audio=bool(has_audio_message),
        )
        action = enums.ChatAction.RECORD_AUDIO if self._should_send_voice_reply() else enums.ChatAction.TYPING
        _typing_stop_event = asyncio.Event()
        _typing_task = asyncio.create_task(
            self._keep_typing_alive(self.client, message.chat.id, action, _typing_stop_event)
        )
        # Переключение ролей
        if has_trigger and any(p in text.lower() for p in ["стань", "будь", "как"]):
            for role in ROLES:
                if role in text.lower():
                    self.current_role = role
                    await message.reply(f"🎭 **Режим изменен:** `{role}`. Слушаю.")
                    _typing_stop_event.set()
                    _typing_task.cancel()
                    await asyncio.gather(_typing_task, return_exceptions=True)
                    return

        temp_msg = message
        if not is_self:
            temp_msg = await message.reply(
                "🦀 Принял запрос.\n\n🛠️ Собираю контекст и запускаю маршрут..."
            )
        else:
            message = await self._safe_edit(
                message,
                f"🦀 {query}\n\n🛠️ Собираю контекст и запускаю маршрут...",
            )

        if self._looks_like_runtime_truth_question(query) or self._looks_like_model_status_question(query):
            runtime_text = await self._build_runtime_truth_status(
                is_allowed_sender=is_allowed_sender,
                access_level=access_profile.level,
            )
            runtime_text = self._apply_optional_disclosure(
                chat_id=chat_id,
                text=runtime_text,
            )
            delivery_result = await self._deliver_response_parts(
                source_message=message,
                temp_message=temp_msg,
                is_self=is_self,
                query=query,
                full_response=runtime_text,
            )
            self._record_incoming_reply_to_inbox(
                incoming_item_result=incoming_item_result,
                response_text=runtime_text,
                delivery_result=delivery_result,
                note="runtime_truth_fastpath",
            )
            _typing_stop_event.set()
            _typing_task.cancel()
            await asyncio.gather(_typing_task, return_exceptions=True)
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
            delivery_result = await self._deliver_response_parts(
                source_message=message,
                temp_message=temp_msg,
                is_self=is_self,
                query=query,
                full_response=capability_text,
            )
            self._record_incoming_reply_to_inbox(
                incoming_item_result=incoming_item_result,
                response_text=capability_text,
                delivery_result=delivery_result,
                note="capability_truth_fastpath",
            )
            _typing_stop_event.set()
            _typing_task.cancel()
            await asyncio.gather(_typing_task, return_exceptions=True)
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
            delivery_result = await self._deliver_response_parts(
                source_message=message,
                temp_message=temp_msg,
                is_self=is_self,
                query=query,
                full_response=commands_text,
            )
            self._record_incoming_reply_to_inbox(
                incoming_item_result=incoming_item_result,
                response_text=commands_text,
                delivery_result=delivery_result,
                note="commands_truth_fastpath",
            )
            _typing_stop_event.set()
            _typing_task.cancel()
            await asyncio.gather(_typing_task, return_exceptions=True)
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
            delivery_result = await self._deliver_response_parts(
                source_message=message,
                temp_message=temp_msg,
                is_self=is_self,
                query=query,
                full_response=integrations_text,
            )
            self._record_incoming_reply_to_inbox(
                incoming_item_result=incoming_item_result,
                response_text=integrations_text,
                delivery_result=delivery_result,
                note="integrations_truth_fastpath",
            )
            _typing_stop_event.set()
            _typing_task.cancel()
            await asyncio.gather(_typing_task, return_exceptions=True)
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
                delivery_result = {
                    "delivery_mode": "edit_error",
                    "text_message_ids": [str(getattr(message, "id", "") or "")] if getattr(message, "id", None) else [],
                    "parts_count": 1,
                }
            else:
                temp_msg = await self._safe_edit(temp_msg, safe_error)
                delivery_result = {
                    "delivery_mode": "edit_error",
                    "text_message_ids": [str(getattr(temp_msg, "id", "") or "")] if getattr(temp_msg, "id", None) else [],
                    "parts_count": 1,
                }
            self._record_incoming_reply_to_inbox(
                incoming_item_result=incoming_item_result,
                response_text=safe_error,
                delivery_result=delivery_result,
                note="photo_route_error",
            )
            _typing_stop_event.set()
            _typing_task.cancel()
            await asyncio.gather(_typing_task, return_exceptions=True)
            return

        # DOCUMENT: Скачиваем и встраиваем содержимое файла в запрос
        if has_document:
            query = await self._process_document_message(message=message, query=query, temp_msg=temp_msg, is_self=is_self)
            if query is None:
                _typing_stop_event.set()
                _typing_task.cancel()
                await asyncio.gather(_typing_task, return_exceptions=True)
                return

        system_prompt = self._build_system_prompt_for_sender(
            is_allowed_sender=is_allowed_sender,
            access_level=access_profile.level,
        )

        # CONTEXT: Добавляем контекст чата для групп
        if is_allowed_sender and message.chat.type != enums.ChatType.PRIVATE:
            context = await self._get_chat_context(message.chat.id)
            if context:
                system_prompt += f"\n\n[CONTEXT OF LAST MESSAGES]\n{context}\n[END CONTEXT]\n\nReply to the user request taking into account the context above."

        force_cloud = bool(getattr(config, "FORCE_CLOUD", False))
        if self._should_force_cloud_for_photo_route(has_images=bool(images)):
            logger.info(
                "userbot_photo_route_forced_to_cloud",
                chat_id=chat_id,
                preferred_vision=str(getattr(config, "LOCAL_PREFERRED_VISION_MODEL", "") or ""),
            )
            force_cloud = True
        should_defer_background = (
            bool(getattr(config, "USERBOT_BACKGROUND_LLM_HANDOFF", True))
            and
            not is_self
            and not bool(images)
            and not bool(has_audio_message)
            and not bool(message.photo)
            and not has_document
        )
        if should_defer_background and self._get_active_chat_background_task(chat_id):
            await self._run_llm_request_flow(
                message=message,
                temp_msg=temp_msg,
                is_self=is_self,
                query=query,
                chat_id=chat_id,
                runtime_chat_id=runtime_chat_id,
                access_profile=access_profile,
                is_allowed_sender=is_allowed_sender,
                incoming_item_result=incoming_item_result,
                images=images,
                force_cloud=force_cloud,
                system_prompt=system_prompt,
                action_stop_event=_typing_stop_event,
                action_task=_typing_task,
                prefer_send_message_for_background=False,
            )
            return

        if should_defer_background:
            try:
                handoff_notice = self._build_background_handoff_notice(query)
                await self._safe_edit(temp_msg, handoff_notice)
            except Exception:
                pass
            self._mark_incoming_item_background_started(
                incoming_item_result=incoming_item_result,
                note="background_processing_started",
            )
            background_task = asyncio.create_task(
                self._finish_ai_request_background(
                    message=message,
                    temp_msg=temp_msg,
                    is_self=is_self,
                    query=query,
                    chat_id=chat_id,
                    runtime_chat_id=runtime_chat_id,
                    access_profile=access_profile,
                    is_allowed_sender=is_allowed_sender,
                    incoming_item_result=incoming_item_result,
                    images=images,
                    force_cloud=force_cloud,
                    system_prompt=system_prompt,
                    action_stop_event=_typing_stop_event,
                    action_task=_typing_task,
                )
            )
            self._register_chat_background_task(chat_id, background_task)
            return

        await self._run_llm_request_flow(
            message=message,
            temp_msg=temp_msg,
            is_self=is_self,
            query=query,
            chat_id=chat_id,
            runtime_chat_id=runtime_chat_id,
            access_profile=access_profile,
            is_allowed_sender=is_allowed_sender,
            incoming_item_result=incoming_item_result,
            images=images,
            force_cloud=force_cloud,
            system_prompt=system_prompt,
            action_stop_event=_typing_stop_event,
            action_task=_typing_task,
            prefer_send_message_for_background=False,
        )

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
            chat_id = str(message.chat.id)
            async with self._get_chat_processing_lock(chat_id):
                if self._consume_batched_followup_message_id(
                    chat_id=chat_id,
                    message_id=str(getattr(message, "id", "") or ""),
                ):
                    logger.info(
                        "skip_batched_followup_message",
                        chat_id=chat_id,
                        message_id=str(getattr(message, "id", "") or ""),
                        user=getattr(user, "username", None),
                    )
                    return
                await self._process_message_serialized(
                    message=message,
                    user=user,
                    access_profile=access_profile,
                    is_allowed_sender=is_allowed_sender,
                    chat_id=chat_id,
                )

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
