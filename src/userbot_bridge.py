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
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pyrogram import Client, enums, filters
from pyrogram.types import Message

from .config import config
from .core.access_control import (
    USERBOT_KNOWN_COMMANDS,
    AccessLevel,
    AccessProfile,
)
from .core.chat_ban_cache import chat_ban_cache
from .core.chat_capability_cache import chat_capability_cache
from .core.exceptions import KrabError, UserInputError
from .core.inbox_service import inbox_service
from .core.logger import get_logger
from .core.operator_identity import build_trace_id
from .core.proactive_watch import proactive_watch
from .core.routing_errors import RouterError, user_message_for_surface
from .core.scheduler import krab_scheduler
from .core.silence_mode import silence_manager
from .core.silence_schedule import silence_schedule_manager
from .core.spam_filter import is_bulk_sender as _is_bulk_sender_ext
from .core.swarm_channels import swarm_channels
from .core.swarm_scheduler import swarm_scheduler
from .core.telegram_rate_limiter import telegram_rate_limiter
from .core.translator_runtime_profile import (
    load_translator_runtime_profile,
    normalize_translator_runtime_profile,
    save_translator_runtime_profile,
)
from .core.translator_session_state import (
    append_translator_history_entry,
    apply_translator_session_update,
    default_translator_session_state,
    load_translator_session_state,
    save_translator_session_state,
)
from .employee_templates import ROLES
from .handlers import (
    apply_spam_action,
    handle_acl,
    handle_afk,
    handle_agent,
    handle_alias,
    handle_ask,
    handle_autodel,
    handle_blocked,
    handle_b64,
    handle_bookmark,
    handle_browser,
    handle_budget,
    handle_calc,
    handle_cap,
    handle_catchup,
    handle_chatban,
    handle_chatinfo,
    handle_chatmute,
    handle_contacts,
    handle_claude_cli,
    handle_clear,
    handle_codex,
    handle_collect,
    handle_archive,
    handle_config,
    handle_context,
    handle_costs,
    handle_cronstatus,
    handle_currency,
    handle_define,
    handle_del,
    handle_diagnose,
    handle_dice,
    handle_digest,
    handle_dns,
    handle_export,
    handle_fwd,
    handle_gemini_cli,
    handle_grep,
    handle_invite,
    handle_hash,
    handle_health,
    handle_help,
    handle_history,
    handle_hs,
    handle_img,
    handle_inbox,
    handle_ip,
    handle_json,
    handle_len,
    handle_log,
    handle_ls,
    handle_macos,
    handle_mark,
    handle_media,
    handle_members,
    handle_memo,
    handle_memory,
    handle_model,
    handle_monitor,
    handle_new_chat_members,
    handle_note,
    handle_notify,
    handle_ocr,
    handle_opencode,
    handle_panel,
    handle_paste,
    handle_pin,
    handle_ping,
    handle_poll,
    handle_purge,
    handle_qr,
    handle_quiz,
    handle_quote,
    handle_rand,
    handle_react,
    handle_read,
    handle_regex,
    handle_reasoning,
    handle_recall,
    handle_remember,
    handle_remind,
    handle_reminders,
    handle_report,
    handle_restart,
    handle_rm_remind,
    handle_role,
    handle_run,
    handle_schedule,
    handle_screenshot,
    handle_search,
    handle_sed,
    handle_set,
    handle_shop,
    handle_silence,
    handle_slowmode,
    handle_snippet,
    handle_spam,
    handle_stats,
    handle_status,
    handle_sticker,
    handle_stopwatch,
    handle_summary,
    handle_swarm,
    handle_sysinfo,
    handle_tag,
    handle_timer,
    handle_todo,
    handle_translate,
    handle_translator,
    handle_tts,
    handle_unarchive,
    handle_unpin,
    handle_uptime,
    handle_urban,
    handle_profile,
    handle_voice,
    handle_watch,
    handle_weather,
    handle_link,
    handle_template,
    handle_top,
    handle_typing,
    handle_web,
    handle_welcome,
    handle_who,
    handle_write,
    handle_time,
    handle_yt,
)
from .model_manager import model_manager
from .openclaw_client import openclaw_client
from .reserve_bot import reserve_bot
from .search_engine import close_search
from .userbot.access_control import AccessControlMixin
from .userbot.background_tasks import BackgroundTasksMixin
from .userbot.llm_flow import (
    LLMFlowMixin,
    _current_runtime_primary_model,
    _resolve_openclaw_stream_timeouts,
)
from .userbot.llm_text_processing import LLMTextProcessingMixin
from .userbot.runtime_status import RuntimeStatusMixin
from .userbot.session import SessionMixin
from .userbot.voice_profile import VoiceProfileMixin

logger = get_logger(__name__)


_RELAY_INTENT_KEYWORDS: frozenset[str] = frozenset(
    {
        "передай",
        "передайте",
        "передать",
        "перешли",
        "переслать",
        "скажи",
        "скажите",
        "сообщи",
        "сообщите",
        "расскажи",
        "расскажите",
        "передайте ему",
        "передай ему",
        "напомни",
        "напомните",
        "напоминание",
        "запомни",
        "запомните",
        "запомнить",
        "хозяину",
        "хозяин",
        "хозяином",
        "владельцу",
        "владелец",
        "владельцу",
        "let know",
        "tell him",
        "tell her",
        "notify",
        "pass along",
        "pass it on",
        "tell pablo",
        "tell the owner",
    }
)

# Слова в ОТВЕТЕ Краба, указывающие на обещание передать/запомнить.
# Используется как backup-триггер: если входящее не попало в _RELAY_INTENT_KEYWORDS,
# но Краб всё равно пообещал передать — форсируем relay после доставки.
_RELAY_PROMISE_IN_RESPONSE: frozenset[str] = frozenset(
    {
        "передам",
        "передаю",
        "передал",
        "сообщу",
        "уведомлю",
        "запомнил",
        "запомню",
        "запомнил это",
        "хозяину передам",
        "передам владельцу",
    }
)


class _TelegramSendQueue:
    """
    Per-chat serialised queue with exponential-backoff retry for outgoing
    Telegram API calls (send_message, edit, reply).

    Зачем нужна очередь:
    - При долгих tool-chain задачах Telegram API может вернуть FLOOD_WAIT или
      временный timeout; без retry сообщение теряется бесследно.
    - Per-chat воркер гарантирует порядок доставки внутри одного чата и изолирует
      медленные чаты от быстрых.
    - Воркер ленив: стартует при первом вызове, самоостанавливается через 30 с простоя.
    """

    _MAX_RETRIES: int = 3
    _BASE_BACKOFF_SEC: float = 0.5

    def __init__(self) -> None:
        self._queues: dict[int, asyncio.Queue] = {}
        self._workers: dict[int, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, chat_id: int, coro_factory: Any) -> Any:
        """
        Ставит вызов Telegram API в очередь чата и ждёт результата.

        coro_factory — callable без аргументов, возвращающий корутину:
            lambda: client.send_message(chat_id, text)

        При FLOOD_WAIT или TimeoutError выполняет до _MAX_RETRIES попыток
        с экспоненциальным откатом. Остальные исключения пробрасываются.
        """
        queue = self._get_or_create_queue(chat_id)
        self._ensure_worker_running(chat_id)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        await queue.put((coro_factory, fut))
        return await fut

    async def stop_all(self) -> None:
        """Останавливает всех воркеров (вызывать при shutdown юзербота)."""
        for task in list(self._workers.values()):
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._workers.clear()
        self._queues.clear()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_or_create_queue(self, chat_id: int) -> asyncio.Queue:
        if chat_id not in self._queues:
            self._queues[chat_id] = asyncio.Queue()
        return self._queues[chat_id]

    def _ensure_worker_running(self, chat_id: int) -> None:
        task = self._workers.get(chat_id)
        if task is None or task.done():
            self._workers[chat_id] = asyncio.create_task(
                self._worker(chat_id), name=f"tg-send-{chat_id}"
            )

    async def _worker(self, chat_id: int) -> None:
        queue = self._queues.get(chat_id)
        if queue is None:
            return
        while True:
            try:
                coro_factory, fut = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                # Очередь пустовала 30 с — воркер самоостанавливается.
                self._workers.pop(chat_id, None)
                return

            result_exc: BaseException | None = None
            result_val: Any = None

            for attempt in range(self._MAX_RETRIES):
                try:
                    # B.7: global API rate limit. acquire() → sleep если
                    # aggregate rate превысил soft cap (default 20 req/s).
                    # Ставим ДО coro_factory чтобы retry тоже учитывались.
                    await telegram_rate_limiter.acquire(purpose="send_queue")
                    result_val = await coro_factory()
                    result_exc = None
                    break
                except Exception as exc:  # noqa: BLE001
                    err_upper = str(exc).upper()
                    is_flood = "FLOOD" in err_upper
                    is_timeout = isinstance(exc, (asyncio.TimeoutError, TimeoutError))
                    if (is_flood or is_timeout) and attempt < self._MAX_RETRIES - 1:
                        delay = self._BASE_BACKOFF_SEC * (2**attempt)
                        if is_flood:
                            m = re.search(r"A wait of (\d+) seconds", str(exc), re.I)
                            if m:
                                delay = max(delay, float(m.group(1)))
                        await asyncio.sleep(delay)
                        continue
                    result_exc = exc
                    break

            if not fut.done():
                if result_exc is not None:
                    fut.set_exception(result_exc)
                else:
                    fut.set_result(result_val)
            queue.task_done()


# Singleton — один на весь процесс юзербота.
_telegram_send_queue = _TelegramSendQueue()


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


class KraabUserbot(
    LLMTextProcessingMixin,
    RuntimeStatusMixin,
    VoiceProfileMixin,
    AccessControlMixin,
    LLMFlowMixin,
    BackgroundTasksMixin,
    SessionMixin,
):
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
    _tool_response_block_pattern = re.compile(r"(?is)<tool_response>.*?(?:<\|im_end\|>|$)")
    _llm_transport_tokens_pattern = re.compile(r"(?i)<\|[^|>]+?\|>|</?tool_response>")
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
    _agentic_scratchpad_line_pattern = re.compile(
        r"(?ix)^("
        r"ready\.?"
        r"|yes\.?"
        r"|let'?s\s+(?:go|execute)\.?"
        r"|\.\.\."
        r"|wait[,.!]?\s+(?:(?:i|we)(?:'ll| will))\s+"
        r"(?:check|verify|inspect|look|use|open|run|try|confirm|explain|answer|draft|respond)\b.*"
        r"|(?:(?:i|we)(?:'ll| will))\s+"
        r"(?:check|verify|inspect|look|use|open|run|try|confirm|explain|answer|draft|respond)\b.*"
        r")$"
    )
    _agentic_scratchpad_command_pattern = re.compile(
        r"(?i)^(?:which|pwd|ls|rg|grep|find|git|python(?:3)?|pytest|ffmpeg|say|opencode|codex|claude|pi)\b.*$"
    )
    _split_chunk_header_pattern = re.compile(r"(?i)^\[часть\s+\d+/\d+\]$")
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
        self._background_task_reaper_task: Optional[asyncio.Task] = None
        self._proactive_watch_task: Optional[asyncio.Task] = None
        self._error_digest_task: Optional[asyncio.Task] = None
        self._silence_schedule_task: Optional[asyncio.Task] = None
        self._swarm_team_clients: dict[str, Any] = {}  # team → Pyrogram Client
        self._session_recovery_lock = asyncio.Lock()
        self._client_lifecycle_lock = asyncio.Lock()
        self._telegram_restart_lock = asyncio.Lock()
        self._telegram_probe_failures = 0
        self._chat_processing_locks: dict[str, asyncio.Lock] = {}
        self._chat_background_tasks: dict[str, asyncio.Task] = {}
        self._batched_followup_message_ids: dict[str, dict[str, float]] = {}
        self._hidden_reasoning_traces: dict[str, dict[str, Any]] = {}
        self._session_workdir = config.BASE_DIR / "data" / "sessions"
        self._disclosure_sent_for_chat_ids: set[str] = set()
        # Время старта и счётчик обработанных сообщений за сессию (для !stats).
        self._session_start_time: float = time.time()
        self._session_messages_processed: int = 0
        # Runtime-состояние старта userbot для health/handoff и контролируемой деградации.
        self._startup_state = "initializing"
        self._startup_error_code = ""
        self._startup_error = ""
        # AFK-режим: in-memory состояние (сбрасывается при рестарте)
        self._afk_mode: bool = False
        self._afk_reason: str = ""
        self._afk_since: float = 0.0
        # Отслеживаем чаты, которым уже отправили автоответ (чтобы не спамить)
        self._afk_replied_chats: set[str] = set()
        self._recreate_client()

    # _get_session_dirs, _session_name, _primary_session_file, _inspect_session_file,
    # _primary_session_snapshot, _restore_primary_session_from_legacy,
    # _recreate_client -> SessionMixin (src/userbot/session.py)

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
                logger.error(
                    "command_error",
                    handler=handler.__name__,
                    error=str(e),
                    error_type=type(e).__name__,
                    exc_info=True,
                )
                safe_err = str(e).replace("`", "'")[:200]
                try:
                    await m.reply(f"Ошибка: {safe_err}" if safe_err else "Внутренняя ошибка.")
                except Exception:  # noqa: BLE001
                    pass
            finally:
                m.stop_propagation()

        # Регистрация командных оберток (Фаза 4.4: модульные хендлеры)
        @self.client.on_message(
            filters.command("status", prefixes=prefixes) & _make_command_filter("status"), group=-1
        )
        async def wrap_status(c, m):
            await run_cmd(handle_status, m)

        @self.client.on_message(
            filters.command("swarm", prefixes=prefixes) & _make_command_filter("swarm"), group=-1
        )
        async def wrap_swarm(c, m):
            await run_cmd(handle_swarm, m)

        @self.client.on_message(
            filters.command("model", prefixes=prefixes) & _make_command_filter("model"), group=-1
        )
        async def wrap_model(c, m):
            await run_cmd(handle_model, m)

        @self.client.on_message(
            filters.command("clear", prefixes=prefixes) & _make_command_filter("clear"), group=-1
        )
        async def wrap_clear(c, m):
            await run_cmd(handle_clear, m)

        @self.client.on_message(
            filters.command("config", prefixes=prefixes) & _make_command_filter("config"), group=-1
        )
        async def wrap_config(c, m):
            await run_cmd(handle_config, m)

        @self.client.on_message(
            filters.command("set", prefixes=prefixes) & _make_command_filter("set"), group=-1
        )
        async def wrap_set(c, m):
            await run_cmd(handle_set, m)

        @self.client.on_message(
            filters.command("role", prefixes=prefixes) & _make_command_filter("role"), group=-1
        )
        async def wrap_role(c, m):
            await run_cmd(handle_role, m)

        @self.client.on_message(
            filters.command("voice", prefixes=prefixes) & _make_command_filter("voice"), group=-1
        )
        async def wrap_voice(c, m):
            await run_cmd(handle_voice, m)

        @self.client.on_message(
            filters.command("notify", prefixes=prefixes) & _make_command_filter("notify"), group=-1
        )
        async def wrap_notify(c, m):
            await run_cmd(handle_notify, m)

        @self.client.on_message(
            filters.command("chatban", prefixes=prefixes) & _make_command_filter("chatban"),
            group=-1,
        )
        async def wrap_chatban(c, m):
            await run_cmd(handle_chatban, m)

        @self.client.on_message(
            filters.command("blocked", prefixes=prefixes) & _make_command_filter("blocked"),
            group=-1,
        )
        async def wrap_blocked(c, m):
            await run_cmd(handle_blocked, m)

        @self.client.on_message(
            filters.command("chatmute", prefixes=prefixes) & _make_command_filter("chatmute"),
            group=-1,
        )
        async def wrap_chatmute(c, m):
            await run_cmd(handle_chatmute, m)

        @self.client.on_message(
            filters.command("translator", prefixes=prefixes) & _make_command_filter("translator"),
            group=-1,
        )
        async def wrap_translator(c, m):
            await run_cmd(handle_translator, m)

        @self.client.on_message(
            filters.command("web", prefixes=prefixes) & _make_command_filter("web"), group=-1
        )
        async def wrap_web(c, m):
            await run_cmd(handle_web, m)

        @self.client.on_message(
            filters.command("mac", prefixes=prefixes) & _make_command_filter("mac"), group=-1
        )
        async def wrap_mac(c, m):
            await run_cmd(handle_macos, m)

        @self.client.on_message(
            filters.command("screenshot", prefixes=prefixes) & _make_command_filter("screenshot"),
            group=-1,
        )
        async def wrap_screenshot(c, m):
            await run_cmd(handle_screenshot, m)

        @self.client.on_message(
            filters.command("cap", prefixes=prefixes) & _make_command_filter("cap"), group=-1
        )
        async def wrap_cap(c, m):
            await run_cmd(handle_cap, m)

        @self.client.on_message(
            filters.command("тишина", prefixes=prefixes) & _make_command_filter("тишина"),
            group=-1,
        )
        async def wrap_silence(c, m):
            await run_cmd(handle_silence, m)

        @self.client.on_message(
            filters.command("slowmode", prefixes=prefixes) & _make_command_filter("slowmode"),
            group=-1,
        )
        async def wrap_slowmode(c, m):
            await run_cmd(handle_slowmode, m)

        @self.client.on_message(
            filters.command("stats", prefixes=prefixes) & _make_command_filter("stats"),
            group=-1,
        )
        async def wrap_stats(c, m):
            await run_cmd(handle_stats, m)

        @self.client.on_message(
            filters.command("who", prefixes=prefixes) & _make_command_filter("who"), group=-1
        )
        async def wrap_who(c, m):
            await run_cmd(handle_who, m)

        @self.client.on_message(
            filters.command("costs", prefixes=prefixes) & _make_command_filter("costs"), group=-1
        )
        async def wrap_costs(c, m):
            await run_cmd(handle_costs, m)

        @self.client.on_message(
            filters.command("budget", prefixes=prefixes) & _make_command_filter("budget"), group=-1
        )
        async def wrap_budget(c, m):
            await run_cmd(handle_budget, m)

        @self.client.on_message(
            filters.command("digest", prefixes=prefixes) & _make_command_filter("digest"), group=-1
        )
        async def wrap_digest(c, m):
            await run_cmd(handle_digest, m)

        @self.client.on_message(
            filters.command("report", prefixes=prefixes) & _make_command_filter("report"), group=-1
        )
        async def wrap_report(c, m):
            await run_cmd(handle_report, m)

        @self.client.on_message(
            filters.command("watch", prefixes=prefixes) & _make_command_filter("watch"), group=-1
        )
        async def wrap_watch(c, m):
            await run_cmd(handle_watch, m)

        @self.client.on_message(
            filters.command("memory", prefixes=prefixes) & _make_command_filter("memory"), group=-1
        )
        async def wrap_memory(c, m):
            await run_cmd(handle_memory, m)

        @self.client.on_message(
            filters.command("inbox", prefixes=prefixes) & _make_command_filter("inbox"), group=-1
        )
        async def wrap_inbox(c, m):
            await run_cmd(handle_inbox, m)

        @self.client.on_message(
            filters.command("sysinfo", prefixes=prefixes) & _make_command_filter("sysinfo"),
            group=-1,
        )
        async def wrap_sysinfo(c, m):
            await run_cmd(handle_sysinfo, m)

        @self.client.on_message(
            filters.command("uptime", prefixes=prefixes) & _make_command_filter("uptime"),
            group=-1,
        )
        async def wrap_uptime(c, m):
            await run_cmd(handle_uptime, m)

        @self.client.on_message(
            filters.command("panel", prefixes=prefixes) & _make_command_filter("panel"), group=-1
        )
        async def wrap_panel(c, m):
            await run_cmd(handle_panel, m)

        @self.client.on_message(
            filters.command("restart", prefixes=prefixes) & _make_command_filter("restart"),
            group=-1,
        )
        async def wrap_restart(c, m):
            await run_cmd(handle_restart, m)

        @self.client.on_message(
            filters.command("search", prefixes=prefixes) & _make_command_filter("search"), group=-1
        )
        async def wrap_search(c, m):
            await run_cmd(handle_search, m)

        @self.client.on_message(
            filters.command("top", prefixes=prefixes) & _make_command_filter("top"), group=-1
        )
        async def wrap_top(c, m):
            await run_cmd(handle_top, m)

        @self.client.on_message(
            filters.command("weather", prefixes=prefixes) & _make_command_filter("weather"),
            group=-1,
        )
        async def wrap_weather(c, m):
            await run_cmd(handle_weather, m)

        @self.client.on_message(
            filters.command("grep", prefixes=prefixes) & _make_command_filter("grep"), group=-1
        )
        async def wrap_grep(c, m):
            await run_cmd(handle_grep, m)

        @self.client.on_message(
            filters.command("ask", prefixes=prefixes) & _make_command_filter("ask"), group=-1
        )
        async def wrap_ask(c, m):
            await run_cmd(handle_ask, m)

        @self.client.on_message(
            filters.command("yt", prefixes=prefixes) & _make_command_filter("yt"), group=-1
        )
        async def wrap_yt(c, m):
            await run_cmd(handle_yt, m)

        @self.client.on_message(
            filters.command("time", prefixes=prefixes) & _make_command_filter("time"), group=-1
        )
        async def wrap_time(c, m):
            await run_cmd(handle_time, m)

        @self.client.on_message(
            filters.command("shop", prefixes=prefixes) & _make_command_filter("shop"), group=-1
        )
        async def wrap_shop(c, m):
            await run_cmd(handle_shop, m)

        @self.client.on_message(
            filters.command("memo", prefixes=prefixes) & _make_command_filter("memo"), group=-1
        )
        async def wrap_memo(c, m):
            await run_cmd(handle_memo, m)

        @self.client.on_message(
            filters.command("note", prefixes=prefixes) & _make_command_filter("note"), group=-1
        )
        async def wrap_note(c, m):
            await run_cmd(handle_note, m)

        @self.client.on_message(
            filters.command(["bookmark", "bm"], prefixes=prefixes)
            & _make_command_filter("bookmark"),
            group=-1,
        )
        async def wrap_bookmark(c, m):
            await run_cmd(handle_bookmark, m)

        @self.client.on_message(
            filters.command("remember", prefixes=prefixes) & _make_command_filter("remember"),
            group=-1,
        )
        async def wrap_remember(c, m):
            await run_cmd(handle_remember, m)

        @self.client.on_message(
            filters.command("recall", prefixes=prefixes) & _make_command_filter("recall"), group=-1
        )
        async def wrap_recall(c, m):
            await run_cmd(handle_recall, m)

        @self.client.on_message(
            filters.command("todo", prefixes=prefixes) & _make_command_filter("todo"), group=-1
        )
        async def wrap_todo(c, m):
            await run_cmd(handle_todo, m)

        @self.client.on_message(
            filters.command("ls", prefixes=prefixes) & _make_command_filter("ls"), group=-1
        )
        async def wrap_ls(c, m):
            await run_cmd(handle_ls, m)

        @self.client.on_message(
            filters.command("read", prefixes=prefixes) & _make_command_filter("read"), group=-1
        )
        async def wrap_read(c, m):
            await run_cmd(handle_read, m)

        @self.client.on_message(
            filters.command("write", prefixes=prefixes) & _make_command_filter("write"), group=-1
        )
        async def wrap_write(c, m):
            await run_cmd(handle_write, m)

        @self.client.on_message(
            filters.command("paste", prefixes=prefixes) & _make_command_filter("paste"), group=-1
        )
        async def wrap_paste(c, m):
            await run_cmd(handle_paste, m)

        @self.client.on_message(
            filters.command("log", prefixes=prefixes) & _make_command_filter("log"), group=-1
        )
        async def wrap_log(c, m):
            await run_cmd(handle_log, m)

        @self.client.on_message(
            filters.command("agent", prefixes=prefixes) & _make_command_filter("agent"), group=-1
        )
        async def wrap_agent(c, m):
            await run_cmd(handle_agent, m)

        # CLI runner команды
        @self.client.on_message(
            filters.command("codex", prefixes=prefixes) & _make_command_filter("codex"), group=-1
        )
        async def wrap_codex(c, m):
            await run_cmd(handle_codex, m)

        @self.client.on_message(
            filters.command("gemini", prefixes=prefixes) & _make_command_filter("gemini"), group=-1
        )
        async def wrap_gemini_cli(c, m):
            await run_cmd(handle_gemini_cli, m)

        @self.client.on_message(
            filters.command("claude_cli", prefixes=prefixes) & _make_command_filter("claude_cli"),
            group=-1,
        )
        async def wrap_claude_cli(c, m):
            await run_cmd(handle_claude_cli, m)

        @self.client.on_message(
            filters.command("opencode", prefixes=prefixes) & _make_command_filter("opencode"),
            group=-1,
        )
        async def wrap_opencode(c, m):
            await run_cmd(handle_opencode, m)

        @self.client.on_message(
            filters.command("hs", prefixes=prefixes) & _make_command_filter("hs"), group=-1
        )
        async def wrap_hs(c, m):
            await run_cmd(handle_hs, m)

        @self.client.on_message(
            filters.command("acl", prefixes=prefixes) & _make_command_filter("acl"), group=-1
        )
        async def wrap_acl(c, m):
            await run_cmd(handle_acl, m)

        @self.client.on_message(
            filters.command("access", prefixes=prefixes) & _make_command_filter("access"), group=-1
        )
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
            filters.command("diagnose", prefixes=prefixes) & _make_command_filter("diagnose"),
            group=-1,
        )
        async def wrap_diagnose(c, m):
            await run_cmd(handle_diagnose, m)

        @self.client.on_message(
            filters.command("health", prefixes=prefixes) & _make_command_filter("health"),
            group=-1,
        )
        async def wrap_health(c, m):
            await run_cmd(handle_health, m)

        @self.client.on_message(
            filters.command("context", prefixes=prefixes) & _make_command_filter("context"),
            group=-1,
        )
        async def wrap_context(c, m):
            await run_cmd(handle_context, m)

        @self.client.on_message(
            filters.command("pin", prefixes=prefixes) & _make_command_filter("pin"), group=-1
        )
        async def wrap_pin(c, m):
            await run_cmd(handle_pin, m)

        @self.client.on_message(
            filters.command("unpin", prefixes=prefixes) & _make_command_filter("unpin"), group=-1
        )
        async def wrap_unpin(c, m):
            await run_cmd(handle_unpin, m)

        # Пометка чатов как прочитанных/непрочитанных
        @self.client.on_message(
            filters.command("mark", prefixes=prefixes) & _make_command_filter("mark"), group=-1
        )
        async def wrap_mark(c, m):
            await run_cmd(handle_mark, m)

        @self.client.on_message(
            filters.command("archive", prefixes=prefixes) & _make_command_filter("archive"),
            group=-1,
        )
        async def wrap_archive(c, m):
            await run_cmd(handle_archive, m)

        @self.client.on_message(
            filters.command("unarchive", prefixes=prefixes) & _make_command_filter("unarchive"),
            group=-1,
        )
        async def wrap_unarchive(c, m):
            await run_cmd(handle_unarchive, m)

        @self.client.on_message(
            filters.command("fwd", prefixes=prefixes) & _make_command_filter("fwd"), group=-1
        )
        async def wrap_fwd(c, m):
            await run_cmd(handle_fwd, m)

        # Приглашение пользователей в группу и управление invite link
        @self.client.on_message(
            filters.command("invite", prefixes=prefixes) & _make_command_filter("invite"),
            group=-1,
        )
        async def wrap_invite(c, m):
            await run_cmd(handle_invite, m)

        @self.client.on_message(
            filters.command("collect", prefixes=prefixes) & _make_command_filter("collect"),
            group=-1,
        )
        async def wrap_collect(c, m):
            await run_cmd(handle_collect, m)

        @self.client.on_message(
            filters.command("help", prefixes=prefixes) & _make_command_filter("help"), group=-1
        )
        async def wrap_help(c, m):
            await run_cmd(handle_help, m)

        @self.client.on_message(
            filters.command("remind", prefixes=prefixes) & _make_command_filter("remind"), group=-1
        )
        async def wrap_remind(c, m):
            await run_cmd(handle_remind, m)

        @self.client.on_message(
            filters.command("reminders", prefixes=prefixes) & _make_command_filter("reminders"),
            group=-1,
        )
        async def wrap_reminders(c, m):
            await run_cmd(handle_reminders, m)

        @self.client.on_message(
            filters.command("rm_remind", prefixes=prefixes) & _make_command_filter("rm_remind"),
            group=-1,
        )
        async def wrap_rm_remind(c, m):
            await run_cmd(handle_rm_remind, m)

        @self.client.on_message(
            filters.command("cronstatus", prefixes=prefixes) & _make_command_filter("cronstatus"),
            group=-1,
        )
        async def wrap_cronstatus(c, m):
            await run_cmd(handle_cronstatus, m)

        @self.client.on_message(
            filters.command("schedule", prefixes=prefixes) & _make_command_filter("schedule"),
            group=-1,
        )
        async def wrap_schedule(c, m):
            await run_cmd(handle_schedule, m)

        @self.client.on_message(
            filters.command("browser", prefixes=prefixes) & _make_command_filter("browser"),
            group=-1,
        )
        async def wrap_browser(c, m):
            await run_cmd(handle_browser, m)

        @self.client.on_message(
            filters.command("monitor", prefixes=prefixes) & _make_command_filter("monitor"),
            group=-1,
        )
        async def wrap_monitor(c, m):
            await run_cmd(handle_monitor, m)

        # Управление сообщениями: !del, !purge, !autodel
        @self.client.on_message(
            filters.command("del", prefixes=prefixes) & _make_command_filter("del"), group=-1
        )
        async def wrap_del(c, m):
            await run_cmd(handle_del, m)

        @self.client.on_message(
            filters.command("purge", prefixes=prefixes) & _make_command_filter("purge"), group=-1
        )
        async def wrap_purge(c, m):
            await run_cmd(handle_purge, m)

        @self.client.on_message(
            filters.command("autodel", prefixes=prefixes) & _make_command_filter("autodel"),
            group=-1,
        )
        async def wrap_autodel(c, m):
            await run_cmd(handle_autodel, m)

        @self.client.on_message(
            filters.command("summary", prefixes=prefixes) & _make_command_filter("summary"),
            group=-1,
        )
        async def wrap_summary(c, m):
            await run_cmd(handle_summary, m)

        @self.client.on_message(
            filters.command("catchup", prefixes=prefixes) & _make_command_filter("catchup"),
            group=-1,
        )
        async def wrap_catchup(c, m):
            await run_cmd(handle_catchup, m)

        @self.client.on_message(
            filters.command("translate", prefixes=prefixes) & _make_command_filter("translate"),
            group=-1,
        )
        async def wrap_translate(c, m):
            await run_cmd(handle_translate, m)

        @self.client.on_message(
            filters.command("export", prefixes=prefixes) & _make_command_filter("export"),
            group=-1,
        )
        async def wrap_export(c, m):
            await run_cmd(handle_export, m)

        @self.client.on_message(
            filters.command("react", prefixes=prefixes) & _make_command_filter("react"),
            group=-1,
        )
        async def wrap_react(c, m):
            await run_cmd(handle_react, m)

        @self.client.on_message(
            filters.command("poll", prefixes=prefixes) & _make_command_filter("poll"),
            group=-1,
        )
        async def wrap_poll(c, m):
            await run_cmd(handle_poll, m)

        @self.client.on_message(
            filters.command("quiz", prefixes=prefixes) & _make_command_filter("quiz"),
            group=-1,
        )
        async def wrap_quiz(c, m):
            await run_cmd(handle_quiz, m)

        @self.client.on_message(
            filters.command("dice", prefixes=prefixes) & _make_command_filter("dice"),
            group=-1,
        )
        async def wrap_dice(c, m):
            await run_cmd(handle_dice, m)

        @self.client.on_message(
            filters.command("alias", prefixes=prefixes) & _make_command_filter("alias"),
            group=-1,
        )
        async def wrap_alias(c, m):
            await run_cmd(handle_alias, m)

        @self.client.on_message(
            filters.command("timer", prefixes=prefixes) & _make_command_filter("timer"),
            group=-1,
        )
        async def wrap_timer(c, m):
            await run_cmd(handle_timer, m)

        @self.client.on_message(
            filters.command("stopwatch", prefixes=prefixes) & _make_command_filter("stopwatch"),
            group=-1,
        )
        async def wrap_stopwatch(c, m):
            await run_cmd(handle_stopwatch, m)

        @self.client.on_message(
            filters.command("qr", prefixes=prefixes) & _make_command_filter("qr"), group=-1
        )
        async def wrap_qr(c, m):
            await run_cmd(handle_qr, m)

        @self.client.on_message(
            filters.command("hash", prefixes=prefixes) & _make_command_filter("hash"), group=-1
        )
        async def wrap_hash(c, m):
            await run_cmd(handle_hash, m)

        # Тестирование регулярных выражений: !regex <паттерн> <текст>
        @self.client.on_message(
            filters.command("regex", prefixes=prefixes) & _make_command_filter("regex"), group=-1
        )
        async def wrap_regex(c, m):
            await run_cmd(handle_regex, m)

        @self.client.on_message(
            filters.command("calc", prefixes=prefixes) & _make_command_filter("calc"), group=-1
        )
        async def wrap_calc(c, m):
            await run_cmd(handle_calc, m)

        @self.client.on_message(
            filters.command("b64", prefixes=prefixes) & _make_command_filter("b64"), group=-1
        )
        async def wrap_b64(c, m):
            await run_cmd(handle_b64, m)

        @self.client.on_message(
            filters.command("define", prefixes=prefixes) & _make_command_filter("define"), group=-1
        )
        async def wrap_define(c, m):
            await run_cmd(handle_define, m)

        @self.client.on_message(
            filters.command("urban", prefixes=prefixes) & _make_command_filter("urban"), group=-1
        )
        async def wrap_urban(c, m):
            await run_cmd(handle_urban, m)

        @self.client.on_message(
            filters.command("rand", prefixes=prefixes) & _make_command_filter("rand"), group=-1
        )
        async def wrap_rand(c, m):
            await run_cmd(handle_rand, m)

        @self.client.on_message(
            filters.command("quote", prefixes=prefixes) & _make_command_filter("quote"), group=-1
        )
        async def wrap_quote(c, m):
            await run_cmd(handle_quote, m)

        @self.client.on_message(
            filters.command("ip", prefixes=prefixes) & _make_command_filter("ip"), group=-1
        )
        async def wrap_ip(c, m):
            await run_cmd(handle_ip, m)

        @self.client.on_message(
            filters.command("dns", prefixes=prefixes) & _make_command_filter("dns"), group=-1
        )
        async def wrap_dns(c, m):
            await run_cmd(handle_dns, m)

        @self.client.on_message(
            filters.command("ping", prefixes=prefixes) & _make_command_filter("ping"), group=-1
        )
        async def wrap_ping(c, m):
            await run_cmd(handle_ping, m)

        @self.client.on_message(
            filters.command("currency", prefixes=prefixes) & _make_command_filter("currency"), group=-1
        )
        async def wrap_currency(c, m):
            await run_cmd(handle_currency, m)

        @self.client.on_message(
            filters.command("len", prefixes=prefixes) & _make_command_filter("len"), group=-1
        )
        async def wrap_len(c, m):
            await run_cmd(handle_len, m)

        @self.client.on_message(
            filters.command("count", prefixes=prefixes) & _make_command_filter("count"), group=-1
        )
        async def wrap_count(c, m):
            await run_cmd(handle_len, m)

        @self.client.on_message(
            filters.command("sticker", prefixes=prefixes) & _make_command_filter("sticker"), group=-1
        )
        async def wrap_sticker(c, m):
            await run_cmd(handle_sticker, m)

        @self.client.on_message(
            filters.command("sed", prefixes=prefixes) & _make_command_filter("sed"), group=-1
        )
        async def wrap_sed(c, m):
            await run_cmd(handle_sed, m)

        @self.client.on_message(
            filters.command("tts", prefixes=prefixes) & _make_command_filter("tts"), group=-1
        )
        async def wrap_tts(c, m):
            await run_cmd(handle_tts, m)

        @self.client.on_message(
            filters.command("img", prefixes=prefixes) & _make_command_filter("img"), group=-1
        )
        async def wrap_img(c, m):
            await run_cmd(handle_img, m)

        @self.client.on_message(
            filters.command("ocr", prefixes=prefixes) & _make_command_filter("ocr"), group=-1
        )
        async def wrap_ocr(c, m):
            await run_cmd(handle_ocr, m)

        @self.client.on_message(
            filters.command("media", prefixes=prefixes) & _make_command_filter("media"), group=-1
        )
        async def wrap_media(c, m):
            await run_cmd(handle_media, m)

        @self.client.on_message(
            filters.command("welcome", prefixes=prefixes) & _make_command_filter("welcome"),
            group=-1,
        )
        async def wrap_welcome(c, m):
            await run_cmd(handle_welcome, m)

        # Антиспам фильтр для групп
        @self.client.on_message(
            filters.command("spam", prefixes=prefixes) & _make_command_filter("spam"), group=-1
        )
        async def wrap_spam(c, m):
            await run_cmd(handle_spam, m)

        # Хранилище кодовых сниппетов
        @self.client.on_message(
            filters.command("snippet", prefixes=prefixes) & _make_command_filter("snippet"),
            group=-1,
        )
        async def wrap_snippet(c, m):
            await run_cmd(handle_snippet, m)

        # Теги на сообщения
        @self.client.on_message(
            filters.command("tag", prefixes=prefixes) & _make_command_filter("tag"),
            group=-1,
        )
        async def wrap_tag(c, m):
            await run_cmd(handle_tag, m)

        # Утилиты для ссылок: preview, expand, reply-анализ
        @self.client.on_message(
            filters.command("link", prefixes=prefixes) & _make_command_filter("link"),
            group=-1,
        )
        async def wrap_link(c, m):
            await run_cmd(handle_link, m)

        # Шаблоны сообщений с подстановкой переменных
        @self.client.on_message(
            filters.command("template", prefixes=prefixes) & _make_command_filter("template"),
            group=-1,
        )
        async def wrap_template(c, m):
            await run_cmd(handle_template, m)

        # Выполнение Python-кода (owner-only)
        @self.client.on_message(
            filters.command("run", prefixes=prefixes) & _make_command_filter("run"), group=-1
        )
        async def wrap_run(c, m):
            await run_cmd(handle_run, m)

        # Симуляция набора текста / записи голосового / загрузки
        @self.client.on_message(
            filters.command("typing", prefixes=prefixes) & _make_command_filter("typing"),
            group=-1,
        )
        async def wrap_typing(c, m):
            await run_cmd(handle_typing, m)

        # Автоприветствие новых участников группы
        @self.client.on_message(filters.new_chat_members, group=-1)
        async def wrap_new_chat_members(c, m):
            await handle_new_chat_members(self, m)

        # AFK-режим: !afk и !back
        @self.client.on_message(
            filters.command("afk", prefixes=prefixes) & _make_command_filter("afk"), group=-1
        )
        async def wrap_afk(c, m):
            await run_cmd(handle_afk, m)

        @self.client.on_message(
            filters.command("back", prefixes=prefixes) & _make_command_filter("back"), group=-1
        )
        async def wrap_back(c, m):
            await run_cmd(handle_afk, m)

        @self.client.on_message(
            filters.command("json", prefixes=prefixes) & _make_command_filter("json"), group=-1
        )
        async def wrap_json(c, m):
            await run_cmd(handle_json, m)

        @self.client.on_message(
            filters.command("chatinfo", prefixes=prefixes) & _make_command_filter("chatinfo"),
            group=-1,
        )
        async def wrap_chatinfo(c, m):
            await run_cmd(handle_chatinfo, m)

        # Управление контактами адресной книги
        @self.client.on_message(
            filters.command("contacts", prefixes=prefixes) & _make_command_filter("contacts"),
            group=-1,
        )
        async def wrap_contacts(c, m):
            await run_cmd(handle_contacts, m)

        @self.client.on_message(
            filters.command("history", prefixes=prefixes) & _make_command_filter("history"),
            group=-1,
        )
        async def wrap_history(c, m):
            await run_cmd(handle_history, m)

        @self.client.on_message(
            filters.command("profile", prefixes=prefixes) & _make_command_filter("profile"),
            group=-1,
        )
        async def wrap_profile(c, m):
            await run_cmd(handle_profile, m)

        # Управление участниками группы (kick/ban/unban/list)
        @self.client.on_message(
            filters.command("members", prefixes=prefixes) & _make_command_filter("members"),
            group=-1,
        )
        async def wrap_members(c, m):
            await run_cmd(handle_members, m)

        # Хендлер для реакций других пользователей на сообщения Краба
        @self.client.on_message_reaction_updated()
        async def wrap_reaction_updated(c, reaction_update):
            await self._handle_message_reaction_updated(reaction_update)

        # Обработка callback query от inline-кнопок
        @self.client.on_callback_query()
        async def wrap_callback_query(c, cq):
            await self._handle_callback_query(cq)

        # Обработка обычных сообщений, медиа, голосовых и документов.
        # Voice/audio проходят в _process_message → _transcribe_audio_message
        # (устаревший wrap_audio с stop_propagation() удалён — он блокировал AI pipeline).
        @self.client.on_message(
            (filters.text | filters.photo | filters.voice | filters.audio | filters.document)
            & ~filters.bot,
            group=0,
        )
        async def wrap_message(c, m):
            await self._process_message(m)

    # _is_sqlite_io_error, _start_client_serialized, _safe_stop_client,
    # _arm_client_session_shutdown_guard, _cancel_client_restart_tasks -> SessionMixin (src/userbot/session.py)

    # _cancel_background_task -> BackgroundTasksMixin (src/userbot/background_tasks.py)

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

    def _mark_transport_degraded(self, *, reason: str, error: str) -> None:
        """
        Помечает Telegram transport деградированным для health/lite и внешнего watchdog.

        Почему это нужно:
        - broken socket может долго жить с ложным `running`, если не обновить runtime-state;
        - внешний watchdog читает `/api/health/lite` и должен увидеть, что transport сломан;
        - `degraded` мягче, чем `login_required`, и не притворяется ручным relogin.
        """
        current_state = str(self._startup_state or "").strip().lower()
        if current_state in {"stopped", "stopping", "login_required"}:
            return
        self._set_startup_state(
            state="degraded",
            error_code="telegram_transport_degraded",
            error=error,
        )
        logger.warning("telegram_transport_marked_degraded", reason=reason, error=error)

    def _restore_running_state_after_probe(self) -> None:
        """
        Возвращает transport в `running`, если heartbeat снова healthy.
        """
        if str(self._startup_state or "").strip().lower() == "degraded":
            self._set_startup_state(state="running")
            logger.info("telegram_transport_probe_recovered")

    async def _handle_callback_query(self, callback_query) -> None:
        """
        Роутер входящих callback query от inline-кнопок.

        Схемы prefix:
          confirm:<action_id>:yes|no  — подтверждение/отказ
          page:<prefix>:<page>        — пагинация
          action:<action_id>          — произвольное действие
        """
        cq = callback_query
        data: str = cq.data or ""
        try:
            if data.startswith("confirm:"):
                await self._cb_confirm(cq, data)
            elif data.startswith("page:"):
                await self._cb_page(cq, data)
            elif data.startswith("action:"):
                await self._cb_action(cq, data)
            else:
                await cq.answer("⚠️ Неизвестное действие")
        except Exception as exc:  # noqa: BLE001
            logger.warning("callback_query_error", data=data, error=str(exc))
            try:
                await cq.answer("❌ Ошибка обработки")
            except Exception:
                pass

    async def _cb_confirm(self, cq, data: str) -> None:
        """Обработка confirm:<action_id>:yes|no."""
        parts = data.split(":", 2)
        if len(parts) < 3:
            await cq.answer("⚠️ Некорректный формат")
            return
        action_id = parts[1]
        choice = parts[2]
        if choice == "yes":
            await cq.answer("✅ Подтверждено")
            await cq.message.reply(f"✅ Действие `{action_id}` подтверждено.")
        else:
            await cq.answer("❌ Отменено")
            await cq.message.reply(f"❌ Действие `{action_id}` отменено.")

    async def _cb_page(self, cq, data: str) -> None:
        """Обработка page:<prefix>:<page>."""
        parts = data.split(":", 2)
        if len(parts) < 3:
            await cq.answer("⚠️ Некорректный формат")
            return
        page_str = parts[2]
        if page_str == "noop":
            await cq.answer()
            return
        try:
            page = int(page_str)
        except ValueError:
            await cq.answer("⚠️ Некорректный номер страницы")
            return
        await cq.answer(f"Страница {page + 1}")

    async def _cb_action(self, cq, data: str) -> None:
        """
        Обработка action:<action_id>.

        Известные action_id:
          swarm_team:<team>  — подсказка по запуску swarm для команды
          costs_detail       — подробная разбивка по моделям
          health_recheck     — повторный health check
        """
        action = data[len("action:"):]
        if action.startswith("swarm_team:"):
            team = action[len("swarm_team:"):]
            await cq.answer(f"🐝 {team}")
            await cq.message.reply(
                f"🐝 Используй команду:\n`!swarm {team} <тема>`\n\n"
                f"Например: `!swarm {team} анализ текущей ситуации`"
            )
        elif action == "costs_detail":
            await cq.answer("📊 Загружаю детали…")
            from .core.cost_analytics import cost_analytics
            report = cost_analytics.build_usage_report_dict()
            by_model: dict = report.get("by_model", {})
            if not by_model:
                await cq.message.reply("ℹ️ Данных по моделям пока нет.")
                return
            lines = ["📊 **Детализация по моделям:**"]
            for mid, d in sorted(by_model.items(), key=lambda x: -x[1].get("cost_usd", 0)):
                calls = d.get("calls", 0)
                tokens = d.get("tokens", 0)
                cost = d.get("cost_usd", 0)
                lines.append(f"• `{mid}`: ${cost:.4f} | {calls} calls | {tokens} tokens")
            await cq.message.reply("\n".join(lines))
        elif action == "health_recheck":
            await cq.answer("🔄 Перепроверяю…")
            await cq.message.reply(
                "🔄 Запускаю повторный health check…\n"
                "Используй `!health` для полного отчёта."
            )
        else:
            await cq.answer(f"⚠️ Неизвестный action: {action}")

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
            # Для периодического auto-export нам нужен быстрый truthful snapshot,
            # а не тяжёлый cloud runtime probe, который может жить дольше maintenance-таймаута.
            handoff_url = "http://127.0.0.1:8080/api/runtime/handoff?probe_cloud_runtime=0"
            req = urllib.request.Request(handoff_url, method="GET")
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
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
        Отправляет watch-alert в Saved Messages владельца через userbot.
        Fallback: если userbot offline — пробует reserve bot (Phase 2.1).
        """
        clean_text = str(text or "").strip()
        if self.client and self.client.is_connected:
            for part in self._split_message(clean_text):
                await self.client.send_message("me", part)
            return
        # userbot недоступен — пробуем reserve bot
        if reserve_bot.is_running:
            logger.info("proactive_watch_alert_via_reserve_bot")
            await reserve_bot.send_to_owner(f"[reserve] {clean_text}")
            return
        raise RuntimeError("telegram_client_not_ready")

    def _ensure_silence_schedule_started(self) -> None:
        """Запускает фоновый loop проверки расписания ночного режима."""
        if self._silence_schedule_task and not self._silence_schedule_task.done():
            return

        def _apply_mute() -> None:
            silence_manager.mute_global(minutes=480)  # максимум 8 часов запас

        def _remove_mute() -> None:
            silence_manager.unmute_global()

        self._silence_schedule_task = asyncio.create_task(
            silence_schedule_manager.run_loop(_apply_mute, _remove_mute)
        )

    def _ensure_proactive_watch_started(self) -> None:
        """Запускает фоновый proactive watch, если он включён конфигом."""
        if not bool(getattr(config, "PROACTIVE_WATCH_ENABLED", False)):
            return
        if self._proactive_watch_task and not self._proactive_watch_task.done():
            return
        self._proactive_watch_task = asyncio.create_task(self._run_proactive_watch_loop())
        # Запускаем периодическую сводку ошибок (каждые 6 часов)
        if self._error_digest_task is None or self._error_digest_task.done():
            self._error_digest_task = proactive_watch.start_error_digest_loop()
        # WeeklyDigest: подключаем Telegram delivery callback + запускаем loop
        try:
            from .core.weekly_digest import weekly_digest  # noqa: PLC0415

            weekly_digest.set_telegram_callback(self._send_proactive_watch_alert)
            wdt = getattr(self, "_weekly_digest_task", None)
            if wdt is None or wdt.done():
                self._weekly_digest_task = weekly_digest.start_weekly_digest_loop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("weekly_digest_setup_failed", error=str(exc))

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

            # Swarm scheduler — рекуррентные автономные прогоны
            if config.SWARM_AUTONOMOUS_ENABLED and self.me:
                owner_chat_id = str(self.me.id)
                system_prompt = self._build_system_prompt_for_sender(
                    is_allowed_sender=True,
                    access_level="owner",
                )

                def _swarm_router_factory(team_name: str):
                    from .handlers.command_handlers import _AgentRoomRouterAdapter

                    return _AgentRoomRouterAdapter(
                        chat_id=f"swarm:scheduled:{team_name}",
                        system_prompt=system_prompt,
                    )

                swarm_scheduler.bind(
                    sender=self._send_scheduled_message,
                    router_factory=_swarm_router_factory,
                    owner_chat_id=owner_chat_id,
                )
                if not swarm_scheduler._started:
                    swarm_scheduler.start()
                    logger.info("swarm_scheduler_runtime_started")

            # Swarm channels — live broadcast в Telegram-группы
            if self.me and self.client:
                swarm_channels.bind(client=self.client, owner_id=self.me.id)
                logger.info("swarm_channels_bound", teams=list(swarm_channels.get_all_team_chats()))
            return

        if krab_scheduler.is_started:
            krab_scheduler.stop()
            logger.info(
                "scheduler_runtime_stopped",
                scheduler_enabled=scheduler_enabled,
                client_connected=client_connected,
            )

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

    # ------------------------------------------------------------------
    # Translator MVP — voice note translation pipeline
    # ------------------------------------------------------------------

    def _is_translator_active_for_chat(self, chat_id: int | str) -> bool:
        """Проверяет, активна ли translator сессия для данного чата."""
        state = self.get_translator_session_state()
        if state.get("session_status") != "active":
            return False
        if state.get("translation_muted"):
            return False
        active_chats = state.get("active_chats") or []
        if not active_chats:
            # Если active_chats пуст — translator активен для ВСЕХ чатов owner'а
            return True
        return str(chat_id) in [str(c) for c in active_chats]

    async def _handle_translator_voice(
        self,
        message: Any,
        transcript: str,
        chat_id: int | str,
    ) -> bool:
        """
        Переводит транскрипт voice note и отправляет результат.

        Возвращает True если перевод выполнен, False если нужно идти в обычный LLM.
        """
        from .core.language_detect import detect_language, resolve_translation_pair  # noqa: PLC0415
        from .core.translator_engine import translate_text  # noqa: PLC0415

        profile = self.get_translator_runtime_profile()
        detected = detect_language(transcript)
        if not detected:
            return False  # не удалось определить язык → обычный LLM

        language_pair = str(profile.get("language_pair") or "es-ru")
        src_lang, tgt_lang = resolve_translation_pair(detected, language_pair)
        if src_lang == tgt_lang:
            return False  # язык совпадает, переводить нечего

        try:
            result = await translate_text(
                transcript,
                src_lang,
                tgt_lang,
                openclaw_client=openclaw_client,
                chat_id=f"translator_{chat_id}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "translator_voice_failed",
                chat_id=str(chat_id),
                error=str(exc),
            )
            return False  # fallback к обычному LLM

        if not result.translated:
            return False

        # Формируем ответ
        reply_text = f"🔄 {src_lang}→{tgt_lang}\n**{result.original}**\n_{result.translated}_"
        await self._safe_reply_or_send_new(message, reply_text)

        # Обновляем session stats и добавляем запись в history
        try:
            state = self.get_translator_session_state()
            stats = state.get("stats") or {"total_translations": 0, "total_latency_ms": 0}
            # Добавляем запись в историю переводов
            updated_state = append_translator_history_entry(
                state,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                original=transcript[:300],
                translation=result.translated[:300],
                latency_ms=result.latency_ms,
            )
            self.update_translator_session_state(
                last_language_pair=f"{src_lang}-{tgt_lang}",
                last_translated_original=transcript[:200],
                last_translated_translation=result.translated[:200],
                last_event="translation_completed",
                history=updated_state["history"],
                stats={
                    "total_translations": stats.get("total_translations", 0) + 1,
                    "total_latency_ms": stats.get("total_latency_ms", 0) + result.latency_ms,
                },
            )
        except Exception:  # noqa: BLE001
            pass  # stats update не должен ломать pipeline

        logger.info(
            "translator_voice_completed",
            chat_id=str(chat_id),
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            latency_ms=result.latency_ms,
            model=result.model_id,
        )
        return True

    async def start(self):
        """Запуск юзербота"""
        self._set_startup_state(state="starting")
        logger.info("starting_userbot")
        # Persisted chat ban cache (B.8) + chat capability cache (B.6).
        # Оба persist путь в ~/.openclaw/krab_runtime_state/, совпадая с
        # swarm_channels.json / inbox_state.json / krab_main.log. Конфигурируется
        # здесь а не в __init__ чтобы любой re-start подхватывал актуальный
        # state с диска (нужно на случай ручного редактирования файла или на
        # случай если cache был очищен между рестартами).
        try:
            _runtime_state_dir = Path(
                os.environ.get("KRAB_RUNTIME_STATE_DIR")
                or str(Path.home() / ".openclaw" / "krab_runtime_state")
            ).expanduser()
            chat_ban_cache.configure_default_path(_runtime_state_dir / "chat_ban_cache.json")
            chat_capability_cache.configure_default_path(
                _runtime_state_dir / "chat_capability_cache.json"
            )
        except Exception as _exc:  # noqa: BLE001
            # silent-failure-hunter review (B.7): raised from warning → error.
            # Если configure упал, singleton остался in-memory only, persist
            # вообще не произойдёт, chat ban cache не переживёт рестарт.
            # Это silent degradation — owner должен видеть это как ERROR
            # чтобы сразу заметить broken state, а не warning в потоке.
            logger.error(
                "chat_state_caches_bootstrap_failed",
                error=str(_exc),
                error_type=type(_exc).__name__,
            )

        # B.7: global Telegram API rate limiter. Default 20 req/s, конфигурируется
        # через env TELEGRAM_GLOBAL_RATE_MAX_PER_SEC. Это soft cap — лимитер НЕ
        # отменяет вызовы, а замедляет (await asyncio.sleep). Нужно чтобы не
        # триггерить SpamBot auto-review за частый polling/sending.
        try:
            _rate_max = int(
                os.environ.get("TELEGRAM_GLOBAL_RATE_MAX_PER_SEC")
                or getattr(config, "TELEGRAM_GLOBAL_RATE_MAX_PER_SEC", 20)
            )
            telegram_rate_limiter.configure(max_per_sec=max(1, _rate_max), window_sec=1.0)
        except Exception as _exc:  # noqa: BLE001
            logger.warning("telegram_rate_limiter_bootstrap_failed", error=str(_exc))
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
                        logger.debug(
                            "telegram_client_stop_after_dblock_failed", error=str(stop_exc)
                        )
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
                        logger.debug(
                            "telegram_stop_after_login_required_failed", error=str(stop_exc)
                        )
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
            # Wait for OpenClaw to spin up (up to 180s)
            # После рестарта gateway через LaunchAgent crash-loop стабилизация занимает 3-5 мин.
            # 180 сек = достаточный запас; wait_for_healthy возвращает True как только /health OK.
            logger.info("waiting_for_openclaw")
            is_claw_ready = await openclaw_client.wait_for_healthy(timeout=180)

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
        self._background_task_reaper_task = asyncio.create_task(self._background_task_reaper())
        self._ensure_proactive_watch_started()
        self._ensure_silence_schedule_started()

        # Reserve bot (Phase 2.1) — запускаем после userbot, не блокируем старт при ошибке
        try:
            await reserve_bot.start()
        except Exception as exc:  # noqa: BLE001
            logger.warning("reserve_bot_start_error", error=str(exc))

        # Per-team swarm clients — отдельные TG аккаунты для каждой команды (background)
        asyncio.create_task(self._init_swarm_team_clients())

    # _is_auth_key_invalid -> SessionMixin (src/userbot/session.py)

    # -- per-team swarm clients ------------------------------------------------

    async def _start_swarm_team_clients(self) -> dict[str, Any]:
        """Создаёт и стартует Pyrogram Clients для per-team аккаунтов свёрма."""
        accounts = config.load_swarm_team_accounts()
        if not accounts:
            return {}

        started: dict[str, Any] = {}
        for team, acct in accounts.items():
            session_name = acct.get("session_name", f"swarm_{team}")
            try:
                # Очистка stale SQLite lock (database is locked)
                _sess_path = Path(self._session_workdir) / f"{session_name}.session"
                if _sess_path.exists():
                    _journal = _sess_path.with_suffix(".session-journal")
                    _wal = _sess_path.with_suffix(".session-wal")
                    for _lockf in (_journal, _wal):
                        if _lockf.exists():
                            try:
                                _lockf.unlink()
                                logger.info(
                                    "swarm_stale_lock_cleaned",
                                    team=team, file=str(_lockf),
                                )
                            except OSError:
                                pass
                cl = Client(
                    session_name,
                    api_id=config.TELEGRAM_API_ID,
                    api_hash=config.TELEGRAM_API_HASH,
                    workdir=str(self._session_workdir),
                )
                await asyncio.wait_for(cl.start(), timeout=15)
                me = await cl.get_me()
                started[team.lower()] = cl
                logger.info(
                    "swarm_team_client_started",
                    team=team,
                    session=session_name,
                    username=getattr(me, "username", None),
                    user_id=getattr(me, "id", None),
                )
                # Warm-up peer cache: get_dialogs загружает все чаты включая недавно
                # добавленные группы (иначе send_message → CHAT_ID_INVALID).
                try:
                    async for _ in cl.get_dialogs(limit=50):
                        pass
                    logger.info("swarm_team_client_warmed_up", team=team)
                except Exception as warm_exc:  # noqa: BLE001
                    logger.warning(
                        "swarm_team_client_warmup_failed",
                        team=team,
                        error=str(warm_exc),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "swarm_team_client_start_failed",
                    team=team,
                    session=session_name,
                    error=repr(exc),
                )
        return started

    async def _stop_swarm_team_clients(self) -> None:
        """Останавливает все per-team swarm clients.

        Безопасно вызывать даже если `_init_swarm_team_clients` не отработал
        (например, в тестовых фикстурах или при раннем сбое старта).
        """
        clients = getattr(self, "_swarm_team_clients", None)
        if not clients:
            return
        for team, cl in list(clients.items()):
            try:
                if cl.is_connected:
                    await cl.stop()
                logger.info("swarm_team_client_stopped", team=team)
            except Exception as exc:  # noqa: BLE001
                logger.warning("swarm_team_client_stop_failed", team=team, error=str(exc))
        clients.clear()

    async def _init_swarm_team_clients(self) -> None:
        """Background init per-team swarm clients (не блокирует основной бот)."""
        try:
            self._swarm_team_clients = await self._start_swarm_team_clients()
            for team, cl in self._swarm_team_clients.items():
                swarm_channels.bind_team_client(team, cl)
            # Регистрируем message handlers для team listener
            if self._swarm_team_clients:
                from .core.swarm_team_listener import register_team_message_handler  # noqa: PLC0415

                for team, cl in self._swarm_team_clients.items():
                    register_team_message_handler(team, cl, openclaw_client)
            if self._swarm_team_clients:
                logger.info(
                    "swarm_team_clients_ready",
                    teams=list(self._swarm_team_clients.keys()),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("swarm_team_clients_init_failed", error=repr(exc))

    # _recover_telegram_session -> SessionMixin (src/userbot/session.py)

    async def restart(self, *, reason: str = "manual_restart") -> None:
        """
        Сериализованный restart Telegram userbot как одна операция.

        Почему нужен отдельный метод:
        - `stop()` и `start()` по отдельности оставляют окно для конкурирующих probe/recovery;
        - web endpoint и watchdog должны использовать один и тот же restart-lock;
        - это минимальный способ убрать гонку без глобального рефакторинга transport-слоя.
        """
        async with self._telegram_restart_lock:
            logger.info("telegram_restart_started", reason=reason)
            await self.stop()
            # После transport-сбоя Pyrogram может оставить внутренние restart-task'и
            # на старом Client. Для чистого restart поднимаем новый экземпляр.
            self._recreate_client()
            await self.start()
            logger.info("telegram_restart_finished", reason=reason)

    # _telegram_session_watchdog, _purge_telegram_session_files, _is_db_locked_error,
    # _cleanup_telegram_session_locks, _session_file_exists -> SessionMixin (src/userbot/session.py)

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

        # Reserve bot (Phase 2.1) — останавливаем до userbot
        try:
            await reserve_bot.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("reserve_bot_stop_error", error=str(exc))

        # Auto-export handoff snapshot before shutdown (Phase 2.2)
        try:
            await self._auto_export_handoff_snapshot(reason="userbot_stop")
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto_handoff_export_failed", error=str(exc), non_fatal=True)

        # Phase 2: pre-shutdown memory flush — persist swarm state
        try:
            from .core.swarm_memory import swarm_memory as _sm  # noqa: PLC0415

            _sm._persist()
            logger.info("pre_shutdown_swarm_memory_flushed")
        except Exception:  # noqa: BLE001
            pass
        try:
            from .core.swarm_task_board import swarm_task_board as _tb  # noqa: PLC0415

            _tb._persist()
            logger.info("pre_shutdown_task_board_flushed")
        except Exception:  # noqa: BLE001
            pass

        if krab_scheduler.is_started:
            try:
                krab_scheduler.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("scheduler_stop_failed", error=str(exc), non_fatal=True)
        if swarm_scheduler._started:
            try:
                swarm_scheduler.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("swarm_scheduler_stop_failed", error=str(exc), non_fatal=True)
        await self._cancel_background_task("_telegram_watchdog_task")
        await self._cancel_background_task("_background_task_reaper_task")
        await self._cancel_background_task("_proactive_watch_task")
        await self._cancel_background_task("_silence_schedule_task")
        # Per-team swarm clients — остановить до основного клиента
        await self._stop_swarm_team_clients()
        try:
            await self._safe_stop_client(reason="runtime_stop")
        except Exception as exc:  # noqa: BLE001
            logger.warning("telegram_stop_failed", error=str(exc), non_fatal=True)
        await model_manager.close()
        await close_search()
        try:
            await _telegram_send_queue.stop_all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("send_queue_stop_failed", error=str(exc), non_fatal=True)
        self._telegram_probe_failures = 0
        self._set_startup_state(state="stopped")

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

    async def _deliver_response_parts(
        self,
        *,
        source_message: Message,
        temp_message: Message,
        is_self: bool,
        query: str,
        full_response: str,
        prefer_send_message_for_background: bool = False,
        force_new_message: bool = False,
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
                    "text_message_ids": [str(getattr(updated, "id", "") or "")]
                    if getattr(updated, "id", None)
                    else [],
                    "parts_count": 1,
                }
            updated = await self._safe_edit(temp_message, placeholder)
            return {
                "delivery_mode": "placeholder_only",
                "text_message_ids": [str(getattr(updated, "id", "") or "")]
                if getattr(updated, "id", None)
                else [],
                "parts_count": 1,
            }

        parts = self._split_message(f"🦀 {query}\n\n{full_response}" if is_self else full_response)
        delivered_ids: list[str] = []

        if is_self and not force_new_message:
            source_message = await self._safe_edit(source_message, parts[0])
            if getattr(source_message, "id", None):
                delivered_ids.append(str(source_message.id))
            for part in parts[1:]:
                sent = await self._safe_reply_or_send_new(source_message, part)
                if getattr(sent, "id", None):
                    delivered_ids.append(str(sent.id))
            self._maybe_schedule_autodel(source_message.chat.id, delivered_ids)
            return {
                "delivery_mode": "edit_and_reply",
                "text_message_ids": delivered_ids,
                "parts_count": len(parts),
            }

        if (
            self._should_send_voice_reply()
            or prefer_send_message_for_background
            or force_new_message
        ):
            # Для связки `text+voice` делаем явную текстовую отправку отдельным
            # сообщением: edit плейсхолдера в некоторых клиентах теряется
            # визуально, а send_message даёт надёжный финальный event доставки.
            # В background-handoff это ещё и разрывает зависимость от старого
            # placeholder-сообщения, которое могло уже устареть к моменту ответа.
            _cid = source_message.chat.id
            for _part in parts:
                _p = _part  # захват переменной для lambda
                sent = await _telegram_send_queue.run(
                    _cid, lambda: self.client.send_message(_cid, _p)
                )
                if getattr(sent, "id", None):
                    delivered_ids.append(str(sent.id))
            try:
                delete_coro = getattr(temp_message, "delete", None)
                if callable(delete_coro):
                    await delete_coro()
            except Exception:
                pass
            self._maybe_schedule_autodel(source_message.chat.id, delivered_ids)
            return {
                "delivery_mode": "send_message",
                "text_message_ids": delivered_ids,
                "parts_count": len(parts),
            }

        temp_message = await self._safe_edit(temp_message, parts[0])
        if getattr(temp_message, "id", None):
            delivered_ids.append(str(temp_message.id))
        for part in parts[1:]:
            sent = await self._safe_reply_or_send_new(source_message, part)
            if getattr(sent, "id", None):
                delivered_ids.append(str(sent.id))
        result = {
            "delivery_mode": "edit_and_reply",
            "text_message_ids": delivered_ids,
            "parts_count": len(parts),
        }
        self._maybe_schedule_autodel(source_message.chat.id, delivered_ids)
        return result

    def _maybe_schedule_autodel(self, chat_id: int, delivered_ids: list[str]) -> None:
        """
        Если для чата включено autodel — планирует удаление доставленных сообщений.
        """
        from .handlers.command_handlers import get_autodel_delay, schedule_autodel

        delay = get_autodel_delay(self, chat_id)
        if not delay or not delivered_ids:
            return
        for msg_id_str in delivered_ids:
            try:
                msg_id = int(msg_id_str)
            except (ValueError, TypeError):
                continue
            schedule_autodel(self.client, chat_id, msg_id, delay)

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
            delivery_mode=str((delivery_result or {}).get("delivery_mode") or "text")
            .strip()
            .lower()
            or "text",
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

    def _acknowledge_open_relay_requests_for_chat(
        self,
        *,
        chat_id: str,
        actor: str = "kraab",
        note: str = "owner_followed_up_after_relay",
    ) -> dict[str, Any]:
        """
        Закрывает открытые relay_request для чата, если владелец уже вернулся в диалог.

        Почему это нужно:
        - relay item создаётся как owner-visible напоминание о том, что в чате был
          запрос "передай/сообщи";
        - если затем owner уже пишет в этот же чат, старый relay долг больше не
          отражает реальное состояние и начинает захламлять inbox summary;
        - закрываем только open/acked relay_request с совпадающим `chat_id`.
        """
        normalized_chat_id = str(chat_id or "").strip()
        if not normalized_chat_id:
            return {"ok": False, "skipped": True, "reason": "chat_id_missing"}

        matched_item_ids: list[str] = []
        for item in inbox_service.list_items(status="open", kind="relay_request", limit=100):
            metadata = item.get("metadata") or {}
            if str(metadata.get("chat_id") or "").strip() != normalized_chat_id:
                continue
            item_id = str(item.get("item_id") or "").strip()
            if item_id:
                matched_item_ids.append(item_id)

        if not matched_item_ids:
            return {"ok": True, "updated_count": 0, "item_ids": []}

        result = inbox_service.bulk_update_status(
            item_ids=matched_item_ids,
            status="done",
            actor=actor,
            note=note,
        )
        result["updated_count"] = int(result.get("success_count") or 0)
        result["item_ids"] = matched_item_ids
        return result

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
        result = inbox_service.upsert_incoming_owner_request(
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
        try:
            self._acknowledge_open_relay_requests_for_chat(
                chat_id=str(getattr(chat_obj, "id", "") or ""),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "relay_request_auto_ack_failed",
                chat_id=str(getattr(chat_obj, "id", "") or ""),
                error=str(exc),
            )
        return result

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
        sender_display = (
            f"@{user.username}"
            if getattr(user, "username", None)
            else f"id:{getattr(user, 'id', '?')}"
        )
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
            sent_message = await self.client.send_message(me.id, notification)
            try:
                inbox_service.record_relay_delivery(
                    chat_id=chat_id_str,
                    message_id=message_id_str,
                    notification_text=notification,
                    delivery_mode="saved_messages",
                    delivered_to_chat_id=str(getattr(me, "id", "") or ""),
                    relay_message_ids=[str(getattr(sent_message, "id", "") or "")],
                    actor="kraab",
                    note="relay_owner_notified",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("relay_inbox_resolution_failed", error=str(exc))
            logger.info(
                "relay_owner_notified",
                sender=sender_display,
                chat_id=chat_id_str,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("relay_owner_notification_failed", error=str(exc))

    async def _forward_guest_incoming_to_owner(
        self,
        *,
        message: Message,
        query: str,
        krab_response: str,
    ) -> None:
        """
        Форвардит входящее сообщение от незнакомого контакта (GUEST) owner-у.

        Почему нужно: аптека пишет 'препараты приехали' → Краб отвечает от лица
        пользователя, но owner не знает об этом. Это решает проблему пропущенных
        входящих от незнакомых контактов.
        """
        try:
            user = getattr(message, "from_user", None) or getattr(message, "sender_chat", None)
            fname = str(getattr(user, "first_name", "") or "").strip()
            lname = str(getattr(user, "last_name", "") or "").strip()
            username = str(getattr(user, "username", "") or "").strip()
            sender_name = f"{fname} {lname}".strip() or ""
            if username:
                sender_name = (
                    f"{sender_name} (@{username})".strip() if sender_name else f"@{username}"
                )
            if not sender_name:
                sender_name = f"id:{getattr(user, 'id', '?')}"

            chat_id_str = str(getattr(getattr(message, "chat", None), "id", "") or "")
            excerpt = str(query or "")[:1500]
            response_excerpt = str(krab_response or "")[:400]

            notification = (
                f"📩 **Незнакомый контакт написал**\n\n"
                f"От: `{sender_name}`\n"
                f"Чат: `{chat_id_str}`\n\n"
                f"**Сообщение:**\n{excerpt}\n\n"
                f"↩️ **Краб ответил:**\n{response_excerpt}"
            )
            me = await self.client.get_me()
            await self.client.send_message(me.id, notification)
            logger.info(
                "guest_incoming_forwarded_to_owner",
                sender=sender_name,
                chat_id=chat_id_str,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("guest_incoming_forward_failed", error=str(exc))

    # Расширения, которые обрабатываем как plain-text (встраиваем содержимое в запрос).
    _TEXT_EXTENSIONS: frozenset[str] = frozenset(
        {
            ".txt",
            ".md",
            ".py",
            ".js",
            ".ts",
            ".jsx",
            ".tsx",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".ini",
            ".cfg",
            ".sh",
            ".bash",
            ".zsh",
            ".log",
            ".csv",
            ".xml",
            ".html",
            ".css",
            ".scss",
            ".sql",
            ".rs",
            ".go",
            ".java",
            ".kt",
            ".swift",
            ".c",
            ".cpp",
            ".h",
            ".hpp",
            ".rb",
            ".php",
            ".env",
            ".conf",
        }
    )
    # Максимальный размер файла и инлайн-вставки.
    _DOC_MAX_BYTES: int = 5 * 1024 * 1024  # 5 MB — не скачиваем больше
    _DOC_INLINE_BYTES: int = 80 * 1024  # 80 KB — встраиваем содержимое текстом

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

    @staticmethod
    def _is_message_too_long_error(exc: Exception) -> bool:
        """Определяет ошибку Telegram при превышении лимита длины сообщения (4096 chars)."""
        return "MESSAGE_TOO_LONG" in str(exc).upper()

    async def _send_message_reaction(self, message: Message, emoji: str) -> None:
        """
        Ставит реакцию на сообщение через pyrofork send_reaction.

        Молча игнорирует ошибки — не все чаты/типы сообщений поддерживают реакции
        (каналы без реакций, анонимные группы, старые клиенты и т.д.).
        Не ставит реакцию если TELEGRAM_REACTIONS_ENABLED=False.
        """
        if not bool(getattr(config, "TELEGRAM_REACTIONS_ENABLED", True)):
            return
        chat_id_int = int(getattr(getattr(message, "chat", None), "id", 0) or 0)
        message_id_int = int(getattr(message, "id", 0) or 0)
        if not chat_id_int or not message_id_int:
            return
        try:
            await self.client.send_reaction(
                chat_id=chat_id_int,
                message_id=message_id_int,
                emoji=emoji,
            )
        except Exception:  # noqa: BLE001
            pass  # реакции — best-effort, не прерываем основной flow

    async def _handle_message_reaction_updated(self, reaction_update: Any) -> None:
        """
        Обрабатывает обновление реакции пользователя на сообщение.

        Логирует реакции как feedback и передаёт в ReactionEngine для накопления статистики.
        Полезно: 👍/❤️ = пользователь доволен ответом, 👎 = недоволен.
        """
        try:
            # Извлекаем поля из MessageReactionUpdated
            chat = getattr(reaction_update, "chat", None)
            from_user = getattr(reaction_update, "from_user", None)
            message_id = int(getattr(reaction_update, "id", 0) or 0)
            chat_id = int(getattr(chat, "id", 0) or 0) if chat else 0
            user_id = int(getattr(from_user, "id", 0) or 0) if from_user else None

            if not chat_id or not message_id:
                return

            # Список Reaction объектов
            new_reactions = list(getattr(reaction_update, "new_reaction", None) or [])
            old_reactions = list(getattr(reaction_update, "old_reaction", None) or [])

            def _extract_emojis(reactions: list) -> list[str]:
                """Извлекает emoji-строки из объектов Reaction."""
                result = []
                for r in reactions:
                    emoji = getattr(r, "emoji", None) or getattr(r, "emoticon", None)
                    if emoji:
                        result.append(str(emoji))
                return result

            new_emojis = _extract_emojis(new_reactions)
            old_emojis = _extract_emojis(old_reactions)

            # Добавленные реакции (не было в old, появились в new)
            added = [e for e in new_emojis if e not in old_emojis]
            removed = [e for e in old_emojis if e not in new_emojis]

            if not added and not removed:
                return

            logger.info(
                "reaction_updated",
                chat_id=chat_id,
                message_id=message_id,
                user_id=user_id,
                added=added,
                removed=removed,
            )

            # Передаём в ReactionEngine для накопления feedback
            try:
                from .core.reaction_engine import reaction_engine  # noqa: PLC0415
                reaction_engine.record_reaction(
                    chat_id=chat_id,
                    message_id=message_id,
                    user_id=user_id,
                    new_emojis=new_emojis,
                    old_emojis=old_emojis,
                )
            except Exception as eng_exc:  # noqa: BLE001
                logger.warning("reaction_engine_record_failed", error=str(eng_exc))

        except Exception:  # noqa: BLE001
            logger.exception("handle_message_reaction_updated_error")

    async def _send_monitor_alert(self, message: Message, matched_keyword: str) -> None:
        """Отправляет alert owner'у в Saved Messages при совпадении keyword в мониторимом чате."""
        try:
            if not self.me:
                return
            # Информация об отправителе
            sender = message.from_user
            sender_name = (
                getattr(sender, "username", None)
                or getattr(sender, "first_name", None)
                or str(getattr(sender, "id", "?"))
            ) if sender else "Unknown"
            # Название чата
            chat_title = (
                getattr(message.chat, "title", None)
                or getattr(message.chat, "first_name", None)
                or str(message.chat.id)
            )
            # Текст сообщения (обрезаем длинные)
            msg_text = (message.text or "").strip()
            if len(msg_text) > 800:
                msg_text = msg_text[:797] + "..."
            alert = (
                f"\U0001f514 **Monitor Alert**\n"
                f"Chat: {chat_title} (`{message.chat.id}`)\n"
                f"From: @{sender_name}\n"
                f"Keyword: `{matched_keyword}`\n"
                f"\u2500\u2500\u2500\u2500\u2500\n"
                f"{msg_text}"
            )
            await self.client.send_message(self.me.id, alert)
            logger.info(
                "monitor_alert_sent",
                chat_id=str(message.chat.id),
                keyword=matched_keyword,
                sender=sender_name,
            )
        except Exception as exc:
            logger.warning("monitor_alert_error", error=str(exc))

    async def _safe_edit(self, msg: Message, text: str) -> Message:
        """
        Безопасно редактирует сообщение через _telegram_send_queue (с retry).
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
        chat_id: int = msg.chat.id
        _text = target_text  # захват для lambda
        try:
            edited = await _telegram_send_queue.run(chat_id, lambda: msg.edit(_text))
            return edited or msg
        except Exception as exc:  # noqa: BLE001 - фильтруем MESSAGE_NOT_MODIFIED
            if self._is_message_not_modified_error(exc):
                return msg
            if self._is_message_id_invalid_error(exc) or self._is_message_empty_error(exc):
                logger.warning("telegram_edit_fallback_send_new", error=str(exc))
                return await _telegram_send_queue.run(
                    chat_id, lambda: self.client.send_message(chat_id, _text)
                )
            if self._is_message_too_long_error(exc):
                # Текст превысил лимит Telegram (4096). Отрезаем и отправляем новым сообщением.
                logger.warning("telegram_edit_too_long_fallback_send_new", error=str(exc))
                _truncated = _text[:4000]
                return await _telegram_send_queue.run(
                    chat_id, lambda: self.client.send_message(chat_id, _truncated)
                )
            raise

    async def _safe_reply_or_send_new(self, msg: Message, text: str) -> Message:
        """
        Безопасно отвечает на сообщение через reply с fallback на send_message.

        Это защищает private owner-path от silent-drop, когда Telegram принимает
        обычную отправку в чат, но валит именно reply на конкретный message id.
        Оба вызова идут через _telegram_send_queue (с retry при FLOOD_WAIT/timeout).
        """
        target_text = (text or "").strip() or "…"
        chat_id: int = msg.chat.id
        _text = target_text  # захват для lambda
        try:
            sent = await _telegram_send_queue.run(chat_id, lambda: msg.reply(_text))
            return sent or msg
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "telegram_reply_fallback_send_new",
                chat_id=str(chat_id),
                message_id=str(getattr(msg, "id", "") or ""),
                error=str(exc),
            )
            return await _telegram_send_queue.run(
                chat_id, lambda: self.client.send_message(chat_id, _text)
            )

    # _get_chat_processing_lock -> BackgroundTasksMixin (src/userbot/background_tasks.py)

    @staticmethod
    def _extract_message_text(message: Message | Any) -> str:
        """Возвращает текст или подпись сообщения единым способом."""
        return str(getattr(message, "text", None) or getattr(message, "caption", None) or "")

    @staticmethod
    def _is_command_like_text(text: str) -> bool:
        """Определяет служебные команды, которые нельзя склеивать с обычным текстом."""
        normalized = str(text or "").lstrip()
        return normalized[:1] in {"!", "/", "."}

    # _keep_typing_alive, _send_delivery_chat_action, _mark_incoming_item_background_started,
    # _log_background_task_exception_cb, _register_chat_background_task,
    # _get_active_chat_background_task, _background_task_reaper
    # -> BackgroundTasksMixin (src/userbot/background_tasks.py)

    # _run_llm_request_flow, _finish_ai_request_background,
    # _finish_ai_request_background_after_previous, _build_background_handoff_notice
    # перенесены в src/userbot/llm_flow.py -> LLMFlowMixin

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
        from .core.command_aliases import alias_service as _alias_svc  # noqa: PLC0415

        text = message.text or message.caption or ""
        has_audio_message = self._message_has_audio(message)

        # Разрешаем алиасы ПЕРЕД routing: !t привет → !translate привет
        if text and text.lstrip()[:1] in ("!", "/", "."):
            resolved = _alias_svc.resolve(text)
            if resolved != text:
                # Подменяем текст сообщения — Pyrogram допускает это до обработки
                message.text = resolved  # type: ignore[assignment]
                text = resolved

        if text and text.lstrip()[:1] in ("!", "/", "."):
            cmd_word = text.lstrip().split()[0].lstrip("!/.").lower()
            if cmd_word in self._known_commands:
                if not access_profile.can_execute_command(cmd_word, self._known_commands):
                    await self._safe_reply_or_send_new(
                        message,
                        self._build_command_access_denied_text(cmd_word, access_profile),
                    )
                return

        has_document = bool(getattr(message, "document", None))
        if not text and not message.photo and not has_audio_message and not has_document:
            return

        # Счётчик обработанных сообщений за сессию (для !stats).
        self._session_messages_processed += 1

        runtime_chat_id = self._build_runtime_chat_scope_id(
            chat_id=chat_id,
            user_id=int(user.id),
            is_allowed_sender=is_allowed_sender,
            access_level=access_profile.level,
        )
        is_self = user.id == self.me.id

        # Phase 3: capability enforcement — проверяем право на chat
        if not is_self:
            from .core.capability_registry import check_capability  # noqa: PLC0415

            access_level_str = str(
                getattr(access_profile.level, "value", access_profile.level) or "guest"
            )
            if not check_capability(access_level_str, "chat"):
                logger.info("capability_denied_chat", chat_id=chat_id, level=access_level_str)
                return
        # Антиспам: проверяем только в группах для не-себя
        if not is_self and message.chat.type not in (
            enums.ChatType.PRIVATE,
            enums.ChatType.BOT,
        ):
            from .core.spam_guard import classify_message as _classify_spam  # noqa: PLC0415
            from .core.spam_guard import is_enabled as _spam_enabled  # noqa: PLC0415

            if _spam_enabled(message.chat.id):
                _spam_reason = _classify_spam(
                    message.chat.id,
                    int(user.id),
                    message,
                )
                if _spam_reason:
                    await apply_spam_action(self, message, _spam_reason)
                    return

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

        # Silence check: если чат заглушён — не обрабатывать AI-запросы.
        # Команды (! / .) обработаны выше и уже return'нули.
        if not is_self and silence_manager.is_silenced(chat_id):
            logger.info("silence_mode_skip", chat_id=chat_id)
            return

        query = self._get_clean_text(text)
        if not query and has_audio_message:
            query, voice_error = await self._transcribe_audio_message(message)
            if not query:
                await self._safe_reply_or_send_new(
                    message,
                    voice_error or "❌ Не удалось распознать голосовое сообщение.",
                )
                return
            # Translator MVP: если сессия активна для этого чата — переводим вместо LLM
            if query and self._is_translator_active_for_chat(chat_id):
                handled = await self._handle_translator_voice(message, query, chat_id)
                if handled:
                    return
        elif query and not message.photo and not has_audio_message:
            message, query = await self._coalesce_text_burst(
                message=message,
                user=user,
                query=query,
            )
            text = query
        if (
            not query
            and not message.photo
            and not has_audio_message
            and not is_reply_to_me
            and not has_document
        ):
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

        # Фильтр спама: shortcodes, рассылки, OTP, scam/fake + ручной blocklist.
        if not is_self and (
            self._is_notification_sender(user)
            or _is_bulk_sender_ext(user)
            or self._is_manually_blocked(user)
        ):
            _block_reason = (
                "manual_blocklist"
                if self._is_manually_blocked(user)
                else "bulk_sender"
                if _is_bulk_sender_ext(user)
                else "notification_sender"
            )
            logger.info("auto_reply_skipped_blocked_sender", chat_id=chat_id, reason=_block_reason)
            if bool(getattr(config, "FORWARD_UNKNOWN_INCOMING", True)):
                asyncio.create_task(
                    self._forward_guest_incoming_to_owner(
                        message=message,
                        query=query or text or "",
                        krab_response="[автоответ пропущен: уведомление/заблокированный отправитель]",
                    )
                )
            return

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

        # Реакция "видит" — owner сразу понимает что Краб получил сообщение.
        # Только для owner-сообщений (is_self=True или is_allowed_sender+is_self check).
        if is_self:
            asyncio.create_task(self._send_message_reaction(message, "👀"))

        _ai_request_start_ts = time.time()
        logger.info(
            "processing_ai_request",
            chat_id=chat_id,
            user=user.username,
            has_photo=bool(message.photo),
            has_audio=bool(has_audio_message),
        )
        action = enums.ChatAction.TYPING
        _typing_stop_event = asyncio.Event()
        _typing_task = asyncio.create_task(
            self._keep_typing_alive(self.client, message.chat.id, action, _typing_stop_event)
        )
        # Переключение ролей
        if has_trigger and any(p in text.lower() for p in ["стань", "будь", "как"]):
            for role in ROLES:
                if role in text.lower():
                    self.current_role = role
                    await self._safe_reply_or_send_new(
                        message,
                        f"🎭 **Режим изменен:** `{role}`. Слушаю.",
                    )
                    _typing_stop_event.set()
                    _typing_task.cancel()
                    await asyncio.gather(_typing_task, return_exceptions=True)
                    return

        temp_msg = message
        # Формируем информативный ack с моделью и маршрутом
        _ack_model = ""
        try:
            from .userbot.llm_flow import _current_runtime_primary_model  # noqa: PLC0415
            _ack_model = _current_runtime_primary_model() or ""
        except Exception:
            pass
        _ack_model_hint = f"\nТекущий маршрут: `{_ack_model}`" if _ack_model else ""
        _ack_text = (
            f"🦀 Принял запрос.\n\n"
            f"🛠️ Собираю контекст и запускаю маршрут...{_ack_model_hint}"
        )
        if not is_self:
            try:
                temp_msg = await asyncio.wait_for(
                    self._safe_reply_or_send_new(message, _ack_text),
                    timeout=10.0,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("initial_request_ack_failed", chat_id=chat_id, error=str(exc))
                try:
                    temp_msg = await self.client.send_message(
                        message.chat.id, _ack_text,
                    )
                except Exception as send_exc:  # noqa: BLE001
                    logger.warning(
                        "initial_request_ack_send_fallback_failed",
                        chat_id=chat_id,
                        error=str(send_exc),
                    )
                    temp_msg = message
        else:
            message = await self._safe_edit(
                message,
                f"🦀 {query}\n\n🛠️ Собираю контекст...{_ack_model_hint}",
            )

        if self._looks_like_runtime_truth_question(query) or self._looks_like_model_status_question(
            query
        ):
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
                    message = await self._safe_edit(
                        message, f"🦀 {query}\n\n👀 *Разглядываю фото...*"
                    )
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
            safe_error = (
                photo_error or "❌ Фото не удалось обработать. Отправь изображение повторно."
            )
            if is_self:
                message = await self._safe_edit(message, f"🦀 {safe_query}\n\n{safe_error}")
                delivery_result = {
                    "delivery_mode": "edit_error",
                    "text_message_ids": [str(getattr(message, "id", "") or "")]
                    if getattr(message, "id", None)
                    else [],
                    "parts_count": 1,
                }
            else:
                temp_msg = await self._safe_edit(temp_msg, safe_error)
                delivery_result = {
                    "delivery_mode": "edit_error",
                    "text_message_ids": [str(getattr(temp_msg, "id", "") or "")]
                    if getattr(temp_msg, "id", None)
                    else [],
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
            query = await self._process_document_message(
                message=message, query=query, temp_msg=temp_msg, is_self=is_self
            )
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
            and not is_self
            and not bool(images)
            and not bool(has_audio_message)
            and not bool(message.photo)
            and not has_document
        )
        if should_defer_background:
            active_background_task = self._get_active_chat_background_task(chat_id)
            try:
                handoff_notice = self._build_background_handoff_notice(query)
                if active_background_task is not None:
                    handoff_notice = (
                        f"{handoff_notice}\n\n"
                        "🧵 В этом чате ещё завершается предыдущая фоновая задача. "
                        "Новый запрос поставлен сразу за ней."
                    )
                await self._safe_edit(temp_msg, handoff_notice)
            except Exception:
                pass
            self._mark_incoming_item_background_started(
                incoming_item_result=incoming_item_result,
                note="background_processing_started",
            )
            background_kwargs = {
                "message": message,
                "temp_msg": temp_msg,
                "is_self": is_self,
                "query": query,
                "chat_id": chat_id,
                "runtime_chat_id": runtime_chat_id,
                "access_profile": access_profile,
                "is_allowed_sender": is_allowed_sender,
                "incoming_item_result": incoming_item_result,
                "images": images,
                "force_cloud": force_cloud,
                "system_prompt": system_prompt,
                "action_stop_event": _typing_stop_event,
                "action_task": _typing_task,
            }
            if active_background_task is not None:
                background_task = asyncio.create_task(
                    self._finish_ai_request_background_after_previous(
                        previous_task=active_background_task,
                        **background_kwargs,
                    )
                )
            else:
                background_task = asyncio.create_task(
                    self._finish_ai_request_background(**background_kwargs)
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

            # Swarm intervention: если owner пишет в swarm-группу — перехватываем
            if self.me and user.id == self.me.id and message.chat and message.text:
                swarm_team = swarm_channels.is_swarm_chat(message.chat.id)
                if swarm_team:
                    # Forum mode: определяем команду по topic_id
                    if swarm_team == "_forum":
                        topic_id = getattr(message, "message_thread_id", None) or getattr(
                            message, "reply_to_top_message_id", None
                        )
                        if topic_id:
                            swarm_team = swarm_channels.resolve_team_from_topic(topic_id)
                    if (
                        swarm_team
                        and swarm_team != "_forum"
                        and swarm_channels.is_round_active(swarm_team)
                    ):
                        swarm_channels.add_intervention(swarm_team, message.text)
                        await message.reply(f"👑 Директива принята для **{swarm_team}**")
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

            # B.8 chat ban cache: если этот чат уже помечен как persistently
            # забаненный (USER_BANNED_IN_CHANNEL / ChatWriteForbidden etc.),
            # то Краб вообще не должен гонять LLM и не должен пытаться писать
            # туда. Owner-override command `!chatban clear <chat_id>` снимет
            # отметку; автоматически она истечёт по cooldown (default 6ч).
            # ВАЖНО: check идёт ДО lock acquisition и owner auto-silence,
            # чтобы не блокировать per-chat lock на каждое входящее от
            # забаненного канала.
            #
            # Исключение: owner (self) должен иметь возможность писать в
            # заблокированный чат команды — например `!chatban clear`, чтобы
            # снять отметку. Поэтому self + command = пропускаем guard.
            _is_self_for_guard = bool(self.me and user.id == self.me.id)
            _raw_text_for_guard = (message.text or "").strip()
            _is_command_for_guard = (
                _raw_text_for_guard[:1] in ("!", "/", ".") if _raw_text_for_guard else False
            )
            if (
                chat_id
                and not (_is_self_for_guard and _is_command_for_guard)
                and chat_ban_cache.is_banned(chat_id)
            ):
                logger.info(
                    "chat_ban_cached_skip",
                    chat_id=chat_id,
                    reason="chat_in_ban_cache",
                    user=getattr(user, "username", None),
                )
                return

            # MONITOR: проверяем активные мониторинги чатов на ключевые слова.
            # Уникальная фича юзербота — видим ВСЕ сообщения во всех чатах.
            # Проверяем только чужие сообщения (не self).
            if not _is_self_for_guard and message.text and chat_id:
                from .core.chat_monitor import chat_monitor_service

                _matched_kw = chat_monitor_service.check_message(chat_id, message.text)
                if _matched_kw is not None:
                    asyncio.create_task(
                        self._send_monitor_alert(message=message, matched_keyword=_matched_kw)
                    )

            # B.6 chat capability cache: fire-and-forget refresh если в кеше
            # нет свежей записи. `_refresh_chat_capabilities_background` сам
            # внутри проверяет TTL и no-ops если данные свежие, так что
            # безопасно вызывать на каждом сообщении.
            #
            # B.7 fix (от silent-failure-hunter review): вешаем done_callback
            # на fire-and-forget task чтобы необработанные исключения внутри
            # корутины НЕ терялись с `Task exception was never retrieved` и
            # попадали в логи нормально. Также логгируем случай no-running-loop.
            if chat_id:
                try:
                    _cap_refresh_task = asyncio.create_task(
                        self._refresh_chat_capabilities_background(chat_id)
                    )
                    _cap_refresh_task.add_done_callback(self._log_background_task_exception_cb)
                except RuntimeError as _no_loop_exc:
                    # Нет running loop — маловероятно внутри pyrogram handler,
                    # но safe-guard против падения обработки. Явно логгируем
                    # чтобы не было silent no-op.
                    logger.debug(
                        "chat_capability_refresh_no_running_loop",
                        chat_id=chat_id,
                        error=str(_no_loop_exc),
                    )

            # Auto-silence: owner сам пишет в чат (не команду) → Краб молчит N мин
            is_self = self.me and user.id == self.me.id
            raw_text = (message.text or "").strip()
            is_command = raw_text[:1] in ("!", "/", ".") if raw_text else False
            if is_self and not is_command and chat_id:
                _auto_min = int(getattr(config, "OWNER_AUTO_SILENCE_MINUTES", 5))
                if _auto_min > 0:
                    silence_manager.auto_silence_owner_typing(chat_id, _auto_min)

            # AFK-режим: owner сам написал (не команду) → автовыключение AFK
            if self._afk_mode and is_self and not is_command:
                self._afk_mode = False
                self._afk_reason = ""
                self._afk_since = 0.0
                self._afk_replied_chats.clear()
                logger.info("afk_auto_disabled", reason="owner_sent_message")

            # AFK-режим: входящий DM от другого пользователя → автоответ (один раз на чат)
            if (
                self._afk_mode
                and not is_self
                and message.chat
                and getattr(message.chat, "type", None) is not None
                and str(message.chat.type).upper().endswith("PRIVATE")
                and chat_id not in self._afk_replied_chats
            ):
                _afk_elapsed = int(time.time() - self._afk_since)
                _afk_mins = _afk_elapsed // 60
                _afk_secs = _afk_elapsed % 60
                _afk_time_str = (
                    f"{_afk_mins} мин {_afk_secs} с" if _afk_mins else f"{_afk_secs} с"
                )
                _afk_reason_part = f"\n📝 Причина: {self._afk_reason}" if self._afk_reason else ""
                try:
                    await message.reply(
                        f"🌙 Я сейчас AFK (отсутствую {_afk_time_str}).{_afk_reason_part}\n"
                        f"Отвечу когда вернусь!"
                    )
                    self._afk_replied_chats.add(chat_id)
                except Exception as _afk_err:  # noqa: BLE001
                    logger.debug("afk_autoreply_failed", error=str(_afk_err))

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
            await self._safe_reply_or_send_new(message, e.user_message or str(e))
        except RouterError as e:
            logger.warning("routing_error", code=e.code, error=str(e))
            await self._safe_reply_or_send_new(message, user_message_for_surface(e, telegram=True))
        except Exception as e:
            if self._is_auth_key_invalid(e):
                logger.error("telegram_session_invalid_in_handler", error=str(e))
                await self._recover_telegram_session(reason=str(e))
                return
            # P0 fix: логируем полный traceback (раньше только str(e) — терялась причина)
            logger.error(
                "process_message_error",
                error=str(e),
                error_type=type(e).__name__,
                chat_id=str(getattr(message, "chat", None) and message.chat.id or "?"),
                exc_info=True,
            )
            # Безопасное форматирование — экранируем markdown спецсимволы в тексте ошибки
            safe_error = str(e).replace("`", "'").replace("*", "")[:200]
            try:
                await self._safe_reply_or_send_new(
                    message,
                    f"🦀❌ Ошибка: {safe_error}"
                    if safe_error
                    else "🦀❌ Внутренняя ошибка. Детали в логах.",
                )
            except Exception:  # noqa: BLE001
                pass  # reply сам может упасть (ChatWriteForbidden etc.)

    async def _run_self_test(self, message: Message):
        """Вызов внешнего теста здоровья"""
        await self._safe_reply_or_send_new(message, "🧪 Запуск теста...")
        proc = await asyncio.create_subprocess_exec(
            "python3",
            "tests/autonomous_test.py",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        asyncio.create_task(proc.wait())  # reap in background
        await self._safe_reply_or_send_new(
            message, "✅ Тест запущен в фоне. Проверьте `health_check.log`."
        )

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
