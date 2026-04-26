# -*- coding: utf-8 -*-
"""
Обработчики Telegram-команд, вынесенные из userbot_bridge (Фаза 4.4).
Каждая функция принимает (bot, message) для тестируемости и уплощения register_handlers.
"""

from __future__ import annotations

import ast as _ast
import asyncio
import datetime
import json
import math as _math
import os
import pathlib
import re
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any

import httpx
from pyrogram.types import Message

from ..cache_manager import history_cache, search_cache
from ..config import config
from ..core.access_control import (
    PARTIAL_ACCESS_COMMANDS,
    AccessLevel,
    get_effective_owner_label,
    load_acl_runtime_state,
    normalize_subject,
    update_acl_subject,
)
from ..core.chat_ban_cache import chat_ban_cache
from ..core.command_aliases import alias_service  # noqa: F401  # re-export
from ..core.command_blocklist import command_blocklist
from ..core.cost_analytics import cost_analytics
from ..core.exceptions import UserInputError
from ..core.inbox_service import inbox_service
from ..core.lm_studio_health import is_lm_studio_available
from ..core.logger import get_logger
from ..core.memory_validator import memory_validator
from ..core.model_aliases import normalize_model_alias
from ..core.openclaw_runtime_models import get_runtime_primary_model
from ..core.openclaw_workspace import (
    append_workspace_memory_entry,
    list_workspace_memory_entries,
)
from ..core.proactive_watch import proactive_watch
from ..core.scheduler import parse_due_time, split_reminder_input
from ..core.telegram_buttons import (
    build_costs_detail_buttons,
)
from ..core.weekly_digest import weekly_digest
from ..employee_templates import ROLES, list_roles
from ..integrations.hammerspoon_bridge import HammerspoonBridgeError, hammerspoon
from ..integrations.macos_automation import macos_automation
from ..mcp_client import mcp_manager
from ..memory_engine import memory_manager
from ..model_manager import model_manager
from ..openclaw_client import openclaw_client

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Phase 2 domain re-exports (Session 27): commands/text_utils.py
# Existing imports `from src.handlers.command_handlers import handle_X`
# remain valid — handlers and helpers are sourced from text_utils now.
# ---------------------------------------------------------------------------
from .commands.chat_commands import (  # noqa: E402, F401
    _WHOIS_FIELD_PATTERNS,
    _parse_whois_output,
    handle_chatinfo,
    handle_history,
    handle_monitor,
    handle_who,
    handle_whois,
)
from .commands.social_commands import (  # noqa: E402, F401
    _STICKERS_FILE,
    _load_stickers,
    _save_stickers,
    handle_alias,
    handle_del,
    handle_dice,
    handle_pin,
    handle_poll,
    handle_purge,
    handle_quiz,
    handle_react,
    handle_sticker,
    handle_unpin,
)
from .commands.text_utils import (  # noqa: E402, F401
    _b64_decode,
    _b64_encode,
    _b64_is_valid,
    _build_diff_output,
    _format_regex_result,
    _json_extract_text,
    _parse_sed_expr,
    handle_b64,
    handle_calc,
    handle_diff,
    handle_hash,
    handle_json,
    handle_len,
    handle_rand,
    handle_regex,
    handle_sed,
    safe_calc,
)

if TYPE_CHECKING:
    from ..userbot_bridge import KraabUserbot


# ---------------------------------------------------------------------------
# Утилита: тех-ответ только в ЛС владельца
# ---------------------------------------------------------------------------


async def _reply_tech(message: Message, bot: "KraabUserbot", text: str, **kwargs: Any) -> None:
    """Отправляет тех-ответ: в группе — редиректит в ЛС, в ЛС — обычный reply.

    Предназначена для команд с техническим выводом (логи, cron и т.п.),
    которые не должны «засорять» групповые чаты.
    """
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", 0) if chat is not None else 0
    if chat_id < 0:
        # Уведомление в группе
        try:
            await message.reply("📬 Ответ в ЛС (тех-команда).")
        except Exception:  # noqa: BLE001
            pass
        # Сам ответ — в Saved Messages
        try:
            await bot.client.send_message("me", text, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("tech_dm_redirect_failed", error=str(exc))
    else:
        await message.reply(text, **kwargs)


# ---------------------------------------------------------------------------
# Phase 2 Wave 3 (Session 27): scheduler_commands extraction
# ---------------------------------------------------------------------------
# State (_active_timers, _stopwatches), helpers (_parse_duration, _fmt_duration)
# и handlers (!timer, !stopwatch, !remind, !cron, !schedule, !autodel, !todo)
# вынесены в commands/scheduler_commands.py. Re-exported для обратной
# совместимости (тесты, _AgentRoomRouterAdapter, handle_debug читает
# _active_timers из этого модуля).
# ---------------------------------------------------------------------------
# Phase 2 Wave 5 (Session 27): memory_commands extraction
# ---------------------------------------------------------------------------
# State (_BUILTIN_QUOTES, _SAVED_QUOTES_PATH, _TAGS_FILE, _MEM_*), helpers
# (_load_saved_quotes/_save_quotes/_load_tags/_save_tags/_make_msg_link,
#  _mem_* helpers, _recall_memory_layer, _format_memory_layer_section,
#  _mem_truncate) и handlers (!remember, !recall, !quote, !tag, !mem)
# вынесены в commands/memory_commands.py. Re-exported для совместимости.
# ---------------------------------------------------------------------------
# Phase 2 Wave 7 (Session 27): ai_commands extraction
# ---------------------------------------------------------------------------
# AI-команды (!ask, !search, !agent, !rate, !explain, !fix, !rewrite,
# !summary, !catchup, !report) и их helpers/constants вынесены в
# commands/ai_commands.py. Re-exported для обратной совместимости (тесты,
# любые external imports `from src.handlers.command_handlers import handle_ask`).
from .commands.ai_commands import (  # noqa: E402, F401
    _EXPLAIN_PROMPT,
    _RATE_CRYPTO_ALIASES,
    _RATE_MAX_ASSETS,
    _REWRITE_MODES,
    _SUMMARY_DEFAULT_N,
    _SUMMARY_EDIT_THRESHOLD,
    _SUMMARY_MAX_HISTORY_CHARS,
    _SUMMARY_MAX_N,
    _build_rate_prompt,
    _collect_daily_report_data,
    _format_chat_history_for_llm,
    _parse_ask_memory_flags,
    _rate_asset_label,
    _render_daily_report,
    handle_agent,
    handle_ask,
    handle_catchup,
    handle_explain,
    handle_fix,
    handle_rate,
    handle_report,
    handle_rewrite,
    handle_search,
    handle_summary,
)
from .commands.memory_commands import (  # noqa: E402, F401
    _BUILTIN_QUOTES,
    _MEM_HELP_TEXT,
    _MEM_SNIPPET_LEN,
    _SAVED_QUOTES_PATH,
    _TAGS_FILE,
    MEMORY_SEARCH_URL,
    _format_memory_layer_section,
    _load_saved_quotes,
    _load_tags,
    _make_msg_link,
    _mem_count,
    _mem_search,
    _mem_stats,
    _mem_summary,
    _mem_truncate,
    _recall_memory_layer,
    _save_quotes,
    _save_tags,
    handle_mem,
    handle_quote,
    handle_recall,
    handle_remember,
    handle_tag,
)
from .commands.scheduler_commands import (  # noqa: E402, F401
    _AUTODEL_STATE_KEY,
    _REMIND_HELP,
    _active_timers,
    _cron_format_last_status,
    _cron_format_schedule,
    _cron_read_jobs,
    _cron_run_openclaw,
    _cron_write_jobs,
    _delete_after,
    _fmt_duration,
    _handle_cron_native,
    _handle_cron_quick,
    _parse_duration,
    _set_autodel_delay,
    _stopwatches,
    _timer_counter,
    get_autodel_delay,
    handle_autodel,
    handle_cron,
    handle_cronstatus,
    handle_remind,
    handle_reminders,
    handle_rm_remind,
    handle_schedule,
    handle_stopwatch,
    handle_timer,
    handle_todo,
    schedule_autodel,
)

# ---------------------------------------------------------------------------
# Phase 2 Wave 8 (Session 27): swarm_commands extraction
# ---------------------------------------------------------------------------
# Команда `!swarm` со всеми subcommands и `_AgentRoomRouterAdapter` вынесены в
# commands/swarm_commands.py. Re-exported для обратной совместимости (тесты,
# userbot_bridge cron, ai_commands.handle_agent fallback).
from .commands.swarm_commands import (  # noqa: E402, F401
    _AgentRoomRouterAdapter,
    handle_swarm,
)

# ---------------------------------------------------------------------------
# Phase 2 Wave 10 (Session 27, финальная волна): system_commands extraction
# ---------------------------------------------------------------------------
# Системные / диагностические handlers (!status, !sysinfo, !uptime, !panel,
# !version, !restart, !diagnose, !debug, !health, !stats, !ip, !dns, !ping,
# !log, !diag) и их private helpers (_format_uptime_str, _render_stats_panel,
# _format_ecosystem_report, _handle_stats_ecosystem, _health_deep_report,
# _get_local_ip, _get_public_ip, _read_log_tail_subprocess, _LOG_*,
# _KRAB_LOG_PATH, _diag_panel_base, _diag_fetch_json, _diag_fmt_section_*,
# _diag_fetch_sentry, _diag_collect_security) вынесены в
# commands/system_commands.py. Re-exported для совместимости.
# ВАЖНО: _swarm_status_deep_report и _split_text_for_telegram остаются в этом
# модуле (multi-use / тесты патчат через namespace).
from .commands.system_commands import (  # noqa: E402, F401
    _KRAB_LOG_PATH,
    _LOG_MAX_INLINE_SIZE,
    _LOG_TEXT_MAX_LINES,
    _diag_collect_security,
    _diag_fetch_json,
    _diag_fetch_sentry,
    _diag_fmt_section_cron,
    _diag_fmt_section_errors,
    _diag_fmt_section_inbox,
    _diag_fmt_section_infra,
    _diag_fmt_section_memory,
    _diag_fmt_section_model,
    _diag_fmt_section_phase2,
    _diag_fmt_section_security,
    _diag_fmt_section_sentry,
    _diag_fmt_section_traffic,
    _diag_panel_base,
    _format_ecosystem_report,
    _format_uptime_str,
    _get_local_ip,
    _get_public_ip,
    _handle_stats_ecosystem,
    _health_deep_report,
    _read_log_tail_subprocess,
    _render_stats_panel,
    handle_debug,
    handle_diag,
    handle_diagnose,
    handle_dns,
    handle_health,
    handle_ip,
    handle_log,
    handle_panel,
    handle_ping,
    handle_restart,
    handle_stats,
    handle_status,
    handle_sysinfo,
    handle_uptime,
    handle_version,
)

# ---------------------------------------------------------------------------
# Phase 2 Wave 9 (Session 27): translator_commands extraction
# ---------------------------------------------------------------------------
# Helpers (_render_translator_profile, _render_translator_session_state,
# _parse_toggle_arg, _TRANSLATE_LANG_ALIASES) и handlers (!translator,
# !translate, !translate auto) вынесены в commands/translator_commands.py.
# Re-exported для совместимости (тесты test_command_handlers_unit.py,
# test_translator_commands.py, test_handle_translator_command.py).
from .commands.translator_commands import (  # noqa: E402, F401
    _TRANSLATE_LANG_ALIASES,
    _parse_toggle_arg,
    _render_translator_profile,
    _render_translator_session_state,
    handle_translate,
    handle_translate_auto,
    handle_translator,
)

# ---------------------------------------------------------------------------
# Phase 2 Wave 4 (Session 27): voice_commands extraction
# ---------------------------------------------------------------------------
# Helpers (_render_voice_profile), state (_TTS_VOICES, _TTS_LANG_ALIASES) и
# handlers (!voice, !tts, audio_message) вынесены в commands/voice_commands.py.
# Re-exported для обратной совместимости (тесты, _AgentRoomRouterAdapter).
from .commands.voice_commands import (  # noqa: E402, F401
    _TTS_LANG_ALIASES,
    _TTS_VOICES,
    _render_voice_profile,
    handle_audio_message,
    handle_tts,
    handle_voice,
)


def _format_size_gb(size_gb: float) -> str:
    """Форматирует размер модели для человекочитаемого вывода."""
    try:
        value = float(size_gb)
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0:
        return "n/a"
    return f"{value:.2f} GB"


def _split_text_for_telegram(text: str, limit: int = 3900) -> list[str]:
    """
    Делит длинный текст на части с сохранением границ строк.
    Telegram ограничивает текст сообщения примерно 4096 символами.
    """
    lines = text.splitlines()
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(line) <= limit:
            current = line
        else:
            # На случай сверхдлинной строки режем принудительно.
            for i in range(0, len(line), limit):
                part = line[i : i + limit]
                if len(part) == limit:
                    chunks.append(part)
                else:
                    current = part
    if current:
        chunks.append(current)
    return chunks or [text[:limit]]


# _AgentRoomRouterAdapter и handle_swarm — extracted to commands/swarm_commands.py
# (Phase 2 Wave 8, Session 27). Re-exported above.


async def handle_shop(bot: "KraabUserbot", message: Message) -> None:
    """Поиск товаров на Mercadona через перехват XHR/Fetch ответов API."""
    from ..skills.mercadona import search_mercadona

    query = bot._get_command_args(message)
    if not query or query.lower() in ["shop", "!shop"]:
        raise UserInputError(user_message="🛒 Что ищем? Напиши: `!shop <товар>`")
    msg = await message.reply(f"🛒 **Краб ищет на Mercadona:** `{query}`...")
    try:
        results = await search_mercadona(query)
        if len(results) > 4000:
            results = results[:3900] + "..."
        await msg.edit(results)
    except Exception as exc:
        logger.error("mercadona_search_failed", error=repr(exc))
        await msg.edit(f"❌ Ошибка при поиске на Mercadona: {exc}")


# handle_remember moved to commands/memory_commands.py (Phase 2 Wave 5)


async def handle_confirm(bot: "KraabUserbot", message: Message) -> None:
    """!confirm <hash> — подтверждает staged memory write (owner-only).

    Без аргументов — показывает список ожидающих подтверждения.
    """
    # Owner-check через ACL (унификация с остальными owner-only командами).
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        await message.reply("⛔ Только для владельца.")
        return

    hash_code = (bot._get_command_args(message) or "").strip().upper()
    if not hash_code:
        pending = memory_validator.list_pending()
        if not pending:
            await message.reply("📭 Нет ожидающих подтверждений.")
            return
        lines = [f"• `{p.hash}` — {p.text[:60]}{'…' if len(p.text) > 60 else ''}" for p in pending]
        await message.reply("⏳ Ожидают подтверждения:\n" + "\n".join(lines))
        return

    ok, reply_msg, pending = memory_validator.confirm(hash_code)
    if not ok or pending is None:
        await message.reply(reply_msg)
        return

    # Выполняем отложенную запись — дублирует логику handle_remember.
    try:
        workspace_saved = append_workspace_memory_entry(
            pending.text,
            source=pending.source or "userbot",
            author=pending.author,
        )
        vector_saved = memory_manager.save_fact(pending.text)
        success = workspace_saved or vector_saved
        if success:
            await message.reply(f"{reply_msg}. Запись сохранена.")
        else:
            await message.reply("❌ Подтверждено, но запись не удалась.")
    except (ValueError, RuntimeError, OSError) as e:
        await message.reply(f"❌ Critical Memory Error: {e}")


# MEMORY_SEARCH_URL / _recall_memory_layer / _format_memory_layer_section / handle_recall
# moved to commands/memory_commands.py (Phase 2 Wave 5)


async def handle_ls(bot: "KraabUserbot", message: Message) -> None:
    """Список файлов."""
    path = bot._get_command_args(message) or str(config.BASE_DIR)
    if ".." in path and not config.is_valid():
        pass
    msg = await message.reply("📂 Scanning...")
    try:
        result = await mcp_manager.list_directory(path)
        await msg.edit(f"📂 **Files in {path}:**\n\n`{result[:3900]}`")
    except (httpx.HTTPError, OSError, ValueError, KeyError, AttributeError) as e:
        await msg.edit(f"❌ Error listing: {e}")


async def handle_read(bot: "KraabUserbot", message: Message) -> None:
    """Чтение файла."""
    path = bot._get_command_args(message)
    if not path:
        raise UserInputError(user_message="📂 Какой файл читать? `!read <path>`")
    if not path.startswith("/"):
        path = os.path.join(config.BASE_DIR, path)
    msg = await message.reply("📂 Reading...")
    try:
        content = await mcp_manager.read_file(path)
        if len(content) > 4000:
            content = content[:1000] + "\n... [truncated]"
        await msg.edit(f"📂 **Content of {os.path.basename(path)}:**\n\n```\n{content}\n```")
    except (httpx.HTTPError, OSError, ValueError, KeyError, AttributeError) as e:
        await msg.edit(f"❌ Reading error: {e}")


async def handle_write(bot: "KraabUserbot", message: Message) -> None:
    """Запись файла (опасно!)."""
    text = bot._get_command_args(message)
    if not text:
        raise UserInputError(user_message="📂 Формат: `!write <filename> <content>`")
    parts = text.split("\n", 1)
    if len(parts) < 2:
        parts = text.split(" ", 1)
        if len(parts) < 2:
            raise UserInputError(user_message="📂 Нет контента для записи.")
    path = parts[0].strip()
    content = parts[1]
    if not path.startswith("/"):
        path = os.path.join(config.BASE_DIR, path)
    result = await mcp_manager.write_file(path, content)
    await message.reply(result)


async def handle_paste(bot: "KraabUserbot", message: Message) -> None:
    """Создать текстовый paste-файл и отправить как документ.

    Поддерживает два режима:
      !paste <текст>   — создаёт файл из аргумента
      !paste (reply)  — создаёт файл из текста исходного сообщения
    """
    args = bot._get_command_args(message)
    reply = getattr(message, "reply_to_message", None)

    # Определяем текст для paste
    if args:
        text = args
    elif reply and getattr(reply, "text", None):
        text = reply.text
    else:
        raise UserInputError(
            user_message=(
                "📋 Формат: `!paste <текст>` или сделай reply на сообщение\n"
                "Полезно для длинных текстов >4096 символов."
            )
        )

    # Формируем имя файла
    now = datetime.datetime.now()
    filename = now.strftime("paste_%Y-%m-%d_%H-%M.txt")
    tmpdir = pathlib.Path(config.BASE_DIR) / ".runtime" / "pastes"
    tmpdir.mkdir(parents=True, exist_ok=True)
    filepath = tmpdir / filename

    try:
        filepath.write_text(text, encoding="utf-8")
        await bot.client.send_document(
            message.chat.id,
            str(filepath),
            caption="📋 Paste",
        )
    except (OSError, IOError) as e:
        await message.reply(f"❌ Ошибка создания paste: {e}")
    finally:
        # Удаляем временный файл после отправки
        try:
            filepath.unlink(missing_ok=True)
        except OSError:
            pass


# handle_status — extracted to commands/system_commands.py (Phase 2 Wave 10, Session 27).
# Re-exported above (handle_status).


async def handle_model(bot: "KraabUserbot", message: Message) -> None:
    """Управление маршрутизацией и загрузкой AI моделей."""
    args = message.text.split()
    sub = args[1].lower() if len(args) > 1 else ""

    async def _is_local_model(model_id: str) -> bool:
        """Определяет, относится ли model_id к локальным моделям LM Studio."""
        normalized = str(model_id or "").strip().lower()
        if normalized in {"local", "lmstudio/local"} or normalized.startswith("lmstudio/"):
            return True
        try:
            models = await model_manager.discover_models()
            return any(m.id == model_id and m.type.name.startswith("LOCAL") for m in models)
        except Exception:
            # Если discovery недоступен, используем безопасную эвристику.
            return normalized.startswith("local/") or "mlx" in normalized

    if not sub:
        force_cloud = getattr(config, "FORCE_CLOUD", False)
        if force_cloud:
            mode_label = "☁️ cloud (принудительно)"
        else:
            mode_label = "🤖 auto"
        current = model_manager._current_model or "нет"
        cloud_model = config.MODEL or "не задана"
        text = (
            "🧭 **Маршрутизация моделей**\n"
            f"---------------------------\n"
            f"**Режим:** {mode_label}\n"
            f"**Активная модель:** `{current}`\n"
            f"**Облачная модель:** `{cloud_model}`\n"
            f"**LM Studio URL:** `{config.LM_STUDIO_URL}`\n"
            f"**FORCE_CLOUD:** `{force_cloud}`\n\n"
            "_Подкоманды: `info`, `local`, `cloud`, `auto`, `set <model_id>`, `load <name>`, `unload`, `scan`_"
        )
        await message.reply(text)
        return

    if sub == "local":
        # Фиксируем режим в .env, чтобы он не слетал после рестартов runtime.
        config.update_setting("FORCE_CLOUD", "0")
        config.FORCE_CLOUD = False
        await message.reply("💻 Режим: **local** — используется локальная модель (LM Studio).")
        return

    if sub == "cloud":
        # Фиксируем режим в .env, чтобы cloud оставался активным после перезапуска.
        config.update_setting("FORCE_CLOUD", "1")
        config.FORCE_CLOUD = True
        await message.reply(f"☁️ Режим: **cloud** — используется `{config.MODEL}`.")
        return

    if sub == "auto":
        # Auto = не форсить cloud, отдаём выбор роутеру.
        config.update_setting("FORCE_CLOUD", "0")
        config.FORCE_CLOUD = False
        await message.reply("🤖 Режим: **auto** — автоматический выбор лучшей модели.")
        return

    if sub == "set":
        if len(args) < 3:
            raise UserInputError(user_message="⚙️ Формат: `!model set <model_id>`")

        raw_id = args[2].strip()
        resolved_id, alias_note = normalize_model_alias(raw_id)
        is_local = await _is_local_model(resolved_id)

        if is_local:
            config.update_setting("LOCAL_PREFERRED_MODEL", resolved_id)
            config.update_setting("FORCE_CLOUD", "0")
            config.FORCE_CLOUD = False
            await message.reply(
                "💻 Зафиксирована локальная модель.\n"
                f"**Model:** `{resolved_id}`\n"
                f"{f'ℹ️ Alias: {alias_note}' if alias_note else ''}\n"
                "Режим переключен в `auto/local` (без принудительного cloud)."
            )
            return

        config.update_setting("MODEL", resolved_id)
        config.update_setting("FORCE_CLOUD", "1")
        config.FORCE_CLOUD = True
        await message.reply(
            "☁️ Зафиксирована облачная модель.\n"
            f"**Model:** `{resolved_id}`\n"
            f"{f'ℹ️ Alias: {alias_note}' if alias_note else ''}\n"
            "Режим переключен в `cloud`."
        )
        return

    if sub == "load":
        if len(args) < 3:
            raise UserInputError(user_message="⚙️ Укажите модель: `!model load <name>`")
        mid = args[2]
        msg = await message.reply(f"⏳ Загружаю `{mid}`...")
        try:
            ok = await model_manager.load_model(mid)
            if ok:
                config.update_setting("MODEL", mid)
                await msg.edit(f"✅ Модель загружена: `{mid}`")
            else:
                await msg.edit(f"❌ Не удалось загрузить `{mid}`")
        except Exception as e:
            await msg.edit(f"❌ Ошибка загрузки: `{str(e)[:200]}`")
        return

    if sub == "unload":
        msg = await message.reply("⏳ Выгружаю модели...")
        try:
            await model_manager.unload_all()
            await msg.edit("✅ Все модели выгружены. VRAM освобождена.")
        except Exception as e:
            await msg.edit(f"❌ Ошибка выгрузки: `{str(e)[:200]}`")
        return

    if sub == "info":
        # Собираем снимок текущего маршрута, providers health и fallback chain.
        text = await _format_model_info()
        await message.reply(text)
        return

    if sub in ("scan", "list"):
        msg = await message.reply("🔍 Сканирую доступные модели...")
        try:
            models = await model_manager.discover_models()
            from ..core.cloud_gateway import get_cloud_fallback_chain

            cloud_ids = [c for c in get_cloud_fallback_chain() if "gemini" in c.lower()]
            local_models = [m for m in models if m.type.name.startswith("LOCAL")]
            cloud_from_api = [m for m in models if m.type.name.startswith("CLOUD")]
            cloud_seen = {m.id for m in cloud_from_api}
            for cid in cloud_ids:
                if cid not in cloud_seen:
                    from ..core.model_types import ModelInfo, ModelStatus, ModelType

                    cloud_from_api.append(
                        ModelInfo(
                            id=cid,
                            name=cid,
                            type=ModelType.CLOUD_GEMINI,
                            status=ModelStatus.AVAILABLE,
                            size_gb=0.0,
                            supports_vision=True,
                        )
                    )
                    cloud_seen.add(cid)
            lines = [
                f"🔍 **Доступные модели** (local={len(local_models)}, cloud={len(cloud_from_api)})\n",
                "☁️ **Облачные**\n",
            ]
            for m in sorted(cloud_from_api, key=lambda x: x.id):
                loaded = " ✅" if m.id == model_manager._current_model else ""
                lines.append(
                    f"☁️ `{m.id}` · `{_format_size_gb(getattr(m, 'size_gb', 0.0))}`{loaded}"
                )
            lines.append("\n💻 **Локальные**\n")
            for m in sorted(local_models, key=lambda x: x.id):
                loaded = " ✅" if m.id == model_manager._current_model else ""
                lines.append(
                    f"💻 `{m.id}` · `{_format_size_gb(getattr(m, 'size_gb', 0.0))}`{loaded}"
                )
            text = "\n".join(lines)
            chunks = _split_text_for_telegram(text)
            await msg.edit(chunks[0])
            for part in chunks[1:]:
                await message.reply(part)
        except Exception as e:
            await msg.edit(f"❌ Ошибка сканирования: `{str(e)[:200]}`")
        return

    raise UserInputError(
        user_message=(
            f"❓ Неизвестная подкоманда `{sub}`.\n"
            "Доступные: `local`, `cloud`, `auto`, `set`, `load`, `unload`, `scan`, `info`"
        )
    )


async def _format_model_info() -> str:
    """Формирует Markdown-отчёт для `!model info`.

    Источники:
    - openclaw_client.get_last_runtime_route() — активный маршрут последнего запроса;
    - http://127.0.0.1:8080/api/openclaw/cloud — providers health через локальный web-app;
    - get_cloud_fallback_chain() — fallback цепочка из конфига;
    - is_lm_studio_available() — статус LM Studio.
    """
    from ..core.cloud_gateway import get_cloud_fallback_chain  # noqa: PLC0415

    # 1) Активный маршрут — используем публичный accessor, с fallback на атрибут.
    try:
        last_route = openclaw_client.get_last_runtime_route() or {}
    except Exception:
        last_route = getattr(openclaw_client, "_last_runtime_route", {}) or {}

    # 2) Providers health — коротко, через локальный web-app (1.5s таймаут).
    cloud_report: dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get("http://127.0.0.1:8080/api/openclaw/cloud")
            if resp.status_code == 200:
                payload = resp.json() or {}
                cloud_report = payload.get("report", {}) if isinstance(payload, dict) else {}
    except Exception:
        cloud_report = {}

    # 3) Fallback chain.
    try:
        fallback_chain = get_cloud_fallback_chain()
    except Exception:
        fallback_chain = []

    # 4) LM Studio availability.
    try:
        lm_available = bool(await is_lm_studio_available())
    except Exception:
        lm_available = False

    lines: list[str] = ["🤖 **Model Info**", "", "**Active route:**"]
    provider = str(last_route.get("provider") or "n/a")
    model_id = str(last_route.get("model") or "n/a")
    tier = str(last_route.get("active_tier") or "n/a")
    status = str(last_route.get("status") or "n/a")
    ts_raw = last_route.get("timestamp")
    if isinstance(ts_raw, int) and ts_raw > 0:
        ts_str = datetime.datetime.fromtimestamp(ts_raw).strftime("%H:%M:%S")
        status_line = f"✅ {status} ({ts_str})" if status == "ok" else f"⚠️ {status} ({ts_str})"
    else:
        status_line = status
    lines.append(f"• Provider: `{provider}`")
    lines.append(f"• Model: `{model_id}`")
    lines.append(f"• Tier: `{tier}`")
    lines.append(f"• Last status: {status_line}")

    # Fallback chain.
    lines.append("")
    lines.append("**Fallback chain:**")
    if fallback_chain:
        for idx, mid in enumerate(fallback_chain[:6], start=1):
            lines.append(f"{idx}. {mid}")
    else:
        lines.append("_(пусто или недоступно)_")

    # Providers health.
    lines.append("")
    lines.append("**Providers health:**")
    providers = cloud_report.get("providers", {}) if isinstance(cloud_report, dict) else {}
    if providers:
        for name, info in providers.items():
            if not isinstance(info, dict):
                continue
            ok = info.get("ok")
            http_status = info.get("http_status")
            provider_status = info.get("provider_status", "n/a")
            icon = "✅" if ok else "❌"
            http_hint = f" (http {http_status})" if http_status else ""
            lines.append(f"• {name}: {icon} {provider_status}{http_hint}")
    else:
        lines.append("• google: ⚠️ недоступно (cloud API off)")

    lm_state = "ready" if lm_available else "idle"
    lines.append(f"• LM Studio: {lm_state}")

    return "\n".join(lines)


async def handle_clear(bot: "KraabUserbot", message: Message) -> None:
    """Очистка контекста / кэшей.

    Синтаксис:
      !clear            — очистить сессию текущего чата (алиас !context clear)
      !clear all        — очистить все сессии (сбросить _sessions целиком)
      !clear cache      — очистить все кэши (history_cache + search_cache)
    """
    chat_id = str(message.chat.id)
    raw = (message.text or "").strip()
    # Извлекаем аргумент после команды (учитываем префикс ! и слово clear)
    parts = raw.split(maxsplit=1)
    sub = parts[1].strip().lower() if len(parts) > 1 else ""

    if sub == "all":
        # Очищаем все сессии
        count = len(openclaw_client._sessions)
        openclaw_client._sessions.clear()
        # Также сбрасываем LM Studio native chat state, если есть
        if hasattr(openclaw_client, "_lm_native_chat_state"):
            openclaw_client._lm_native_chat_state.clear()
        res = f"🧹 **Все сессии очищены** (`{count}` чат(ов)). Краб начинает с чистого листа!"
    elif sub == "cache":
        # Очищаем все кэши
        h_count = history_cache.clear_all()
        s_count = search_cache.clear_all()
        res = (
            f"🗑️ **Кэши очищены**\n"
            f"• history_cache: `{h_count}` записей\n"
            f"• search_cache: `{s_count}` записей"
        )
    else:
        # Очистить сессию текущего чата (поведение по умолчанию)
        openclaw_client.clear_session(chat_id)
        res = "🧹 **Память очищена. Клешни как новые!**"

    if message.from_user and message.from_user.id == bot.me.id:
        await message.edit(res)
    else:
        await message.reply(res)


# ---------------------------------------------------------------------------
# !forget / !clear_session — быстрая очистка session history текущего чата
# ---------------------------------------------------------------------------


async def handle_forget(bot: "KraabUserbot", message: Message) -> None:
    """!forget — очистить session history текущего чата.

    Алиас: !clear_session
    Owner-only (или DM владельца).
    Удаляет накопленный _sessions[chat_id] + history_cache, чтобы LLM начал
    с чистого листа. Полезно перед memory-recall запросами, чтобы старые
    stale-ответы не отравляли атрибуцию.
    """
    # Owner-check
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 Команда доступна только владельцу.")

    chat_id = str(message.chat.id)
    openclaw_client.clear_session(chat_id)
    await message.reply("🧠 Контекст чата очищен. Начинаю свежий разговор.")


# ---------------------------------------------------------------------------
# !reset — агрессивная многослойная очистка истории
# ---------------------------------------------------------------------------


async def handle_reset(bot: "KraabUserbot", message: Message) -> None:
    """Очищает все слои истории одной операцией.

    Syntax:
        !reset                    — Krab + OpenClaw + Gemini (archive НЕ включён)
        !reset --all              — все чаты (Krab + OpenClaw + Gemini, требует --force)
        !reset --layer=krab       — только Krab history_cache.db
        !reset --layer=openclaw   — только OpenClaw session
        !reset --layer=gemini     — только Gemini cache invalidate (nonce)
        !reset --layer=archive    — удалить из archive.db (opt-in destructive)
        !reset --dry-run          — показать что удалится, не удалять
        !reset --force            — пропустить confirmation для --all

    Возвращает отчёт по каждому слою. Owner-only для --all (destructive).
    Archive destructive → не включён в default scope: требует явного --layer=archive.
    """
    from ..core.gemini_cache_nonce import invalidate_gemini_cache_for_chat
    from ..core.reset_helpers import (
        clear_archive_db_for_chat,
        count_archive_messages_for_chat,
    )

    # Разрешённые значения для --layer=<...>. Неизвестные — ошибка (предсказуемое API).
    valid_layers = {"krab", "openclaw", "gemini", "archive"}

    chat_id = str(message.chat.id)
    raw_args = ""
    if hasattr(bot, "_get_command_args"):
        try:
            raw_args = (bot._get_command_args(message) or "").strip()
        except Exception:  # noqa: BLE001
            raw_args = ""

    tokens = raw_args.split() if raw_args else []
    is_all = "--all" in tokens
    is_force = "--force" in tokens
    # Безопасный alias: пользователь может написать `!reset dry-run` с телефона.
    # Раньше такой вариант не считался dry-run и мог уйти в реальное выполнение reset.
    dry_run_aliases = {"--dry-run", "dry-run", "dryrun", "dry"}
    dry_run = any(token.lower() in dry_run_aliases for token in tokens)
    layer: str | None = None
    for token in tokens:
        if token.startswith("--layer="):
            layer = token.split("=", 1)[1].strip().lower() or None

    # Валидация --layer=<value>: неизвестные слои возвращают ошибку, а не silent pass.
    if layer is not None and layer not in valid_layers:
        await message.reply(f"❌ Unknown layer: `{layer}`. Valid: {sorted(valid_layers)}")
        return

    # Owner-check для --all. Userbot — message.from_user.id должен совпадать с bot.me.id.
    if is_all:
        me_id = getattr(getattr(bot, "me", None), "id", None)
        sender_id = getattr(getattr(message, "from_user", None), "id", None)
        if me_id is None or sender_id != me_id:
            await message.reply("🚫 `!reset --all` доступен только владельцу.")
            return

    # Confirmation flow для --all без --force (и не dry-run)
    if is_all and not is_force and not dry_run:
        await message.reply(
            "⚠️ `!reset --all` удалит историю из **ВСЕХ** чатов (все слои).\n"
            "Это необратимо.\n\nПовтори с флагом `--force`:\n`!reset --all --force`"
        )
        return

    # Список целевых чатов
    if is_all:
        target_chat_ids = [str(cid) for cid in openclaw_client._sessions.keys()]
        # Если _sessions пустой, но просили --all — всё равно пытаемся обработать
        # текущий чат как минимум, чтобы не вернуть no-op-отчёт без смысла.
        if not target_chat_ids:
            target_chat_ids = [chat_id]
    else:
        target_chat_ids = [chat_id]

    # Audit log: destructive --all --force execution — фиксируем факт для post-mortem.
    if is_all and is_force and not dry_run:
        sender_id = getattr(getattr(message, "from_user", None), "id", None)
        logger.warning(
            "reset_all_force_executed",
            chat_count=len(target_chat_ids),
            user_id=sender_id,
            layer=layer or "all",
        )

    # ── dry-run: только превью ────────────────────────────────────────────
    impact = {"krab": 0, "openclaw": 0, "gemini": 0, "archive": 0}

    if layer in (None, "krab"):
        for cid in target_chat_ids:
            try:
                if history_cache.get(f"chat_history:{cid}"):
                    impact["krab"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("reset_krab_probe_failed", chat_id=cid, error=str(exc))

    if layer in (None, "openclaw"):
        impact["openclaw"] = sum(1 for cid in target_chat_ids if cid in openclaw_client._sessions)

    if layer in (None, "gemini"):
        # Nonce-инвалидация — per chat, независимо от текущего состояния
        impact["gemini"] = len(target_chat_ids)

    if layer == "archive":
        for cid in target_chat_ids:
            impact["archive"] += count_archive_messages_for_chat(cid)

    if dry_run:
        scope = "все чаты" if is_all else "текущий чат"
        # Archive не в default scope — честно предупреждаем в превью.
        archive_hint = ""
        if layer is None:
            archive_hint = (
                "\n⚠️ Archive НЕ включён в default scope. "
                "Используй `--layer=archive` для очистки archive.db."
            )
        preview = (
            f"🔍 **Dry-run** (nothing deleted)\n"
            f"Scope: {scope}\n"
            f"Layer filter: `{layer or 'all'}`\n\n"
            f"Удалилось бы:\n"
            f"• Krab cache: {impact['krab']}\n"
            f"• OpenClaw: {impact['openclaw']}\n"
            f"• Gemini cache invalidate: {impact['gemini']}\n"
            f"• Archive: {impact['archive']}{archive_hint}"
        )
        if message.from_user and message.from_user.id == bot.me.id:
            await message.edit(preview)
        else:
            await message.reply(preview)
        return

    # ── EXECUTE ───────────────────────────────────────────────────────────
    stats = {"krab": 0, "openclaw": 0, "gemini": 0, "archive": 0}

    # Progress message для длинного --all (>10 чатов).
    # Обновляем каждые 10 итераций, чтобы не спамить Telegram API.
    total = len(target_chat_ids)
    progress_msg = None
    if total > 10:
        try:
            progress_msg = await message.reply(f"🔄 Reset: 0 / {total}...")
        except Exception as exc:  # noqa: BLE001
            logger.warning("reset_progress_init_failed", error=str(exc))
            progress_msg = None

    for idx, cid in enumerate(target_chat_ids):
        if layer in (None, "krab"):
            # Избегаем double-count: clear_session() ниже тоже удаляет chat_history:*,
            # поэтому инкрементим stats только если ключ реально был.
            key = f"chat_history:{cid}"
            try:
                if history_cache.get(key):
                    history_cache.delete(key)
                    stats["krab"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("reset_krab_failed", chat_id=cid, error=str(exc))

        if layer in (None, "openclaw"):
            try:
                openclaw_client.clear_session(cid)
                stats["openclaw"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("reset_openclaw_failed", chat_id=cid, error=str(exc))

        if layer in (None, "gemini"):
            try:
                invalidate_gemini_cache_for_chat(cid)
                stats["gemini"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("reset_gemini_failed", chat_id=cid, error=str(exc))

        if layer == "archive":
            try:
                stats["archive"] += clear_archive_db_for_chat(cid)
            except Exception as exc:  # noqa: BLE001
                logger.warning("reset_archive_failed", chat_id=cid, error=str(exc))

        # Прогресс каждые 10 чатов — не спамим и не валим на edit-ошибке.
        if progress_msg is not None and (idx + 1) % 10 == 0 and (idx + 1) < total:
            try:
                await progress_msg.edit(f"🔄 Reset: {idx + 1} / {total}...")
            except Exception:  # noqa: BLE001
                pass

    # Удаляем progress-message перед финальным отчётом (best-effort).
    if progress_msg is not None:
        try:
            await progress_msg.delete()
        except Exception:  # noqa: BLE001
            pass

    scope = "всех чатов" if is_all else "текущего чата"
    res = (
        f"🗑️ **Reset выполнен** ({scope})\n"
        f"• Krab cache: {stats['krab']}\n"
        f"• OpenClaw: {stats['openclaw']}\n"
        f"• Gemini: {stats['gemini']}\n"
        f"• Archive: {stats['archive']}"
    )
    if message.from_user and message.from_user.id == bot.me.id:
        await message.edit(res)
    else:
        await message.reply(res)


##############################################################################
# Группы ключей для !config — технические/системные настройки
##############################################################################

# (config_key, описание)
_CONFIG_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Модель и routing",
        [
            ("MODEL", "Основная модель"),
            ("FORCE_CLOUD", "Принудительный cloud-маршрут"),
            ("LOCAL_FALLBACK_ENABLED", "Fallback cloud→local при ошибках"),
            ("LOCAL_PREFERRED_MODEL", "Локальная модель (LM Studio)"),
            ("LOCAL_PREFERRED_VISION_MODEL", "Локальная vision-модель"),
            ("SINGLE_LOCAL_MODEL_MODE", "Держать одну локальную модель"),
            ("GUARDED_IDLE_UNLOAD", "Guarded idle-unload локальной модели"),
            ("GUARDED_IDLE_UNLOAD_GRACE_SEC", "Пауза перед idle-unload (сек)"),
            ("RESTORE_PREFERRED_ON_IDLE_UNLOAD", "Восстановить preferred после unload"),
        ],
    ),
    (
        "Таймауты и retry",
        [
            ("OPENCLAW_CHUNK_TIMEOUT_SEC", "Таймаут chunk стриминга (сек)"),
            ("OPENCLAW_FIRST_CHUNK_TIMEOUT_SEC", "Таймаут первого chunk (сек)"),
            ("OPENCLAW_PHOTO_FIRST_CHUNK_TIMEOUT_SEC", "Таймаут первого chunk фото (сек)"),
            ("OPENCLAW_AUTO_RETRY_COUNT", "Кол-во auto-retry при ошибках"),
            ("OPENCLAW_AUTO_RETRY_DELAY_SEC", "Задержка auto-retry (сек)"),
            ("OPENCLAW_PROGRESS_NOTICE_INITIAL_SEC", "Первый progress-notice (сек)"),
            ("OPENCLAW_PROGRESS_NOTICE_REPEAT_SEC", "Повтор progress-notice (сек)"),
        ],
    ),
    (
        "Userbot и Telegram",
        [
            ("USERBOT_MAX_OUTPUT_TOKENS", "Макс. токенов ответа (текст)"),
            ("USERBOT_PHOTO_MAX_OUTPUT_TOKENS", "Макс. токенов ответа (фото)"),
            ("USERBOT_FORCE_CLOUD_FOR_PHOTO", "Cloud-маршрут для фото"),
            ("TELEGRAM_STREAM_UPDATE_INTERVAL_SEC", "Интервал stream UI (сек)"),
            ("TELEGRAM_STREAM_SHOW_REASONING", "Показывать reasoning в stream"),
            ("TELEGRAM_REACTIONS_ENABLED", "Реакции 👀✅❌"),
            ("TELEGRAM_MESSAGE_BATCH_WINDOW_SEC", "Окно склейки сообщений (сек)"),
            ("TELEGRAM_SESSION_HEARTBEAT_SEC", "Heartbeat MTProto (сек)"),
            ("TOOL_NARRATION_ENABLED", "Tool narration в Telegram"),
        ],
    ),
    (
        "Фоновые задачи",
        [
            ("SCHEDULER_ENABLED", "Планировщик reminders/cron"),
            ("DEFERRED_ACTION_GUARD_ENABLED", "Guard deferred-actions"),
            ("SWARM_AUTONOMOUS_ENABLED", "Автономные задачи свёрма"),
            ("SILENCE_DEFAULT_MINUTES", "Tишина по умолчанию (мин)"),
            ("OWNER_AUTO_SILENCE_MINUTES", "Авто-тишина при owner-write (мин)"),
        ],
    ),
    (
        "Доступ и безопасность",
        [
            ("OWNER_USERNAME", "Username владельца (fallback)"),
            ("NON_OWNER_SAFE_MODE_ENABLED", "Safe-mode для гостей"),
            ("GUEST_TOOLS_DISABLED", "Запрет tools для GUEST"),
            ("FORWARD_UNKNOWN_INCOMING", "Пересылать неизвестные входящие"),
            ("AI_DISCLOSURE_ENABLED", "Дисклеймер ИИ в начале диалога"),
            ("MANUAL_BLOCKLIST", "Чёрный список (usernames/IDs)"),
        ],
    ),
    (
        "Голос",
        [
            ("VOICE_MODE_DEFAULT", "Voice-режим по умолчанию"),
            ("VOICE_REPLY_SPEED", "Скорость TTS"),
            ("VOICE_REPLY_VOICE", "TTS-голос"),
            ("VOICE_REPLY_DELIVERY", "Режим доставки (text+voice/voice/text)"),
        ],
    ),
    (
        "История диалога",
        [
            ("HISTORY_WINDOW_MESSAGES", "Окно cloud-истории (сообщений)"),
            ("LOCAL_HISTORY_WINDOW_MESSAGES", "Окно local-истории (сообщений)"),
            ("RETRY_HISTORY_WINDOW_MESSAGES", "Окно retry-истории (сообщений)"),
        ],
    ),
    (
        "Сеть и прокси",
        [
            ("TOR_ENABLED", "Tor SOCKS5 прокси"),
            ("TOR_SOCKS_PORT", "Порт Tor SOCKS5"),
            ("BROWSER_FOCUS_TAB", "Фокус вкладки браузера"),
            ("LM_STUDIO_URL", "URL LM Studio"),
            ("OPENCLAW_URL", "URL OpenClaw Gateway"),
        ],
    ),
    (
        "Прочее",
        [
            ("DEFAULT_WEATHER_CITY", "Город погоды по умолчанию"),
            ("MAX_RAM_GB", "Лимит RAM (GB)"),
            ("LOG_LEVEL", "Уровень логирования"),
            ("GEMINI_PAID_KEY_ENABLED", "Платный Gemini API ключ"),
        ],
    ),
]

# Плоский индекс key→описание для быстрого поиска
_CONFIG_KEY_DESC: dict[str, str] = {k: desc for _, group in _CONFIG_GROUPS for k, desc in group}


def _render_config_value(key: str) -> str:
    """Возвращает строковое представление значения ключа конфига."""
    val = getattr(config, key, None)
    if val is None:
        return "—"
    if isinstance(val, (list, frozenset)):
        items = list(val)
        if not items:
            return "(пусто)"
        return ", ".join(str(i) for i in items)
    return str(val)


def _render_config_all() -> str:
    """Форматирует полный вывод !config."""
    lines: list[str] = ["**Конфигурация Краба** — все настройки", ""]
    for group_name, keys in _CONFIG_GROUPS:
        lines.append(f"**{group_name}**")
        for key, desc in keys:
            val = _render_config_value(key)
            lines.append(f"  `{key}` = `{val}` — {desc}")
        lines.append("")
    lines.append("Использование:")
    lines.append("`!config` — показать все")
    lines.append("`!config <KEY>` — одна настройка")
    lines.append("`!config <KEY> <value>` — установить")
    return "\n".join(lines)


async def handle_config(bot: "KraabUserbot", message: Message) -> None:
    """
    Просмотр и редактирование технических настроек Краба.

    !config                 — все ключевые настройки (сгруппировано)
    !config <KEY>           — показать значение одной настройки
    !config <KEY> <value>   — установить значение

    Принимает прямые CONFIG-ключи в любом регистре.
    Отличие от !set: !config охватывает ВСЕ системные настройки,
    !set — user-friendly алиасы.
    """
    raw_args = bot._get_command_args(message).strip() if hasattr(bot, "_get_command_args") else ""

    # Режим 1: показать все настройки
    if not raw_args:
        await message.reply(_render_config_all())
        return

    parts = raw_args.split(maxsplit=1)
    key_input = parts[0].upper()
    has_value = len(parts) > 1
    value_str = parts[1] if has_value else ""

    # Режим 2: показать одну настройку
    if not has_value:
        if hasattr(config, key_input):
            val = _render_config_value(key_input)
            desc = _CONFIG_KEY_DESC.get(key_input, "")
            suffix = f" — {desc}" if desc else ""
            await message.reply(f"`{key_input}` = `{val}`{suffix}")
        else:
            raise UserInputError(
                user_message=(
                    f"❓ Настройка `{key_input}` не найдена.\n"
                    "`!config` — показать все доступные настройки."
                )
            )
        return

    # Режим 3: установить значение
    if not hasattr(config, key_input):
        raise UserInputError(
            user_message=(
                f"❓ Настройка `{key_input}` не найдена.\n"
                "`!config` — показать все доступные настройки."
            )
        )

    ok = config.update_setting(key_input, value_str)
    if ok:
        new_val = _render_config_value(key_input)
        await message.reply(f"✅ `{key_input}` = `{new_val}`")
    else:
        await message.reply(f"❌ Не удалось обновить `{key_input}`. Проверь значение.")


##############################################################################
# Алиасы !set: короткое имя → реальный ключ config (или специальный режим)
##############################################################################
_SET_ALIASES: dict[str, str] = {
    "stream_interval": "TELEGRAM_STREAM_UPDATE_INTERVAL_SEC",
    "reactions": "TELEGRAM_REACTIONS_ENABLED",
    "weather_city": "DEFAULT_WEATHER_CITY",
    "language": "_TRANSLATOR_LANGUAGE",  # особый: через translator-профиль
    "autodel": "_AUTODEL_DEFAULT",  # особый: глобальный default autodel
}

# (алиас → (config_key_или_метка, описание))
_SET_FRIENDLY: dict[str, tuple[str, str]] = {
    "stream_interval": ("TELEGRAM_STREAM_UPDATE_INTERVAL_SEC", "Интервал стриминга (сек)"),
    "reactions": ("TELEGRAM_REACTIONS_ENABLED", "Реакции 👀✅❌ (on/off)"),
    "weather_city": ("DEFAULT_WEATHER_CITY", "Город погоды по умолчанию"),
    "autodel": ("_AUTODEL_DEFAULT", "Автоудаление ответов (сек, 0=выкл)"),
    "language": ("_TRANSLATOR_LANGUAGE", "Языковая пара переводчика"),
}


def _get_set_value(bot: "KraabUserbot", alias: str) -> str:
    """Возвращает текущее значение управляемой настройки по алиасу."""
    if alias == "autodel":
        state: dict = getattr(bot, "_runtime_state", {}) or {}
        settings: dict = state.get(_AUTODEL_STATE_KEY, {})
        val = settings.get("_default", 0)
        return str(val) if val else "0 (выключен)"
    if alias == "language":
        fn = getattr(bot, "get_translator_runtime_profile", None)
        profile: dict = fn() if callable(fn) else {}
        return str(profile.get("language_pair", "es-ru"))
    config_key = _SET_ALIASES.get(alias, alias.upper())
    return str(getattr(config, config_key, "—"))


def _render_all_settings(bot: "KraabUserbot") -> str:
    """Формирует текст со всеми управляемыми настройками."""
    lines = ["⚙️ **Управляемые настройки** (`!set`):", ""]
    for alias, (_, description) in _SET_FRIENDLY.items():
        value = _get_set_value(bot, alias)
        lines.append(f"- `{alias}` = `{value}` — {description}")
    lines.append("")
    lines.append("Использование:")
    lines.append("`!set` — показать всё")
    lines.append("`!set <key>` — показать одну настройку")
    lines.append("`!set <key> <value>` — установить")
    lines.append("")
    lines.append(
        "Также поддерживаются RAW ключи config: `!set TELEGRAM_STREAM_UPDATE_INTERVAL_SEC 3`"
    )
    return "\n".join(lines)


async def handle_set(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление настройками Краба из Telegram.

    !set                    — показать все управляемые настройки
    !set <key>              — показать значение одной настройки
    !set <key> <value>      — установить значение

    Поддерживаемые алиасы:
      stream_interval  → TELEGRAM_STREAM_UPDATE_INTERVAL_SEC
      reactions        → TELEGRAM_REACTIONS_ENABLED (on/off)
      weather_city     → DEFAULT_WEATHER_CITY
      autodel          → глобальный default autodel (сек, 0=выкл)
      language         → языковая пара переводчика (es-ru, en-ru, ...)

    Также принимаются прямые CONFIG-ключи (upper-case).
    """
    raw_args = bot._get_command_args(message).strip() if hasattr(bot, "_get_command_args") else ""
    if not raw_args:
        # Режим 1: показать все настройки
        await message.reply(_render_all_settings(bot))
        return

    parts = raw_args.split(maxsplit=1)
    alias_or_key = parts[0].lower()
    has_value = len(parts) > 1
    value_str = parts[1] if has_value else ""

    # Режим 2: показать одну настройку
    if not has_value:
        if alias_or_key in _SET_FRIENDLY:
            val = _get_set_value(bot, alias_or_key)
            desc = _SET_FRIENDLY[alias_or_key][1]
            await message.reply(f"⚙️ `{alias_or_key}` = `{val}` — {desc}")
        else:
            config_key = alias_or_key.upper()
            if hasattr(config, config_key):
                val = str(getattr(config, config_key))
                await message.reply(f"⚙️ `{config_key}` = `{val}`")
            else:
                raise UserInputError(
                    user_message=f"❓ Настройка `{alias_or_key}` не найдена.\n`!set` — список всех настроек."
                )
        return

    # Режим 3: установить значение
    if alias_or_key == "autodel":
        try:
            delay = float(value_str)
        except ValueError:
            raise UserInputError(
                user_message="❌ `autodel` принимает число секунд (0 = выключить)."
            )
        if not hasattr(bot, "_runtime_state") or bot._runtime_state is None:
            bot._runtime_state = {}
        autodel_settings: dict = bot._runtime_state.setdefault(_AUTODEL_STATE_KEY, {})
        if delay <= 0:
            autodel_settings.pop("_default", None)
            await message.reply("✅ Автоудаление по умолчанию **выключено**.")
        else:
            autodel_settings["_default"] = delay
            await message.reply(f"✅ Автоудаление по умолчанию: **{delay:.0f} сек**.")
        return

    if alias_or_key == "language":
        if not hasattr(bot, "update_translator_runtime_profile"):
            raise UserInputError(user_message="❌ Translator не инициализирован.")
        value_norm = value_str.strip().lower()
        try:
            from ..core.translator_runtime_profile import ALLOWED_LANGUAGE_PAIRS

            if value_norm != "auto-detect" and value_norm not in ALLOWED_LANGUAGE_PAIRS:
                pairs = ", ".join(sorted(ALLOWED_LANGUAGE_PAIRS))
                raise UserInputError(
                    user_message=(
                        f"❌ Неизвестная языковая пара `{value_norm}`.\n"
                        f"Доступны: `{pairs}`, `auto-detect`."
                    )
                )
            profile = bot.update_translator_runtime_profile(language_pair=value_norm, persist=True)
            new_pair = profile.get("language_pair", value_norm)
            await message.reply(f"✅ Языковая пара переводчика: `{new_pair}`")
        except UserInputError:
            raise
        except Exception as exc:  # noqa: BLE001
            await message.reply(f"❌ Ошибка установки языковой пары: {str(exc)[:120]}")
        return

    # Стандартный путь: алиас → CONFIG-ключ или прямой ключ
    config_key = _SET_ALIASES.get(alias_or_key, alias_or_key.upper())
    if config_key.startswith("_"):
        # Спец-ключ без config-атрибута — не должен сюда попасть (autodel/language уже выше)
        raise UserInputError(
            user_message=f"❓ Настройка `{alias_or_key}` не найдена.\n`!set` — список всех настроек."
        )

    if config.update_setting(config_key, value_str):
        extra = ""
        if config_key == "SCHEDULER_ENABLED" and hasattr(bot, "_sync_scheduler_runtime"):
            try:
                bot._sync_scheduler_runtime()
                sched_state = "ON" if bool(getattr(config, "SCHEDULER_ENABLED", False)) else "OFF"
                extra = f"\n⏰ Scheduler runtime: `{sched_state}`"
            except Exception as exc:  # noqa: BLE001
                extra = f"\n⚠️ Scheduler sync warning: `{str(exc)[:120]}`"
        display_name = alias_or_key if alias_or_key in _SET_ALIASES else config_key
        await message.reply(f"✅ `{display_name}` обновлено!{extra}")
    else:
        await message.reply("❌ Ошибка обновления.")


async def handle_acl(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление runtime ACL userbot.

    Доступно только owner-контуру.
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(
            user_message=(
                "🔒 Управление ACL доступно только владельцу.\n"
                "Можно попросить владельца выдать full или partial доступ."
            )
        )

    raw_args = bot._get_command_args(message).strip()
    parts = raw_args.split()
    action = str(parts[0] or "status").strip().lower() if parts else "status"
    state = load_acl_runtime_state()

    def _render_state() -> str:
        full_items = state.get(AccessLevel.FULL.value, [])
        partial_items = state.get(AccessLevel.PARTIAL.value, [])
        owner_items = state.get(AccessLevel.OWNER.value, [])
        return (
            "🛂 **Runtime ACL userbot**\n"
            "-----------------------\n"
            f"- Файл: `{config.USERBOT_ACL_FILE}`\n"
            f"- Владелец (effective): `{get_effective_owner_label()}`\n"
            f"- Fallback owner_username (config): `{config.OWNER_USERNAME}`\n"
            f"- Owner в runtime-файле: `{', '.join(owner_items) if owner_items else '-'}`\n"
            f"- Full: `{', '.join(full_items) if full_items else '-'}`\n"
            f"- Partial: `{', '.join(partial_items) if partial_items else '-'}`\n"
            f"- Partial-команды: `{', '.join(sorted(PARTIAL_ACCESS_COMMANDS))}`\n\n"
            "Команды:\n"
            "- `!acl status`\n"
            "- `!acl grant full @username`\n"
            "- `!acl grant partial @username`\n"
            "- `!acl revoke full @username`\n"
            "- `!acl revoke partial @username`\n"
            "- `!acl list`"
        )

    if action in {"", "status", "list"}:
        await message.reply(_render_state())
        return

    if action not in {"grant", "revoke"}:
        raise UserInputError(
            user_message=(
                "❌ Неизвестное действие ACL.\nИспользуй: `status`, `list`, `grant`, `revoke`."
            )
        )

    if len(parts) < 3:
        raise UserInputError(
            user_message=(
                "❌ Формат ACL-команды:\n"
                "- `!acl grant full @username`\n"
                "- `!acl grant partial 123456789`\n"
                "- `!acl revoke full @username`"
            )
        )

    level = str(parts[1] or "").strip().lower()
    subject = str(parts[2] or "").strip()
    if level not in {AccessLevel.FULL.value, AccessLevel.PARTIAL.value}:
        raise UserInputError(user_message="❌ Можно изменять только уровни `full` и `partial`.")

    result = update_acl_subject(level, subject, add=(action == "grant"))
    state = result["state"]
    verb = "выдан" if action == "grant" else "снят"
    changed_note = "обновлено" if result["changed"] else "без изменений"
    await message.reply(
        "✅ ACL обновлён.\n"
        f"- Уровень: `{level}`\n"
        f"- Subject: `{result['subject']}`\n"
        f"- Результат: `{verb}` / {changed_note}\n"
        f"- Full: `{', '.join(state.get('full', [])) if state.get('full') else '-'}`\n"
        f"- Partial: `{', '.join(state.get('partial', [])) if state.get('partial') else '-'}`"
    )


async def handle_scope(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление ACL правами из Telegram через команду !scope.

    Без аргументов — показывает ACL-уровень отправителя (доступно любому).
    Подкоманды (owner-only): grant, revoke, list.

    Синтаксис:
    - !scope                         — мой текущий уровень доступа
    - !scope grant <user_id> full    — выдать full доступ
    - !scope grant <user_id> partial — выдать partial доступ
    - !scope revoke <user_id>        — отозвать все уровни (full + partial)
    - !scope list                    — список всех ACL-записей
    """
    raw_args = bot._get_command_args(message).strip()
    parts = raw_args.split()
    action = parts[0].strip().lower() if parts else ""

    # Без аргументов — показываем уровень доступа отправителя (доступно всем)
    if not action:
        user = message.from_user
        profile = bot._get_access_profile(user)
        user_id = str(getattr(user, "id", "") or "")
        username = str(getattr(user, "username", "") or "")
        display = f"@{username}" if username else f"id:{user_id}"
        level_emoji = {
            AccessLevel.OWNER: "👑",
            AccessLevel.FULL: "🔓",
            AccessLevel.PARTIAL: "🔑",
            AccessLevel.GUEST: "👤",
        }.get(profile.level, "❓")
        await message.reply(
            f"🛂 **Уровень доступа**: {level_emoji} `{profile.level.value}`\n"
            f"- Пользователь: {display}\n"
            f"- Источник ACL: `{profile.source}`\n\n"
            "Используй `!scope grant <user_id> full|partial` чтобы выдать права.\n"
            "Используй `!scope revoke <user_id>` чтобы отозвать.\n"
            "Используй `!scope list` для просмотра всех ACL-записей."
        )
        return

    # list — только owner
    if action == "list":
        access_profile = bot._get_access_profile(message.from_user)
        if access_profile.level != AccessLevel.OWNER:
            raise UserInputError(user_message="🔒 `!scope list` доступен только владельцу.")
        state = load_acl_runtime_state()
        owner_items = state.get(AccessLevel.OWNER.value, [])
        full_items = state.get(AccessLevel.FULL.value, [])
        partial_items = state.get(AccessLevel.PARTIAL.value, [])
        await message.reply(
            "🛂 **Все ACL-записи userbot**\n"
            "--------------------------\n"
            f"👑 Owner: `{', '.join(owner_items) if owner_items else '-'}`\n"
            f"🔓 Full:  `{', '.join(full_items) if full_items else '-'}`\n"
            f"🔑 Partial: `{', '.join(partial_items) if partial_items else '-'}`\n\n"
            f"Effective owner: `{get_effective_owner_label()}`"
        )
        return

    # grant / revoke — только owner
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(
            user_message=("🔒 Управление правами `!scope grant/revoke` доступно только владельцу.")
        )

    if action == "grant":
        # !scope grant <user_id> full|partial
        if len(parts) < 3:
            raise UserInputError(
                user_message=(
                    "❌ Формат: `!scope grant <user_id> full|partial`\n"
                    "Пример: `!scope grant 123456789 full`"
                )
            )
        subject = parts[1].strip()
        level_raw = parts[2].strip().lower()
        if level_raw not in {AccessLevel.FULL.value, AccessLevel.PARTIAL.value}:
            raise UserInputError(user_message="❌ Уровень должен быть `full` или `partial`.")
        result = update_acl_subject(level_raw, subject, add=True)
        state = result["state"]
        changed_note = "обновлено" if result["changed"] else "без изменений"
        await message.reply(
            f"✅ Доступ выдан.\n"
            f"- Subject: `{result['subject']}`\n"
            f"- Уровень: `{level_raw}`\n"
            f"- Статус: {changed_note}\n"
            f"- Full: `{', '.join(state.get('full', [])) if state.get('full') else '-'}`\n"
            f"- Partial: `{', '.join(state.get('partial', [])) if state.get('partial') else '-'}`"
        )
        return

    if action == "revoke":
        # !scope revoke <user_id> — удаляет из ВСЕХ уровней (full + partial)
        if len(parts) < 2:
            raise UserInputError(
                user_message=(
                    "❌ Формат: `!scope revoke <user_id>`\nПример: `!scope revoke 123456789`"
                )
            )
        subject = parts[1].strip()
        # Удаляем из full и partial (owner нельзя отозвать через !scope)
        removed_from: list[str] = []
        for level_val in (AccessLevel.FULL.value, AccessLevel.PARTIAL.value):
            res = update_acl_subject(level_val, subject, add=False)
            if res["changed"]:
                removed_from.append(level_val)
        if removed_from:
            removed_str = ", ".join(f"`{lvl}`" for lvl in removed_from)
            await message.reply(
                f"✅ Доступ отозван.\n"
                f"- Subject: `{normalize_subject(subject)}`\n"
                f"- Удалён из уровней: {removed_str}"
            )
        else:
            await message.reply(
                f"ℹ️ Subject `{normalize_subject(subject)}` не найден ни в одном уровне ACL.\n"
                "Ничего не изменено."
            )
        return

    raise UserInputError(
        user_message=(
            "❌ Неизвестное действие. Доступные подкоманды:\n"
            "- `!scope` — мой уровень доступа\n"
            "- `!scope grant <user_id> full|partial`\n"
            "- `!scope revoke <user_id>`\n"
            "- `!scope list`"
        )
    )


async def handle_reasoning(bot: "KraabUserbot", message: Message) -> None:
    """
    Показывает скрытую reasoning-trace отдельно от основного ответа.

    Это owner/debug-команда: мысли не идут в обычный ответ, но владелец может
    посмотреть последний скрытый trace по явному запросу.
    """
    args = str(bot._get_command_args(message) or "").strip().lower()
    chat_id = str(getattr(getattr(message, "chat", None), "id", "") or "")

    if args in {"clear", "reset"}:
        cleared = bool(bot.clear_hidden_reasoning_trace_snapshot(chat_id))
        await message.reply(
            "🧼 Скрытая reasoning-trace для этого чата очищена."
            if cleared
            else "🧼 Для этого чата пока нечего очищать: reasoning-trace ещё не накоплена."
        )
        return

    trace = bot.get_hidden_reasoning_trace_snapshot(chat_id)
    if not trace:
        await message.reply(
            "🧠 Для этого чата пока нет сохранённой reasoning-trace.\n"
            "Сначала дождись обычного ответа Краба, а потом вызови `!reasoning`."
        )
        return

    lines = [
        "🧠 **Скрытая reasoning-trace**",
        f"- Updated: `{trace.get('updated_at') or '-'}`",
        f"- Transport: `{trace.get('transport_mode') or 'unknown'}`",
        f"- Route: `{trace.get('route_channel') or '-'} / {trace.get('route_model') or '-'}`",
        f"- Query: `{trace.get('query') or '-'}`",
        f"- Preview: `{trace.get('answer_preview') or '-'}`",
    ]
    if not bool(trace.get("available")):
        lines.append(
            "⚠️ Для последнего ответа отдельный reasoning-блок не пришёл: "
            "провайдер вернул только финальный текст или скрытые мысли не доехали до транспорта."
        )
        await message.reply("\n".join(lines))
        return

    body = "\n".join(lines + ["", str(trace.get("reasoning") or "").strip()])
    for chunk in _split_text_for_telegram(body):
        await message.reply(chunk)


async def handle_role(bot: "KraabUserbot", message: Message) -> None:
    """Смена системного промпта (личности)."""
    args = message.text.split()
    if len(args) < 2 or args[1] == "list":
        await message.reply(f"🎭 **Роли:**\n{list_roles()}")
    else:
        role = args[1] if len(args) == 2 else args[2]
        if role in ROLES:
            bot.current_role = role
            await message.reply(f"🎭 Теперь я: `{role}`")
        else:
            raise UserInputError(user_message="❌ Роль не найдена.")


async def handle_notify(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление streaming tool notifications в Telegram.

    !notify on  — показывать какой инструмент вызывается (🔍 Ищу..., 📸 Скриншот...)
    !notify off — не показывать (чище, меньше сообщений)
    !notify     — текущий статус
    """
    from ..config import config as _cfg

    args = bot._get_command_args(message).strip().lower()
    if args in {"on", "1", "true", "yes"}:
        _cfg.update_setting("TOOL_NARRATION_ENABLED", "1")
        await message.reply(
            "🔔 Tool notifications: **ON**\nБуду показывать какой инструмент вызываю."
        )
    elif args in {"off", "0", "false", "no"}:
        _cfg.update_setting("TOOL_NARRATION_ENABLED", "0")
        await message.reply(
            "🔕 Tool notifications: **OFF**\nИнструменты молча, только финальный ответ."
        )
    else:
        status = "ON ✅" if getattr(_cfg, "TOOL_NARRATION_ENABLED", True) else "OFF 🔕"
        await message.reply(
            f"🔔 Tool notifications: **{status}**\n\n"
            "Команды:\n"
            "`!notify on` — показывать инструменты\n"
            "`!notify off` — скрыть инструменты"
        )


def _render_chat_ban_entries(entries: list[dict[str, Any]]) -> str:
    """Форматирует список chat ban cache записей для `!chatban status`."""
    if not entries:
        return (
            "🚫 **Chat ban cache** пуст.\n"
            "Записи создаются автоматически при `USER_BANNED_IN_CHANNEL` / "
            "`ChatWriteForbidden`. Ручное управление: `!chatban clear <chat_id>`."
        )
    lines: list[str] = [f"🚫 **Chat ban cache ({len(entries)}):**"]
    for entry in entries:
        chat_id = entry.get("chat_id", "—")
        code = entry.get("last_error_code") or entry.get("error_code") or "?"
        expires = entry.get("expires_at") or "permanent"
        hits = entry.get("hit_count", 1)
        lines.append(f"- `{chat_id}` · {code} · expires={expires} · hits={hits}")
    lines.append("\nСнять: `!chatban clear <chat_id>`")
    return "\n".join(lines)


async def handle_chatban(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление persisted chat ban cache (B.8).

    Когда Telegram возвращает `USER_BANNED_IN_CHANNEL` / `ChatWriteForbidden`
    на попытку отправить сообщение, Краб помечает чат в cache. Пока запись
    активна, Краб вообще не обрабатывает входящие из этого чата —
    не гоняет LLM и не пытается `send_message`. Это защищает от:
    - бессмысленных Gemini-токенов на ответы которые не дойдут;
    - повторных API-вызовов в забаненный чат, которые Telegram считает
      агрессивным паттерном и за которые продлевает SpamBot limit.

    Subcommands:
    - `!chatban` / `!chatban status` — показать текущие записи;
    - `!chatban clear <chat_id>` — убрать конкретную запись вручную
      (если уверен что ban снят).
    """
    args = str(message.text or "").split()
    sub = args[1].strip().lower() if len(args) >= 2 else "status"

    if sub in {"", "status", "show", "list"}:
        entries = chat_ban_cache.list_entries()
        await message.reply(_render_chat_ban_entries(entries))
        return

    if sub == "clear":
        if len(args) < 3 or not args[2].strip():
            raise UserInputError(user_message="❌ Укажи chat_id: `!chatban clear <chat_id>`")
        target = args[2].strip()
        removed = chat_ban_cache.clear(target)
        if removed:
            await message.reply(
                f"✅ Убрал `{target}` из chat ban cache. "
                f"Краб снова будет обрабатывать сообщения оттуда."
            )
        else:
            await message.reply(f"ℹ️ `{target}` не был в chat ban cache (уже снят или не помечен).")
        return

    raise UserInputError(
        user_message="❌ Неизвестная подкоманда chatban. Используй `!chatban status` или `!chatban clear <chat_id>`."
    )


async def handle_cmdblock(bot: "KraabUserbot", message: Message) -> None:
    """!block <cmd> — заблокировать команду в текущем чате (owner-only).

    Краб будет молча игнорировать команду в этом чате (silent skip, без ошибки).
    Полезно когда в чате есть другой бот с тем же триггером.
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="❌ Только owner.")

    args = str(message.text or "").split()
    if len(args) < 2 or not args[1].strip():
        raise UserInputError(user_message="❌ Укажи команду: `!block <cmd>` (без префикса)")

    cmd = args[1].strip()
    chat_id = message.chat.id
    added = command_blocklist.add_block(chat_id, cmd)
    if added:
        await message.reply(
            f"✅ Команда `!{cmd}` заблокирована в этом чате. "
            f"Краб будет молча её игнорировать.\n"
            f"`!blocklist` — посмотреть все блоки."
        )
    else:
        await message.reply(f"ℹ️ `!{cmd}` уже была в blocklist для этого чата.")


async def handle_cmdunblock(bot: "KraabUserbot", message: Message) -> None:
    """!unblock <cmd> — убрать команду из blocklist текущего чата (owner-only)."""
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="❌ Только owner.")

    args = str(message.text or "").split()
    if len(args) < 2 or not args[1].strip():
        raise UserInputError(user_message="❌ Укажи команду: `!unblock <cmd>`")

    cmd = args[1].strip()
    chat_id = message.chat.id
    removed = command_blocklist.remove_block(chat_id, cmd)
    if removed:
        await message.reply(f"✅ `!{cmd}` убрана из blocklist. Краб снова будет обрабатывать её.")
    else:
        await message.reply(f"ℹ️ `!{cmd}` не была в blocklist для этого чата.")


async def handle_blocklist(bot: "KraabUserbot", message: Message) -> None:
    """!blocklist [<chat_id>] — показать per-chat command blocklist (owner-only).

    Без аргументов — все блоки текущего чата.
    С аргументом `all` — все чаты.
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="❌ Только owner.")

    args = str(message.text or "").split()
    show_all = len(args) >= 2 and args[1].strip().lower() == "all"

    if show_all:
        all_blocks = command_blocklist.list_blocks()
        if not all_blocks:
            await message.reply("ℹ️ Command blocklist пуст.")
            return
        lines = ["**Command Blocklist (все чаты):**"]
        for key, cmds in all_blocks.items():
            label = "global (*)" if key == "*" else f"chat `{key}`"
            lines.append(f"  {label}: `{'`, `'.join(cmds)}`")
        await message.reply("\n".join(lines))
    else:
        chat_id = message.chat.id
        blocks = command_blocklist.list_blocks(chat_id)
        global_blocks = command_blocklist.list_blocks("*")
        if not blocks and not global_blocks:
            await message.reply(f"ℹ️ Blocklist для чата `{chat_id}` пуст.\nДобавить: `!block <cmd>`")
            return
        lines = [f"**Command Blocklist** (чат `{chat_id}`):"]
        if global_blocks:
            lines.append(f"  global (*): `{'`, `'.join(global_blocks)}`")
        if blocks:
            lines.append(f"  этот чат: `{'`, `'.join(blocks)}`")
        lines.append("\nУбрать: `!unblock <cmd>`")
        await message.reply("\n".join(lines))


# !translator — extracted to commands/translator_commands.py (Phase 2 Wave 9, Session 27).
# Re-exported above (handle_translator + render helpers + _parse_toggle_arg).


async def handle_web(bot: "KraabUserbot", message: Message) -> None:
    """Автоматизация браузера."""
    from ..web_session import web_manager

    args = message.text.split()
    if len(args) < 2:
        from urllib.parse import quote

        def link(c: str) -> str:
            return f"https://t.me/share/url?url={quote(c)}"

        await message.reply(
            "🌏 **Web Control**\n\n"
            f"[🔑 Login]({link('!web login')}) | [📸 Screen]({link('!web screen')})\n"
            f"[🤖 GPT]({link('!web gpt привет')})",
            disable_web_page_preview=True,
        )
        return
    sub = args[1].lower()
    if sub == "login":
        await message.reply(await web_manager.login_mode())
    elif sub == "screen":
        path = await web_manager.take_screenshot()
        if path:
            await message.reply_photo(path)
            if os.path.exists(path):
                os.remove(path)
    elif sub == "stop":
        await web_manager.stop()
        await message.reply("🛑 Web остановлен.")
    elif sub == "self-test":
        await bot._run_self_test(message)


# _format_uptime_str + handle_sysinfo + handle_uptime + handle_panel + handle_version —
# extracted to commands/system_commands.py (Phase 2 Wave 10, Session 27).


async def handle_macos(bot: "KraabUserbot", message: Message) -> None:
    """
    Базовое управление macOS из owner/full-контура.

    Держим здесь только понятные и контролируемые действия:
    clipboard, уведомления, активные приложения, `open` и Finder reveal.
    Это даёт реальную пользу уже сейчас и служит фундаментом для следующего
    этапа с Calendar/Reminders/Notes.
    """
    del bot
    raw_args = str(message.text or "").split(maxsplit=1)
    args = raw_args[1].strip() if len(raw_args) > 1 else ""

    if not macos_automation.is_available():
        await message.reply(
            "🍎 macOS automation сейчас недоступен.\n"
            "Нужны `osascript`, `open`, `pbcopy`, `pbpaste` и запуск на macOS."
        )
        return

    if not args:
        await message.reply(
            "🍎 **macOS control layer**\n\n"
            "`!mac status` — краткий статус desktop-контура\n"
            "`!mac clip get` — прочитать clipboard\n"
            "`!mac clip set <текст>` — записать clipboard\n"
            "`!mac notify <текст>` — показать системное уведомление\n"
            "`!mac notify <заголовок> | <текст>` — уведомление с заголовком\n"
            "`!mac app front` — активное приложение\n"
            "`!mac app list` — список видимых приложений\n"
            "`!mac app open <имя>` — открыть приложение\n"
            "`!mac focus <имя>` — вывести приложение на передний план\n"
            "`!mac type <текст>` — напечатать текст в активном окне\n"
            "`!mac typeclip <текст>` — вставить текст через clipboard (Unicode/кириллица)\n"
            "`!mac click <приложение> <кнопка>` — нажать кнопку UI элемент\n"
            "`!mac key <клавиша>` — нажать клавишу (return/tab/escape/...)\n"
            "`!mac reminders list` — список напоминаний из macOS Reminders\n"
            "`!mac reminders add <время> | <текст>` — создать reminder в Reminders\n"
            "`!mac notes list` — список заметок\n"
            "`!mac notes add <заголовок> | <текст>` — создать заметку\n"
            "`!mac calendar list` — список календарей\n"
            "`!mac calendar events` — ближайшие события\n"
            "`!mac calendar add <время> | <название>` — создать событие (30 мин)\n"
            "`!mac open <url|path>` — открыть URL или путь\n"
            "`!mac finder reveal <path>` — показать файл/папку в Finder"
        )
        return

    parts = args.split(maxsplit=2)
    sub = parts[0].lower()

    if sub == "status":
        status = await macos_automation.status()
        lines = [
            "🍎 **macOS control layer**",
            f"- Доступность: {'ON' if status.get('available') else 'OFF'}",
            f"- Активное приложение: `{status.get('frontmost_app') or 'n/a'}`",
        ]
        if status.get("frontmost_window"):
            lines.append(f"- Переднее окно: `{status.get('frontmost_window')}`")
        running_apps = status.get("running_apps") or []
        if running_apps:
            lines.append("- Видимые приложения: " + ", ".join(f"`{item}`" for item in running_apps))
        lines.append(
            f"- Clipboard: {int(status.get('clipboard_chars', 0) or 0)} символов"
            + (f" (`{status.get('clipboard_preview')}`)" if status.get("clipboard_preview") else "")
        )
        warnings = status.get("warnings") or []
        if warnings:
            lines.append("- Warnings: " + "; ".join(str(item) for item in warnings[:3]))
        reminder_lists = status.get("reminder_lists") or []
        note_folders = status.get("note_folders") or []
        calendars = status.get("calendars") or []
        if reminder_lists:
            lines.append(
                "- Reminders lists: " + ", ".join(f"`{item}`" for item in reminder_lists[:5])
            )
        if note_folders:
            lines.append("- Notes folders: " + ", ".join(f"`{item}`" for item in note_folders[:5]))
        if calendars:
            lines.append("- Calendars: " + ", ".join(f"`{item}`" for item in calendars[:6]))
        await message.reply("\n".join(lines))
        return

    if sub == "reminders":
        if len(parts) < 2:
            raise UserInputError(
                user_message="🍎 Формат: `!mac reminders list` или `!mac reminders add <время> | <текст>`"
            )
        rem_action = parts[1].lower()
        if rem_action == "list":
            rows = await macos_automation.list_reminders(limit=8)
            if not rows:
                await message.reply("📝 В macOS Reminders сейчас нет незавершённых напоминаний.")
                return
            lines = ["📝 **Reminders (macOS)**"]
            for item in rows:
                due = f" · `{item['due_label']}`" if item.get("due_label") else ""
                lines.append(f"- `{item['title']}` — список `{item['list_name']}`{due}")
            await message.reply("\n".join(lines))
            return
        if rem_action == "add":
            payload = args.split(maxsplit=2)[2] if len(args.split(maxsplit=2)) > 2 else ""
            time_spec, reminder_text = split_reminder_input(payload)
            if not time_spec or not reminder_text:
                raise UserInputError(
                    user_message="🍎 Формат: `!mac reminders add <время> | <текст>`"
                )
            due_at = parse_due_time(time_spec)
            created = await macos_automation.create_reminder(title=reminder_text, due_at=due_at)
            due_label = due_at.astimezone().strftime("%d.%m.%Y %H:%M")
            await message.reply(
                "✅ Reminder создан в macOS Reminders.\n"
                f"- ID: `{created['id']}`\n"
                f"- Список: `{created['list_name']}`\n"
                f"- Когда: `{due_label}`\n"
                f"- Текст: {reminder_text}"
            )
            return
        raise UserInputError(
            user_message="🍎 Формат: `!mac reminders list` или `!mac reminders add <время> | <текст>`"
        )

    if sub == "notes":
        if len(parts) < 2:
            raise UserInputError(
                user_message="🍎 Формат: `!mac notes list` или `!mac notes add <заголовок> | <текст>`"
            )
        notes_action = parts[1].lower()
        if notes_action == "list":
            rows = await macos_automation.list_notes(limit=8)
            if not rows:
                await message.reply("🗒️ В Notes пока ничего не найдено.")
                return
            lines = ["🗒️ **Notes (macOS)**"]
            for item in rows:
                lines.append(
                    f"- `{item['title']}` — папка `{item['folder_name']}`, аккаунт `{item['account_name']}`"
                )
            await message.reply("\n".join(lines))
            return
        if notes_action == "add":
            payload = args.split(maxsplit=2)[2] if len(args.split(maxsplit=2)) > 2 else ""
            if "|" not in payload:
                raise UserInputError(
                    user_message="🍎 Формат: `!mac notes add <заголовок> | <текст>`"
                )
            raw_title, raw_body = payload.split("|", 1)
            title = raw_title.strip()
            body = raw_body.strip()
            if not title or not body:
                raise UserInputError(
                    user_message="🍎 Заголовок и текст заметки не должны быть пустыми."
                )
            created = await macos_automation.create_note(title=title, body=body)
            await message.reply(
                "✅ Заметка создана в Notes.\n"
                f"- ID: `{created['id']}`\n"
                f"- Папка: `{created['folder_name']}`\n"
                f"- Заголовок: `{title}`"
            )
            return
        raise UserInputError(
            user_message="🍎 Формат: `!mac notes list` или `!mac notes add <заголовок> | <текст>`"
        )

    if sub == "calendar":
        if len(parts) < 2:
            raise UserInputError(
                user_message="🍎 Формат: `!mac calendar list`, `!mac calendar events` или `!mac calendar add <время> | <название>`"
            )
        cal_action = parts[1].lower()
        if cal_action == "list":
            rows = await macos_automation.list_calendars()
            await message.reply(
                "📆 **Calendars (macOS)**\n"
                + ("\n".join(f"- `{item}`" for item in rows[:12]) if rows else "- список пуст")
            )
            return
        if cal_action == "events":
            rows = await macos_automation.list_upcoming_calendar_events(limit=8, days_ahead=7)
            if not rows:
                await message.reply("📆 На ближайшие 7 дней событий не найдено.")
                return
            lines = ["📆 **Ближайшие события Calendar**"]
            for item in rows:
                lines.append(
                    f"- `{item['title']}` — календарь `{item['calendar_name']}` · `{item['start_label']}`"
                )
            await message.reply("\n".join(lines))
            return
        if cal_action == "add":
            payload = args.split(maxsplit=2)[2] if len(args.split(maxsplit=2)) > 2 else ""
            time_spec, event_title = split_reminder_input(payload)
            if not time_spec or not event_title:
                raise UserInputError(
                    user_message="🍎 Формат: `!mac calendar add <время> | <название>`"
                )
            start_at = parse_due_time(time_spec)
            created = await macos_automation.create_calendar_event(
                title=event_title, start_at=start_at, duration_minutes=30
            )
            start_label = start_at.astimezone().strftime("%d.%m.%Y %H:%M")
            await message.reply(
                "✅ Событие создано в Calendar.\n"
                f"- ID: `{created['id']}`\n"
                f"- Календарь: `{created['calendar_name']}`\n"
                f"- Начало: `{start_label}`\n"
                f"- Название: `{event_title}`"
            )
            return
        raise UserInputError(
            user_message="🍎 Формат: `!mac calendar list`, `!mac calendar events` или `!mac calendar add <время> | <название>`"
        )

    if sub in {"clip", "clipboard"}:
        if len(parts) < 2:
            raise UserInputError(
                user_message="🍎 Формат: `!mac clip get` или `!mac clip set <текст>`"
            )
        clip_action = parts[1].lower()
        if clip_action == "get":
            content = await macos_automation.get_clipboard_text()
            preview = content if len(content) <= 3400 else content[:3400] + "…"
            await message.reply(
                "📋 **Clipboard**\n\n"
                + (f"```\n{preview}\n```" if preview else "_Буфер обмена пустой или не текстовый._")
            )
            return
        if clip_action == "set":
            if len(parts) < 3 or not parts[2].strip():
                raise UserInputError(user_message="🍎 Формат: `!mac clip set <текст>`")
            await macos_automation.set_clipboard_text(parts[2])
            await message.reply(f"📋 Clipboard обновлён: `{parts[2][:120]}`")
            return
        raise UserInputError(user_message="🍎 Формат: `!mac clip get` или `!mac clip set <текст>`")

    if sub == "notify":
        payload = args[len("notify") :].strip()
        if not payload:
            raise UserInputError(
                user_message="🍎 Формат: `!mac notify <текст>` или `!mac notify <заголовок> | <текст>`"
            )
        title = "Краб"
        body = payload
        if "|" in payload:
            raw_title, raw_body = payload.split("|", 1)
            title = raw_title.strip() or "Краб"
            body = raw_body.strip()
        if not body:
            raise UserInputError(user_message="🍎 Уведомление не может быть пустым.")
        await macos_automation.show_notification(title=title, message=body)
        await message.reply(f"🔔 Уведомление отправлено: `{title}`")
        return

    if sub == "app":
        if len(parts) < 2:
            raise UserInputError(user_message="🍎 Формат: `!mac app front|list|open <имя>`")
        app_action = parts[1].lower()
        if app_action == "front":
            front = await macos_automation.get_frontmost_app()
            reply = f"🪟 Активное приложение: `{front.get('app_name') or 'n/a'}`"
            if front.get("window_title"):
                reply += f"\nЗаголовок окна: `{front['window_title']}`"
            await message.reply(reply)
            return
        if app_action == "list":
            apps = await macos_automation.list_running_apps(limit=12)
            await message.reply(
                "🧩 **Видимые приложения**\n"
                + ("\n".join(f"- `{item}`" for item in apps) if apps else "\n- список пуст")
            )
            return
        if app_action == "open":
            if len(parts) < 3 or not parts[2].strip():
                raise UserInputError(user_message="🍎 Формат: `!mac app open <имя приложения>`")
            opened = await macos_automation.open_app(parts[2])
            await message.reply(f"🚀 Открываю приложение: `{opened}`")
            return
        raise UserInputError(user_message="🍎 Формат: `!mac app front|list|open <имя>`")

    if sub == "open":
        target = args[len("open") :].strip()
        if not target:
            raise UserInputError(user_message="🍎 Формат: `!mac open <url|path>`")
        opened = await macos_automation.open_target(target)
        await message.reply(f"🚀 Открываю {opened['kind']}: `{opened['target']}`")
        return

    if sub == "finder":
        if len(parts) < 3 or parts[1].lower() != "reveal":
            raise UserInputError(user_message="🍎 Формат: `!mac finder reveal <path>`")
        revealed = await macos_automation.reveal_in_finder(parts[2])
        await message.reply(f"📂 Показываю в Finder: `{revealed}`")
        return

    # Phase 3 Шаг 4: UI automation
    if sub == "focus":
        app_arg = args[len("focus") :].strip()
        if not app_arg:
            raise UserInputError(user_message="🍎 Формат: `!mac focus <имя приложения>`")
        result = await macos_automation.focus_app(app_arg)
        await message.reply(f"🪟 Фокус: `{result['app_name']}`")
        return

    if sub == "type":
        text_arg = args[len("type") :].strip()
        if not text_arg:
            raise UserInputError(user_message="🍎 Формат: `!mac type <текст>`")
        result = await macos_automation.type_text(text_arg)
        await message.reply(
            f"⌨️ Напечатано {result['text_length']} символов в `{result['app_name']}`"
        )
        return

    if sub == "typeclip":
        text_arg = args[len("typeclip") :].strip()
        if not text_arg:
            raise UserInputError(
                user_message="🍎 Формат: `!mac typeclip <текст>` (через clipboard, поддерживает Unicode)"
            )
        result = await macos_automation.type_text_via_clipboard(text_arg)
        await message.reply(
            f"📋→⌨️ Вставлено {result['text_length']} символов в `{result['app_name']}`"
        )
        return

    if sub == "click":
        # !mac click <app> <element>
        if len(parts) < 3:
            raise UserInputError(user_message="🍎 Формат: `!mac click <приложение> <кнопка>`")
        app_arg = parts[1]
        elem_arg = " ".join(parts[2:])
        result = await macos_automation.click_ui_element(app_arg, elem_arg)
        await message.reply(f"🖱 Нажато: `{result['element']}` в `{result['app_name']}`")
        return

    if sub == "key":
        key_arg = args[len("key") :].strip()
        if not key_arg:
            raise UserInputError(
                user_message="🍎 Формат: `!mac key <клавиша>` (return/tab/escape/...)"
            )
        result = await macos_automation.press_key(key_arg)
        await message.reply(f"⌨️ Нажато: `{result['key']}`")
        return

    raise UserInputError(
        user_message=(
            "🍎 Неизвестная подкоманда macOS.\n"
            "Используй: `!mac status`, `!mac clip ...`, `!mac notify ...`, "
            "`!mac app ...`, `!mac focus ...`, `!mac type ...`, `!mac click ...`, "
            "`!mac key ...`, `!mac open ...`, `!mac finder reveal ...`"
        )
    )


# handle_restart — extracted to commands/system_commands.py (Phase 2 Wave 10, Session 27).
# Re-exported above.


# !agent — extracted to commands/ai_commands.py (Phase 2 Wave 7, Session 27).
# Re-exported above (handle_agent).


async def handle_help(bot: "KraabUserbot", message: Message) -> None:
    """Справка по командам — генерируется из command_registry, с пагинацией."""
    from ..core.command_registry import registry as _reg

    # Эмодзи-иконки для категорий
    _category_icons: dict[str, str] = {
        "basic": "📋",
        "ai": "💬",
        "models": "🤖",
        "translator": "🔄",
        "swarm": "🐝",
        "costs": "💰",
        "notes": "📝",
        "management": "⚙️",
        "modes": "🔇",
        "users": "👤",
        "scheduler": "⏰",
        "system": "🖥️",
        "dev": "🛠️",
    }
    _category_labels: dict[str, str] = {
        "basic": "Основные",
        "ai": "AI",
        "models": "Модели",
        "translator": "Translator",
        "swarm": "Swarm (рой агентов)",
        "costs": "Расходы и бюджет",
        "notes": "Заметки и закладки",
        "management": "Управление сообщениями",
        "modes": "Режимы и фильтры",
        "users": "Пользователи и доступ",
        "scheduler": "Планировщик",
        "system": "Система и macOS",
        "dev": "Dev / AI CLI",
    }

    def _build_part(cat_list: list[str], header: str) -> str:
        """Строит текст части справки для заданных категорий."""
        parts = [header]
        for cat in cat_list:
            icon = _category_icons.get(cat, "•")
            label = _category_labels.get(cat, cat)
            lines = [f"{icon} **{label}**"]
            for cmd in _reg.by_category(cat):
                lines.append(f"`!{cmd.name}` — {cmd.description}")
            parts.append("\n".join(lines))
        return "\n\n".join(parts)

    cats = _reg.categories()
    half = len(cats) // 2
    part1_cats = cats[:half]
    part2_cats = cats[half:]

    part1 = _build_part(part1_cats, "🦀 **Krab Commands** (1/2)\n━━━━━━━━━━━━━━━")
    part2 = _build_part(part2_cats, "🦀 **Krab Commands** (2/2)\n━━━━━━━━━━━━━━━")

    # Отправляем одним или несколькими сообщениями (Telegram лимит 4096)
    page_limit = 4000

    # Проверяем, может ли быть отправлено одним сообщением
    combined = part1 + "\n\n" + part2
    if len(combined) <= page_limit:
        await message.reply(combined)
    # Если одна из частей превышает лимит, отправляем по частям
    elif len(part1) <= page_limit and len(part2) <= page_limit:
        await message.reply(part1)
        await message.reply(part2)
    # Если даже одна часть слишком большая, разбиваем дальше по категориям
    else:
        current_msg = []
        for cat in cats:
            icon = _category_icons.get(cat, "•")
            label = _category_labels.get(cat, cat)
            cat_header = f"{icon} **{label}**"
            cat_lines = [cat_header]
            for cmd in _reg.by_category(cat):
                cat_lines.append(f"`!{cmd.name}` — {cmd.description}")
            cat_text = "\n".join(cat_lines)

            # Проверяем, можем ли добавить категорию к текущему сообщению
            test_msg = "\n\n".join(current_msg + [cat_text]) if current_msg else cat_text
            if len(test_msg) > page_limit and current_msg:
                # Отправляем накопленное сообщение
                await message.reply("\n\n".join(current_msg))
                current_msg = [cat_text]
            else:
                current_msg.append(cat_text)

        # Отправляем оставшееся сообщение
        if current_msg:
            await message.reply("\n\n".join(current_msg))


# handle_diagnose + handle_debug — extracted to commands/system_commands.py
# (Phase 2 Wave 10, Session 27). Re-exported above.


async def handle_watch(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление proactive watch контуром.

    Команды:
    - `!watch status` — persisted состояние фонового watch;
    - `!watch now` — принудительно снять digest и записать его в общую память.
    """
    del bot
    raw_args = str(message.text or "").split(maxsplit=2)
    action = raw_args[1].strip().lower() if len(raw_args) > 1 else "status"

    if action == "status":
        status = proactive_watch.get_status()
        snapshot = status.get("last_snapshot") or {}
        route_model = str(snapshot.get("route_model") or snapshot.get("primary_model") or "n/a")
        await message.reply(
            "🛰️ **Proactive Watch**\n"
            f"- enabled: `{status.get('enabled')}`\n"
            f"- interval_sec: `{status.get('interval_sec')}`\n"
            f"- alert_cooldown_sec: `{status.get('alert_cooldown_sec')}`\n"
            f"- last_reason: `{status.get('last_reason') or '-'}`\n"
            f"- last_digest_ts: `{status.get('last_digest_ts') or '-'}`\n"
            f"- last_alert_ts: `{status.get('last_alert_ts') or '-'}`\n"
            f"- last_model: `{route_model}`"
        )
        return

    if action == "now":
        result = await proactive_watch.capture(manual=True, persist_memory=True, notify=False)
        suffix = (
            "\n- Память: записано в workspace memory"
            if result.get("wrote_memory")
            else "\n- Память: запись пропущена"
        )
        await message.reply(str(result.get("digest") or "watch digest unavailable") + suffix)
        return

    raise UserInputError(user_message="🛰️ Формат: `!watch status` или `!watch now`")


async def handle_memory(bot: "KraabUserbot", message: Message) -> None:
    """
    Команды работы с общей памятью и Memory Layer архивом.

    Поддерживаемые субкоманды:
    - `!memory recent [source_filter]` — последние записи workspace-памяти;
    - `!memory stats` — агрегированная статистика по archive.db / indexer / validator;
    - `!memory clear` — preview чатов в archive.db;
    - `!memory clear --chat=<id> [--confirm]` — selective delete по чату;
    - `!memory clear --before=YYYY-MM-DD [--confirm]` — delete old messages;
    - `!memory rebuild` — запуск repair_sqlite_vec.py для починки FTS5 + vec_chunks.
    """
    raw_args = str(message.text or "").split(maxsplit=2)
    action = raw_args[1].strip().lower() if len(raw_args) > 1 else "recent"
    rest = raw_args[2].strip() if len(raw_args) > 2 else ""

    if action == "stats":
        del bot
        await _handle_memory_stats(message)
        return

    if action == "clear":
        # Owner-only: деструктивная операция
        me_id = getattr(getattr(bot, "me", None), "id", None)
        sender_id = getattr(getattr(message, "from_user", None), "id", None)
        if me_id is None or sender_id != me_id:
            await message.reply("🚫 `!memory clear` доступен только владельцу.")
            return
        await _handle_memory_clear(message, rest)
        return

    if action == "rebuild":
        # Owner-only: запускает repair_sqlite_vec.py
        me_id = getattr(getattr(bot, "me", None), "id", None)
        sender_id = getattr(getattr(message, "from_user", None), "id", None)
        if me_id is None or sender_id != me_id:
            await message.reply("🚫 `!memory rebuild` доступен только владельцу.")
            return
        del bot
        await _handle_memory_rebuild(message)
        return

    del bot
    source_filter = rest

    if action != "recent":
        raise UserInputError(
            user_message=(
                "🧠 Формат: `!memory recent [source_filter]` | `!memory stats`"
                " | `!memory clear` | `!memory rebuild`"
            )
        )

    rows = list_workspace_memory_entries(limit=8, source_filter=source_filter)
    if not rows:
        await message.reply("🧠 В общей памяти пока нет подходящих записей.")
        return
    lines = ["🧠 **Последние записи общей памяти**"]
    for item in rows:
        author_suffix = f":{item['author']}" if item.get("author") else ""
        lines.append(
            f"- `{item['date']} {item['time']}` [{item['source']}{author_suffix}] {item['text']}"
        )
    await message.reply("\n".join(lines))


async def _handle_memory_stats(message: Message) -> None:
    """Собирает архив/индексер/валидатор статистику и отправляет reply."""
    archive_stats = _collect_memory_archive_stats()
    indexer_stats = _collect_memory_indexer_stats()
    validator_stats = _collect_memory_validator_stats()
    reply = format_memory_stats(archive_stats, indexer_stats, validator_stats)
    await message.reply(reply)


async def _handle_memory_clear(message: Message, args_str: str) -> None:
    """Selective cleanup archive.db — по чату или по дате.

    Форматы:
        !memory clear                              — preview чатов (топ 20)
        !memory clear --chat=<id>                  — показать сколько удалится
        !memory clear --chat=<id> --confirm        — удалить
        !memory clear --before=YYYY-MM-DD          — показать сколько удалится
        !memory clear --before=YYYY-MM-DD --confirm — удалить
    """
    import re
    from datetime import datetime

    from ..core.reset_helpers import (
        clear_archive_db_for_chat,
        delete_archive_messages_before,
        list_archive_chats,
    )

    db_path = _ARCHIVE_DB_PATH_FOR_CLEAR
    if not db_path.exists():
        await message.reply("📭 Archive не существует — нечего удалять.")
        return

    chat_match = re.search(r"--chat=(\S+)", args_str)
    before_match = re.search(r"--before=(\d{4}-\d{2}-\d{2})", args_str)
    confirm = "--confirm" in args_str

    # --- Preview mode: список чатов ---
    if not chat_match and not before_match:
        chats = list_archive_chats(db_path=db_path, limit=20)
        try:
            import sqlite3 as _sq3

            with _sq3.connect(f"file:{db_path}?mode=ro", uri=True) as _c:
                total = _c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        except Exception:  # noqa: BLE001
            total = sum(r["message_count"] for r in chats)

        lines = [f"🧠 **Archive preview** — {_fmt_int_ru(total)} total messages\n"]
        if chats:
            lines.append("**Топ чатов:**")
            for row in chats:
                title_s = (row["title"] or f"chat_{row['chat_id']}")[:40]
                lines.append(
                    f"• `{row['chat_id']}` → {title_s}: {_fmt_int_ru(row['message_count'])} msgs"
                )
        else:
            lines.append("_(чаты не найдены)_")
        lines.append("\n**Использование:**")
        lines.append("• `!memory clear --chat=<id> --confirm`")
        lines.append("• `!memory clear --before=YYYY-MM-DD --confirm`")
        await message.reply("\n".join(lines))
        return

    # --- Dry-run: предупреждение без --confirm ---
    if not confirm:
        if chat_match:
            chat_id = chat_match.group(1)
            from ..core.reset_helpers import count_archive_messages_for_chat

            count = count_archive_messages_for_chat(chat_id, db_path=db_path)
            await message.reply(
                f"⚠️ Будет удалено **{_fmt_int_ru(count)}** сообщений для чата `{chat_id}`.\n"
                f"Добавьте `--confirm` для подтверждения."
            )
        else:
            date_str = before_match.group(1)  # type: ignore[union-attr]
            try:
                cutoff_ts = int(datetime.strptime(date_str, "%Y-%m-%d").timestamp())
            except ValueError:
                await message.reply(f"❌ Неверный формат даты: `{date_str}`. Ожидается YYYY-MM-DD.")
                return
            import sqlite3 as _sq3

            try:
                with _sq3.connect(f"file:{db_path}?mode=ro", uri=True) as _c:
                    count = _c.execute(
                        "SELECT COUNT(*) FROM messages WHERE date < ?", (cutoff_ts,)
                    ).fetchone()[0]
            except Exception:  # noqa: BLE001
                count = 0
            await message.reply(
                f"⚠️ Будет удалено **{_fmt_int_ru(count)}** сообщений старше `{date_str}`.\n"
                f"Добавьте `--confirm` для подтверждения."
            )
        return

    # --- Confirmed delete ---
    try:
        if chat_match:
            chat_id = chat_match.group(1)
            deleted = clear_archive_db_for_chat(chat_id, db_path=db_path)
            await message.reply(
                f"🗑️ Удалено **{_fmt_int_ru(deleted)}** сообщений чата `{chat_id}` из archive.db.\n"
                f"_(chunks + chunk_messages тоже очищены)_"
            )
        else:
            date_str = before_match.group(1)  # type: ignore[union-attr]
            try:
                cutoff_ts = int(datetime.strptime(date_str, "%Y-%m-%d").timestamp())
            except ValueError:
                await message.reply(f"❌ Неверный формат даты: `{date_str}`. Ожидается YYYY-MM-DD.")
                return
            deleted = delete_archive_messages_before(cutoff_ts, db_path=db_path)
            await message.reply(
                f"🗑️ Удалено **{_fmt_int_ru(deleted)}** сообщений старше `{date_str}` из archive.db.\n"
                f"_(осиротевшие chunks тоже очищены)_"
            )
    except Exception as exc:  # noqa: BLE001
        await message.reply(f"❌ Ошибка при очистке archive: {exc}")


_REPAIR_SCRIPT_RELPATH = pathlib.Path("scripts") / "repair_sqlite_vec.py"
_MEMORY_REBUILD_TIMEOUT = 60.0  # секунд


async def _handle_memory_rebuild(message: Message) -> None:
    """Запускает repair_sqlite_vec.py в фоне и возвращает результат в reply.

    Предупреждает: во время repair FTS/vec retrieval может быть нестабилен ~20s.
    """
    from ..core.subprocess_env import clean_subprocess_env  # noqa: PLC0415

    krab_root = pathlib.Path.home() / "Antigravity_AGENTS" / "Краб"
    script_path = krab_root / _REPAIR_SCRIPT_RELPATH

    if not script_path.exists():
        await message.reply(f"❌ Script not found: {_REPAIR_SCRIPT_RELPATH}")
        return

    await message.reply(
        "🔄 Запускаю repair sqlite-vec... (~20s)\n"
        "⚠️ На время repair retrieval memory может быть нестабилен."
    )

    t0 = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(script_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(krab_root),
            env=clean_subprocess_env(),
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_MEMORY_REBUILD_TIMEOUT)
        except asyncio.TimeoutError:
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                else:
                    # SIGTERM → 2с grace → SIGKILL → предотвращаем orphan
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        try:
                            proc.kill()
                        except ProcessLookupError:
                            pass
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=1.0)
                        except asyncio.TimeoutError:
                            logger.warning(
                                "openclaw_cli_force_killed_but_no_reap",
                                pid=proc.pid,
                            )
            elapsed = time.monotonic() - t0
            await message.reply(f"⚠️ Repair timeout после {elapsed:.0f}s. Проверь лог вручную.")
            logger.warning("memory_rebuild_timeout", elapsed=elapsed)
            return
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb

        logger.error(
            "memory_rebuild_launch_error",
            error=str(exc),
            error_type=type(exc).__name__,
            traceback=_tb.format_exc(),
        )
        await message.reply(f"❌ Ошибка запуска repair: {exc}")
        return

    elapsed = time.monotonic() - t0
    output = stdout.decode("utf-8", errors="replace").strip()
    if len(output) > 2000:
        output = "..." + output[-2000:]

    if proc.returncode == 0:
        summary_line = ""
        for line in output.splitlines():
            if "[DONE]" in line or "[OK]" in line:
                summary_line = line.strip()
                break
        tail = f"\n`{summary_line}`" if summary_line else ""
        snippet = output[-800:]
        await message.reply(f"✅ Repair done за {elapsed:.1f}s.{tail}\n\n```\n{snippet}\n```")
        logger.info("memory_rebuild_done", elapsed=elapsed, returncode=0)
    else:
        snippet = output[-800:]
        await message.reply(
            f"❌ Repair завершился с кодом {proc.returncode} за {elapsed:.1f}s.\n"
            f"```\n{snippet}\n```"
        )
        logger.error(
            "memory_rebuild_failed",
            returncode=proc.returncode,
            elapsed=elapsed,
        )


def _collect_memory_archive_stats() -> dict[str, Any]:
    """Read-only снимок archive.db: counts + size. Graceful fallback."""
    import sqlite3 as _sqlite3
    from pathlib import Path as _Path

    db_path = _Path("~/.openclaw/krab_memory/archive.db").expanduser()
    stats: dict[str, Any] = {"exists": db_path.exists()}
    if not stats["exists"]:
        return stats
    try:
        conn = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            stats["messages"] = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            stats["chats"] = conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
            stats["chunks"] = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        finally:
            conn.close()
        stats["size_mb"] = db_path.stat().st_size / 1024 / 1024
    except Exception as exc:  # noqa: BLE001 — stats команда не должна падать
        stats["error"] = str(exc)
    return stats


def _collect_memory_indexer_stats() -> dict[str, Any]:
    """Снимок состояния real-time индексера; при недоступности — заглушка."""
    try:
        from ..core.memory_indexer_worker import get_indexer  # noqa: PLC0415

        snap = get_indexer().get_stats()
        return {
            "state": "running" if getattr(snap, "is_running", False) else "stopped",
            "queue_size": getattr(snap, "queue_size", 0),
            "queue_maxsize": getattr(snap, "queue_maxsize", 0),
            "processed_total": getattr(snap, "processed_total", 0),
            "failed": dict(getattr(snap, "failed", {}) or {}),
        }
    except Exception:  # noqa: BLE001 — индексер опционален
        return {"state": "unavailable"}


def _collect_memory_validator_stats() -> dict[str, Any]:
    """Снимок счётчиков memory_validator; при отсутствии модуля — заглушка."""
    try:
        from ..core.memory_validator import memory_validator  # noqa: PLC0415

        stats = dict(getattr(memory_validator, "stats", {}) or {})
        try:
            stats["pending_count"] = len(memory_validator.list_pending())
        except Exception:  # noqa: BLE001
            stats.setdefault("pending_count", 0)
        return stats
    except Exception:  # noqa: BLE001 — модуля может не быть
        return {"error": "not loaded"}


def _fmt_int_ru(value: int) -> str:
    """Целое с пробелом-разделителем тысяч (RU-стиль)."""
    return f"{int(value):,}".replace(",", " ")


def format_memory_stats(
    archive: dict[str, Any],
    indexer: dict[str, Any],
    validator: dict[str, Any],
) -> str:
    """Формирует Markdown-сообщение с агрегатом статистики Memory Layer."""
    lines: list[str] = ["🧠 **Memory Layer Stats**", ""]

    # Archive block
    lines.append("**Archive.db:**")
    if archive.get("exists"):
        if "error" in archive:
            lines.append(f"• Error: {archive['error']}")
        else:
            lines.append(f"• Messages: **{_fmt_int_ru(archive.get('messages', 0))}**")
            lines.append(f"• Chats: {archive.get('chats', 0)}")
            lines.append(f"• Chunks: {_fmt_int_ru(archive.get('chunks', 0))}")
            size_mb = archive.get("size_mb", 0)
            lines.append(f"• Size: {size_mb:.1f} MB")
    else:
        lines.append("• Not initialized")

    # Indexer block
    lines.append("")
    lines.append("**Indexer:**")
    state = indexer.get("state", "unknown")
    lines.append(f"• State: `{state}`")
    if state != "unavailable":
        q_size = indexer.get("queue_size", 0)
        q_max = indexer.get("queue_maxsize") or 0
        if q_max:
            lines.append(f"• Queue: {q_size} / {q_max}")
        else:
            lines.append(f"• Queue: {q_size}")
        lines.append(f"• Processed: {_fmt_int_ru(indexer.get('processed_total', 0))}")
        failed = indexer.get("failed") or {}
        if failed:
            parts = ", ".join(f"{k}={v}" for k, v in failed.items())
            lines.append(f"• Failed: {parts}")

    # Validator block
    lines.append("")
    lines.append("**Validator:**")
    if "error" in validator:
        lines.append(f"• {validator['error']}")
    else:
        lines.append(f"• Safe: {_fmt_int_ru(validator.get('safe_total', 0))}")
        lines.append(f"• Blocked: {validator.get('injection_blocked_total', 0)}")
        lines.append(f"• Confirmed: {validator.get('confirmed_total', 0)}")
        lines.append(f"• Pending: {validator.get('pending_count', 0)}")

    # Timestamp
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append("")
    lines.append(f"_Последнее обновление: {ts}_")

    return "\n".join(lines)


async def handle_inbox(bot: "KraabUserbot", message: Message) -> None:
    """
    Owner-visible inbox и escalation foundation.

    Поддерживаем owner workflow-подмножество:
    - `!inbox` / `!inbox list` — открыть текущие open items;
    - `!inbox status` — краткий summary;
    - `!inbox ack <id>` — отметить как просмотренное;
    - `!inbox done <id>` — закрыть item;
    - `!inbox cancel <id>` — отменить item вручную.
    - `!inbox approve <id>` / `!inbox reject <id>` — принять решение по approval item;
    - `!inbox task <title> | <body>` — создать owner-task;
    - `!inbox taskfrom <source_id> | <title> | <body>` — эскалировать item в owner-task;
    - `!inbox approval <scope> | <title> | <body>` — создать approval-request.
    - `!inbox approvalfrom <source_id> | <scope> | <title> | <body>` — эскалировать item в approval.
    """
    del bot
    raw_args = str(message.text or "").split(maxsplit=2)
    action = raw_args[1].strip().lower() if len(raw_args) > 1 else "list"

    if action == "status":
        summary = inbox_service.get_summary()
        await message.reply(
            "📥 **Inbox / Escalation**\n"
            f"- operator: `{summary.get('operator_id')}`\n"
            f"- account_id: `{summary.get('account_id')}`\n"
            f"- open_items: `{summary.get('open_items')}`\n"
            f"- attention_items: `{summary.get('attention_items')}`\n"
            f"- pending_reminders: `{summary.get('pending_reminders')}`\n"
            f"- open_escalations: `{summary.get('open_escalations')}`\n"
            f"- pending_owner_tasks: `{summary.get('pending_owner_tasks')}`\n"
            f"- pending_approvals: `{summary.get('pending_approvals')}`\n"
            f"- pending_owner_requests: `{summary.get('pending_owner_requests')}`\n"
            f"- pending_owner_mentions: `{summary.get('pending_owner_mentions')}`\n"
            f"- state: `{summary.get('state_path')}`"
        )
        return

    if action in {"list", "open"}:
        rows = inbox_service.list_items(status="open", limit=8)
        if not rows:
            await message.reply("📥 Inbox сейчас пуст: открытых items нет.")
            return
        lines = ["📥 **Открытые inbox items**"]
        for item in rows:
            meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            due = str(meta.get("due_at_iso") or "").strip()
            due_suffix = f" · due `{due}`" if due else ""
            approval_scope = str((item.get("identity") or {}).get("approval_scope") or "").strip()
            approval_suffix = (
                f" · scope `{approval_scope}`"
                if approval_scope and item["kind"] == "approval_request"
                else ""
            )
            lines.append(
                f"- `{item['item_id']}` · `{item['kind']}` · `{item['severity']}`{due_suffix}{approval_suffix}\n"
                f"  {item['title']}"
            )
        await message.reply("\n".join(lines))
        return

    if action == "task":
        if len(raw_args) < 3 or "|" not in raw_args[2]:
            raise UserInputError(user_message="📥 Формат: `!inbox task <title> | <body>`")
        title, body = [part.strip() for part in raw_args[2].split("|", maxsplit=1)]
        if not title or not body:
            raise UserInputError(user_message="📥 Для task нужны и заголовок, и описание.")
        created = inbox_service.upsert_owner_task(title=title, body=body, source="telegram-owner")
        await message.reply(
            "📝 Owner-task создан.\n"
            f"- ID: `{created['item']['item_id']}`\n"
            f"- Title: {created['item']['title']}"
        )
        return

    if action == "taskfrom":
        if len(raw_args) < 3 or raw_args[2].count("|") < 2:
            raise UserInputError(
                user_message="📥 Формат: `!inbox taskfrom <source_id> | <title> | <body>`"
            )
        source_item_id, title, body = [part.strip() for part in raw_args[2].split("|", maxsplit=2)]
        if not source_item_id or not title or not body:
            raise UserInputError(
                user_message="📥 Для taskfrom нужны source_id, заголовок и описание."
            )
        created = inbox_service.escalate_item_to_owner_task(
            source_item_id=source_item_id,
            title=title,
            body=body,
            source="telegram-owner",
            metadata={"requested_via": "telegram"},
        )
        if not created.get("ok"):
            raise UserInputError(user_message=f"📥 Source item `{source_item_id}` не найден.")
        await message.reply(
            "📝 Owner-task создан из inbox item.\n"
            f"- Source: `{source_item_id}`\n"
            f"- ID: `{created['item']['item_id']}`\n"
            f"- Trace: `{created['item']['identity']['trace_id']}`"
        )
        return

    if action == "approval":
        if len(raw_args) < 3 or raw_args[2].count("|") < 2:
            raise UserInputError(
                user_message="📥 Формат: `!inbox approval <scope> | <title> | <body>`"
            )
        scope, title, body = [part.strip() for part in raw_args[2].split("|", maxsplit=2)]
        if not scope or not title or not body:
            raise UserInputError(user_message="📥 Для approval нужны scope, заголовок и описание.")
        created = inbox_service.upsert_approval_request(
            title=title,
            body=body,
            source="telegram-owner",
            approval_scope=scope,
            requested_action=title,
            metadata={"requested_via": "telegram"},
        )
        await message.reply(
            "🛂 Approval-request создан.\n"
            f"- ID: `{created['item']['item_id']}`\n"
            f"- Scope: `{scope}`\n"
            f"- Title: {created['item']['title']}"
        )
        return

    if action == "approvalfrom":
        if len(raw_args) < 3 or raw_args[2].count("|") < 3:
            raise UserInputError(
                user_message="📥 Формат: `!inbox approvalfrom <source_id> | <scope> | <title> | <body>`"
            )
        source_item_id, scope, title, body = [
            part.strip() for part in raw_args[2].split("|", maxsplit=3)
        ]
        if not source_item_id or not scope or not title or not body:
            raise UserInputError(
                user_message="📥 Для approvalfrom нужны source_id, scope, заголовок и описание."
            )
        created = inbox_service.escalate_item_to_approval_request(
            source_item_id=source_item_id,
            title=title,
            body=body,
            source="telegram-owner",
            approval_scope=scope,
            requested_action=title,
            metadata={"requested_via": "telegram"},
        )
        if not created.get("ok"):
            raise UserInputError(user_message=f"📥 Source item `{source_item_id}` не найден.")
        await message.reply(
            "🛂 Approval-request создан из inbox item.\n"
            f"- Source: `{source_item_id}`\n"
            f"- ID: `{created['item']['item_id']}`\n"
            f"- Scope: `{scope}`\n"
            f"- Trace: `{created['item']['identity']['trace_id']}`"
        )
        return

    if action not in {"ack", "done", "cancel", "approve", "reject"}:
        raise UserInputError(
            user_message=(
                "📥 Формат: "
                "`!inbox [list|status|ack <id>|done <id>|cancel <id>|approve <id>|reject <id>|task <title> | <body>|taskfrom <source_id> | <title> | <body>|approval <scope> | <title> | <body>|approvalfrom <source_id> | <scope> | <title> | <body>]`"
            )
        )

    if len(raw_args) < 3 or not raw_args[2].strip():
        raise UserInputError(
            user_message="📥 Укажи item id: `!inbox ack|done|cancel|approve|reject <id> [| note]`"
        )
    target_payload = raw_args[2].strip()
    target_id, note = (
        [part.strip() for part in target_payload.split("|", maxsplit=1)]
        if "|" in target_payload
        else (target_payload, "")
    )
    if not target_id:
        raise UserInputError(
            user_message="📥 Укажи корректный item id: `!inbox ack|done|cancel|approve|reject <id> [| note]`"
        )
    if action in {"approve", "reject"}:
        result = inbox_service.resolve_approval(
            target_id,
            approved=(action == "approve"),
            actor="telegram-owner",
            note=note,
        )
        target_status = "approved" if action == "approve" else "rejected"
    else:
        target_status = {"ack": "acked", "done": "done", "cancel": "cancelled"}[action]
        result = inbox_service.set_item_status(
            target_id,
            status=target_status,
            actor="telegram-owner",
            note=note,
        )
    if not result.get("ok"):
        if result.get("error") == "inbox_item_not_approval":
            raise UserInputError(
                user_message=f"📥 Item `{target_id}` не является approval-request."
            )
        raise UserInputError(user_message=f"📥 Item `{target_id}` не найден.")
    await message.reply(
        "✅ Inbox item обновлён.\n"
        f"- ID: `{target_id}`\n"
        f"- Новый статус: `{target_status}`" + (f"\n- Note: {note}" if note else "")
    )


async def handle_browser(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление Chrome через CDP.

    !browser status              — статус подключения
    !browser tabs                — список вкладок
    !browser open <url>          — навигация
    !browser read                — текст текущей страницы
    !browser shot                — скриншот (фото в Telegram)
    !browser js <code>           — выполнить JS
    !browser ai [gemini|chatgpt] <prompt> — запрос через браузерный AI
    """
    from ..integrations.browser_bridge import browser_bridge

    args = str(message.text or "").split(maxsplit=2)
    sub = str(args[1] if len(args) > 1 else "status").strip().lower()

    if sub == "status":
        attached = await browser_bridge.is_attached()
        if not attached:
            await message.reply(
                "🌐 Браузер: **отключён**\n"
                "Запусти `new Enable Chrome Remote Debugging.command` для подключения."
            )
            return
        tabs = await browser_bridge.list_tabs()
        active = tabs[-1] if tabs else None
        active_info = f"\n🔗 Активная: {active['url']}" if active else ""
        await message.reply(f"🌐 Браузер: **подключён** ({len(tabs)} вкладок){active_info}")
        return

    if sub == "tabs":
        tabs = await browser_bridge.list_tabs()
        if not tabs:
            await message.reply("🌐 Вкладок не найдено (браузер отключён или пуст).")
            return
        lines = [
            f"{i + 1}. {t.get('title') or t.get('url')}\n   {t['url']}" for i, t in enumerate(tabs)
        ]
        await message.reply("🌐 Вкладки:\n" + "\n".join(lines))
        return

    if sub == "open":
        url = str(args[2] if len(args) > 2 else "").strip()
        if not url:
            raise UserInputError(user_message="❌ Укажи URL: `!browser open <url>`")
        final_url = await browser_bridge.navigate(url)
        await message.reply(f"✅ Переход: {final_url}")
        return

    if sub == "read":
        text = await browser_bridge.get_page_text()
        if not text:
            await message.reply("❌ Не удалось получить текст страницы.")
            return
        await message.reply(f"📄 Страница (до 2000 символов):\n```\n{text[:2000]}\n```")
        return

    if sub == "shot":
        data = await browser_bridge.screenshot()
        if data is None:
            await message.reply("❌ Не удалось сделать скриншот.")
            return
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as _tmp:
            _tmp.write(data)
            _tmp_path = _tmp.name
        try:
            await message.reply_photo(_tmp_path)
        except Exception as _photo_err:
            logger.warning("reply_photo_failed_browser_shot", error=str(_photo_err))
            try:
                await message.reply_document(_tmp_path, caption="📸 Screenshot (fallback)")
            except Exception as _doc_err:
                logger.error("reply_document_failed_browser_shot", error=str(_doc_err))
                await message.reply(f"❌ Не удалось отправить скриншот: `{str(_photo_err)[:200]}`")
        finally:
            os.unlink(_tmp_path)
        return

    if sub == "js":
        code = str(args[2] if len(args) > 2 else "").strip()
        if not code:
            raise UserInputError(user_message="❌ Укажи код: `!browser js <code>`")
        result = await browser_bridge.execute_js(code)
        await message.reply(f"✅ Результат:\n```\n{str(result)[:1000]}\n```")
        return

    if sub == "ai":
        from ..integrations.browser_ai_provider import browser_ai_provider

        # Разбираем: !browser ai [gemini|chatgpt] <prompt>
        rest_parts = str(message.text or "").split(maxsplit=3)
        service = "gemini"
        prompt = ""
        if len(rest_parts) >= 3:
            maybe_service = rest_parts[2].lower()
            if maybe_service in ("gemini", "chatgpt"):
                service = maybe_service
                prompt = rest_parts[3] if len(rest_parts) > 3 else ""
            else:
                prompt = " ".join(rest_parts[2:])
        if not prompt:
            raise UserInputError(
                user_message=(
                    "❌ Укажи запрос: `!browser ai <prompt>` или\n"
                    "`!browser ai gemini <prompt>` / `!browser ai chatgpt <prompt>`"
                )
            )

        status_msg = await message.reply(f"🌐 Отправляю в {service}... ⏳")
        response = await browser_ai_provider.chat(prompt, service=service)  # type: ignore[arg-type]

        if response.startswith("[ERROR]"):
            await status_msg.edit(f"❌ {response}")
        else:
            # Обрезаем до разумного лимита
            preview = response[:3500]
            if len(response) > 3500:
                preview += "\n\n_[ответ обрезан]_"
            await status_msg.edit(f"🌐 **{service}**:\n\n{preview}")
        return

    raise UserInputError(
        user_message=(
            "🌐 Команды браузера:\n"
            "`!browser status` — статус\n"
            "`!browser tabs` — список вкладок\n"
            "`!browser open <url>` — навигация\n"
            "`!browser read` — текст страницы\n"
            "`!browser shot` — скриншот\n"
            "`!browser js <code>` — выполнить JS\n"
            "`!browser ai [gemini|chatgpt] <prompt>` — запрос через браузерный AI"
        )
    )


async def _cli_keepalive(
    status_msg: Message,
    tool: str,
    started_at: float,
    *,
    interval: float = 20.0,
    stop_event: asyncio.Event,
) -> None:
    """Фоновая задача: обновляет сообщение с прогрессом пока CLI работает."""
    step = 0
    spinners = ["⏳", "⏳⏳", "⏳⏳⏳"]
    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    asyncio.shield(stop_event.wait()),
                    timeout=interval,
                )
                break
            except asyncio.TimeoutError:
                pass
            elapsed = int(time.monotonic() - started_at)
            mins, secs = divmod(elapsed, 60)
            time_str = f"{mins}м {secs}с" if mins else f"{secs}с"
            indicator = spinners[step % len(spinners)]
            try:
                await status_msg.edit(f"{indicator} `{tool}` работает... {time_str}")
            except Exception:
                pass
            step += 1
    except asyncio.CancelledError:
        pass


async def _run_cli_with_progress(
    bot: "KraabUserbot",
    message: Message,
    tool: str,
    prompt: str,
    *,
    timeout: float = 120.0,
    tool_label: str | None = None,
) -> None:
    """Общая реализация для handle_codex/handle_gemini/handle_claude."""
    from ..integrations.cli_runner import run_cli

    if not prompt:
        raise UserInputError(
            user_message=(
                f"🤖 Использование: `!{tool} <запрос>`\n"
                f"Пример: `!{tool} напиши hello world на Python`"
            )
        )

    label = tool_label or tool
    status_msg = await message.reply(f"⏳ Запускаю `{label}`...")
    started_at = time.monotonic()
    stop_event = asyncio.Event()
    keepalive = asyncio.create_task(
        _cli_keepalive(status_msg, label, started_at, stop_event=stop_event)
    )
    try:
        result = await run_cli(tool, prompt, timeout=timeout)
    finally:
        stop_event.set()
        keepalive.cancel()
        try:
            await keepalive
        except asyncio.CancelledError:
            pass

    elapsed = int(time.monotonic() - started_at)
    output = result.output or "(нет вывода)"
    header = f"🤖 **{label}** (`{elapsed}с`)"
    if result.exit_code != 0 and not result.timed_out:
        header += f" — код {result.exit_code}"

    full_text = f"{header}\n\n{output}"
    chunks = _split_text_for_telegram(full_text)
    await status_msg.edit(chunks[0])
    for part in chunks[1:]:
        await message.reply(part)


async def handle_codex(bot: "KraabUserbot", message: Message) -> None:
    """Запустить codex-cli с запросом. Использование: !codex <запрос>"""
    prompt = bot._get_command_args(message)
    timeout = float(getattr(config, "CLI_CODEX_TIMEOUT_SEC", 120.0))
    await _run_cli_with_progress(bot, message, "codex", prompt, timeout=timeout)


async def handle_gemini_cli(bot: "KraabUserbot", message: Message) -> None:
    """Запустить gemini-cli с запросом. Использование: !gemini <запрос>"""
    prompt = bot._get_command_args(message)
    timeout = float(getattr(config, "CLI_GEMINI_TIMEOUT_SEC", 120.0))
    await _run_cli_with_progress(
        bot, message, "gemini", prompt, timeout=timeout, tool_label="gemini-cli"
    )


async def handle_claude_cli(bot: "KraabUserbot", message: Message) -> None:
    """Запустить claude code CLI с запросом. Использование: !claude_cli <запрос>"""
    prompt = bot._get_command_args(message)
    timeout = float(getattr(config, "CLI_CLAUDE_TIMEOUT_SEC", 120.0))
    await _run_cli_with_progress(
        bot, message, "claude", prompt, timeout=timeout, tool_label="claude-code"
    )


async def handle_opencode(bot: "KraabUserbot", message: Message) -> None:
    """Запустить opencode с запросом. Использование: !opencode <запрос>"""
    prompt = bot._get_command_args(message)
    timeout = float(getattr(config, "CLI_OPENCODE_TIMEOUT_SEC", 180.0))
    await _run_cli_with_progress(
        bot, message, "opencode", prompt, timeout=timeout, tool_label="opencode"
    )


async def handle_hs(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление окнами macOS через Hammerspoon.

    Требует:
    - Установленного Hammerspoon (https://www.hammerspoon.org/)
    - ~/.hammerspoon/init.lua из репозитория Краба (hammerspoon/init.lua)
    - Accessibility permission для Hammerspoon в System Settings

    Использование:
      !hs               — эта справка
      !hs status        — версия Hammerspoon, количество экранов
      !hs windows       — список видимых окон
      !hs focus <app>   — сфокусировать окно приложения
      !hs tile <preset> [<app>]  — раскладка: left|right|top|bottom|full
      !hs move <app> <x> <y> <w> <h>  — переместить/изменить размер окна
                        (координаты 0..1 = доля экрана, >2 = пиксели)
    """
    del bot
    raw = str(message.text or "").split(maxsplit=1)
    args_str = raw[1].strip() if len(raw) > 1 else ""

    _HELP = (  # noqa: N806
        "🔨 **Hammerspoon window control**\n\n"
        "`!hs status` — версия и статус HS\n"
        "`!hs windows` — список видимых окон\n"
        "`!hs focus <app>` — сфокусировать окно\n"
        "`!hs tile <preset> [<app>]` — раскладка: `left` `right` `top` `bottom` `full`\n"
        "`!hs move <app> <x> <y> <w> <h>` — переместить окно\n\n"
        "_Установи Hammerspoon и скопируй `hammerspoon/init.lua` в `~/.hammerspoon/init.lua`_"
    )

    if not args_str:
        await message.reply(_HELP)
        return

    if not hammerspoon.is_available():
        await message.reply(
            "🔨 Hammerspoon недоступен.\n\n"
            "Убедись, что:\n"
            "1. Hammerspoon установлен и запущен\n"
            "2. `~/.hammerspoon/init.lua` содержит krab-hs server\n"
            "3. Выданы разрешения Accessibility в System Settings → Privacy & Security"
        )
        return

    parts = args_str.split()
    sub = parts[0].lower()

    try:
        if sub == "status":
            data = await hammerspoon.status()
            lines = [
                "🔨 **Hammerspoon**",
                f"- Версия: `{data.get('version', '?')}`",
                f"- Build: `{data.get('build', '?')}`",
                f"- Экранов: `{data.get('screens', '?')}`",
            ]
            await message.reply("\n".join(lines))

        elif sub == "windows":
            windows = await hammerspoon.list_windows()
            if not windows:
                await message.reply("🔨 Нет видимых окон.")
                return
            lines = ["🔨 **Видимые окна**"]
            for w in windows[:20]:
                lines.append(f"- `{w.get('app', '?')}` — {w.get('title', '')}")
            await message.reply("\n".join(lines))

        elif sub == "focus":
            app = " ".join(parts[1:]) if len(parts) > 1 else ""
            if not app:
                raise UserInputError(user_message="🔨 Формат: `!hs focus <app>`")
            result = await hammerspoon.focus(app)
            await message.reply(
                f"🔨 Сфокусировано: `{result.get('app', app)}`"
                + (f" — {result.get('title', '')}" if result.get("title") else "")
            )

        elif sub == "tile":
            preset = parts[1].lower() if len(parts) > 1 else "left"
            app = " ".join(parts[2:]) if len(parts) > 2 else ""
            result = await hammerspoon.tile(preset=preset, app=app)
            await message.reply(
                f"🔨 Раскладка `{preset}` применена: `{result.get('app', app or 'активное окно')}`"
            )

        elif sub == "move":
            # !hs move <app> <x> <y> <w> <h>   или   !hs move <x> <y> <w> <h>
            floats: list[float] = []
            app_parts: list[str] = []
            for p in parts[1:]:
                try:
                    floats.append(float(p))
                except ValueError:
                    if not floats:
                        app_parts.append(p)
            if len(floats) < 4:
                raise UserInputError(
                    user_message="🔨 Формат: `!hs move [<app>] <x> <y> <w> <h>`\n"
                    "Пример: `!hs move Cursor 0 0 0.5 1` (левая половина экрана)"
                )
            x, y, w, h = floats[:4]
            app = " ".join(app_parts)
            result = await hammerspoon.move(app=app, x=x, y=y, w=w, h=h)
            frame = result.get("frame", {})
            await message.reply(
                f"🔨 Окно перемещено: x={frame.get('x')} y={frame.get('y')} "
                f"w={frame.get('w')} h={frame.get('h')}"
            )

        else:
            await message.reply(_HELP)

    except HammerspoonBridgeError as exc:
        await message.reply(f"🔨 Ошибка Hammerspoon: `{exc}`")


async def handle_screenshot(bot: "KraabUserbot", message: Message) -> None:
    """Снимок экрана текущей вкладки Chrome через CDP.

    Использование:
      !screenshot          — скриншот активной вкладки
      !screenshot health   — статус CDP подключения (без снимка)
      !screenshot ocr      — снимок + OCR (tesseract, brew install tesseract)
      !screenshot ocr rus  — OCR с указанием языка
    """
    del bot
    from ..integrations.browser_bridge import browser_bridge as _bb

    raw_parts = str(message.text or "").split()
    sub = raw_parts[1].lower().strip() if len(raw_parts) > 1 else ""

    if sub == "health":
        probe = await _bb.health_check()
        status = (
            "✅ CDP ready"
            if probe.get("ok")
            else ("🚫 blocked" if probe.get("blocked") else "⚠️ degraded")
        )
        tabs = probe.get("tab_count", 0)
        err = probe.get("error", "")
        text = f"📡 **Browser CDP**\n{status} — {tabs} вкладок"
        if err:
            text += f"\n`{err[:200]}`"
        await message.reply(text)
        return

    if sub == "ocr":
        lang = raw_parts[2] if len(raw_parts) > 2 else ""
        probe = await _bb.health_check(timeout_sec=4.0)
        if not probe.get("ok"):
            err_detail = probe.get("error") or "CDP недоступен"
            await message.reply(f"📡 **!screenshot ocr**: браузер недоступен\n`{err_detail[:300]}`")
            return
        try:
            png_bytes = await asyncio.wait_for(_bb.screenshot(), timeout=15.0)
        except asyncio.TimeoutError:
            await message.reply("⏱ Таймаут снимка (15 с).")
            return
        if not png_bytes:
            await message.reply("❌ Снимок пустой.")
            return
        try:
            from ..integrations.macos_automation import macos_automation as _ma

            if not _ma.is_ocr_available():
                await message.reply(
                    "📄 **OCR**: tesseract не установлен.\n"
                    "`brew install tesseract` + `brew install tesseract-lang` для русского"
                )
                return
            text_result = await asyncio.wait_for(_ma.ocr_image(png_bytes, lang=lang), timeout=30.0)
        except asyncio.TimeoutError:
            await message.reply("⏱ OCR таймаут (30 с).")
            return
        except Exception as exc:
            await message.reply(f"❌ OCR ошибка: `{str(exc)[:300]}`")
            return
        if not text_result:
            await message.reply("📄 OCR: текст не найден.")
            return
        lang_label = f" [{lang}]" if lang else ""
        await message.reply(f"📄 **OCR{lang_label}:**\n```\n{text_result[:4000]}\n```")
        return

    # Проверяем доступность перед снимком; при необходимости — auto-start Chrome
    probe = await _bb.health_check(timeout_sec=4.0)
    if not probe.get("ok"):
        # Auto-start: пробуем запустить dedicated Chrome и повторить probe
        try:
            from ..integrations.dedicated_chrome import launch_dedicated_chrome

            ok_launch, _reason = await asyncio.to_thread(launch_dedicated_chrome)
            if ok_launch:
                logger.info("screenshot_auto_started_chrome")
                probe = await _bb.health_check(timeout_sec=6.0)
        except Exception as _ce:
            logger.warning("screenshot_chrome_autostart_failed", error=repr(_ce))

    png_bytes: bytes | None = None
    cdp_ok = probe.get("ok", False)

    if cdp_ok:
        await message.reply("📸 Делаю снимок…")
        try:
            png_bytes = await asyncio.wait_for(_bb.screenshot(), timeout=15.0)
        except asyncio.TimeoutError:
            await message.reply("⏱ Таймаут снимка (15 с). Попробуй позже.")
            return
        except Exception as exc:
            logger.warning("screenshot_cdp_failed", error=repr(exc))
            png_bytes = None

    if not png_bytes:
        # Fallback на macOS screencapture (работает всегда без Chrome)
        try:
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as _sc_tmp:
                _sc_path = _sc_tmp.name
            _sc_proc = await asyncio.create_subprocess_exec(
                "screencapture",
                "-x",
                "-t",
                "png",
                _sc_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(_sc_proc.wait(), timeout=10.0)
            import pathlib

            _sc_file = pathlib.Path(_sc_path)
            if _sc_file.exists() and _sc_file.stat().st_size > 0:
                png_bytes = _sc_file.read_bytes()
                _sc_file.unlink(missing_ok=True)
                logger.info("screenshot_screencapture_fallback_ok")
            else:
                _sc_file.unlink(missing_ok=True)
                png_bytes = None
        except Exception as _sc_exc:
            logger.warning("screenshot_screencapture_fallback_failed", error=repr(_sc_exc))
            png_bytes = None

    if not png_bytes:
        err_detail = probe.get("error") or (
            "Chrome не запущен или CDP недоступен" if probe.get("blocked") else "неизвестная ошибка"
        )
        await message.reply(
            f"❌ **!screenshot**: снимок не удался\n"
            f"• CDP: {err_detail[:200]}\n"
            f"• macOS screencapture тоже не сработал\n"
            f"Запусти Chrome: `./scripts/start_dedicated_chrome.command`"
        )
        return

    import tempfile

    if not cdp_ok:
        caption = "📸 Screenshot (macOS screencapture — Chrome CDP недоступен)"
    else:
        caption = "📸 Screenshot"

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as _tmp:
        _tmp.write(png_bytes)
        _tmp_path = _tmp.name
    try:
        await message.reply_photo(_tmp_path, caption=caption)
    except Exception as _photo_err:
        logger.warning("reply_photo_failed", error=str(_photo_err))
        try:
            await message.reply_document(_tmp_path, caption=caption + " (doc)")
        except Exception as _doc_err:
            logger.error("reply_document_failed", error=str(_doc_err))
            await message.reply(f"❌ Не удалось отправить скриншот: `{str(_photo_err)[:200]}`")
    finally:
        os.unlink(_tmp_path)


async def handle_cap(bot: "KraabUserbot", message: Message) -> None:
    """Управление Policy Matrix — горячий toggle capabilities.

    Использование:
      !cap                     — список текущих оверрайдов + все валидные capability
      !cap <capability> on     — включить capability для всех ролей
      !cap <capability> off    — выключить capability для всех ролей
      !cap reset               — сбросить все оверрайды (вернуть дефолты)
    """
    del bot
    from ..core.capability_registry import (
        _VALID_CAPABILITIES,  # type: ignore[attr-defined]
        clear_capability_overrides,
        get_capability_overrides,
        set_capability_override,
    )

    raw_parts = str(message.text or "").split()
    sub = raw_parts[1].lower().strip() if len(raw_parts) > 1 else ""

    if not sub or sub == "list":
        overrides = get_capability_overrides()
        valid = sorted(_VALID_CAPABILITIES)
        lines = ["🎛 **Policy Matrix — capability overrides**"]
        if overrides:
            lines.append("")
            lines.append("**Активные оверрайды:**")
            for cap, val in sorted(overrides.items()):
                icon = "✅" if val else "🚫"
                lines.append(f"  {icon} `{cap}`")
        else:
            lines.append("_(оверрайды не заданы — используются дефолты ролей)_")
        lines.append("")
        lines.append("**Все capabilities:**")
        lines.append("`" + "`, `".join(valid) + "`")
        lines.append("")
        lines.append("📝 `!cap <name> on/off` · `!cap reset`")
        await message.reply("\n".join(lines))
        return

    if sub == "reset":
        clear_capability_overrides()
        await message.reply("♻️ Все оверрайды сброшены — используются дефолты ролей.")
        return

    # !cap <capability> on/off
    if len(raw_parts) < 3:
        raise UserInputError(user_message="🎛 `!cap <capability> on|off`")
    cap_name = sub
    action = raw_parts[2].lower().strip()
    if action not in ("on", "off"):
        raise UserInputError(user_message="🎛 Значение должно быть `on` или `off`")

    result = set_capability_override(cap_name, action == "on")
    if "error" in result:
        valid = sorted(_VALID_CAPABILITIES)
        await message.reply(
            f"❌ `{cap_name}` — неизвестная capability.\nДоступные: `{'`, `'.join(valid)}`"
        )
        return

    icon = "✅" if action == "on" else "🚫"
    await message.reply(f"{icon} `{cap_name}` → **{action.upper()}** (все роли)")


# _render_stats_panel + _format_ecosystem_report + _handle_stats_ecosystem + handle_stats —
# extracted to commands/system_commands.py (Phase 2 Wave 10, Session 27). Re-exported above.


async def handle_silence(bot: "KraabUserbot", message: Message) -> None:
    """!тишина — управление режимом тишины.

    Синтаксис:
      !тишина               — toggle текущего чата (30 мин)
      !тишина 15            — mute текущего чата на 15 минут
      !тишина стоп          — снять mute текущего чата
      !тишина глобально     — глобальный mute (60 мин)
      !тишина глобально 30  — глобальный mute на 30 мин
      !тишина статус        — показать все активные mutes
      !тишина расписание 23:00-08:00 — ночной режим по расписанию
      !тишина расписание статус      — статус расписания
      !тишина расписание выкл        — отключить расписание
    """
    from ..core.silence_mode import silence_manager
    from ..core.silence_schedule import silence_schedule_manager

    chat_id = str(message.chat.id)
    raw = (message.text or "").strip()
    # Убираем префикс команды
    parts = raw.split(maxsplit=1)
    args = parts[1].strip().lower() if len(parts) > 1 else ""

    # ── Расписание ночного режима ──────────────────────────────
    if args.startswith("расписание"):
        sched_arg = args[len("расписание") :].strip()
        if not sched_arg or sched_arg == "статус":
            await message.reply(silence_schedule_manager.format_status())
            return
        if sched_arg in ("выкл", "off", "стоп"):
            silence_schedule_manager.disable_schedule()
            await message.reply("🌙 Расписание тишины **отключено**.")
            return
        # Ожидаем формат HH:MM-HH:MM
        if "-" in sched_arg:
            time_parts = sched_arg.split("-", 1)
            if len(time_parts) == 2:
                start_s, end_s = time_parts[0].strip(), time_parts[1].strip()
                try:
                    silence_schedule_manager.set_schedule(start_s, end_s)
                    active_marker = (
                        " (сейчас активно ✅)"
                        if silence_schedule_manager.is_schedule_active()
                        else ""
                    )
                    await message.reply(
                        f"🌙 Расписание тишины установлено: **{start_s}–{end_s}**{active_marker}\n"
                        f"Краб будет молчать в эти часы.\n"
                        f"`!тишина расписание выкл` — отключить"
                    )
                    return
                except ValueError as exc:
                    await message.reply(f"❌ {exc}")
                    return
        await message.reply("❌ Неверный формат. Пример: `!тишина расписание 23:00-08:00`")
        return

    if args in ("статус", "status"):
        await message.reply(silence_manager.format_status())
        return

    if args in ("on", "off"):
        if args == "off":
            was_chat = silence_manager.unmute_chat(chat_id)
            was_global = silence_manager.unmute_global()
            if was_chat or was_global:
                await message.reply("🔊 Тишина снята.")
            else:
                await message.reply("ℹ️ Тишина не была активна.")
            return
        minutes = int(getattr(config, "SILENCE_DEFAULT_MINUTES", 30))
        silence_manager.mute_chat(chat_id, minutes)
        await message.reply(f"🤫 Тишина в этом чате на **{minutes}** мин.")
        return

    if args.startswith("стоп"):
        was_chat = silence_manager.unmute_chat(chat_id)
        was_global = silence_manager.unmute_global()
        if was_chat or was_global:
            await message.reply("🔊 Тишина снята.")
        else:
            await message.reply("ℹ️ Тишина не была активна.")
        return

    if args.startswith("глобально"):
        rest = args.replace("глобально", "").strip()
        minutes = (
            int(rest) if rest.isdigit() else int(getattr(config, "SILENCE_DEFAULT_MINUTES", 60))
        )
        silence_manager.mute_global(minutes)
        await message.reply(f"🤫 Глобальная тишина на **{minutes}** мин.")
        return

    # Per-chat: toggle или с указанием минут
    if silence_manager.is_chat_muted(chat_id):
        silence_manager.unmute_chat(chat_id)
        await message.reply("🔊 Тишина в этом чате снята.")
        return

    minutes = int(args) if args.isdigit() else int(getattr(config, "SILENCE_DEFAULT_MINUTES", 30))
    silence_manager.mute_chat(chat_id, minutes)
    await message.reply(f"🤫 Тишина в этом чате на **{minutes}** мин.\n`!тишина стоп` чтобы снять.")


def _costs_filter_calls(calls: list, *, days: int | None = None) -> list:
    """Вернуть вызовы за последние `days` дней (None = все)."""
    if days is None:
        return calls
    import time as _time

    cutoff = _time.time() - days * 86400
    return [r for r in calls if r.timestamp >= cutoff]


def _costs_aggregate(calls: list) -> dict:
    """Агрегировать список CallRecord в сводку."""
    from collections import defaultdict as _dd

    by_model: dict = _dd(lambda: {"cost_usd": 0.0, "calls": 0, "tokens": 0})
    by_provider: dict = _dd(lambda: {"cost_usd": 0.0, "calls": 0})
    total_cost = 0.0
    total_tokens = 0
    for r in calls:
        total_cost += r.cost_usd
        total_tokens += r.input_tokens + r.output_tokens
        by_model[r.model_id]["cost_usd"] += r.cost_usd
        by_model[r.model_id]["calls"] += 1
        by_model[r.model_id]["tokens"] += r.input_tokens + r.output_tokens
        # Провайдер — часть до «/»
        provider = r.model_id.split("/")[0] if "/" in r.model_id else r.model_id
        by_provider[provider]["cost_usd"] += r.cost_usd
        by_provider[provider]["calls"] += 1
    return {
        "total_cost": total_cost,
        "total_tokens": total_tokens,
        "calls_count": len(calls),
        "by_model": dict(by_model),
        "by_provider": dict(by_provider),
    }


def _costs_ascii_trend(calls: list, days: int = 30) -> str:
    """Построить ASCII-график расходов за последние `days` дней."""
    import datetime

    now = datetime.date.today()
    # Сгруппировать вызовы по дате
    daily: dict[datetime.date, float] = {}
    for d in range(days):
        day = now - datetime.timedelta(days=days - 1 - d)
        daily[day] = 0.0
    for r in calls:
        day = datetime.date.fromtimestamp(r.timestamp)
        if day in daily:
            daily[day] += r.cost_usd

    values = [daily[d] for d in sorted(daily)]
    max_v = max(values) if values else 0.0
    total = sum(values)
    avg = total / len(values) if values else 0.0

    bars = " ▁▂▃▄▅▆▇█"

    def _bar(v: float) -> str:
        if max_v == 0:
            return " "
        idx = round((v / max_v) * (len(bars) - 1))
        return bars[idx]

    bar_line = "".join(_bar(v) for v in values)

    lines = [
        f"📈 **Тренд за {days} дней ($):**",
        f"`{bar_line}`",
        f"↑ {days}d ago {'·' * max(0, len(values) - 10)} ↑ today",
        f"avg=${avg:.2f}/d · total=${total:.4f}",
    ]
    return "\n".join(lines)


async def _handle_costs_today(bot: "KraabUserbot", message: Message) -> None:
    """!costs today — расходы за сегодня."""
    calls = _costs_filter_calls(getattr(cost_analytics, "_calls", []), days=1)
    agg = _costs_aggregate(calls)
    lines = [
        "💰 **Costs: сегодня**",
        "─────────────────",
        f"Вызовов: {agg['calls_count']} | Токенов: {agg['total_tokens']}",
        f"Стоимость: ${agg['total_cost']:.4f}",
    ]
    if agg["by_model"]:
        lines.append("")
        lines.append("**По моделям:**")
        for mid, data in sorted(agg["by_model"].items(), key=lambda x: -x[1]["cost_usd"]):
            lines.append(f"• {mid}: ${data['cost_usd']:.4f} ({data['calls']} calls)")
    await message.reply("\n".join(lines))


async def _handle_costs_week(bot: "KraabUserbot", message: Message) -> None:
    """!costs week — расходы за 7 дней."""
    calls = _costs_filter_calls(getattr(cost_analytics, "_calls", []), days=7)
    agg = _costs_aggregate(calls)
    lines = [
        "💰 **Costs: 7 дней**",
        "─────────────────",
        f"Вызовов: {agg['calls_count']} | Токенов: {agg['total_tokens']}",
        f"Стоимость: ${agg['total_cost']:.4f}",
        f"Средняя в день: ${agg['total_cost'] / 7:.4f}",
    ]
    if agg["by_model"]:
        lines.append("")
        lines.append("**По моделям:**")
        for mid, data in sorted(agg["by_model"].items(), key=lambda x: -x[1]["cost_usd"]):
            lines.append(f"• {mid}: ${data['cost_usd']:.4f} ({data['calls']} calls)")
    await message.reply("\n".join(lines))


async def _handle_costs_breakdown(bot: "KraabUserbot", message: Message) -> None:
    """!costs breakdown — разбивка по провайдерам."""
    calls = getattr(cost_analytics, "_calls", [])
    agg = _costs_aggregate(calls)
    lines = [
        "💰 **Costs: по провайдерам**",
        "─────────────────",
        f"Всего: ${agg['total_cost']:.4f} | Вызовов: {agg['calls_count']}",
        "",
        "**По провайдерам:**",
    ]
    total = agg["total_cost"] or 1.0  # защита от деления на 0
    for provider, data in sorted(agg["by_provider"].items(), key=lambda x: -x[1]["cost_usd"]):
        pct = round(data["cost_usd"] / total * 100, 1)
        lines.append(f"• {provider}: ${data['cost_usd']:.4f} ({pct}%, {data['calls']} calls)")
    if agg["by_model"]:
        lines.append("")
        lines.append("**По моделям:**")
        for mid, data in sorted(agg["by_model"].items(), key=lambda x: -x[1]["cost_usd"])[:8]:
            lines.append(f"• {mid}: ${data['cost_usd']:.4f} ({data['calls']} calls)")
    await message.reply("\n".join(lines))


async def _handle_costs_budget(bot: "KraabUserbot", message: Message) -> None:
    """!costs budget — бюджет vs фактические расходы."""
    budget = cost_analytics.get_monthly_budget_usd()
    cost_month = cost_analytics.get_monthly_cost_usd()
    cost_session = cost_analytics.get_cost_so_far_usd()
    forecast = cost_analytics.monthly_calls_forecast()
    lines = [
        "💰 **Costs: бюджет**",
        "─────────────────",
        f"Сессия: ${cost_session:.4f}",
        f"Месяц: ${cost_month:.4f}",
    ]
    if budget > 0:
        pct = min(100, round(cost_month / budget * 100, 1))
        remaining = max(0.0, budget - cost_month)
        bar_filled = round(pct / 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        lines += [
            f"Бюджет: ${budget:.2f}",
            f"[{bar}] {pct}%",
            f"Остаток: ${remaining:.4f}",
        ]
        if pct >= 90:
            lines.append("⚠️ Бюджет почти исчерпан!")
        elif pct >= 75:
            lines.append("⚡ Более 75% бюджета израсходовано.")
    else:
        lines.append("Бюджет: не задан (`!budget 10.00` чтобы задать)")
    if forecast is not None:
        lines.append(f"Прогноз вызовов (конец месяца): ~{int(forecast)}")
    await message.reply("\n".join(lines))


async def _handle_costs_trend(bot: "KraabUserbot", message: Message) -> None:
    """!costs trend — ASCII-тренд за 30 дней."""
    calls = getattr(cost_analytics, "_calls", [])
    trend_text = _costs_ascii_trend(calls, days=30)
    await message.reply(trend_text)


async def handle_costs(bot: "KraabUserbot", message: Message) -> None:
    """!costs [today|week|breakdown|budget|trend] — cost report (owner-only)."""
    # Проверка: только владелец
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 Команда доступна только владельцу.")

    args = (bot._get_command_args(message) or "").strip().lower()
    sub = args.split()[0] if args else ""

    if sub == "today":
        return await _handle_costs_today(bot, message)
    if sub == "week":
        return await _handle_costs_week(bot, message)
    if sub == "breakdown":
        return await _handle_costs_breakdown(bot, message)
    if sub == "budget":
        return await _handle_costs_budget(bot, message)
    if sub == "trend":
        return await _handle_costs_trend(bot, message)

    # Default — текущий month summary
    report = cost_analytics.build_usage_report_dict()

    cost_session = report.get("cost_session_usd", 0.0)
    budget = report.get("monthly_budget_usd") or 0.0
    cost_month = report.get("cost_month_usd", 0.0)
    total_calls = len(getattr(cost_analytics, "_calls", []))
    total_tokens = report.get("total_tokens", 0)
    total_fallbacks = report.get("total_fallbacks", 0)
    total_tool_calls = report.get("total_tool_calls", 0)
    by_model: dict = report.get("by_model", {})
    by_channel: dict = report.get("by_channel", {})

    # Процент бюджета
    if budget > 0:
        pct = min(100, round(cost_month / budget * 100, 1))
        budget_line = f"Бюджет: ${budget:.2f} ({pct}% использовано)"
    else:
        budget_line = "Бюджет: не задан"

    lines = [
        "💰 **Cost Report**",
        "─────────────────",
        f"Потрачено: ${cost_session:.4f}",
        budget_line,
        f"Вызовов: {total_calls} | Токенов: {total_tokens}",
        f"Fallbacks: {total_fallbacks} | Tool calls: {total_tool_calls}",
    ]

    if by_model:
        lines.append("")
        lines.append("**По моделям:**")
        for mid, data in sorted(by_model.items(), key=lambda x: -x[1].get("cost_usd", 0)):
            lines.append(f"• {mid}: ${data.get('cost_usd', 0):.4f} ({data.get('calls', 0)} calls)")

    if by_channel:
        lines.append("")
        lines.append("**По каналам:**")
        ch_parts = [f"{ch}: {cnt}" for ch, cnt in sorted(by_channel.items())]
        lines.append("• " + " | ".join(ch_parts))

    await message.reply("\n".join(lines), reply_markup=build_costs_detail_buttons())


async def handle_models(bot: "KraabUserbot", message: Message) -> None:
    """!models — распределение вызовов по тирам моделей (owner-only)."""
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 Команда доступна только владельцу.")

    from ..core.model_tier_tracker import format_tier_summary_text, get_tier_summary

    args = (bot._get_command_args(message) or "").strip().lower()
    # Поддерживаем !models 48 (часов) и !models week
    hours = 24.0
    if args == "week":
        hours = 168.0
    elif args == "month":
        hours = 720.0
    else:
        try:
            hours = float(args)
        except ValueError:
            pass

    calls = getattr(cost_analytics, "_calls", [])
    summary = get_tier_summary(calls, since_hours=hours)
    period_label = {24.0: "24ч", 168.0: "неделя", 720.0: "месяц"}.get(hours, f"{hours:.0f}ч")
    text = format_tier_summary_text(summary).replace("за 24ч", f"за {period_label}")
    await message.reply(text)


async def handle_budget(bot: "KraabUserbot", message: Message) -> None:
    """!budget [сумма] — показать или установить месячный бюджет (owner-only)."""
    # Проверка: только владелец
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 Команда доступна только владельцу.")

    raw_args = bot._get_command_args(message).strip()

    if not raw_args:
        # Показать текущий бюджет
        current = cost_analytics.get_monthly_budget_usd()
        cost_month = cost_analytics.get_monthly_cost_usd()
        if current > 0:
            pct = min(100, round(cost_month / current * 100, 1))
            remaining = max(0.0, current - cost_month)
            await message.reply(
                f"💳 **Месячный бюджет:** ${current:.2f}\n"
                f"Потрачено: ${cost_month:.4f} ({pct}%)\n"
                f"Осталось: ${remaining:.4f}"
            )
        else:
            await message.reply(
                f"💳 **Месячный бюджет:** не задан\n"
                f"Потрачено за месяц: ${cost_month:.4f}\n"
                f"Чтобы задать: `!budget 10.00`"
            )
        return

    # Установить новый бюджет
    try:
        new_budget = float(raw_args.replace(",", "."))
    except ValueError:
        raise UserInputError(
            user_message=f"❌ Некорректное значение: `{raw_args}`. Укажи число, например `!budget 15.00`."
        )

    if new_budget < 0:
        raise UserInputError(user_message="❌ Бюджет не может быть отрицательным.")

    cost_analytics._monthly_budget_usd = new_budget

    if new_budget == 0:
        await message.reply("✅ Месячный бюджет сброшен (без ограничений).")
    else:
        await message.reply(f"✅ Месячный бюджет установлен: **${new_budget:.2f}**")


async def handle_digest(bot: "KraabUserbot", message: Message) -> None:
    """
    !digest — немедленно сгенерировать и отправить daily + weekly digest (owner-only).

    Отправляет:
    1. Nightly Summary (daily) — данные за сегодня.
    2. Weekly Digest — данные за 7 дней (swarm/cost/inbox сводка).
    """
    # Проверка: только владелец
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 Команда доступна только владельцу.")

    await message.reply("⏳ Генерирую digest...")

    # --- Nightly (daily) summary ---
    try:
        from ..core.nightly_summary import generate_summary  # noqa: PLC0415

        daily_text = await generate_summary()
        await message.reply(daily_text, parse_mode="markdown")
    except Exception as exc:  # noqa: BLE001
        logger.warning("handle_digest_nightly_failed", error=str(exc))
        await message.reply(f"⚠️ Daily summary не удался: {exc}")

    # --- Weekly digest ---
    try:
        result = await weekly_digest.generate_digest()
    except Exception as exc:  # noqa: BLE001
        logger.warning("handle_digest_weekly_failed", error=str(exc))
        await message.reply(f"❌ Weekly digest не удался: {exc}")
        return

    if not result.get("ok"):
        err = result.get("error", "неизвестная ошибка")
        await message.reply(f"❌ Weekly digest не удался: {err}")
        return

    rounds = result.get("total_rounds", 0)
    cost = result.get("cost_week_usd", 0.0)
    attention = result.get("attention_count", 0)

    if not weekly_digest._telegram_callback:
        await message.reply(
            f"✅ **Weekly Digest**\n"
            f"Swarm rounds (7д): {rounds}\n"
            f"Cost (7д): ${cost:.4f}\n"
            f"Attention items: {attention}"
        )
    else:
        await message.reply(
            f"✅ Weekly digest отправлен.\n"
            f"Rounds: {rounds} | Cost 7д: ${cost:.4f} | Attention: {attention}"
        )


# ---------------------------------------------------------------------------
# !bench — запуск бенчмарков производительности через subprocess
# ---------------------------------------------------------------------------


async def handle_bench(bot: "KraabUserbot", message: Message) -> None:
    """
    !bench [fast|full|fts|semantic] — запуск subset бенчмарков перфоманса.

    Пресеты:
      fast     — 20 итераций (по умолчанию, ~15 сек)
      full     — 100 итераций (~60 сек)
      fts      — 50 итераций для FTS (~30 сек)
      semantic — 10 итераций для семантического поиска (~20 сек)

    Только для владельца (owner-only).
    """
    # Доступ только для владельца
    access = bot._get_access_profile(message.from_user)
    if access.level != AccessLevel.OWNER:
        await message.reply("⛔ Только для владельца.")
        return

    # Парсим аргументы
    args = (bot._get_command_args(message) or "").strip().lower()
    preset = args if args in ("fast", "full", "fts", "semantic") else "fast"

    # Bump command в реестр
    from ..core.command_registry import bump_command

    bump_command("bench")

    # Маппинг пресетов на количество итераций
    iterations_map = {
        "fast": 20,
        "full": 100,
        "fts": 50,
        "semantic": 10,
    }
    iterations = iterations_map.get(preset, 20)

    # Отправляем статус
    await message.reply(f"⏱ Benchmark `{preset}` (iterations={iterations})...")

    try:
        krab_root = pathlib.Path.home() / "Antigravity_AGENTS" / "Краб"
        result = subprocess.run(
            [sys.executable, "scripts/benchmark_suite.py", "--iterations", str(iterations)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(krab_root),
        )

        # Берём последние 1500 символов для вывода
        output = result.stdout[-1500:] if len(result.stdout) > 1500 else result.stdout

        if not output:
            output = "(empty output)"

        await message.reply(
            f"📊 **Benchmark results ({preset})**:\n```\n{output}\n```",
        )
        logger.info("handle_bench_done", preset=preset, iterations=iterations)

    except subprocess.TimeoutExpired:
        await message.reply("⚠️ Benchmark timed out после 120 сек")
        logger.warning("handle_bench_timeout", preset=preset)
    except Exception as exc:  # noqa: BLE001
        logger.error("handle_bench_error", preset=preset, error=str(exc))
        await message.reply(f"❌ Benchmark failed: {exc}")


# _health_deep_report — extracted to commands/system_commands.py (Phase 2 Wave 10).
# Re-exported above. _swarm_status_deep_report остаётся ниже (тесты патчат через namespace).


async def _swarm_status_deep_report() -> str:
    """Собирает подробный диагностический отчёт (!swarm status deep). Owner-only.

    Возвращает markdown-строку до 4000 символов.
    8 секций: teams clients, listeners, channels, active rounds,
    memory, task board, contacts, recent DM events.
    """
    from ..core.swarm_bus import TEAM_REGISTRY
    from ..core.swarm_channels import swarm_channels
    from ..core.swarm_memory import swarm_memory
    from ..core.swarm_task_board import swarm_task_board
    from ..core.swarm_team_listener import is_listeners_enabled

    # Лимит символов под Telegram
    _limit = 4000

    _team_emoji: dict[str, str] = {
        "traders": "📈",
        "coders": "💻",
        "analysts": "📊",
        "creative": "🎨",
    }

    sections: list[str] = ["🐝 **Swarm Status Deep**", "══════════════════════"]

    all_teams = list(TEAM_REGISTRY.keys())

    # ── 1. Team clients ──────────────────────────────────────────────────────
    client_lines: list[str] = ["**1. Team clients:**"]
    team_clients: dict[str, object] = getattr(swarm_channels, "_team_clients", {})
    for team in all_teams:
        emoji = _team_emoji.get(team, "🤖")
        cl = team_clients.get(team.lower())
        if cl is None:
            client_lines.append(f"  {emoji} {team}: ❌ нет клиента")
        else:
            connected = getattr(cl, "is_connected", False)
            username = getattr(cl, "_username", None) or getattr(cl, "username", None) or "?"
            icon = "🟢" if connected else "🔴"
            client_lines.append(f"  {emoji} {team}: {icon} @{username}")
    sections.append("\n".join(client_lines))

    # ── 2. Listeners state ───────────────────────────────────────────────────
    listeners_on = is_listeners_enabled()
    listener_icon = "✅ ON" if listeners_on else "🔇 OFF"
    sections.append(
        f"**2. Listeners:** {listener_icon}\n  owner detection: `access_control.is_owner_user_id`"
    )

    # ── 3. Channels ──────────────────────────────────────────────────────────
    chan_lines: list[str] = ["**3. Channels:**"]
    forum_chat_id: int | None = getattr(swarm_channels, "_forum_chat_id", None)
    team_topics: dict[str, int] = getattr(swarm_channels, "_team_topics", {})
    if forum_chat_id:
        chan_lines.append(f"  forum_chat_id: `{forum_chat_id}`")
        for team in all_teams:
            topic_id = team_topics.get(team.lower())
            icon = "✅" if topic_id else "❌"
            tip = f"topic `{topic_id}`" if topic_id else "нет топика"
            chan_lines.append(f"  {_team_emoji.get(team, '•')} {team}: {icon} {tip}")
    else:
        chan_lines.append("  ⚠️ forum mode не настроен")
        team_chats: dict[str, int] = getattr(swarm_channels, "_team_chats", {})
        if team_chats:
            for team, cid in team_chats.items():
                chan_lines.append(f"  {team}: legacy chat `{cid}`")
        else:
            chan_lines.append("  нет привязанных групп")
    sections.append("\n".join(chan_lines))

    # ── 4. Active rounds ─────────────────────────────────────────────────────
    round_lines: list[str] = ["**4. Active rounds:**"]
    any_active = False
    for team in all_teams:
        if swarm_channels.is_round_active(team):
            any_active = True
            round_lines.append(f"  🟢 {team}: раунд активен")
    if not any_active:
        round_lines.append("  ⚪ нет активных раундов")
    sections.append("\n".join(round_lines))

    # ── 5. Memory ────────────────────────────────────────────────────────────
    mem_lines: list[str] = ["**5. Memory:**"]
    known_mem_teams = swarm_memory.all_teams()
    for team in all_teams:
        if team in known_mem_teams:
            stats = swarm_memory.get_team_stats(team)
            total = stats.get("total_runs", 0)
            last = stats.get("last_run", "—")
            if hasattr(last, "isoformat"):
                last = last.isoformat()[:16]
            elif isinstance(last, str) and len(last) > 16:
                last = last[:16]
            mem_lines.append(
                f"  {_team_emoji.get(team, '•')} {team}: {total} прогонов (послед.: {last})"
            )
        else:
            mem_lines.append(f"  {_team_emoji.get(team, '•')} {team}: 0 прогонов")
    sections.append("\n".join(mem_lines))

    # ── 6. Task board ────────────────────────────────────────────────────────
    board_summary = swarm_task_board.get_board_summary()
    by_team = board_summary.get("by_team", {})
    by_status = board_summary.get("by_status", {})
    total_tasks = board_summary.get("total", 0)
    task_lines: list[str] = [f"**6. Task board:** {total_tasks} задач"]
    # Статусы глобально
    for st in ("pending", "in_progress", "done", "failed"):
        cnt = by_status.get(st, 0)
        if cnt:
            st_icon = {"pending": "⏳", "in_progress": "🔄", "done": "✅", "failed": "❌"}.get(
                st, "•"
            )
            task_lines.append(f"  {st_icon} {st}: {cnt}")
    # По командам
    for team in all_teams:
        cnt = by_team.get(team, 0)
        if cnt:
            task_lines.append(f"  {_team_emoji.get(team, '•')} {team}: {cnt}")
    sections.append("\n".join(task_lines))

    # ── 7. Contacts status ───────────────────────────────────────────────────
    # p0lrd MCP недоступен из handler-слоя напрямую — skip с заметкой
    sections.append(
        "**7. Contacts:** ℹ️ проверка через p0lrd MCP недоступна из handler-слоя\n"
        "  (используй !swarm contacts для проверки через внешний MCP)"
    )

    # ── 8. Recent DM events ──────────────────────────────────────────────────
    # swarm_team_listener не хранит историю входящих DM — статичный статус
    dm_lines: list[str] = ["**8. Recent DM events:**"]
    if listeners_on:
        dm_lines.append("  🎧 Listeners ON — team accounts слушают DM")
        dm_lines.append("  ℹ️ история DM не персистируется (in-memory only)")
    else:
        dm_lines.append("  🔇 Listeners OFF — DM игнорируются")
    sections.append("\n".join(dm_lines))

    # Сборка отчёта
    report = "\n\n".join(sections)
    if len(report) > _limit:
        # Считаем сколько символов обрезали
        extra_chars = len(report) - _limit
        report = report[: _limit - 40] + f"\n…(truncated {extra_chars} chars)"
    return report


# handle_health — extracted to commands/system_commands.py (Phase 2 Wave 10, Session 27).
# Re-exported above.


# ─────────────────────────────────────────────────────────────────────────────
# handle_context — показ / сброс / сохранение контекста чата (!context)
# ─────────────────────────────────────────────────────────────────────────────


def _estimate_session_tokens(messages: list[dict]) -> int:
    """Грубая оценка токенов в истории чата (символы / 4)."""
    total_chars = 0
    for msg in messages:
        content = msg.get("content") or ""
        if isinstance(content, list):
            # multipart: собираем текстовые части
            for part in content:
                if isinstance(part, dict):
                    total_chars += len(str(part.get("text") or ""))
                else:
                    total_chars += len(str(part))
        else:
            total_chars += len(str(content))
    return max(0, (total_chars + 3) // 4)


def _format_time_ago(seconds: float) -> str:
    """Человекочитаемое 'X мин/сек/ч назад'."""
    if seconds < 60:
        return f"{int(seconds)} сек назад"
    if seconds < 3600:
        return f"{int(seconds // 60)} мин назад"
    return f"{int(seconds // 3600)} ч назад"


_CHECKPOINTS_DIR = pathlib.Path.home() / ".openclaw" / "krab_runtime_state" / "context_checkpoints"


async def handle_context(bot: "KraabUserbot", message: Message) -> None:
    """!context — управление контекстом чата OpenClaw.

    Синтаксис:
      !context              — показать текущий контекст чата
      !context clear        — очистить историю (сброс)
      !context save         — сохранить checkpoint контекста
    """
    chat_id = str(message.chat.id)
    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    sub = parts[1].strip().lower() if len(parts) > 1 else ""

    if sub in ("clear", "очисти", "сброс"):
        # Сброс контекста чата
        openclaw_client.clear_session(chat_id)
        await message.reply(
            "🗑️ **Контекст очищен**\nИстория чата сброшена. Следующее сообщение начнёт новую сессию."
        )
        return

    if sub in ("save", "сохрани", "checkpoint"):
        # Сохранение checkpoint контекста в JSON
        messages = list(openclaw_client._sessions.get(chat_id) or [])
        if not messages:
            await message.reply("⚠️ Контекст пуст — нечего сохранять.")
            return
        try:
            _CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
            filename = _CHECKPOINTS_DIR / f"{chat_id}_{ts}.json"
            payload = {
                "chat_id": chat_id,
                "saved_at": ts,
                "message_count": len(messages),
                "estimated_tokens": _estimate_session_tokens(messages),
                "messages": messages,
            }
            filename.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
            await message.reply(
                f"💾 **Checkpoint сохранён**\nФайл: `{filename.name}`\nСообщений: {len(messages)}"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("context_checkpoint_save_failed", chat_id=chat_id, error=str(exc))
            await message.reply(f"❌ Не удалось сохранить checkpoint: {exc}")
        return

    # Показ текущего контекста (по умолчанию)
    messages = list(openclaw_client._sessions.get(chat_id) or [])
    # Считаем только не-системные сообщения как «диалоговые»
    dialog_msgs = [m for m in messages if m.get("role") != "system"]
    msg_count = len(dialog_msgs)
    token_est = _estimate_session_tokens(messages)

    # Определяем текущую модель через runtime route или config
    model = ""
    if hasattr(openclaw_client, "get_last_runtime_route"):
        route_meta = openclaw_client.get_last_runtime_route() or {}
        model = str(route_meta.get("model") or "").strip()
    if not model:
        model = str(get_runtime_primary_model() or getattr(config, "MODEL", "") or "unknown")

    # Время последнего обновления из атрибута _session_last_updated (если есть)
    last_update_str = "—"
    try:
        if hasattr(openclaw_client, "_session_last_updated"):
            ts_map: dict = openclaw_client._session_last_updated  # type: ignore[attr-defined]
            last_ts = ts_map.get(chat_id)
            if last_ts is not None:
                elapsed = time.time() - last_ts
                last_update_str = _format_time_ago(elapsed)
    except Exception:  # noqa: BLE001
        pass

    # Считаем сохранённые checkpoint'ы для этого чата
    checkpoint_count = 0
    try:
        if _CHECKPOINTS_DIR.exists():
            checkpoint_count = sum(1 for _ in _CHECKPOINTS_DIR.glob(f"{chat_id}_*.json"))
    except Exception:  # noqa: BLE001
        pass

    lines = [
        "📎 **Контекст чата**",
        "─────────────────────",
        f"Сообщений: `{msg_count}`",
        f"Токенов (оценка): `~{token_est:,}`".replace(",", "_"),
        f"Модель: `{model}`",
        f"Session ID: `telegram_{chat_id}`",
        f"Последнее обновление: {last_update_str}",
    ]
    if checkpoint_count:
        lines.append(f"Checkpoints: `{checkpoint_count}`")
    lines += [
        "",
        "Команды:",
        "`!context clear` — сбросить контекст",
        "`!context save` — сохранить checkpoint",
    ]

    await message.reply("\n".join(lines))


# !pin / !unpin — extracted to commands/social_commands.py (Phase 2 Wave 6, Session 27).
# Re-exported above (handle_pin, handle_unpin).


async def handle_archive(bot: "KraabUserbot", message: Message) -> None:
    """
    Архивация и разархивация чатов. Owner-only.

    Форматы:
      !archive          — архивировать текущий чат
      !unarchive        — разархивировать текущий чат
      !archive list     — показать список архивированных чатов (до 20)
      !archive stats    — статистика archive.db (размер, кол-во, чанки)
      !archive growth   — рост archive.db за период
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!archive` доступен только владельцу.")

    args = bot._get_command_args(message).strip().lower()

    if args == "list":
        # Получаем список архивированных диалогов
        try:
            archived = []
            async for dialog in bot.client.get_dialogs(folder_id=1):
                chat = dialog.chat
                title = (
                    getattr(chat, "title", None)
                    or getattr(chat, "first_name", None)
                    or str(chat.id)
                )
                archived.append(f"• `{chat.id}` — {title}")
                if len(archived) >= 20:
                    break
        except Exception as exc:
            reply = f"❌ Не удалось получить архив: `{exc}`"
        else:
            if archived:
                lines = ["📦 **Архивированные чаты** (до 20):"] + archived
                reply = "\n".join(lines)
            else:
                reply = "📦 Архив пуст."

    elif args == "stats":
        # Статистика archive.db: размер, кол-во сообщений, чанков, последняя запись
        import sqlite3

        from src.core.archive_growth_monitor import ARCHIVE_DB

        if not ARCHIVE_DB.exists():
            reply = "📊 archive.db не найден — Memory Layer не инициализирован."
        else:
            size_mb = ARCHIVE_DB.stat().st_size / 1024 / 1024
            try:
                conn = sqlite3.connect(f"file:{ARCHIVE_DB}?mode=ro", uri=True)
                msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                # chunks могут отсутствовать если индексация не запускалась
                try:
                    chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                except sqlite3.OperationalError:
                    chunk_count = 0
                # последняя запись по полю date
                try:
                    last_row = conn.execute("SELECT MAX(date) FROM messages").fetchone()[0]
                    last_write = last_row if last_row else "—"
                except sqlite3.OperationalError:
                    last_write = "—"
                conn.close()
                reply = (
                    f"📊 **Archive.db stats**\n"
                    f"• Размер: `{size_mb:.2f} MB`\n"
                    f"• Сообщений: `{msg_count:,}`\n"
                    f"• Чанков: `{chunk_count:,}`\n"
                    f"• Последнее сообщение: `{last_write}`"
                )
            except Exception as exc:
                reply = f"❌ Ошибка чтения archive.db: `{exc}`"

    elif args == "growth":
        # Динамика роста archive.db из истории снапшотов
        from src.core.archive_growth_monitor import growth_summary, take_snapshot

        snap = take_snapshot()
        summary = growth_summary()
        if snap is None:
            reply = "📈 archive.db не найден — данные недоступны."
        elif isinstance(summary.get("summary"), str):
            # Недостаточно данных
            reply = (
                f"📈 **Archive.db growth**\n"
                f"• Снапшотов: `{summary['snapshots']}`\n"
                f"• Текущий размер: `{snap.size_mb:.2f} MB`\n"
                f"• Сообщений сейчас: `{snap.message_count:,}`\n"
                f"• {summary['summary']}"
            )
        else:
            reply = (
                f"📈 **Archive.db growth** (за {summary['days_tracked']:.1f} дн.)\n"
                f"• Снапшотов: `{summary['snapshots']}`\n"
                f"• Размер: `{summary['first_size_mb']:.2f}` → `{summary['latest_size_mb']:.2f} MB`\n"
                f"• Рост: `+{summary['growth_mb_per_day']:.2f} MB/день`\n"
                f"• Сообщений: `{summary['latest_messages']:,}` "
                f"(+{summary['growth_messages_per_day']:.0f}/день)"
            )

    else:
        # Архивируем текущий чат
        chat_id = message.chat.id
        try:
            await bot.client.archive_chats(chat_id)
            reply = "📦 Чат добавлен в архив."
        except Exception as exc:
            reply = f"❌ Не удалось архивировать: `{exc}`"

    if message.from_user and message.from_user.id == bot.me.id:
        await message.edit(reply)
    else:
        await message.reply(reply)


async def handle_unarchive(bot: "KraabUserbot", message: Message) -> None:
    """
    Разархивирует текущий чат. Owner-only.

    Формат:
      !unarchive        — разархивировать текущий чат
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!unarchive` доступен только владельцу.")

    chat_id = message.chat.id
    try:
        await bot.client.unarchive_chats(chat_id)
        reply = "📤 Чат извлечён из архива."
    except Exception as exc:
        reply = f"❌ Не удалось разархивировать: `{exc}`"

    if message.from_user and message.from_user.id == bot.me.id:
        await message.edit(reply)
    else:
        await message.reply(reply)


async def handle_memo(bot: "KraabUserbot", message: Message) -> None:
    """
    Быстрые заметки из Telegram в Obsidian vault (00_Inbox).

    Синтаксис:
      !memo <текст>           — сохранить заметку
      !memo list [N]          — последние N заметок (по умолчанию 5)
      !memo search <запрос>   — поиск по заметкам
    """
    from ..core.memo_service import memo_service

    args = bot._get_command_args(message).strip()

    # Определяем название чата
    chat = message.chat
    chat_title: str = (
        getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(chat.id)
    )

    if not args or args.lower() in ("memo", "!memo"):
        raise UserInputError(
            user_message=(
                "📝 **Memo — быстрые заметки в Obsidian**\n\n"
                "`!memo <текст>` — сохранить заметку\n"
                "`!memo list [N]` — последние N заметок\n"
                "`!memo search <запрос>` — поиск по заметкам"
            )
        )

    # --- list ---
    if args.lower().startswith("list"):
        parts = args.split(maxsplit=1)
        n = 5
        if len(parts) == 2 and parts[1].isdigit():
            n = max(1, min(int(parts[1]), 50))
        items = memo_service.list_recent(n)
        if not items:
            await message.reply("📭 Заметок в 00_Inbox пока нет.")
            return
        lines = [f"📝 **Последние заметки ({len(items)}):**\n"]
        for i, item in enumerate(items, 1):
            lines.append(
                f"{i}. `{item['filename']}`\n   🕐 {item['created']}\n   {item['preview']}"
            )
        await message.reply("\n".join(lines))
        return

    # --- search ---
    if args.lower().startswith("search "):
        query = args[7:].strip()
        if not query:
            raise UserInputError(user_message="🔍 Укажи запрос: `!memo search <текст>`")
        results = memo_service.search(query)
        if not results:
            await message.reply(f"🔍 Ничего не найдено по запросу: `{query}`")
            return
        lines = [f"🔍 **Найдено ({len(results)}):**\n"]
        for item in results:
            lines.append(f"📄 `{item['filename']}`\n   {item['match']}")
        await message.reply("\n".join(lines))
        return

    # --- save ---
    result = await memo_service.save_async(args, chat_title=chat_title)
    if result.success:
        await message.reply(f"✅ {result.message}")
    else:
        await message.reply(f"❌ {result.message}")


async def handle_fwd(bot: "KraabUserbot", message: Message) -> None:
    """
    Пересылка сообщений без метки «Forwarded» (copy_message).

    Синтаксис:
      !fwd <chat_id>          — в ответ на сообщение: скопировать его в chat_id
      !fwd <chat_id> last N   — скопировать последние N сообщений из текущего чата

    Owner-only.
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!fwd` доступен только владельцу.")

    args = bot._get_command_args(message).strip()
    if not args:
        raise UserInputError(
            user_message=(
                "📤 **Форвард без метки**\n\n"
                "`!fwd <chat_id>` — скопировать сообщение (в ответ)\n"
                "`!fwd <chat_id> last N` — скопировать последние N сообщений"
            )
        )

    parts = args.split()
    try:
        to_chat_id = int(parts[0])
    except ValueError:
        raise UserInputError(user_message=f"❌ Неверный chat_id: `{parts[0]}`")

    from_chat_id = message.chat.id

    # Режим: last N
    if len(parts) >= 3 and parts[1].lower() == "last":
        try:
            n = int(parts[2])
        except ValueError:
            raise UserInputError(user_message=f"❌ N должно быть числом, получено: `{parts[2]}`")
        if n < 1 or n > 200:
            raise UserInputError(user_message="❌ N должно быть от 1 до 200.")

        try:
            # Собираем сообщения (get_chat_history возвращает newest-first)
            msgs = []
            async for msg in bot.client.get_chat_history(from_chat_id, limit=n):
                msgs.append(msg)
            # Пересылаем в хронологическом порядке (oldest first)
            msgs.reverse()
            copied = 0
            for msg in msgs:
                try:
                    await bot.client.copy_message(to_chat_id, from_chat_id, msg.id)
                    copied += 1
                except Exception:
                    pass  # пропускаем сервисные/недоступные сообщения
            reply = f"📤 Скопировано {copied}/{len(msgs)} сообщений → `{to_chat_id}`"
        except Exception as exc:
            reply = f"❌ Ошибка при копировании: `{exc}`"

    # Режим: reply на конкретное сообщение
    else:
        target = message.reply_to_message
        if target is None:
            raise UserInputError(
                user_message=(
                    "📤 Ответь на сообщение, которое хочешь переслать, "
                    "или используй `!fwd <chat_id> last N`."
                )
            )
        try:
            await bot.client.copy_message(to_chat_id, from_chat_id, target.id)
            reply = f"📤 Сообщение скопировано → `{to_chat_id}`"
        except Exception as exc:
            reply = f"❌ Не удалось скопировать: `{exc}`"

    if message.from_user and message.from_user.id == bot.me.id:
        await message.edit(reply)
    else:
        await message.reply(reply)


async def handle_collect(bot: "KraabUserbot", message: Message) -> None:
    """
    Собирает последние N сообщений из указанного чата и выводит в текущий.

    Синтаксис:
      !collect <chat_id> <N>

    Полезно для мониторинга: позволяет просмотреть историю любого чата,
    к которому юзербот имеет доступ.

    Owner-only.
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!collect` доступен только владельцу.")

    args = bot._get_command_args(message).strip()
    parts = args.split()
    if len(parts) < 2:
        raise UserInputError(
            user_message=(
                "📥 **Collect — просмотр истории чата**\n\n"
                "`!collect <chat_id> <N>` — вывести последние N сообщений из чата"
            )
        )

    try:
        src_chat_id = int(parts[0])
    except ValueError:
        raise UserInputError(user_message=f"❌ Неверный chat_id: `{parts[0]}`")

    try:
        n = int(parts[1])
    except ValueError:
        raise UserInputError(user_message=f"❌ N должно быть числом, получено: `{parts[1]}`")

    if n < 1 or n > 100:
        raise UserInputError(user_message="❌ N должно быть от 1 до 100.")

    to_chat_id = message.chat.id

    try:
        msgs = []
        async for msg in bot.client.get_chat_history(src_chat_id, limit=n):
            msgs.append(msg)
        msgs.reverse()  # хронологический порядок

        if not msgs:
            reply = f"📭 Чат `{src_chat_id}` пуст или недоступен."
            if message.from_user and message.from_user.id == bot.me.id:
                await message.edit(reply)
            else:
                await message.reply(reply)
            return

        # Шапка с количеством
        header = f"📥 **Collect** из `{src_chat_id}` — последние {len(msgs)} сообщений:"
        if message.from_user and message.from_user.id == bot.me.id:
            await message.edit(header)
        else:
            await message.reply(header)

        # Копируем сообщения по одному
        copied = 0
        for msg in msgs:
            try:
                await bot.client.copy_message(to_chat_id, src_chat_id, msg.id)
                copied += 1
            except Exception:
                pass  # сервисные сообщения могут не копироваться

        if copied < len(msgs):
            await message.reply(
                f"⚠️ Скопировано {copied}/{len(msgs)} (часть сообщений недоступна для копирования)"
            )

    except Exception as exc:
        reply = f"❌ Ошибка при сборе: `{exc}`"
        if message.from_user and message.from_user.id == bot.me.id:
            await message.edit(reply)
        else:
            await message.reply(reply)


# ---------------------------------------------------------------------------
# Команды управления сообщениями: !del, !purge, !autodel
# ---------------------------------------------------------------------------


# !del / !purge — extracted to commands/social_commands.py (Phase 2 Wave 6, Session 27).
# Re-exported above (handle_del, handle_purge).


# !summary / !catchup — extracted to commands/ai_commands.py (Phase 2 Wave 7, Session 27).
# Re-exported above (handle_summary, handle_catchup, _SUMMARY_*, _format_chat_history_for_llm).


# !translate / !translate auto — extracted to commands/translator_commands.py (Phase 2 Wave 9, Session 27).
# Re-exported above (handle_translate, handle_translate_auto, _TRANSLATE_LANG_ALIASES).


async def handle_bookmark(bot: "KraabUserbot", message: Message) -> None:
    """
    Закладки на важные Telegram-сообщения.

    Синтаксис:
      !bookmark (или !bm) — в ответ на сообщение, сохраняет закладку
      !bookmark list       — список всех закладок с превью
      !bookmark search <запрос> — поиск по закладкам
      !bookmark del <id>   — удалить закладку по ID
    """
    import datetime as _dt

    from ..core.bookmark_service import bookmark_service

    args = bot._get_command_args(message).strip()

    # ─── help ───────────────────────────────────────────────────────────────
    if args.lower() in ("help", "помощь", "?"):
        raise UserInputError(
            user_message=(
                "🔖 **Bookmark — закладки на сообщения**\n\n"
                "Ответь на сообщение и введи:\n"
                "`!bookmark` или `!bm` — сохранить закладку\n"
                "`!bookmark list` — все закладки\n"
                "`!bookmark search <запрос>` — поиск\n"
                "`!bookmark del <id>` — удалить"
            )
        )

    # ─── list ───────────────────────────────────────────────────────────────
    if args.lower() in ("list", "список", "ls"):
        items = bookmark_service.list_all()
        if not items:
            await message.reply("📭 Закладок пока нет.")
            return
        lines = [f"🔖 **Закладки ({len(items)}):**\n"]
        for b in items:
            ts = _dt.datetime.fromtimestamp(b["timestamp"]).strftime("%d.%m %H:%M")
            preview = b["text_preview"] or "—"
            from_user = b.get("from_user") or "?"
            lines.append(f"**#{b['id']}** · {b['chat_title']} · {from_user} · {ts}\n   {preview}")
        await message.reply("\n".join(lines))
        return

    # ─── search ─────────────────────────────────────────────────────────────
    if args.lower().startswith("search ") or args.lower().startswith("поиск "):
        query = args.split(maxsplit=1)[1].strip() if " " in args else ""
        if not query:
            raise UserInputError(user_message="🔍 Укажи запрос: `!bookmark search <текст>`")
        results = bookmark_service.search(query)
        if not results:
            await message.reply(f"🔍 Ничего не найдено по запросу: `{query}`")
            return
        lines = [f"🔍 **Найдено ({len(results)}):**\n"]
        for b in results:
            ts = _dt.datetime.fromtimestamp(b["timestamp"]).strftime("%d.%m %H:%M")
            lines.append(
                f"**#{b['id']}** · {b['chat_title']} · {ts}\n   {b['text_preview'] or '—'}"
            )
        await message.reply("\n".join(lines))
        return

    # ─── del ────────────────────────────────────────────────────────────────
    if (
        args.lower().startswith("del ")
        or args.lower().startswith("delete ")
        or args.lower().startswith("rm ")
    ):
        id_str = args.split(maxsplit=1)[1].strip() if " " in args else ""
        if not id_str.isdigit():
            raise UserInputError(user_message="❌ Укажи числовой ID: `!bookmark del <id>`")
        bm_id = int(id_str)
        ok = await bookmark_service.delete_async(bm_id)
        if ok:
            await message.reply(f"🗑 Закладка **#{bm_id}** удалена.")
        else:
            await message.reply(f"❌ Закладка #{bm_id} не найдена.")
        return

    # ─── save (ответ на сообщение) ──────────────────────────────────────────
    reply = getattr(message, "reply_to_message", None)
    if reply is None:
        raise UserInputError(
            user_message=(
                "🔖 Ответь на сообщение, которое хочешь сохранить как закладку:\n"
                "`!bookmark` или `!bm`\n\n"
                "Другие команды:\n"
                "`!bookmark list` · `!bookmark search <текст>` · `!bookmark del <id>`"
            )
        )

    # Собираем данные о сообщении
    chat = message.chat
    chat_title: str = (
        getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(chat.id)
    )
    reply_from = getattr(reply, "from_user", None)
    if reply_from:
        from_name = (
            getattr(reply_from, "username", None)
            and f"@{reply_from.username}"
            or getattr(reply_from, "first_name", None)
            or str(getattr(reply_from, "id", "?"))
        )
    else:
        from_name = "?"

    text = str(getattr(reply, "text", None) or getattr(reply, "caption", None) or "").strip()
    if not text:
        text = "[медиа-сообщение без текста]"

    bm = await bookmark_service.add_async(
        chat_id=chat.id,
        chat_title=chat_title,
        message_id=reply.id,
        text=text,
        from_user=from_name,
    )
    await message.reply(
        f"🔖 Закладка **#{bm['id']}** сохранена!\n"
        f"Чат: {chat_title} · От: {from_name}\n"
        f"_{bm['text_preview'][:100]}{'…' if len(bm['text_preview']) > 100 else ''}_"
    )


# ---------------------------------------------------------------------------
# !export — экспорт истории чата в Markdown
# ---------------------------------------------------------------------------

EXPORT_VAULT_DIR = pathlib.Path("/Users/pablito/Documents/Obsidian Vault/30_Recordings/32_Chats")
EXPORT_DEFAULT_LIMIT = 100
EXPORT_MAX_LIMIT = 1000


def _sanitize_filename(name: str) -> str:
    """Убирает символы, запрещённые в именах файлов."""
    return "".join(c if c.isalnum() or c in " _-" else "_" for c in name).strip()


def _format_sender(msg) -> str:
    """Возвращает отображаемое имя отправителя сообщения."""
    if msg.from_user:
        u = msg.from_user
        parts = [u.first_name or "", u.last_name or ""]
        full = " ".join(p for p in parts if p).strip()
        return full or u.username or str(u.id)
    if msg.sender_chat:
        return msg.sender_chat.title or str(msg.sender_chat.id)
    return "Unknown"


def _msg_text(msg) -> str:
    """Возвращает текстовое содержимое сообщения (текст или подпись)."""
    return (msg.text or msg.caption or "").strip()


def _render_export_markdown(
    chat_title: str,
    chat_id: int,
    messages: list,
    exported_at: datetime.datetime,
) -> str:
    """Рендерит список сообщений в Markdown-формат с YAML frontmatter."""
    header = (
        "---\n"
        f"chat_title: {chat_title}\n"
        f"chat_id: {chat_id}\n"
        f"exported: {exported_at.strftime('%Y-%m-%dT%H:%M:%S')}\n"
        f"messages: {len(messages)}\n"
        "---\n"
    )

    # Группируем по дате
    days: dict[str, list] = {}
    for msg in messages:
        if msg.date is None:
            continue
        day_key = msg.date.strftime("%Y-%m-%d")
        days.setdefault(day_key, []).append(msg)

    body_parts: list[str] = []
    for day_key in sorted(days):
        body_parts.append(f"\n## {day_key}\n")
        for msg in days[day_key]:
            time_str = msg.date.strftime("%H:%M")
            sender = _format_sender(msg)
            text = _msg_text(msg)
            # Медиа без подписи
            if not text:
                if msg.photo:
                    text = "_[фото]_"
                elif msg.video:
                    text = "_[видео]_"
                elif msg.audio or msg.voice:
                    text = "_[аудио]_"
                elif msg.document:
                    text = "_[документ]_"
                elif msg.sticker:
                    text = f"_[стикер: {msg.sticker.emoji or ''}]_"
                else:
                    text = "_[медиа]_"
            body_parts.append(f"### {time_str} — {sender}\n{text}\n")

    return header + "".join(body_parts)


async def handle_export(bot: "KraabUserbot", message: Message) -> None:
    """
    !export [N|all] — экспортирует историю чата в Markdown-файл.

    !export        — последние 100 сообщений (default)
    !export 200    — последние 200 сообщений
    !export all    — все сообщения (до 1000)
    """
    # Парсим аргумент
    raw_args = (message.text or "").split(maxsplit=1)
    arg = raw_args[1].strip() if len(raw_args) > 1 else ""

    if arg.lower() == "all":
        limit = EXPORT_MAX_LIMIT
    elif arg.isdigit():
        limit = min(int(arg), EXPORT_MAX_LIMIT)
    elif arg == "":
        limit = EXPORT_DEFAULT_LIMIT
    else:
        await message.reply(
            "❌ Неверный аргумент. Примеры:\n`!export` / `!export 200` / `!export all`"
        )
        return

    chat = message.chat
    chat_id = chat.id
    chat_title = getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(chat_id)

    status_msg = await message.reply(f"⏳ Экспортирую {limit} сообщений из «{chat_title}»…")

    try:
        # Собираем сообщения через MTProto (get_chat_history — обратный порядок, новые первые)
        raw_msgs = []
        async for msg in bot.client.get_chat_history(chat_id, limit=limit):
            raw_msgs.append(msg)
        # Разворачиваем в хронологический порядок
        raw_msgs.reverse()
    except Exception as exc:
        logger.exception("handle_export: ошибка получения истории")
        await status_msg.edit(f"❌ Ошибка получения истории: {str(exc)[:200]}")
        return

    if not raw_msgs:
        await status_msg.edit("⚠️ Нет сообщений для экспорта.")
        return

    exported_at = datetime.datetime.now()
    md_content = _render_export_markdown(chat_title, chat_id, raw_msgs, exported_at)

    # Формируем имя файла
    safe_title = _sanitize_filename(chat_title)[:60]
    date_prefix = exported_at.strftime("%Y-%m-%d")
    filename = f"{date_prefix}_{safe_title}.md"

    # Создаём директорию если не существует
    EXPORT_VAULT_DIR.mkdir(parents=True, exist_ok=True)
    file_path = EXPORT_VAULT_DIR / filename

    try:
        file_path.write_text(md_content, encoding="utf-8")
    except OSError as exc:
        logger.exception("handle_export: ошибка записи файла")
        await status_msg.edit(f"❌ Ошибка записи файла: {str(exc)[:200]}")
        return

    # Отправляем файл в чат
    try:
        await bot.client.send_document(
            chat_id=chat_id,
            document=str(file_path),
            caption=(
                f"📄 Экспорт чата «{chat_title}»\nСообщений: {len(raw_msgs)}\nФайл: `{filename}`"
            ),
        )
        await status_msg.delete()
    except Exception as exc:
        logger.exception("handle_export: ошибка отправки документа")
        await status_msg.edit(
            f"✅ Файл сохранён: `{file_path}`\n⚠️ Не удалось отправить документ: {str(exc)[:200]}"
        )


# !react — extracted to commands/social_commands.py (Phase 2 Wave 6, Session 27).
# Re-exported above (handle_react).


async def handle_note(bot: "KraabUserbot", message: Message) -> None:
    """
    Голосовая заметка в Obsidian через транскрибацию.

    Использование:
      !note              — в ответ на голосовое сообщение → транскрибирует и сохраняет
      !note <тег>        — добавляет тег к заметке (например: !note идея)

    Записывает с пометкой [voice] и source: krab-voice.
    """
    from ..core.memo_service import memo_service

    # Проверяем: !note должна быть reply на голосовое сообщение
    reply = message.reply_to_message
    if reply is None:
        raise UserInputError(
            user_message=(
                "🎤 **Note — голосовая заметка в Obsidian**\n\n"
                "Ответь командой `!note` на голосовое сообщение.\n"
                "Добавь тег: `!note идея`"
            )
        )

    # Проверяем что reply содержит аудио
    has_voice = bool(getattr(reply, "voice", None))
    has_audio = bool(getattr(reply, "audio", None))
    has_video_note = bool(getattr(reply, "video_note", None))

    if not (has_voice or has_audio or has_video_note):
        raise UserInputError(
            user_message="❌ Команда `!note` работает только в ответ на голосовое сообщение."
        )

    # Извлекаем опциональный тег из аргументов команды
    raw_args = bot._get_command_args(message).strip()
    # Убираем само слово "note" если пользователь написал "!note note"
    if raw_args.lower() == "note":
        raw_args = ""
    user_tag = raw_args if raw_args else None

    # Определяем название чата
    chat = message.chat
    chat_title: str = (
        getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(chat.id)
    )

    # Транскрибируем через существующий _transcribe_audio_message
    status_msg = await message.reply("⏳ Транскрибирую голосовое сообщение…")

    transcript, voice_error = await bot._transcribe_audio_message(reply)

    if not transcript:
        err = voice_error or "❌ Не удалось распознать голосовое сообщение."
        try:
            await status_msg.edit(err)
        except Exception:  # noqa: BLE001
            await message.reply(err)
        return

    # Формируем тело заметки с пометкой [voice]
    note_body = f"[voice] {transcript}"

    # Теги: всегда "voice", плюс пользовательский тег если задан
    tags: list[str] = ["voice"]
    if user_tag:
        tags.append(user_tag)

    result = await memo_service.save_async(
        text=note_body,
        chat_title=chat_title,
        tags=tags,
        source_type="krab-voice",
    )

    if result.success:
        tag_display = f" #{user_tag}" if user_tag else ""
        reply_text = (
            f"✅ Голосовая заметка сохранена{tag_display}\n"
            f"📄 `{result.file_path.name if result.file_path else '?'}`\n"
            f"\n_{transcript[:300]}{'…' if len(transcript) > 300 else ''}_"
        )
    else:
        reply_text = f"❌ {result.message}"

    try:
        await status_msg.edit(reply_text)
    except Exception:  # noqa: BLE001
        await message.reply(reply_text)


# !alias — extracted to commands/social_commands.py (Phase 2 Wave 6, Session 27).
# Re-exported above (handle_alias).


# !ask — extracted to commands/ai_commands.py (Phase 2 Wave 7, Session 27).
# Re-exported above (handle_ask, _parse_ask_memory_flags).


# !fix — extracted to commands/ai_commands.py (Phase 2 Wave 7, Session 27).
# Re-exported above (handle_fix).


# !rewrite — extracted to commands/ai_commands.py (Phase 2 Wave 7, Session 27).
# Re-exported above (handle_rewrite, _REWRITE_MODES).


# !report — extracted to commands/ai_commands.py (Phase 2 Wave 7, Session 27).
# Re-exported above (handle_report, _collect_daily_report_data, _render_daily_report).
# !poll / !quiz / !dice — extracted to commands/social_commands.py (Phase 2 Wave 6).


async def handle_grep(bot: "KraabUserbot", message: Message) -> None:
    """
    !grep <query> [@chat] [N] — поиск по истории чата.

    Форматы:
      !grep биткоин              — ищет в текущем чате (последние 200 сообщений)
      !grep биткоин 500          — ищет в последних 500 сообщениях
      !grep биткоин @durov 100   — ищет в чате @durov (последние 100 сообщений)
      !grep /pattern/            — regex-поиск (case-insensitive)
    """
    import re

    raw = bot._get_command_args(message)
    if not raw:
        raise UserInputError(
            user_message=(
                "🔍 Использование:\n"
                "`!grep <запрос> [@чат] [N]`\n\n"
                "Примеры:\n"
                "`!grep биткоин` — ищет в этом чате (200 последних сообщений)\n"
                "`!grep биткоин 500` — ищет в 500 последних сообщениях\n"
                "`!grep биткоин @durov 100` — ищет в другом чате\n"
                "`!grep /паттерн/` — regex-поиск"
            )
        )

    parts = raw.split()

    # --- Парсим аргументы ---
    query_parts: list[str] = []
    target_chat: int | str = message.chat.id
    limit: int = 200

    i = 0
    while i < len(parts):
        part = parts[i]
        # @chat — указание альтернативного чата
        if part.startswith("@") and len(part) > 1:
            target_chat = part  # pyrogram принимает username
        # Числовой лимит (только если выглядит как standalone число)
        elif part.isdigit():
            limit = min(int(part), 2000)  # защита от очень больших лимитов
        else:
            query_parts.append(part)
        i += 1

    query_str = " ".join(query_parts).strip()
    if not query_str:
        raise UserInputError(user_message="🔍 Укажи поисковый запрос после `!grep`")

    # --- Определяем тип поиска: regex или plain ---
    use_regex = False
    pattern: re.Pattern | None = None

    if query_str.startswith("/") and query_str.endswith("/") and len(query_str) > 2:
        # /pattern/ — regex-режим
        regex_src = query_str[1:-1]
        try:
            pattern = re.compile(regex_src, re.IGNORECASE)
            use_regex = True
            display_query = f"/{regex_src}/"
        except re.error as exc:
            raise UserInputError(user_message=f"❌ Невалидный regex: `{exc}`") from exc
    else:
        display_query = query_str

    status_msg = await message.reply(
        f"🔍 Ищу `{display_query}` в последних **{limit}** сообщениях..."
    )

    matches: list[str] = []
    scanned = 0

    try:
        async for msg in bot.client.get_chat_history(target_chat, limit=limit):
            scanned += 1
            text = msg.text or msg.caption or ""
            if not text:
                continue

            # Фильтрация: regex или plain case-insensitive
            if use_regex and pattern is not None:
                found = bool(pattern.search(text))
            else:
                found = query_str.lower() in text.lower()

            if not found:
                continue

            # Форматируем метаданные
            dt = msg.date
            time_str = dt.strftime("%d.%m %H:%M") if dt else "??:??"

            sender = ""
            if msg.from_user:
                sender = (
                    f"@{msg.from_user.username}"
                    if msg.from_user.username
                    else msg.from_user.first_name or "Unknown"
                )
            elif msg.sender_chat:
                sender = msg.sender_chat.title or "Channel"

            # Обрезаем длинный текст, показываем контекст вокруг совпадения
            preview = text.replace("\n", " ")
            if len(preview) > 200:
                if use_regex and pattern is not None:
                    m = pattern.search(preview)
                    if m:
                        start = max(0, m.start() - 60)
                        end = min(len(preview), m.end() + 60)
                        prefix = "..." if start > 0 else ""
                        suffix = "..." if end < len(preview) else ""
                        preview = prefix + preview[start:end] + suffix
                    else:
                        preview = preview[:200] + "..."
                else:
                    idx = preview.lower().find(query_str.lower())
                    if idx >= 0:
                        start = max(0, idx - 60)
                        end = min(len(preview), idx + len(query_str) + 60)
                        prefix = "..." if start > 0 else ""
                        suffix = "..." if end < len(preview) else ""
                        preview = prefix + preview[start:end] + suffix
                    else:
                        preview = preview[:200] + "..."

            matches.append(f"[{time_str}] {sender}: {preview}")

            # Не более 20 совпадений в ответе
            if len(matches) >= 20:
                break

    except Exception as exc:  # noqa: BLE001
        logger.warning("handle_grep_error", error=str(exc))
        await status_msg.edit(f"❌ Ошибка при поиске: {exc}")
        return

    if not matches:
        await status_msg.edit(
            f"🔍 Ничего не найдено для `{display_query}` в последних {scanned} сообщениях."
        )
        return

    # --- Форматируем результат ---
    header = f"🔍 Найдено **{len(matches)}** совпадений для `{display_query}`"
    if len(matches) >= 20:
        header += " (показаны первые 20)"
    header += ":\n\n"

    lines = [f"{i + 1}. {m}" for i, m in enumerate(matches)]
    body = "\n".join(lines)

    # Telegram limit 4096 символов
    full = header + body
    if len(full) > 4000:
        full = full[:3950] + "\n...(обрезано)"

    await status_msg.edit(full)


# ---------------------------------------------------------------------------
# QR-генерация
# ---------------------------------------------------------------------------


async def handle_qr(bot: "KraabUserbot", message: Message) -> None:
    """Генерирует QR-код из текста/URL и отправляет фото."""
    import os
    import tempfile

    # Получаем текст: из аргументов или из reply-сообщения
    raw_args = bot._get_command_args(message).strip()

    if raw_args:
        text = raw_args
    elif message.reply_to_message:
        replied = message.reply_to_message
        # берём текст или подпись к медиа
        text = replied.text or replied.caption or ""
        text = text.strip()
    else:
        text = ""

    if not text:
        raise UserInputError(
            user_message="📷 Укажи текст или URL: `!qr <текст>`, либо ответь на сообщение."
        )

    # Генерируем QR через segno (чистый Python, без зависимостей от Pillow)
    try:
        import segno
    except ImportError:
        await message.reply("❌ Библиотека `segno` не установлена. Запусти: `pip install segno`")
        return

    # Создаём временный файл
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="krab_qr_")
    os.close(tmp_fd)

    try:
        qr = segno.make(text, error="m")
        # scale=10 → ~350px при версии 1; border=4 — стандартный quiet zone
        qr.save(tmp_path, kind="png", scale=10, border=4)

        caption = f"📷 QR: `{text[:80]}{'...' if len(text) > 80 else ''}`"
        await bot.client.send_photo(
            chat_id=message.chat.id,
            photo=tmp_path,
            caption=caption,
            reply_to_message_id=message.id,
        )
    finally:
        # Удаляем временный файл в любом случае
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# !weather — погода через wttr.in (fast, no API key) с LLM fallback
# ---------------------------------------------------------------------------

_WTTR_URL = "https://wttr.in/{city}?format=4&lang=ru"
_WTTR_TIMEOUT = 8.0


async def _fetch_wttr(city: str) -> str | None:
    """Получает погоду через wttr.in format=4 (compact one-liner).

    Returns:
        Строка погоды или None при ошибке.
    """
    url = _WTTR_URL.format(city=city.replace(" ", "+"))
    try:
        async with httpx.AsyncClient(timeout=_WTTR_TIMEOUT) as client:
            resp = await client.get(url, follow_redirects=True)
        if resp.status_code == 200:
            text = resp.text.strip()
            # wttr.in возвращает "City: ☀️ +22°C ↗20km/h …" или похожее
            if text and len(text) > 5:
                return text
    except (httpx.TimeoutException, httpx.RequestError):
        pass
    return None


async def handle_weather(bot: "KraabUserbot", message: Message) -> None:
    """
    Показывает текущую погоду для города.

    Форматы:
    - !weather          — погода в городе по умолчанию (DEFAULT_WEATHER_CITY)
    - !weather <город>  — погода в указанном городе

    Приоритет: wttr.in (быстро, без API-ключа) → LLM web_search (fallback).
    """
    city = bot._get_command_args(message).strip()
    if not city:
        city = config.DEFAULT_WEATHER_CITY

    msg = await message.reply(f"🌤 Смотрю погоду в **{city}**...")

    # Быстрый путь: wttr.in
    wttr_result = await _fetch_wttr(city)
    if wttr_result:
        await msg.edit(f"🌤 {wttr_result}")
        return

    # Fallback: LLM + web_search (медленнее, но надёжнее для экзотических городов)
    session_id = f"weather_{message.chat.id}"
    prompt = (
        f"Какая сейчас погода в {city}? "
        "Дай краткий ответ: температура, облачность, осадки. "
        "Используй актуальные данные из веб-поиска."
    )

    try:
        chunks: list[str] = []
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            disable_tools=False,
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()
        if not result:
            await msg.edit("❌ Не удалось получить данные о погоде.")
            return

        parts = _split_text_for_telegram(result)
        await msg.edit(parts[0])
        for part in parts[1:]:
            await message.reply(part)

    except Exception as exc:  # noqa: BLE001
        logger.error("handle_weather_error", error=str(exc))
        await msg.edit(f"❌ Ошибка получения погоды: {exc}")


# !hash — extracted to commands/text_utils.py (Phase 2, Session 27)
# Re-exported below: handle_hash


# !calc — extracted to commands/text_utils.py (Phase 2, Session 27)
# Re-exported below: handle_calc, safe_calc


# !b64 — extracted to commands/text_utils.py (Phase 2, Session 27)
# Re-exported below: handle_b64, _b64_encode, _b64_decode, _b64_is_valid

import base64 as _base64  # noqa: E402  (kept for !encrypt/!decrypt below)

# ---------------------------------------------------------------------------
# !encrypt / !decrypt — симметричное шифрование (XOR + SHA-256 + Base64)
# ---------------------------------------------------------------------------
import hashlib as _hashlib  # noqa: E402


def _derive_key(password: str) -> bytes:
    """Выводит 32-байтный ключ из пароля через SHA-256."""
    return _hashlib.sha256(password.encode("utf-8")).digest()


def _xor_crypt(data: bytes, key: bytes) -> bytes:
    """XOR-шифрование/дешифрование с циклическим ключом."""
    key_len = len(key)
    return bytes(b ^ key[i % key_len] for i, b in enumerate(data))


def encrypt_text(password: str, text: str) -> str:
    """Шифрует текст паролем, возвращает Base64-строку."""
    key = _derive_key(password)
    ciphertext = _xor_crypt(text.encode("utf-8"), key)
    return _base64.b64encode(ciphertext).decode("ascii")


def decrypt_text(password: str, b64_cipher: str) -> str:
    """Дешифрует Base64-шифртекст паролем, возвращает исходный текст."""
    key = _derive_key(password)
    # мягкий паддинг
    stripped = b64_cipher.strip().replace("\n", "").replace(" ", "")
    padded = stripped + "=" * ((4 - len(stripped) % 4) % 4)
    ciphertext = _base64.b64decode(padded)
    return _xor_crypt(ciphertext, key).decode("utf-8")


async def handle_encrypt(bot: "KraabUserbot", message: Message) -> None:
    """
    Команда !encrypt — шифрование текста паролем.

    Формат: !encrypt <password> <текст>
    Возвращает зашифрованный Base64-блоб.
    """
    args = bot._get_command_args(message).strip()
    parts = args.split(" ", 1)
    if len(parts) < 2 or not parts[0] or not parts[1].strip():
        raise UserInputError(
            user_message=(
                "🔒 **Encrypt — справка**\n\n"
                "`!encrypt <пароль> <текст>` — зашифровать текст\n"
                "`!decrypt <пароль> <base64>` — расшифровать\n\n"
                "Алгоритм: XOR + SHA-256(пароль) + Base64"
            )
        )
    password, plaintext = parts[0], parts[1].strip()
    result = encrypt_text(password, plaintext)
    await message.reply(f"🔒 **Encrypted:**\n`{result}`")


async def handle_decrypt(bot: "KraabUserbot", message: Message) -> None:
    """
    Команда !decrypt — расшифровка текста паролем.

    Формат: !decrypt <password> <base64>
    Возвращает расшифрованный текст.
    """
    args = bot._get_command_args(message).strip()
    parts = args.split(" ", 1)
    if len(parts) < 2 or not parts[0] or not parts[1].strip():
        raise UserInputError(
            user_message=(
                "🔓 **Decrypt — справка**\n\n"
                "`!decrypt <пароль> <base64>` — расшифровать\n"
                "`!encrypt <пароль> <текст>` — зашифровать\n\n"
                "Алгоритм: XOR + SHA-256(пароль) + Base64"
            )
        )
    password, b64_cipher = parts[0], parts[1].strip()
    try:
        result = decrypt_text(password, b64_cipher)
    except Exception as exc:  # noqa: BLE001
        raise UserInputError(
            user_message=f"❌ Не удалось расшифровать: {exc}\n\nПроверь пароль и корректность Base64."
        ) from exc
    await message.reply(f"🔓 **Decrypted:**\n`{result}`")


# ---------------------------------------------------------------------------
# Вспомогательные функции для сетевых утилит
# ---------------------------------------------------------------------------


# !ip / !dns / !ping handlers + helpers (_get_local_ip, _get_public_ip) —
# extracted to commands/system_commands.py (Phase 2 Wave 10, Session 27). Re-exported above.
# !rand — генератор случайных значений
# ---------------------------------------------------------------------------


# !rand — extracted to commands/text_utils.py (Phase 2, Session 27)
# Re-exported below: handle_rand


# ---------------------------------------------------------------------------
# !quote — moved to commands/memory_commands.py (Phase 2 Wave 5)
# ---------------------------------------------------------------------------

# _BUILTIN_QUOTES / _SAVED_QUOTES_PATH moved to commands/memory_commands.py
# Путь к archive.db для команды !memory clear (тот же что в reset_helpers)
_ARCHIVE_DB_PATH_FOR_CLEAR = pathlib.Path.home() / ".openclaw" / "krab_memory" / "archive.db"


# _load_saved_quotes / _save_quotes / handle_quote moved to commands/memory_commands.py


# ---------------------------------------------------------------------------
# !define — определение слова/термина через AI
# ---------------------------------------------------------------------------

# Модификаторы режима и языка
_DEFINE_DETAILED_KEYWORDS = {"подробно", "detailed", "full", "полно", "полностью", "расширенно"}
_DEFINE_EN_KEYWORDS = {"en", "english", "англ", "английский"}


def _parse_define_args(raw_args: str) -> tuple[str, str, bool]:
    """
    Разбирает аргументы команды !define.

    Возвращает (слово, язык, подробно).
    язык: "ru" | "en"
    подробно: True если запрошено развёрнутое определение.

    Примеры:
      "Python"           -> ("Python", "ru", False)
      "Python en"        -> ("Python", "en", False)
      "Python подробно"  -> ("Python", "ru", True)
      "Python en подробно" -> ("Python", "en", True)
    """
    parts = raw_args.strip().split()
    if not parts:
        return ("", "ru", False)

    lang = "ru"
    detailed = False
    term_parts: list[str] = []

    for part in parts:
        lower = part.lower()
        if lower in _DEFINE_EN_KEYWORDS:
            lang = "en"
        elif lower in _DEFINE_DETAILED_KEYWORDS:
            detailed = True
        else:
            term_parts.append(part)

    term = " ".join(term_parts).strip()
    return (term, lang, detailed)


def _build_define_prompt(term: str, lang: str, detailed: bool) -> str:
    """Формирует промпт для запроса определения."""
    if lang == "en":
        if detailed:
            return (
                f"Give a detailed definition of the term or word: «{term}». "
                "Include etymology if relevant, main meanings, examples of use, "
                "and related concepts. Answer in English."
            )
        return (
            f"Give a brief definition of the term or word: «{term}» in 2-3 sentences. "
            "Be precise and clear. Answer in English."
        )
    # ru
    if detailed:
        return (
            f"Дай развёрнутое определение термина или слова: «{term}». "
            "Включи этимологию если уместно, основные значения, примеры использования "
            "и связанные понятия. Отвечай на русском."
        )
    return (
        f"Дай краткое определение термина или слова: «{term}» в 2-3 предложениях. "
        "Будь точен и лаконичен. Отвечай на русском."
    )


# !len / !count — extracted to commands/text_utils.py (Phase 2, Session 27)
# Re-exported below: handle_len


async def handle_define(bot: "KraabUserbot", message: Message) -> None:
    """
    !define <слово> [en] [подробно] — определение слова/термина через AI.

    Варианты:
      !define Python           — краткое определение на русском
      !define Python en        — краткое определение на английском
      !define Python подробно  — развёрнутое определение на русском
    """
    raw_args = bot._get_command_args(message).strip()

    # Если аргументов нет — пробуем взять текст из reply
    if not raw_args and message.reply_to_message:
        raw_args = (message.reply_to_message.text or "").strip()

    if not raw_args:
        raise UserInputError(
            user_message=(
                "📖 **!define — определение слова/термина**\n\n"
                "`!define <слово>` — краткое определение (рус.)\n"
                "`!define <слово> en` — краткое определение (англ.)\n"
                "`!define <слово> подробно` — развёрнутое определение\n\n"
                "_Пример: `!define энтропия` или `!define recursion en`_"
            )
        )

    term, lang, detailed = _parse_define_args(raw_args)

    if not term:
        raise UserInputError(user_message="❓ Укажи слово или термин: `!define <слово>`")

    # Формируем визуальный маркер режима
    lang_label = " (EN)" if lang == "en" else ""
    status_msg = await message.reply(f"📖 Определяю «{term}»{lang_label}...")

    prompt = _build_define_prompt(term, lang, detailed)
    # Изолированная сессия — не смешивается с контекстом основного чата
    session_id = f"define_{message.chat.id}"
    max_tokens = 800 if detailed else 350

    try:
        chunks: list[str] = []
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            disable_tools=True,
            max_output_tokens=max_tokens,
        ):
            chunks.append(chunk)

        definition = "".join(chunks).strip()
        if not definition:
            raise ValueError("пустой ответ от модели")

        # Форматируем ответ
        header = f"📖 **{term}**{lang_label}"
        if detailed:
            header += " _(подробно)_"
        response_text = f"{header}\n\n{definition}"

        # Ограничиваем длину для Telegram
        if len(response_text) > 4000:
            response_text = response_text[:3950] + "..."

        await status_msg.edit(response_text)

    except Exception as exc:  # noqa: BLE001
        logger.warning("handle_define_failed", term=term, error=str(exc))
        await status_msg.edit(f"❌ Не удалось получить определение «{term}»: {exc}")


# ---------------------------------------------------------------------------
# !currency — конвертер валют (open.er-api.com, без ключа)
# ---------------------------------------------------------------------------

# Дефолтная целевая валюта — переопределяется через CURRENCY_DEFAULT_TARGET
_CURRENCY_DEFAULT_TARGET: str = os.getenv("CURRENCY_DEFAULT_TARGET", "EUR").upper()

# URL шаблон: base-валюта подставляется при запросе
_CURRENCY_API_URL = "https://open.er-api.com/v6/latest/{base}"

# Тайм-аут запроса к API (секунды)
_CURRENCY_HTTP_TIMEOUT = 10.0


def _parse_currency_args(raw: str) -> tuple[float, str, str | None]:
    """
    Разбирает аргументы команды !currency.

    Форматы:
      !currency 100 USD EUR    → (100.0, "USD", "EUR")
      !currency 100 usd        → (100.0, "USD", None)  — дефолтная цель
      !currency 100.50 GBP EUR → (100.5, "GBP", "EUR")

    Raises:
        UserInputError: при неправильном формате.
    """
    parts = raw.strip().split()
    if len(parts) < 2 or len(parts) > 3:
        raise UserInputError(
            user_message=(
                "💱 **Конвертер валют**\n\n"
                "Использование:\n"
                "`!currency <сумма> <FROM> [TO]`\n\n"
                "Примеры:\n"
                "`!currency 100 USD EUR` → 100 USD в EUR\n"
                f"`!currency 100 USD` → 100 USD в {_CURRENCY_DEFAULT_TARGET} (дефолт)\n\n"
                "_Курсы: open.er-api.com (обновляются ежечасно)_"
            )
        )
    try:
        amount = float(parts[0].replace(",", "."))
    except ValueError:
        raise UserInputError(user_message=f"❌ Неверная сумма: `{parts[0]}`")

    if amount < 0:
        raise UserInputError(user_message="❌ Сумма не может быть отрицательной.")

    from_currency = parts[1].upper()
    to_currency = parts[2].upper() if len(parts) == 3 else None
    return amount, from_currency, to_currency


async def fetch_exchange_rate(from_currency: str, to_currency: str) -> float:
    """
    Получает курс from_currency → to_currency через open.er-api.com.

    Returns:
        Курс (float) — сколько единиц to_currency за 1 from_currency.

    Raises:
        UserInputError: при неверном коде валюты или недоступности API.
    """
    url = _CURRENCY_API_URL.format(base=from_currency)
    async with httpx.AsyncClient(timeout=_CURRENCY_HTTP_TIMEOUT) as client:
        try:
            resp = await client.get(url)
        except httpx.TimeoutException:
            raise UserInputError(user_message="❌ API курсов валют не отвечает (таймаут).")
        except httpx.RequestError as exc:
            raise UserInputError(user_message=f"❌ Ошибка соединения с API: {exc}")

    if resp.status_code != 200:
        raise UserInputError(
            user_message=f"❌ API вернул статус {resp.status_code}. Попробуй позже."
        )

    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        raise UserInputError(user_message="❌ Не удалось разобрать ответ API.")

    # Проверяем статус ответа
    if data.get("result") != "success":
        error_type = data.get("error-type", "unknown")
        if error_type == "unsupported-code":
            raise UserInputError(
                user_message=f"❌ Неизвестная валюта: `{from_currency}`. Проверь код (ISO 4217)."
            )
        raise UserInputError(user_message=f"❌ API ошибка: `{error_type}`")

    rates = data.get("rates", {})
    if to_currency not in rates:
        raise UserInputError(
            user_message=f"❌ Неизвестная целевая валюта: `{to_currency}`. Проверь код (ISO 4217)."
        )

    return float(rates[to_currency])


async def handle_currency(bot: "KraabUserbot", message: Message) -> None:
    """
    !currency <сумма> <FROM> [TO] — конвертер валют.

    Примеры:
      !currency 100 USD EUR     → 💱 100 USD = 92.35 EUR (курс: 0.9235)
      !currency 100 usd         → 💱 100 USD = 92.35 EUR (курс: 0.9235)
      !currency 1500 RUB EUR    → 💱 1500 RUB = 15.20 EUR (курс: 0.0101)
    """
    raw_args = bot._get_command_args(message).strip()
    if not raw_args:
        raise UserInputError(
            user_message=(
                "💱 **Конвертер валют**\n\n"
                "Использование:\n"
                "`!currency <сумма> <FROM> [TO]`\n\n"
                "Примеры:\n"
                "`!currency 100 USD EUR` → 100 USD в EUR\n"
                f"`!currency 100 USD` → 100 USD в {_CURRENCY_DEFAULT_TARGET} (дефолт)\n\n"
                "_Курсы: open.er-api.com (обновляются ежечасно)_"
            )
        )

    amount, from_currency, to_currency_raw = _parse_currency_args(raw_args)
    to_currency = to_currency_raw or _CURRENCY_DEFAULT_TARGET

    # Тривиальный случай: одна и та же валюта
    if from_currency == to_currency:
        formatted_amount = _fmt_currency(amount)
        await message.reply(
            f"💱 {formatted_amount} {from_currency} = {formatted_amount} {to_currency} (курс: 1)"
        )
        return

    rate = await fetch_exchange_rate(from_currency, to_currency)
    converted = amount * rate

    amount_str = _fmt_currency(amount)
    converted_str = _fmt_currency(converted)
    rate_str = _fmt_currency(rate)

    await message.reply(
        f"💱 {amount_str} {from_currency} = {converted_str} {to_currency} (курс: {rate_str})"
    )


def _fmt_currency(val: float) -> str:
    """Форматирует число для вывода в !currency (убирает лишние нули)."""
    if val >= 1000:
        return f"{val:,.2f}"
    if val >= 0.01:
        return f"{val:.4f}".rstrip("0").rstrip(".")
    # Очень маленькие значения — больше знаков
    return f"{val:.6f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# !welcome — автоприветствие новых участников
# ---------------------------------------------------------------------------

_WELCOME_FILE = pathlib.Path.home() / ".openclaw" / "krab_runtime_state" / "welcome_messages.json"

# Переменные, доступные в шаблоне приветствия
_WELCOME_TEMPLATE_VARS = "{name}, {username}, {chat}, {count}"


def _load_welcome_config() -> dict:
    """Загружает конфиг приветствий из JSON-файла."""
    if not _WELCOME_FILE.exists():
        return {}
    try:
        return json.loads(_WELCOME_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_welcome_config(data: dict) -> None:
    """Сохраняет конфиг приветствий в JSON-файл."""
    _WELCOME_FILE.parent.mkdir(parents=True, exist_ok=True)
    _WELCOME_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _render_welcome_text(template: str, *, name: str, username: str, chat: str, count: int) -> str:
    """Подставляет переменные в шаблон приветствия."""
    return (
        template.replace("{name}", name)
        .replace("{username}", username)
        .replace("{chat}", chat)
        .replace("{count}", str(count))
    )


async def handle_welcome(bot: "KraabUserbot", message: Message) -> None:
    """
    !welcome — управление автоприветствием новых участников группы.

    Синтаксис:
      !welcome set <текст>   — установить шаблон (доступны: {name}, {username}, {chat}, {count})
      !welcome off           — выключить приветствие для этого чата
      !welcome status        — показать текущий шаблон и статус
      !welcome test          — отправить тестовое приветствие (preview)
    """
    chat_id = str(message.chat.id)
    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=2)
    sub = parts[1].strip().lower() if len(parts) >= 2 else "status"

    cfg = _load_welcome_config()

    if sub == "set":
        if len(parts) < 3 or not parts[2].strip():
            raise UserInputError(
                user_message=(
                    "❌ Укажи текст приветствия.\n\n"
                    "Пример: `!welcome set Привет, {name}! Добро пожаловать в {chat}!`\n\n"
                    f"Доступные переменные: `{_WELCOME_TEMPLATE_VARS}`"
                )
            )
        template = parts[2].strip()
        cfg[chat_id] = {"enabled": True, "template": template}
        _save_welcome_config(cfg)
        await message.reply(
            f"✅ Приветствие для этого чата установлено:\n\n_{template}_\n\n"
            f"Переменные: `{_WELCOME_TEMPLATE_VARS}`\n"
            "`!welcome test` — проверить, `!welcome off` — выключить"
        )
        return

    if sub == "off":
        if chat_id in cfg:
            cfg[chat_id]["enabled"] = False
            _save_welcome_config(cfg)
        await message.reply("🔇 Автоприветствие для этого чата **выключено**.")
        return

    if sub in ("status", "show"):
        entry = cfg.get(chat_id)
        if not entry or not entry.get("enabled"):
            await message.reply(
                "ℹ️ Автоприветствие в этом чате **не настроено** или выключено.\n"
                f"`!welcome set <текст>` — установить\n"
                f"Переменные: `{_WELCOME_TEMPLATE_VARS}`"
            )
        else:
            await message.reply(
                f"✅ Автоприветствие **включено**:\n\n_{entry['template']}_\n\n"
                "`!welcome off` — выключить | `!welcome test` — preview"
            )
        return

    if sub == "test":
        entry = cfg.get(chat_id)
        if not entry or not entry.get("enabled"):
            raise UserInputError(
                user_message="❌ Приветствие не настроено. Сначала: `!welcome set <текст>`"
            )
        # Формируем preview с данными текущего юзера
        user = message.from_user
        name = getattr(user, "first_name", None) or "Новичок"
        uname = f"@{user.username}" if getattr(user, "username", None) else name
        chat_title = getattr(message.chat, "title", None) or "этом чате"
        preview = _render_welcome_text(
            entry["template"],
            name=name,
            username=uname,
            chat=chat_title,
            count=1,
        )
        await message.reply(f"🧪 **Preview приветствия:**\n\n{preview}")
        return

    raise UserInputError(
        user_message=(
            "❌ Неизвестная подкоманда.\n\n"
            "Доступно:\n"
            "`!welcome set <текст>` — установить\n"
            "`!welcome off` — выключить\n"
            "`!welcome status` — показать\n"
            "`!welcome test` — preview"
        )
    )


async def handle_new_chat_members(bot: "KraabUserbot", message: Message) -> None:
    """Автоприветствие новых участников — вызывается на filters.new_chat_members."""
    chat_id = str(message.chat.id)
    cfg = _load_welcome_config()
    entry = cfg.get(chat_id)
    if not entry or not entry.get("enabled") or not entry.get("template"):
        return

    chat_title = getattr(message.chat, "title", None) or str(chat_id)
    new_members = getattr(message, "new_chat_members", None) or []
    count = len(new_members)

    for member in new_members:
        name = getattr(member, "first_name", None) or "Новичок"
        uname = f"@{member.username}" if getattr(member, "username", None) else name
        text = _render_welcome_text(
            entry["template"],
            name=name,
            username=uname,
            chat=chat_title,
            count=count,
        )
        try:
            await message.reply(text)
        except Exception as exc:
            logger.warning("welcome_send_failed", chat_id=chat_id, error=str(exc))


# !sed, !diff — extracted to commands/text_utils.py (Phase 2, Session 27)
# Re-exported below: handle_sed, handle_diff, _parse_sed_expr, _build_diff_output


# ---------------------------------------------------------------------------
# Управление стикерами (!sticker)
# ---------------------------------------------------------------------------

# !sticker (+ helpers _STICKERS_FILE/_load_stickers/_save_stickers) —
# extracted to commands/social_commands.py (Phase 2 Wave 6, Session 27).
# Re-exported above (handle_sticker, _STICKERS_FILE, _load_stickers, _save_stickers).


# ---------------------------------------------------------------------------
# !tts — extracted to commands/voice_commands.py (Phase 2 Wave 4, Session 27).
# State (_TTS_VOICES, _TTS_LANG_ALIASES) и handle_tts re-exported сверху.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AFK-режим (!afk / !back)
# ---------------------------------------------------------------------------


async def handle_afk(bot: "KraabUserbot", message: Message) -> None:
    """!afk [причина] / !afk off / !afk status / !back — режим отсутствия.

    Синтаксис:
      !afk               — включить AFK (без причины)
      !afk <причина>     — включить AFK с причиной
      !afk off           — выключить AFK
      !back              — выключить AFK (алиас)
      !afk status        — показать текущий статус
    """
    import time as _time  # noqa: PLC0415

    raw = (message.text or "").strip()
    # Парсим аргументы: убираем команду (!afk / !back)
    parts = raw.split(maxsplit=1)
    cmd_word = parts[0].lstrip("!/. ").lower()
    args = parts[1].strip() if len(parts) > 1 else ""

    # !back — всегда выключение
    if cmd_word == "back":
        if not bot._afk_mode:
            await message.reply("ℹ️ AFK-режим и так не активен.")
            return
        elapsed = int(_time.time() - bot._afk_since)
        mins = elapsed // 60
        secs = elapsed % 60
        time_str = f"{mins} мин {secs} с" if mins else f"{secs} с"
        bot._afk_mode = False
        bot._afk_reason = ""
        bot._afk_since = 0.0
        bot._afk_replied_chats.clear()
        await message.reply(f"👋 Добро пожаловать обратно! Отсутствовал: **{time_str}**")
        return

    # !afk off / !afk стоп
    if args.lower() in ("off", "стоп", "выкл", "выключить"):
        if not bot._afk_mode:
            await message.reply("ℹ️ AFK-режим и так не активен.")
            return
        elapsed = int(_time.time() - bot._afk_since)
        mins = elapsed // 60
        secs = elapsed % 60
        time_str = f"{mins} мин {secs} с" if mins else f"{secs} с"
        bot._afk_mode = False
        bot._afk_reason = ""
        bot._afk_since = 0.0
        bot._afk_replied_chats.clear()
        await message.reply(f"👋 AFK выключен. Отсутствовал: **{time_str}**")
        return

    # !afk status / !afk статус
    if args.lower() in ("status", "статус", "stat"):
        if not bot._afk_mode:
            await message.reply("ℹ️ AFK-режим не активен.")
        else:
            elapsed = int(_time.time() - bot._afk_since)
            mins = elapsed // 60
            secs = elapsed % 60
            time_str = f"{mins} мин {secs} с" if mins else f"{secs} с"
            reason_part = f"\n📝 Причина: {bot._afk_reason}" if bot._afk_reason else ""
            replied_count = len(bot._afk_replied_chats)
            await message.reply(
                f"🌙 **AFK активен** — отсутствую уже **{time_str}**{reason_part}\n"
                f"Автоответ отправлен в {replied_count} чат(ах)."
            )
        return

    # !afk [причина] — включить (или обновить причину если уже активен)
    if bot._afk_mode:
        bot._afk_reason = args
        reason_part = f"\n📝 Причина обновлена: {args}" if args else " Причина сброшена."
        await message.reply(f"🌙 AFK уже активен.{reason_part}")
        return

    bot._afk_mode = True
    bot._afk_reason = args
    bot._afk_since = _time.time()
    bot._afk_replied_chats.clear()
    reason_part = f"\n📝 Причина: {args}" if args else ""
    await message.reply(
        f"🌙 AFK-режим включён.{reason_part}\n"
        f"Входящие DM получат автоответ.\n"
        f"`!afk off` или `!back` — вернуться."
    )


# ---------------------------------------------------------------------------
# !img — описание фото через AI vision
# ---------------------------------------------------------------------------


async def handle_img(bot: "KraabUserbot", message: Message) -> None:
    """
    Описывает фото через AI vision (multimodal).

    Использование:
      !img                — reply на фото → краткое описание
      !img <вопрос>       — reply на фото → ответ на вопрос о фото

    Требуется reply на сообщение с фото или документом-изображением.
    Сессия изолирована: img_{chat_id} (не засоряет основной контекст).
    Всегда force_cloud=True — vision требует облачной модели.
    """
    import base64
    import io

    question = bot._get_command_args(message).strip()

    # Проверяем, что есть reply на сообщение
    replied = message.reply_to_message
    if replied is None:
        raise UserInputError(
            user_message=(
                "🖼 **!img** — описание фото через AI vision\n\n"
                "Ответь на сообщение с фото:\n"
                "`!img` — описание\n"
                "`!img <вопрос>` — ответ на вопрос о фото"
            )
        )

    # Определяем наличие фото в сообщении
    has_photo = bool(replied.photo)
    # Документ-изображение (jpg/png/webp отправленные без сжатия)
    has_doc_image = bool(
        replied.document
        and replied.document.mime_type
        and replied.document.mime_type.startswith("image/")
    )

    if not has_photo and not has_doc_image:
        raise UserInputError(
            user_message=(
                "🖼 Это сообщение не содержит фото. Ответь командой на сообщение с фотографией."
            )
        )

    # Статусное сообщение
    status_msg = await message.reply("🔍 Анализирую фото...")

    try:
        # Скачиваем фото в память
        img_bytes_io = io.BytesIO()
        await replied.download(in_memory=img_bytes_io)
        img_bytes_io.seek(0)
        img_bytes = img_bytes_io.read()

        if not img_bytes:
            await status_msg.edit("❌ Не удалось скачать фото.")
            return

        # Base64-кодирование для передачи в API
        img_b64 = base64.b64encode(img_bytes).decode("ascii")

        # Формируем промпт: вопрос пользователя или дефолтное описание
        if question:
            prompt = question
        else:
            prompt = (
                "Опиши это фото подробно. "
                "Что на нём изображено? Текст, объекты, люди, место — всё что видишь."
            )

        # Изолированная сессия, чтобы vision-контент не загрязнял основной диалог чата
        session_id = f"img_{message.chat.id}"

        chunks: list[str] = []
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            images=[img_b64],
            force_cloud=True,
            disable_tools=True,
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()

        if not result:
            await status_msg.edit("❌ AI не смог проанализировать фото.")
            return

        # Разбиваем длинный ответ если нужно
        parts = _split_text_for_telegram(result)
        await status_msg.edit(parts[0])
        for part in parts[1:]:
            await message.reply(part)

    except UserInputError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("handle_img_error", error=str(exc))
        await status_msg.edit(f"❌ Ошибка анализа фото: {exc}")


# ---------------------------------------------------------------------------
# !ocr — извлечение текста из изображения через AI vision
# ---------------------------------------------------------------------------


async def handle_ocr(bot: "KraabUserbot", message: Message) -> None:
    """
    Извлекает текст из изображения через AI vision (OCR).

    Использование:
      !ocr                — reply на фото → дословный текст с изображения
      !ocr <подсказка>    — reply на фото → OCR с доп. контекстом

    Требуется reply на сообщение с фото или документом-изображением.
    Сессия изолирована: ocr_{chat_id} (не засоряет основной контекст).
    Всегда force_cloud=True — vision требует облачной модели.
    """
    import base64
    import io

    hint = bot._get_command_args(message).strip()

    # Проверяем наличие reply на сообщение с фото
    replied = message.reply_to_message
    if replied is None:
        raise UserInputError(
            user_message=(
                "📄 **!ocr** — извлечение текста из изображения\n\n"
                "Ответь командой на сообщение с фото:\n"
                "`!ocr` — извлечь весь текст\n"
                "`!ocr <подсказка>` — OCR с дополнительным контекстом"
            )
        )

    # Определяем тип медиа в сообщении
    has_photo = bool(replied.photo)
    has_doc_image = bool(
        replied.document
        and replied.document.mime_type
        and replied.document.mime_type.startswith("image/")
    )

    if not has_photo and not has_doc_image:
        raise UserInputError(
            user_message=(
                "📄 Это сообщение не содержит фото. Ответь командой на сообщение с изображением."
            )
        )

    # Статусное сообщение
    status_msg = await message.reply("🔍 Извлекаю текст...")

    try:
        # Скачиваем изображение в память
        img_bytes_io = io.BytesIO()
        await replied.download(in_memory=img_bytes_io)
        img_bytes_io.seek(0)
        img_bytes = img_bytes_io.read()

        if not img_bytes:
            await status_msg.edit("❌ Не удалось скачать изображение.")
            return

        # Base64-кодирование для передачи в API
        img_b64 = base64.b64encode(img_bytes).decode("ascii")

        # Формируем OCR-промпт
        if hint:
            prompt = (
                f"Извлеки весь текст с этого изображения дословно. "
                f"Дополнительный контекст: {hint}. "
                f"Верни только сам текст без пояснений."
            )
        else:
            prompt = (
                "Извлеки весь текст с этого изображения дословно. "
                "Сохрани оригинальное форматирование (абзацы, списки, таблицы). "
                "Верни только текст без пояснений и комментариев."
            )

        # Изолированная OCR-сессия, чтобы не засорять основной диалог
        session_id = f"ocr_{message.chat.id}"

        chunks: list[str] = []
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            images=[img_b64],
            force_cloud=True,
            disable_tools=True,
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()

        if not result:
            await status_msg.edit("❌ Текст на изображении не найден.")
            return

        # Разбиваем длинный результат если нужно
        parts = _split_text_for_telegram(result)
        await status_msg.edit(f"📄 **OCR:**\n{parts[0]}")
        for part in parts[1:]:
            await message.reply(part)

    except UserInputError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("handle_ocr_error", error=str(exc))
        await status_msg.edit(f"❌ Ошибка OCR: {exc}")


# ---------------------------------------------------------------------------
# !media — скачивание медиафайлов (фото/видео/документ)
# ---------------------------------------------------------------------------


async def handle_media(bot: "KraabUserbot", message: Message) -> None:
    """
    Скачивает медиафайлы из Telegram (userbot-only).

    Использование (в reply на фото/видео/документ):
      !media           — скачать и переслать как файл (документ)
      !media save      — скачать в ~/Downloads/krab_media/
      !media info      — показать метаданные (размер, тип, разрешение)

    Поддерживаемые типы: фото, видео, документ, аудио, голосовое, стикер.
    """
    import mimetypes
    import tempfile

    args = bot._get_command_args(message).strip().lower()
    subcommand = args.split()[0] if args else ""

    replied = message.reply_to_message
    if replied is None:
        raise UserInputError(
            user_message=(
                "📥 **!media** — скачивание медиафайлов\n\n"
                "Ответь на сообщение с медиа:\n"
                "`!media` — скачать и переслать как файл\n"
                "`!media save` — сохранить в ~/Downloads/krab_media/\n"
                "`!media info` — метаданные файла"
            )
        )

    # Определяем тип медиа и метаданные
    media_type = None
    file_name = None
    file_size = None
    mime_type = None
    width = height = duration = None

    if replied.photo:
        media_type = "photo"
        mime_type = "image/jpeg"
        width = replied.photo.width
        height = replied.photo.height
        file_size = replied.photo.file_size
        file_name = f"photo_{replied.photo.file_unique_id}.jpg"

    elif replied.video:
        media_type = "video"
        mime_type = replied.video.mime_type or "video/mp4"
        width = replied.video.width
        height = replied.video.height
        duration = replied.video.duration
        file_size = replied.video.file_size
        ext = mimetypes.guess_extension(mime_type) or ".mp4"
        file_name = replied.video.file_name or f"video_{replied.video.file_unique_id}{ext}"

    elif replied.document:
        media_type = "document"
        mime_type = replied.document.mime_type or "application/octet-stream"
        file_size = replied.document.file_size
        file_name = replied.document.file_name or f"doc_{replied.document.file_unique_id}"

    elif replied.audio:
        media_type = "audio"
        mime_type = replied.audio.mime_type or "audio/mpeg"
        duration = replied.audio.duration
        file_size = replied.audio.file_size
        ext = mimetypes.guess_extension(mime_type) or ".mp3"
        file_name = replied.audio.file_name or f"audio_{replied.audio.file_unique_id}{ext}"

    elif replied.voice:
        media_type = "voice"
        mime_type = replied.voice.mime_type or "audio/ogg"
        duration = replied.voice.duration
        file_size = replied.voice.file_size
        ext = mimetypes.guess_extension(mime_type) or ".ogg"
        file_name = f"voice_{replied.voice.file_unique_id}{ext}"

    elif replied.sticker:
        media_type = "sticker"
        mime_type = replied.sticker.mime_type or "image/webp"
        width = replied.sticker.width
        height = replied.sticker.height
        file_size = replied.sticker.file_size
        ext = ".tgs" if getattr(replied.sticker, "is_animated", False) else ".webp"
        file_name = f"sticker_{replied.sticker.file_unique_id}{ext}"

    else:
        raise UserInputError(
            user_message=(
                "📥 Это сообщение не содержит медиафайл.\n"
                "Ответь командой на фото, видео, документ, аудио, голосовое или стикер."
            )
        )

    # --- !media info: только метаданные, без скачивания ---
    if subcommand == "info":
        lines = [f"📋 **Метаданные медиафайла** (`{media_type}`)"]
        lines.append(f"• Имя: `{file_name}`")
        if mime_type:
            lines.append(f"• MIME: `{mime_type}`")
        if file_size:
            size_kb = file_size / 1024
            if size_kb >= 1024:
                lines.append(f"• Размер: `{size_kb / 1024:.1f} МБ`")
            else:
                lines.append(f"• Размер: `{size_kb:.1f} КБ`")
        if width and height:
            lines.append(f"• Разрешение: `{width}×{height}`")
        if duration is not None:
            lines.append(f"• Длительность: `{duration} сек`")
        await message.reply("\n".join(lines))
        return

    # --- !media save: скачать в ~/Downloads/krab_media/ ---
    if subcommand == "save":
        save_dir = pathlib.Path.home() / "Downloads" / "krab_media"
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / (file_name or "media_file")

        status_msg = await message.reply(f"⬇️ Сохраняю `{file_name}`...")
        try:
            await replied.download(file_name=str(save_path))
            size_str = ""
            if save_path.exists():
                sz = save_path.stat().st_size / 1024
                size_str = f" ({sz / 1024:.1f} МБ)" if sz >= 1024 else f" ({sz:.1f} КБ)"
            await status_msg.edit(f"✅ Сохранено: `{save_path}`{size_str}")
        except Exception as exc:  # noqa: BLE001
            logger.error("handle_media_save_error", file_name=file_name, error=str(exc))
            await status_msg.edit(f"❌ Ошибка сохранения: {exc}")
        return

    # --- !media (по умолчанию): скачать и переслать как документ ---
    status_msg = await message.reply(f"⬇️ Скачиваю `{file_name}`...")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = pathlib.Path(tmpdir) / (file_name or "media_file")
            await replied.download(file_name=str(tmp_path))

            if not tmp_path.exists() or tmp_path.stat().st_size == 0:
                await status_msg.edit("❌ Не удалось скачать файл (пустой или недоступен).")
                return

            sz = tmp_path.stat().st_size / 1024
            size_str = f"{sz / 1024:.1f} МБ" if sz >= 1024 else f"{sz:.1f} КБ"
            caption = f"📥 `{file_name}` · {size_str}"

            await bot.client.send_document(
                message.chat.id,
                str(tmp_path),
                caption=caption,
                reply_to_message_id=message.id,
            )
            # Статусное сообщение удаляем: документ уже отправлен
            try:
                await status_msg.delete()
            except Exception:  # noqa: BLE001
                pass

    except Exception as exc:  # noqa: BLE001
        logger.error("handle_media_error", file_name=file_name, error=str(exc))
        try:
            await status_msg.edit(f"❌ Ошибка скачивания: {exc}")
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Антиспам фильтр для групп (!spam)
# ---------------------------------------------------------------------------


async def handle_spam(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление антиспам фильтром в группе.

    Subcommands:
      !spam on            — включить в текущем чате
      !spam off           — выключить в текущем чате
      !spam status        — показать настройки
      !spam action ban    — банить нарушителей
      !spam action mute   — ограничивать (restrict) нарушителей
      !spam action delete — только удалять сообщения (default)

    Owner-only.
    """
    from ..core.spam_guard import (  # noqa: PLC0415
        VALID_ACTIONS,
        get_status,
        set_action,
        set_enabled,
    )

    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!spam` доступен только владельцу.")

    chat_id = message.chat.id
    args = (message.text or "").split()
    sub = args[1].strip().lower() if len(args) >= 2 else "status"

    # --- !spam on ---
    if sub == "on":
        set_enabled(chat_id, True)
        status = get_status(chat_id)
        await message.reply(
            f"✅ Антиспам **включён** в чате `{chat_id}`.\n"
            f"Действие при детекте: `{status['action']}`"
        )
        return

    # --- !spam off ---
    if sub == "off":
        set_enabled(chat_id, False)
        await message.reply(f"🔕 Антиспам **выключен** в чате `{chat_id}`.")
        return

    # --- !spam status ---
    if sub in {"status", "show", ""}:
        status = get_status(chat_id)
        state_icon = "✅" if status["enabled"] else "❌"
        await message.reply(
            f"🛡 **Антиспам** — `{chat_id}`\n\n"
            f"Статус: {state_icon} {'включён' if status['enabled'] else 'выключен'}\n"
            f"Действие: `{status['action']}`\n\n"
            f"Детект срабатывает при:\n"
            f"• flood: >5 сообщений за 10 сек\n"
            f"• >3 ссылок в одном сообщении\n"
            f"• пересланное сообщение со ссылками"
        )
        return

    # --- !spam action <ban|mute|delete> ---
    if sub == "action":
        action = args[2].strip().lower() if len(args) >= 3 else ""
        if action not in VALID_ACTIONS:
            raise UserInputError(
                user_message=(
                    f"❌ Неизвестное действие: `{action}`.\nДоступны: `ban`, `mute`, `delete`"
                )
            )
        set_action(chat_id, action)
        await message.reply(f"⚙️ Действие при спаме установлено: `{action}`")
        return

    raise UserInputError(
        user_message=(
            "🛡 **!spam — антиспам фильтр**\n\n"
            "`!spam on` — включить\n"
            "`!spam off` — выключить\n"
            "`!spam status` — текущие настройки\n"
            "`!spam action ban|mute|delete` — действие при детекте"
        )
    )


# ---------------------------------------------------------------------------
# !eval — безопасный eval Python-выражений через AST (без statements)
# ---------------------------------------------------------------------------

# Типы AST-узлов, разрешённые в !eval
_EVAL_ALLOWED_NODES = (
    _ast.Expression,
    _ast.Constant,
    _ast.BinOp,
    _ast.UnaryOp,
    _ast.BoolOp,
    _ast.Compare,
    _ast.IfExp,
    _ast.Call,
    _ast.Name,
    _ast.Attribute,
    _ast.Subscript,
    _ast.Slice,
    _ast.List,
    _ast.Tuple,
    _ast.Dict,
    _ast.Set,
    _ast.ListComp,
    _ast.SetComp,
    _ast.DictComp,
    _ast.GeneratorExp,
    _ast.comprehension,
    _ast.Add,
    _ast.Sub,
    _ast.Mult,
    _ast.Div,
    _ast.FloorDiv,
    _ast.Mod,
    _ast.Pow,
    _ast.BitAnd,
    _ast.BitOr,
    _ast.BitXor,
    _ast.LShift,
    _ast.RShift,
    _ast.Invert,
    _ast.Not,
    _ast.UAdd,
    _ast.USub,
    _ast.And,
    _ast.Or,
    _ast.Eq,
    _ast.NotEq,
    _ast.Lt,
    _ast.LtE,
    _ast.Gt,
    _ast.GtE,
    _ast.Is,
    _ast.IsNot,
    _ast.In,
    _ast.NotIn,
    _ast.Load,
    _ast.Store,
    _ast.Del,
)

# Запрещённые имена в !eval
_EVAL_FORBIDDEN_NAMES = frozenset(
    {
        "import",
        "exec",
        "eval",
        "open",
        "__builtins__",
        "__import__",
        "__loader__",
        "__spec__",
        "__build_class__",
        "compile",
        "globals",
        "locals",
        "vars",
        "dir",
        "delattr",
        "setattr",
        "getattr",
        "breakpoint",
        "input",
        "print",
    }
)

# Безопасное пространство имён для !eval
_EVAL_NAMESPACE: dict[str, object] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "len": len,
    "sorted": sorted,
    "reversed": reversed,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "list": list,
    "tuple": tuple,
    "set": set,
    "dict": dict,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "complex": complex,
    "bytes": bytes,
    "bytearray": bytearray,
    "range": range,
    "type": type,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "repr": repr,
    "hash": hash,
    "hex": hex,
    "oct": oct,
    "bin": bin,
    "ord": ord,
    "chr": chr,
    "divmod": divmod,
    "pow": pow,
    "all": all,
    "any": any,
    # math-функции
    "sqrt": _math.sqrt,
    "sin": _math.sin,
    "cos": _math.cos,
    "tan": _math.tan,
    "log": _math.log,
    "log2": _math.log2,
    "log10": _math.log10,
    "ceil": _math.ceil,
    "floor": _math.floor,
    "trunc": _math.trunc,
    # константы
    "pi": _math.pi,
    "e": _math.e,
    "inf": _math.inf,
    "nan": _math.nan,
    "tau": _math.tau,
    "True": True,
    "False": False,
    "None": None,
}


def _eval_check_node(node: _ast.AST) -> None:
    """Рекурсивно проверяет AST-узел на допустимость для !eval."""
    if not isinstance(node, _EVAL_ALLOWED_NODES):
        raise UserInputError(
            user_message=f"\u274c Недопустимая конструкция: `{type(node).__name__}`"
        )
    # Запрещаем __dunder__ атрибуты
    if isinstance(node, _ast.Attribute):
        if node.attr.startswith("__"):
            raise UserInputError(user_message=f"\u274c Доступ к `{node.attr}` запрещён.")
    # Запрещённые имена и dunder-переменные
    if isinstance(node, _ast.Name):
        if node.id in _EVAL_FORBIDDEN_NAMES or node.id.startswith("__"):
            raise UserInputError(user_message=f"\u274c Имя `{node.id}` запрещено.")
    for child in _ast.iter_child_nodes(node):
        _eval_check_node(child)


def safe_eval(expression: str) -> object:
    """
    Безопасно вычисляет Python-выражение через AST + ограниченный namespace.

    Поддерживает: literals, арифметику, списки, строки, bool, comprehensions.
    Не поддерживает: statements (import/def/class/print/exec/eval/open).
    Timeout — на уровне handle_eval (asyncio, 2 сек).
    """
    expression = expression.strip()
    if not expression:
        raise UserInputError(user_message="\u274c Пустое выражение.")
    if len(expression) > 500:
        raise UserInputError(user_message="\u274c Выражение слишком длинное (макс. 500 символов).")

    # Парсим как expression (не statement)
    try:
        tree = compile(expression, "<eval>", "eval", _ast.PyCF_ONLY_AST)
    except SyntaxError as exc:
        raise UserInputError(user_message=f"\u274c Синтаксическая ошибка: {exc.msg}")

    # Проверяем безопасность всех AST-узлов
    _eval_check_node(tree)

    # Вычисляем через eval с пустыми builtins + whitelisted namespace
    try:
        result = eval(  # noqa: S307
            compile(tree, "<eval>", "eval"),
            {"__builtins__": {}},
            _EVAL_NAMESPACE,
        )
    except ZeroDivisionError:
        raise UserInputError(user_message="\u274c Деление на ноль.")
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise UserInputError(user_message=f"\u274c Ошибка вычисления: {exc}")
    except MemoryError:
        raise UserInputError(user_message="\u274c Результат слишком большой (MemoryError).")
    except Exception as exc:  # noqa: BLE001
        raise UserInputError(user_message=f"\u274c Ошибка: {exc}")

    return result


async def handle_eval(bot: "KraabUserbot", message: Message) -> None:
    """
    !eval <выражение> — безопасный eval Python-выражений.

    Отличие от !calc: поддерживает любые Python-expressions (строки, списки, bool).
    Отличие от !run: только expressions, без statements (import/def/class запрещены).

    Примеры:
      !eval 2**100                     → большое число
      !eval len("hello")               → 5
      !eval sorted([3,1,2])            → [1, 2, 3]
      !eval [x**2 for x in range(5)]  → [0, 1, 4, 9, 16]

    Timeout: 2 секунды.
    """
    expr = bot._get_command_args(message).strip()
    if not expr:
        raise UserInputError(
            user_message=(
                "\U0001f40d **!eval — Python expressions**\n\n"
                "Использование: `!eval <выражение>`\n\n"
                "Примеры:\n"
                "`!eval 2**100` → большое число\n"
                '`!eval len("hello")` → `5`\n'
                "`!eval sorted([3,1,2])` → `[1, 2, 3]`\n"
                "`!eval [x**2 for x in range(5)]` → `[0, 1, 4, 9, 16]`\n\n"
                "Только expressions. Statements (import, def, class) запрещены.\n"
                "Timeout: 2 секунды."
            )
        )

    # Выполняем с таймаутом 2 секунды через executor (блокирующая операция)
    loop = asyncio.get_event_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, safe_eval, expr),
            timeout=2.0,
        )
    except asyncio.TimeoutError:
        await message.reply("\u23f1 Timeout: вычисление прервано (>2 сек).")
        return

    # Форматируем результат
    result_repr = repr(result)
    # Обрезаем слишком длинные результаты
    if len(result_repr) > 3000:
        result_repr = result_repr[:3000] + "\u2026"

    await message.reply(f"= {result_repr}")


# ---------------------------------------------------------------------------
# !run — выполнение Python-выражений (owner-only, subprocess-изолированно)
# ---------------------------------------------------------------------------


async def handle_run(bot: "KraabUserbot", message: Message) -> None:
    """
    !run <код>  — выполнить Python-выражение или блок кода.

    Варианты использования:
      !run print("hello")   → stdout в ответ
      !run 2**100           → результат выражения
      !run (в reply)        → выполнить код из ответного сообщения

    Ограничения:
      - Только владелец (owner-only)
      - Timeout: 5 секунд
      - Выполняется в subprocess (изолированно от основного процесса)
    """
    from ..core.subprocess_env import clean_subprocess_env  # noqa: PLC0415

    # Проверка: только владелец
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!run` доступен только владельцу.")

    # Получаем код: из аргументов или из reply-сообщения
    code = bot._get_command_args(message).strip()
    if not code and message.reply_to_message:
        code = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()

    if not code:
        raise UserInputError(
            user_message=(
                "🐍 **!run — выполнение Python**\n\n"
                "Использование:\n"
                "`!run print('hello')` — выполнить код\n"
                "`!run 2**100` — вычислить выражение\n"
                "`!run` (в reply) — выполнить код из ответного сообщения\n\n"
                "Timeout: 5 секунд."
            )
        )

    # Оборачиваем одиночное выражение в print() если это не statement
    # Определяем: если код парсится как expression — оборачиваем
    exec_code = code
    try:
        _ast.parse(code, mode="eval")
        # Это выражение — оборачиваем в print для вывода результата
        exec_code = f"__r = {code}\nif __r is not None: print(__r)"
    except SyntaxError:
        # Это statement (def, print(...), if ... и т.д.) — выполняем как есть
        exec_code = code

    # Запускаем в subprocess с timeout
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            exec_code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=clean_subprocess_env(),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        await message.reply("⏱ Timeout: выполнение прервано (>5 сек).")
        return

    # Формируем ответ
    out = stdout.decode("utf-8", errors="replace").rstrip()
    err = stderr.decode("utf-8", errors="replace").rstrip()

    parts: list[str] = []
    if out:
        parts.append(f"```\n{out}\n```")
    if err:
        parts.append(f"⚠️ stderr:\n```\n{err}\n```")
    if not parts:
        rc = proc.returncode
        parts.append(f"✅ Код выполнен (exit {rc}, без вывода).")

    await message.reply("\n".join(parts))


async def apply_spam_action(
    bot: "KraabUserbot",
    message: Message,
    reason: str,
) -> None:
    """
    Применяет действие антиспама к отправителю.
    Вызывается из _process_message при детекте спама.
    """
    import time as _time  # noqa: PLC0415

    from ..core.spam_guard import get_action  # noqa: PLC0415

    chat_id = message.chat.id
    user_id = getattr(message.from_user, "id", None) if message.from_user else None
    action = get_action(chat_id)

    _reason_labels = {
        "flood": "флуд (>5 сообщений за 10 сек)",
        "links": "слишком много ссылок",
        "fwd_links": "пересланное со ссылками",
    }
    reason_text = _reason_labels.get(reason, reason)

    logger.info(
        "spam_detected",
        chat_id=str(chat_id),
        user_id=str(user_id),
        reason=reason,
        action=action,
    )

    # Удаляем сообщение (всегда, при любом действии)
    try:
        await message.delete()
    except Exception:  # noqa: BLE001
        pass

    if action == "ban" and user_id:
        try:
            await bot.client.ban_chat_member(chat_id, user_id)
            await bot.client.send_message(
                chat_id,
                f"🚫 Пользователь заблокирован за спам ({reason_text}).",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("spam_ban_failed", error=str(exc))

    elif action == "mute" and user_id:
        from pyrogram.types import ChatPermissions  # noqa: PLC0415

        try:
            # restrict на 1 час
            until = int(_time.time()) + 3600
            await bot.client.restrict_chat_member(
                chat_id,
                user_id,
                ChatPermissions(),  # все права отозваны
                until_date=until,
            )
            await bot.client.send_message(
                chat_id,
                f"🔇 Пользователь ограничен на 1 час за спам ({reason_text}).",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("spam_mute_failed", error=str(exc))
    # action == "delete": сообщение уже удалено выше


# !json — extracted to commands/text_utils.py (Phase 2, Session 27)
# Re-exported below: handle_json, _json_extract_text


# ---------------------------------------------------------------------------
# !snippet — хранилище кодовых сниппетов
# ---------------------------------------------------------------------------

_SNIPPETS_FILE = pathlib.Path.home() / ".openclaw" / "krab_runtime_state" / "code_snippets.json"


def _load_snippets() -> dict[str, dict]:
    """Загружает словарь {name: {code, created_at}} из JSON-файла."""
    try:
        if _SNIPPETS_FILE.exists():
            return json.loads(_SNIPPETS_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_snippets(data: dict[str, dict]) -> None:
    """Сохраняет сниппеты в JSON-файл."""
    _SNIPPETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SNIPPETS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def handle_snippet(bot: "KraabUserbot", message: Message) -> None:
    """
    !snippet save <name> <code>  — сохранить сниппет (код после имени)
    !snippet save <name>          — в reply на сообщение → сохраняет текст reply
    !snippet <name>               — показать сниппет в code block
    !snippet list                 — список всех сниппетов
    !snippet del <name>           — удалить сниппет
    !snippet search <query>       — поиск по содержимому
    """
    import datetime as _dt  # noqa: PLC0415

    raw_args = bot._get_command_args(message).strip()
    parts = raw_args.split(None, 1)

    # --- !snippet list ---
    if not parts or parts[0].lower() == "list":
        snippets = _load_snippets()
        if not snippets:
            await message.reply(
                "📭 Нет сохранённых сниппетов.\n"
                "Используй `!snippet save <name> <code>` или ответь на сообщение с `!snippet save <name>`"
            )
            return
        lines = [f"• `{name}`" for name in sorted(snippets)]
        await message.reply("📋 **Сниппеты:**\n" + "\n".join(lines))
        return

    subcommand = parts[0].lower()

    # --- !snippet save <name> [code] ---
    if subcommand == "save":
        rest = parts[1].strip() if len(parts) > 1 else ""
        # Разбиваем на имя и код
        name_and_code = rest.split(None, 1)
        if not name_and_code:
            raise UserInputError(
                user_message="❌ Укажи имя: `!snippet save <name> <code>` или ответь на сообщение"
            )
        name = name_and_code[0].strip().lower()
        if not name:
            raise UserInputError(user_message="❌ Имя сниппета не может быть пустым.")

        # Если код передан inline
        if len(name_and_code) > 1 and name_and_code[1].strip():
            code = name_and_code[1].strip()
        else:
            # Ищем текст в replied сообщении
            replied = message.reply_to_message
            if replied is None or not (replied.text or replied.caption):
                raise UserInputError(
                    user_message=(
                        "❌ Укажи код после имени: `!snippet save <name> <code>`\n"
                        "Или ответь на сообщение с кодом командой `!snippet save <name>`"
                    )
                )
            code = (replied.text or replied.caption or "").strip()

        snippets = _load_snippets()
        snippets[name] = {
            "code": code,
            "created_at": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
        }
        _save_snippets(snippets)
        await message.reply(f"✅ Сниппет `{name}` сохранён ({len(code)} символов).")
        return

    # --- !snippet del <name> ---
    if subcommand == "del":
        if len(parts) < 2 or not parts[1].strip():
            raise UserInputError(user_message="❌ Укажи имя: `!snippet del <name>`")
        name = parts[1].strip().lower()
        snippets = _load_snippets()
        if name not in snippets:
            raise UserInputError(user_message=f"❌ Сниппет `{name}` не найден.")
        del snippets[name]
        _save_snippets(snippets)
        await message.reply(f"🗑 Сниппет `{name}` удалён.")
        return

    # --- !snippet search <query> ---
    if subcommand == "search":
        if len(parts) < 2 or not parts[1].strip():
            raise UserInputError(user_message="❌ Укажи запрос: `!snippet search <query>`")
        query = parts[1].strip().lower()
        snippets = _load_snippets()
        matches = [
            name
            for name, data in snippets.items()
            if query in name or query in data.get("code", "").lower()
        ]
        if not matches:
            await message.reply(f"🔍 Ничего не найдено по запросу `{query}`.")
            return
        lines = [f"• `{name}`" for name in sorted(matches)]
        await message.reply(f"🔍 Найдено ({len(matches)}):\n" + "\n".join(lines))
        return

    # --- !snippet <name> — показать сниппет ---
    name = parts[0].lower()
    snippets = _load_snippets()
    if name not in snippets:
        raise UserInputError(user_message=f"❌ Сниппет `{name}` не найден. Список: `!snippet list`")
    code = snippets[name].get("code", "")
    created = snippets[name].get("created_at", "")
    header = f"📄 **{name}**" + (f" _(сохранён {created[:10]})_" if created else "")
    await message.reply(f"{header}\n```\n{code}\n```")


# ---------------------------------------------------------------------------
# !tag — moved to commands/memory_commands.py (Phase 2 Wave 5)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# handle_top — лидерборд активности чата
# ---------------------------------------------------------------------------


def _plural_messages(n: int) -> str:
    """Возвращает правильную форму слова 'сообщение' для числа n."""
    if 11 <= n % 100 <= 19:
        return "сообщений"
    rem = n % 10
    if rem == 1:
        return "сообщение"
    if 2 <= rem <= 4:
        return "сообщения"
    return "сообщений"


async def handle_top(bot: "KraabUserbot", message: Message) -> None:
    """
    Лидерборд активности чата на основе истории сообщений.

    Варианты:
      !top [N]     — топ N самых активных за последние 24 часа (default N=10)
      !top week    — за последние 7 дней
      !top all     — за всё время (последние 1000 сообщений)
    """
    args = bot._get_command_args(message).strip().lower()

    # Парсим аргументы
    limit = 1000  # сколько сообщений из истории тянуть
    top_n = 10  # сколько участников показать
    period_label = "24ч"

    # Временные рамки фильтрации
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff: datetime.datetime | None = now - datetime.timedelta(hours=24)

    if args == "week":
        cutoff = now - datetime.timedelta(days=7)
        period_label = "неделя"
    elif args == "all":
        cutoff = None
        period_label = "всё время"
    elif args:
        # Пробуем распарсить число N
        try:
            top_n = max(1, min(int(args), 50))
        except ValueError:
            raise UserInputError(
                user_message=(
                    "❌ Неверный аргумент.\n"
                    "Использование:\n"
                    "`!top [N]` — топ за 24ч (N до 50)\n"
                    "`!top week` — за неделю\n"
                    "`!top all` — за всё время"
                )
            )

    # Статусное сообщение
    status_msg = await message.reply(f"⏳ Считаю активность за {period_label}...")

    # Собираем историю чата
    chat_id = message.chat.id
    counts: dict[int, tuple[str, int]] = {}  # user_id → (display_name, count)

    try:
        async for msg in bot.client.get_chat_history(chat_id, limit=limit):
            # Фильтр по дате
            if cutoff is not None:
                msg_date = msg.date
                # Pyrogram возвращает datetime (aware или naive UTC)
                if msg_date is not None:
                    if msg_date.tzinfo is None:
                        msg_date = msg_date.replace(tzinfo=datetime.timezone.utc)
                    if msg_date < cutoff:
                        break  # история идёт в обратном порядке — дальше старее

            # Считаем только сообщения с живым отправителем (не каналы/боты/сервисные)
            user = msg.from_user
            if user is None:
                continue

            uid = user.id
            if uid not in counts:
                # Формируем отображаемое имя
                if user.username:
                    display = f"@{user.username}"
                elif user.first_name or user.last_name:
                    parts = filter(None, [user.first_name, user.last_name])
                    display = " ".join(parts)
                else:
                    display = f"user_{uid}"
                counts[uid] = (display, 0)

            display_name, cnt = counts[uid]
            counts[uid] = (display_name, cnt + 1)

    except Exception as exc:  # noqa: BLE001
        logger.warning("handle_top: ошибка при получении истории чата %s: %s", chat_id, exc)
        await status_msg.edit(f"❌ Не удалось получить историю чата: {exc}")
        return

    if not counts:
        await status_msg.edit(f"📭 Нет сообщений за {period_label}.")
        return

    # Сортируем по убыванию
    ranking = sorted(counts.values(), key=lambda x: x[1], reverse=True)[:top_n]

    # Формируем текст
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"🏆 **Топ чата ({period_label})**", "─────────────"]
    for i, (name, cnt) in enumerate(ranking, start=1):
        prefix = medals[i - 1] if i <= 3 else f"{i}."
        word = _plural_messages(cnt)
        lines.append(f"{prefix} {name} — {cnt} {word}")

    text = "\n".join(lines)
    await status_msg.edit(text)


# ---------------------------------------------------------------------------
# handle_link — утилиты для URL: preview, expand, reply-анализ
# ---------------------------------------------------------------------------

# Паттерн для поиска URL в тексте
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

# Набор коротких доменов (для _is_short_url)
_SHORT_DOMAINS = frozenset(
    [
        "bit.ly",
        "tinyurl.com",
        "t.co",
        "goo.gl",
        "ow.ly",
        "buff.ly",
        "short.link",
        "rb.gy",
        "cutt.ly",
        "is.gd",
        "v.gd",
        "tiny.cc",
        "shorturl.at",
        "clck.ru",
        "vk.cc",
    ]
)


def _is_short_url(url: str) -> bool:
    """Проверяет, является ли URL коротким (шорт-линк)."""
    try:
        from urllib.parse import urlparse  # noqa: PLC0415

        host = urlparse(url).netloc.lower().lstrip("www.")
        return host in _SHORT_DOMAINS
    except Exception:  # noqa: BLE001
        return False


async def _fetch_link_meta(url: str, *, timeout: float = 10.0) -> dict:
    """
    Загружает страницу по URL и извлекает мета-теги: title, description, og:image.
    Возвращает dict с ключами title, description, image, final_url.
    """
    _headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ru,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    result: dict = {
        "title": "",
        "description": "",
        "image": "",
        "final_url": url,
    }
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        headers=_headers,
    ) as client:
        resp = await client.get(url)
        result["final_url"] = str(resp.url)
        html = resp.text

    # Парсим <title>
    title_match = re.search(r"<title[^>]*>([^<]{1,300})</title>", html, re.IGNORECASE | re.DOTALL)
    if title_match:
        result["title"] = re.sub(r"\s+", " ", title_match.group(1)).strip()

    # og:title перекрывает <title>
    og_title = re.search(
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']{1,300})["\']',
        html,
        re.IGNORECASE,
    )
    if og_title:
        result["title"] = og_title.group(1).strip()

    # og:description или meta description
    og_desc = re.search(
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']{1,500})["\']',
        html,
        re.IGNORECASE,
    )
    if not og_desc:
        og_desc = re.search(
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']{1,500})["\']',
            html,
            re.IGNORECASE,
        )
    if og_desc:
        result["description"] = og_desc.group(1).strip()

    # og:image
    og_img = re.search(
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']{1,500})["\']',
        html,
        re.IGNORECASE,
    )
    if og_img:
        result["image"] = og_img.group(1).strip()

    return result


async def _expand_url(url: str, *, timeout: float = 10.0) -> str:
    """
    Разворачивает короткий URL через HEAD-запрос с редиректами.
    Возвращает финальный URL.
    """
    _headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
    }
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        headers=_headers,
    ) as client:
        resp = await client.head(url)
        return str(resp.url)


def _format_link_preview(meta: dict) -> str:
    """Форматирует мета-данные ссылки в стандартный блок."""
    lines = ["🔗 **Link Preview**", "─────"]
    if meta.get("title"):
        lines.append(f"Title: {meta['title']}")
    if meta.get("description"):
        desc = meta["description"]
        if len(desc) > 200:
            desc = desc[:197] + "..."
        lines.append(f"Description: {desc}")
    lines.append(f"URL: {meta['final_url']}")
    if meta.get("image"):
        lines.append(f"Image: {meta['image']}")
    return "\n".join(lines)


async def handle_link(bot: "KraabUserbot", message: Message) -> None:
    """
    Команда !link — утилиты для ссылок.

    !link preview <URL>   — мета-данные страницы (title, description, og:image)
    !link expand <URL>    — разворачивает короткий URL (HEAD + follow redirects)
    !link (в reply)       — анализирует первую ссылку из reply-сообщения
    """
    args_raw = bot._get_command_args(message).strip()

    # --- reply без аргументов: берём первую ссылку из quoted сообщения ---
    if not args_raw and message.reply_to_message:
        reply_text = message.reply_to_message.text or message.reply_to_message.caption or ""
        urls = _URL_RE.findall(reply_text)
        if not urls:
            raise UserInputError(user_message="❌ В reply-сообщении нет ссылок.")
        url = urls[0]
        await message.reply("⏳ Анализирую ссылку...")
        try:
            meta = await _fetch_link_meta(url)
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(user_message=f"❌ Не удалось загрузить: {exc}") from exc
        await message.reply(_format_link_preview(meta), disable_web_page_preview=True)
        return

    parts = args_raw.split(maxsplit=1)
    if not parts:
        raise UserInputError(
            user_message=(
                "❌ Использование:\n"
                "`!link preview <URL>` — превью страницы\n"
                "`!link expand <URL>` — развернуть короткую ссылку\n"
                "Или ответь на сообщение: `!link`"
            )
        )

    subcommand = parts[0].lower()

    # --- !link preview <URL> ---
    if subcommand == "preview":
        if len(parts) < 2 or not parts[1].strip():
            raise UserInputError(user_message="❌ Укажи URL: `!link preview <URL>`")
        url = parts[1].strip()
        await message.reply("⏳ Загружаю превью...")
        try:
            meta = await _fetch_link_meta(url)
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(user_message=f"❌ Не удалось загрузить: {exc}") from exc
        await message.reply(_format_link_preview(meta), disable_web_page_preview=True)
        return

    # --- !link expand <URL> ---
    if subcommand == "expand":
        if len(parts) < 2 or not parts[1].strip():
            raise UserInputError(user_message="❌ Укажи URL: `!link expand <URL>`")
        url = parts[1].strip()
        await message.reply("⏳ Разворачиваю ссылку...")
        try:
            final = await _expand_url(url)
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(user_message=f"❌ Не удалось развернуть: {exc}") from exc
        if final == url:
            text = f"🔗 URL не изменился:\n`{final}`"
        else:
            text = f"🔗 **Expand**\n─────\nИсходный: `{url}`\nФинальный: `{final}`"
        await message.reply(text, disable_web_page_preview=True)
        return

    # --- !link <URL> без subcommand (автоопределение) ---
    # Если первый аргумент выглядит как URL — делаем preview
    if parts[0].startswith(("http://", "https://")):
        url = args_raw.strip()
        await message.reply("⏳ Загружаю превью...")
        try:
            meta = await _fetch_link_meta(url)
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(user_message=f"❌ Не удалось загрузить: {exc}") from exc
        await message.reply(_format_link_preview(meta), disable_web_page_preview=True)
        return

    raise UserInputError(
        user_message=(
            "❌ Неизвестная подкоманда. Использование:\n"
            "`!link preview <URL>` — превью страницы\n"
            "`!link expand <URL>` — развернуть короткую ссылку\n"
            "Или ответь на сообщение: `!link`"
        )
    )


# !regex — extracted to commands/text_utils.py (Phase 2, Session 27)
# Re-exported below: handle_regex, _format_regex_result


# ---------------------------------------------------------------------------
# !yt — информация о YouTube видео
# ---------------------------------------------------------------------------

# Регулярки для извлечения YouTube URL из текста
_YT_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/watch\?[^\s]*v=[\w-]+|youtu\.be/[\w-]+|youtube\.com/shorts/[\w-]+)"
)

_YT_PROMPT_TEMPLATE = (
    "Найди информацию об этом YouTube видео: {url}. "
    "Покажи: название, автор, длительность, дата, описание (кратко)."
)


def _extract_yt_url(text: str) -> str | None:
    """Извлекает первый YouTube URL из текста. Возвращает None если не найдено."""
    m = _YT_URL_RE.search(text or "")
    return m.group(0) if m else None


async def handle_yt(bot: "KraabUserbot", message: Message) -> None:
    """
    !yt <URL>       — информация о YouTube видео через AI + web_search.
    !yt (в reply)   — извлекает URL из цитируемого сообщения.

    Сессия изолирована: yt_{chat_id}.
    """
    args = bot._get_command_args(message).strip()

    # Пытаемся найти URL: сначала в аргументах, затем в reply
    url: str | None = _extract_yt_url(args)
    if url is None and message.reply_to_message is not None:
        replied_text = message.reply_to_message.text or message.reply_to_message.caption or ""
        url = _extract_yt_url(replied_text)

    if url is None:
        raise UserInputError(
            user_message=(
                "🎬 Использование:\n"
                "`!yt <YouTube URL>` — информация о видео\n"
                "или ответь командой `!yt` на сообщение с YouTube ссылкой"
            )
        )

    prompt = _YT_PROMPT_TEMPLATE.format(url=url)
    session_id = f"yt_{message.chat.id}"

    msg = await message.reply(f"🎬 Ищу информацию о видео: `{url}`...")

    try:
        chunks: list[str] = []
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            disable_tools=False,  # web_search нужен для поиска инфо о видео
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()
        if not result:
            await msg.edit("❌ AI вернул пустой ответ.")
            return

        parts = _split_text_for_telegram(result)
        await msg.edit(parts[0])
        for part in parts[1:]:
            await message.reply(part)

    except Exception as exc:  # noqa: BLE001
        logger.error("handle_yt_error", error=str(exc))
        await msg.edit(f"❌ Ошибка: {exc}")


# ---------------------------------------------------------------------------
# !template — шаблоны сообщений с подстановкой переменных
# ---------------------------------------------------------------------------

_TEMPLATES_FILE = (
    pathlib.Path.home() / ".openclaw" / "krab_runtime_state" / "message_templates.json"
)


def _load_templates() -> dict[str, str]:
    """Загружает шаблоны из JSON. Формат: {name: text}."""
    try:
        if _TEMPLATES_FILE.exists():
            return json.loads(_TEMPLATES_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_templates(data: dict[str, str]) -> None:
    """Сохраняет шаблоны в JSON-файл."""
    _TEMPLATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TEMPLATES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _apply_template_vars(text: str, positional_args: list[str]) -> str:
    """
    Подставляет позиционные переменные {var1}, {var2}, ... в порядке появления.
    Например: 'Привет, {name}! Ты {age} лет' + ['Павел', '30'] -> 'Привет, Павел! Ты 30 лет'
    """
    # Ищем все уникальные плейсхолдеры в порядке появления
    placeholders = list(dict.fromkeys(re.findall(r"\{(\w+)\}", text)))
    if not placeholders:
        return text  # нет переменных — возвращаем как есть
    result = text
    for idx, ph in enumerate(placeholders):
        if idx < len(positional_args):
            result = result.replace(f"{{{ph}}}", positional_args[idx])
    return result


async def handle_template(bot: "KraabUserbot", message: Message) -> None:
    """
    !template save <name> <text>  — сохранить шаблон
    !template list                — список всех шаблонов
    !template del <name>          — удалить шаблон
    !template <name>              — отправить шаблон (без переменных)
    !template <name> val1 val2 …  — отправить с подстановкой переменных
    """
    raw_args = bot._get_command_args(message).strip()
    parts = raw_args.split(None, 1)

    # --- !template list ---
    if not parts or parts[0].lower() == "list":
        templates = _load_templates()
        if not templates:
            await message.reply(
                "📭 Нет сохранённых шаблонов.\n"
                "Используй `!template save <name> <text>` чтобы создать шаблон."
            )
            return
        lines = []
        for name, text in sorted(templates.items()):
            preview = text[:60].replace("\n", " ")
            if len(text) > 60:
                preview += "…"
            lines.append(f"• `{name}` — {preview}")
        await message.reply("📋 **Шаблоны:**\n" + "\n".join(lines))
        return

    subcommand = parts[0].lower()

    # --- !template save <name> <text> ---
    if subcommand == "save":
        rest = parts[1].strip() if len(parts) > 1 else ""
        name_and_text = rest.split(None, 1)
        if not name_and_text:
            raise UserInputError(
                user_message="❌ Укажи имя и текст: `!template save <name> <text>`"
            )
        name = name_and_text[0].strip().lower()
        if not name:
            raise UserInputError(user_message="❌ Имя шаблона не может быть пустым.")
        if len(name_and_text) < 2 or not name_and_text[1].strip():
            raise UserInputError(
                user_message=(
                    "❌ Укажи текст шаблона: `!template save <name> <text>`\n"
                    "Переменные задаются как `{var1}`, `{var2}` и т.д."
                )
            )
        text = name_and_text[1].strip()
        templates = _load_templates()
        templates[name] = text
        _save_templates(templates)
        # Показываем найденные переменные в подсказке
        vars_found = list(dict.fromkeys(re.findall(r"\{(\w+)\}", text)))
        var_hint = (
            f" Переменные: {', '.join(f'`{{{v}}}`' for v in vars_found)}" if vars_found else ""
        )
        await message.reply(f"✅ Шаблон `{name}` сохранён.{var_hint}")
        return

    # --- !template del <name> ---
    if subcommand == "del":
        if len(parts) < 2 or not parts[1].strip():
            raise UserInputError(user_message="❌ Укажи имя: `!template del <name>`")
        name = parts[1].strip().lower()
        templates = _load_templates()
        if name not in templates:
            raise UserInputError(user_message=f"❌ Шаблон `{name}` не найден.")
        del templates[name]
        _save_templates(templates)
        await message.reply(f"🗑 Шаблон `{name}` удалён.")
        return

    # --- !template <name> [val1] [val2] ... ---
    name = subcommand  # уже lower()
    templates = _load_templates()
    if name not in templates:
        raise UserInputError(user_message=f"❌ Шаблон `{name}` не найден. Список: `!template list`")
    template_text = templates[name]
    # Позиционные аргументы: всё что после имени шаблона, разбитое по пробелам
    positional_args: list[str] = parts[1].split() if len(parts) > 1 else []
    result_text = _apply_template_vars(template_text, positional_args)
    await message.reply(result_text)


# ---------------------------------------------------------------------------
# !time — мировые часы и конвертация времени
# ---------------------------------------------------------------------------

from zoneinfo import ZoneInfo  # noqa: E402

# Маппинг: имя города (нижний регистр) → IANA timezone
_TIME_CITY_MAP: dict[str, str] = {
    # Европа
    "madrid": "Europe/Madrid",
    "barcelona": "Europe/Madrid",
    "moscow": "Europe/Moscow",
    "москва": "Europe/Moscow",
    "london": "Europe/London",
    "лондон": "Europe/London",
    "berlin": "Europe/Berlin",
    "берлин": "Europe/Berlin",
    "paris": "Europe/Paris",
    "париж": "Europe/Paris",
    "amsterdam": "Europe/Amsterdam",
    "rome": "Europe/Rome",
    "рим": "Europe/Rome",
    "istanbul": "Europe/Istanbul",
    "стамбул": "Europe/Istanbul",
    # Америка
    "new york": "America/New_York",
    "newyork": "America/New_York",
    "nyc": "America/New_York",
    "нью-йорк": "America/New_York",
    "нью йорк": "America/New_York",
    "los angeles": "America/Los_Angeles",
    "la": "America/Los_Angeles",
    "лос-анджелес": "America/Los_Angeles",
    "chicago": "America/Chicago",
    "чикаго": "America/Chicago",
    "toronto": "America/Toronto",
    "торонто": "America/Toronto",
    "sao paulo": "America/Sao_Paulo",
    "são paulo": "America/Sao_Paulo",
    "mexico": "America/Mexico_City",
    "mexico city": "America/Mexico_City",
    # Азия / Тихий океан
    "tokyo": "Asia/Tokyo",
    "токио": "Asia/Tokyo",
    "beijing": "Asia/Shanghai",
    "shanghai": "Asia/Shanghai",
    "пекин": "Asia/Shanghai",
    "шанхай": "Asia/Shanghai",
    "seoul": "Asia/Seoul",
    "сеул": "Asia/Seoul",
    "dubai": "Asia/Dubai",
    "дубай": "Asia/Dubai",
    "singapore": "Asia/Singapore",
    "сингапур": "Asia/Singapore",
    "hong kong": "Asia/Hong_Kong",
    "гонконг": "Asia/Hong_Kong",
    "mumbai": "Asia/Kolkata",
    "delhi": "Asia/Kolkata",
    "мумбаи": "Asia/Kolkata",
    "дели": "Asia/Kolkata",
    "bangkok": "Asia/Bangkok",
    "бангкок": "Asia/Bangkok",
    "sydney": "Australia/Sydney",
    "сидней": "Australia/Sydney",
}

# Города по умолчанию для `!time` без аргументов
_TIME_DEFAULT_CITIES: list[tuple[str, str]] = [
    ("Madrid", "Europe/Madrid"),
    ("Moscow", "Europe/Moscow"),
    ("New York", "America/New_York"),
    ("Tokyo", "Asia/Tokyo"),
]


def _time_format_dt(dt: "datetime.datetime") -> str:
    """Форматирует datetime в '10:35 Mon, Apr 12' (с днём и датой)."""
    return dt.strftime("%H:%M %a, %b %-d")


def _time_lookup_tz(city: str) -> str | None:
    """
    Возвращает IANA timezone для города или None.
    Сначала ищем в маппинге, затем пробуем как IANA-строку напрямую.
    """
    key = city.strip().lower()
    if key in _TIME_CITY_MAP:
        return _TIME_CITY_MAP[key]
    # Пробуем напрямую (например, "Europe/Berlin")
    try:
        ZoneInfo(city)
        return city
    except Exception:  # noqa: BLE001
        return None


async def handle_time(bot: "KraabUserbot", message: Message) -> None:
    """
    !time — мировые часы и конвертация времени.

    Форматы:
      !time                              — время в Madrid, Moscow, NYC, Tokyo
      !time <город>                      — время в конкретном городе
      !time convert <HH:MM> <из> <в>    — конвертация между зонами
    """
    args = bot._get_command_args(message).strip()

    # --- !time convert HH:MM <из> <в> ---
    if args.lower().startswith("convert "):
        rest = args[len("convert ") :].strip()
        # Первый токен — время, далее два города
        time_match = re.match(r"^(\d{1,2}:\d{2})\s+(.+)$", rest)
        if not time_match:
            raise UserInputError(
                user_message=(
                    "❌ Формат: `!time convert HH:MM <город_из> <город_в>`\n"
                    "Пример: `!time convert 15:00 Madrid Moscow`"
                )
            )
        time_str = time_match.group(1)
        cities_part = time_match.group(2).strip()

        # Ищем разделение на два города (перебираем точки разреза)
        from_tz: str | None = None
        to_tz: str | None = None
        city_from_name = ""
        city_to_name = ""
        tokens = cities_part.split()
        found = False
        for split_i in range(1, len(tokens)):
            cf = " ".join(tokens[:split_i])
            ct = " ".join(tokens[split_i:])
            tz_f = _time_lookup_tz(cf)
            tz_t = _time_lookup_tz(ct)
            if tz_f and tz_t:
                from_tz, to_tz = tz_f, tz_t
                city_from_name, city_to_name = cf.title(), ct.title()
                found = True
                break

        if not found:
            raise UserInputError(
                user_message=(
                    "❌ Не могу распознать города.\n"
                    "Поддерживаемые: Madrid, Moscow, New York, Tokyo, London, Dubai и др.\n"
                    "Пример: `!time convert 15:00 Madrid Moscow`"
                )
            )

        # Парсим HH:MM
        try:
            hh, mm = map(int, time_str.split(":"))
            if not (0 <= hh <= 23 and 0 <= mm <= 59):
                raise ValueError("out of range")
        except ValueError:
            raise UserInputError(
                user_message=f"❌ Некорректное время: `{time_str}`. Формат HH:MM (00:00–23:59)."
            )

        # Строим datetime в исходной зоне (сегодняшняя дата)
        today = datetime.date.today()
        dt_from = datetime.datetime(
            today.year, today.month, today.day, hh, mm, 0, tzinfo=ZoneInfo(from_tz)
        )
        dt_to = dt_from.astimezone(ZoneInfo(to_tz))

        await message.reply(
            f"🕐 **Конвертация времени**\n"
            f"`{time_str}` ({city_from_name}, {from_tz})\n"
            f"→ `{dt_to.strftime('%H:%M')}` ({city_to_name}, {to_tz})\n\n"
            f"_{_time_format_dt(dt_from)} → {_time_format_dt(dt_to)}_"
        )
        return

    # --- !time <город> ---
    if args:
        tz_name = _time_lookup_tz(args)

        # Частичное совпадение если прямой поиск не дал результата
        if not tz_name:
            args_lower = args.lower()
            for city_key, tz in _TIME_CITY_MAP.items():
                if city_key.startswith(args_lower) and len(args_lower) >= 3:
                    tz_name = tz
                    break

        if not tz_name:
            raise UserInputError(
                user_message=(
                    f"❌ Город `{args}` не найден.\n\n"
                    "Поддерживаемые города:\n"
                    "Madrid, Barcelona, Moscow, London, Berlin, Paris,\n"
                    "New York, Los Angeles, Chicago, Toronto,\n"
                    "Tokyo, Dubai, Singapore, Hong Kong, Mumbai, Bangkok, Sydney\n\n"
                    "Или IANA timezone напрямую: `!time Europe/Berlin`"
                )
            )

        dt = datetime.datetime.now(ZoneInfo(tz_name))
        display_name = args.title()
        offset = dt.strftime("%z")
        offset_fmt = f"UTC{offset[:3]}:{offset[3:]}" if offset else ""

        await message.reply(
            f"🕐 **{display_name}** ({tz_name})\n`{_time_format_dt(dt)}` {offset_fmt}"
        )
        return

    # --- !time (без аргументов) — несколько городов ---
    now_utc = datetime.datetime.now(ZoneInfo("UTC"))
    lines: list[str] = ["🌍 **Мировое время**\n"]
    for city_name, tz_name in _TIME_DEFAULT_CITIES:
        dt = now_utc.astimezone(ZoneInfo(tz_name))
        offset = dt.strftime("%z")
        offset_fmt = f"UTC{offset[:3]}:{offset[3:]}" if offset else ""
        lines.append(f"**{city_name}** — `{_time_format_dt(dt)}` {offset_fmt}")

    await message.reply("\n".join(lines))


# ---------------------------------------------------------------------------
# !mark — пометка чатов как прочитанных/непрочитанных
# ---------------------------------------------------------------------------


async def handle_mark(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление статусом прочитанности чатов.

    Подкоманды:
        !mark read     — пометить текущий чат как прочитанный
        !mark unread   — пометить текущий чат как непрочитанный
        !mark readall  — пометить ВСЕ чаты как прочитанные

    Owner-only.
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!mark` доступен только владельцу.")

    subcmd = bot._get_command_args(message).strip().lower()

    async def _reply(text: str) -> None:
        """Редактирует если сообщение от self, иначе отвечает."""
        if message.from_user and message.from_user.id == bot.me.id:
            await message.edit(text)
        else:
            await message.reply(text)

    if subcmd == "read":
        # Пометить текущий чат как прочитанный
        try:
            await bot.client.read_chat_history(chat_id=message.chat.id)
            await _reply("✅ Чат помечен как прочитанный.")
        except Exception as exc:
            await _reply(f"❌ Не удалось пометить как прочитанный: `{exc}`")

    elif subcmd == "unread":
        # Пометить текущий чат как непрочитанный
        try:
            await bot.client.mark_chat_unread(chat_id=message.chat.id)
            await _reply("🔵 Чат помечен как непрочитанный.")
        except Exception as exc:
            await _reply(f"❌ Не удалось пометить как непрочитанный: `{exc}`")

    elif subcmd == "readall":
        # Пометить ВСЕ диалоги как прочитанные
        success_count = 0
        fail_count = 0
        try:
            async for dialog in bot.client.get_dialogs():
                try:
                    await bot.client.read_chat_history(chat_id=dialog.chat.id)
                    success_count += 1
                except Exception:  # noqa: BLE001
                    fail_count += 1

            result = f"✅ Все чаты помечены как прочитанные ({success_count} чатов)."
            if fail_count:
                result += f"\n⚠️ Не удалось обработать: {fail_count}."
            await _reply(result)
        except Exception as exc:
            await _reply(f"❌ Ошибка при получении диалогов: `{exc}`")

    else:
        raise UserInputError(
            user_message=(
                "📖 **Управление статусом прочтения**\n\n"
                "`!mark read` — пометить текущий чат как прочитанный\n"
                "`!mark unread` — пометить как непрочитанный\n"
                "`!mark readall` — пометить ВСЕ чаты как прочитанные"
            )
        )


# ---------------------------------------------------------------------------
# !typing — симуляция набора текста / записи голосового / загрузки файла
# ---------------------------------------------------------------------------

_TYPING_ACTION_MAP: dict[str, str] = {
    "typing": "TYPING",
    "record": "RECORD_AUDIO",
    "upload": "UPLOAD_DOCUMENT",
}

_TYPING_LABEL_MAP: dict[str, str] = {
    "typing": "⌨️ typing...",
    "record": "🎙 recording voice...",
    "upload": "📤 uploading...",
}

_TYPING_DEFAULT_SECONDS = 5
_TYPING_MAX_SECONDS = 30


async def handle_typing(bot: "KraabUserbot", message: Message) -> None:
    """
    Симулирует действие в чате (typing / recording / uploading).

    Синтаксис:
      !typing [seconds]        — показывает «typing...» N секунд (default 5, max 30)
      !typing record [seconds] — показывает «recording voice...»
      !typing upload [seconds] — показывает «uploading...»

    Owner-only.
    """
    from pyrogram import enums as _pyrogram_enums

    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!typing` доступен только владельцу.")

    args = bot._get_command_args(message).strip().lower().split()

    # Определяем режим и длительность
    action_key = "typing"
    seconds = _TYPING_DEFAULT_SECONDS

    if args:
        if args[0] in _TYPING_ACTION_MAP:
            # !typing record [N] / !typing upload [N]
            action_key = args[0]
            if len(args) >= 2:
                try:
                    seconds = int(args[1])
                except ValueError:
                    raise UserInputError(
                        user_message=f"❌ Длительность должна быть числом, получено: `{args[1]}`"
                    )
        else:
            # !typing N
            try:
                seconds = int(args[0])
            except ValueError:
                raise UserInputError(
                    user_message=(
                        "⌨️ **Симуляция набора текста**\n\n"
                        "`!typing [N]` — typing N секунд (default 5, max 30)\n"
                        "`!typing record [N]` — recording voice...\n"
                        "`!typing upload [N]` — uploading..."
                    )
                )

    # Клэмп длительности
    seconds = max(1, min(seconds, _TYPING_MAX_SECONDS))

    pyrogram_action = getattr(_pyrogram_enums.ChatAction, _TYPING_ACTION_MAP[action_key])
    label = _TYPING_LABEL_MAP[action_key]
    chat_id = message.chat.id

    # Удаляем команду, чтобы не оставлять следов
    try:
        await message.delete()
    except Exception:  # noqa: BLE001
        pass

    # Отправляем chat action каждые ~4 секунды (Telegram сбрасывает статус ~5 сек)
    logger.info("handle_typing: %s в чате %s на %ss", action_key, chat_id, seconds)
    elapsed = 0
    interval = 4
    while elapsed < seconds:
        try:
            await bot.client.send_chat_action(chat_id, pyrogram_action)
        except Exception as exc:  # noqa: BLE001
            logger.warning("handle_typing: send_chat_action ошибка: %s", exc)
            break
        sleep_time = min(interval, seconds - elapsed)
        await asyncio.sleep(sleep_time)
        elapsed += sleep_time

    # Сбрасываем статус явно
    try:
        await bot.client.send_chat_action(chat_id, _pyrogram_enums.ChatAction.CANCEL)
    except Exception:  # noqa: BLE001
        pass

    logger.info("handle_typing: завершено (%s, %ss)", label, seconds)


# Допустимые значения slowmode (секунды) согласно Telegram API
_SLOWMODE_VALID = {0, 10, 30, 60, 300, 900, 3600}
_SLOWMODE_LABELS: dict[int, str] = {
    0: "выключен",
    10: "10 сек",
    30: "30 сек",
    60: "1 мин",
    300: "5 мин",
    900: "15 мин",
    3600: "1 час",
}


async def handle_slowmode(bot: "KraabUserbot", message: Message) -> None:
    """!slowmode — управление slowmode в группе (требует прав администратора).

    Синтаксис:
      !slowmode <seconds>  — установить задержку (0, 10, 30, 60, 300, 900, 3600)
      !slowmode off        — выключить slowmode (= 0 секунд)
      !slowmode status     — показать текущий slowmode чата
    """
    # Только группы и каналы поддерживают slowmode
    chat = message.chat
    if chat.type.name not in ("GROUP", "SUPERGROUP", "CHANNEL"):
        raise UserInputError(user_message="❌ Slowmode доступен только в группах и каналах.")

    raw_text = (message.text or "").strip()
    parts = raw_text.split(maxsplit=1)
    arg = parts[1].strip().lower() if len(parts) > 1 else ""

    # ── status ──────────────────────────────────────────────────────────────
    if not arg or arg == "status":
        try:
            full_chat = await bot.client.get_chat(chat.id)
            delay = getattr(full_chat, "slow_mode_delay", None) or 0
            label = _SLOWMODE_LABELS.get(delay, f"{delay} сек")
            await message.reply(
                f"🐢 **Slowmode** в `{chat.title or chat.id}`\n"
                f"Текущее значение: **{label}**\n\n"
                f"Допустимые значения: 0, 10, 30, 60, 300, 900, 3600\n"
                f"`!slowmode <сек>` — установить | `!slowmode off` — выключить"
            )
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(
                user_message=f"❌ Не удалось получить информацию о чате: {exc}"
            ) from exc
        return

    # ── off → 0 ─────────────────────────────────────────────────────────────
    if arg in ("off", "выкл", "0"):
        seconds = 0
    else:
        # Числовой аргумент
        if not arg.isdigit():
            raise UserInputError(
                user_message=(
                    "❌ Неверный аргумент. Используй:\n"
                    "`!slowmode <сек>` — 0, 10, 30, 60, 300, 900, 3600\n"
                    "`!slowmode off` — выключить\n"
                    "`!slowmode status` — текущее значение"
                )
            )
        seconds = int(arg)
        if seconds not in _SLOWMODE_VALID:
            raise UserInputError(
                user_message=(
                    f"❌ Недопустимое значение `{seconds}`.\n"
                    f"Telegram принимает: **0, 10, 30, 60, 300, 900, 3600**"
                )
            )

    # ── Применяем ───────────────────────────────────────────────────────────
    try:
        await bot.client.set_slow_mode(chat.id, seconds)
    except Exception as exc:  # noqa: BLE001
        err_str = str(exc)
        if "CHAT_ADMIN_REQUIRED" in err_str or "admin" in err_str.lower():
            raise UserInputError(
                user_message="❌ Нет прав администратора для управления slowmode."
            ) from exc
        raise UserInputError(user_message=f"❌ Ошибка установки slowmode: {exc}") from exc

    label = _SLOWMODE_LABELS.get(seconds, f"{seconds} сек")
    if seconds == 0:
        await message.reply(f"✅ Slowmode **выключен** в `{chat.title or chat.id}`.")
    else:
        await message.reply(f"🐢 Slowmode установлен: **{label}** в `{chat.title or chat.id}`.")


# ---------------------------------------------------------------------------
# !chatmute — управление Telegram-уведомлениями per-chat
# ---------------------------------------------------------------------------

# Таймаут mute в секундах: 2147483647 = максимум int32 (~68 лет, «навсегда»)
_MUTE_FOREVER_UNTIL: int = 2_147_483_647


async def handle_chatmute(bot: "KraabUserbot", message: Message) -> None:
    """Управление Telegram-уведомлениями текущего чата через MTProto.

    Команды:
      !chatmute off     — отключить уведомления (mute навсегда)
      !chatmute on      — включить уведомления
      !chatmute status  — показать текущий статус
      !chatmute         — показать справку
    """
    from pyrogram import raw as _raw

    args = bot._get_command_args(message).strip().lower()
    chat_id = message.chat.id

    async def _get_peer_settings() -> dict:
        """Получить текущие настройки уведомлений для чата."""
        try:
            peer = await bot.client.resolve_peer(chat_id)
            notify_peer = _raw.types.InputNotifyPeer(peer=peer)
            result = await bot.client.invoke(
                _raw.functions.account.GetNotifySettings(peer=notify_peer)
            )
            return {
                "mute_until": getattr(result, "mute_until", 0) or 0,
            }
        except Exception:
            return {"mute_until": 0}

    if args in {"off", "mute", "выкл", "тихо"}:
        # Mute навсегда
        try:
            peer = await bot.client.resolve_peer(chat_id)
            notify_peer = _raw.types.InputNotifyPeer(peer=peer)
            settings = _raw.types.InputPeerNotifySettings(
                mute_until=_MUTE_FOREVER_UNTIL,
                silent=True,
            )
            await bot.client.invoke(
                _raw.functions.account.UpdateNotifySettings(peer=notify_peer, settings=settings)
            )
            await message.reply(
                "🔕 Уведомления в этом чате **отключены**.\n`!chatmute on` — включить обратно."
            )
        except Exception as exc:
            raise UserInputError(
                user_message=f"❌ Не удалось отключить уведомления: {exc}"
            ) from exc

    elif args in {"on", "unmute", "вкл", "громко"}:
        # Unmute — mute_until=0 снимает mute
        try:
            peer = await bot.client.resolve_peer(chat_id)
            notify_peer = _raw.types.InputNotifyPeer(peer=peer)
            settings = _raw.types.InputPeerNotifySettings(
                mute_until=0,
                silent=False,
            )
            await bot.client.invoke(
                _raw.functions.account.UpdateNotifySettings(peer=notify_peer, settings=settings)
            )
            await message.reply("🔔 Уведомления в этом чате **включены**.")
        except Exception as exc:
            raise UserInputError(user_message=f"❌ Не удалось включить уведомления: {exc}") from exc

    elif args in {"status", "статус"}:
        import time as _time_mod

        s = await _get_peer_settings()
        mute_until = s.get("mute_until", 0)
        now_ts = int(_time_mod.time())

        if mute_until and mute_until > now_ts:
            if mute_until >= _MUTE_FOREVER_UNTIL:
                status_line = "🔕 **Заглушён** (навсегда)"
            else:
                import datetime as _dt_mod

                dt_until = _dt_mod.datetime.fromtimestamp(mute_until)
                status_line = f"🔕 **Заглушён** до {dt_until.strftime('%d.%m.%Y %H:%M')}"
        else:
            status_line = "🔔 **Уведомления включены**"

        await message.reply(
            f"📢 Статус уведомлений чата:\n{status_line}\n\n"
            "`!chatmute off` — отключить\n"
            "`!chatmute on`  — включить"
        )

    else:
        await message.reply(
            "📢 **Управление уведомлениями чата**\n\n"
            "`!chatmute off`    — отключить уведомления\n"
            "`!chatmute on`     — включить уведомления\n"
            "`!chatmute status` — текущий статус"
        )


# ---------------------------------------------------------------------------
# !urban — Urban Dictionary lookup через AI + web_search
# ---------------------------------------------------------------------------


async def handle_urban(bot: "KraabUserbot", message: Message) -> None:
    """
    !urban <слово> — определение слова из Urban Dictionary через AI.

    Краб использует web_search для поиска актуального определения на urbandictionary.com.
    Промпт гарантирует формат: определение, пример использования, автор.
    """
    word = bot._get_command_args(message).strip()

    # Поддержка reply: берём текст ответного сообщения если аргументов нет
    if not word and message.reply_to_message:
        word = (message.reply_to_message.text or "").strip()

    if not word:
        raise UserInputError(
            user_message=(
                "📖 **!urban — Urban Dictionary lookup**\n\n"
                "`!urban <слово>` — поиск сленгового определения\n\n"
                "_Пример: `!urban yeet` или `!urban ghosting`_"
            )
        )

    status_msg = await message.reply(f"📖 Ищу «{word}» на Urban Dictionary...")

    # Изолированная сессия: не смешивается с основным контекстом чата
    session_id = f"urban_{message.chat.id}"

    prompt = (
        f"Найди определение слова '{word}' на Urban Dictionary. "
        "Используй web_search чтобы найти актуальное определение. "
        "Покажи в ответе: определение, пример использования, автор. "
        "Если слово не найдено — скажи об этом честно."
    )

    try:
        chunks: list[str] = []
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            disable_tools=False,  # web_search обязателен для поиска UD
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()

        if not result:
            await status_msg.edit(f"❌ Не удалось получить определение «{word}».")
            return

        # Заголовок + результат
        header = f"📖 **Urban Dictionary: {word}**\n\n"
        full_text = header + result

        # Пагинация: Telegram ограничивает ~4096 символов
        parts = _split_text_for_telegram(full_text)
        total = len(parts)

        first = parts[0]
        if total > 1:
            first += f"\n\n_(часть 1/{total})_"
        await status_msg.edit(first)

        for i, part in enumerate(parts[1:], start=2):
            suffix = f"\n\n_(часть {i}/{total})_" if total > 2 else ""
            await message.reply(part + suffix)

    except Exception as exc:  # noqa: BLE001
        logger.error("handle_urban_error", word=word, error=str(exc))
        await status_msg.edit(f"❌ Ошибка поиска Urban Dictionary: {exc}")


async def handle_contacts(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление контактами Telegram (только userbot).

    Синтаксис:
      !contacts                      — количество контактов
      !contacts search <запрос>      — поиск по имени или номеру
      !contacts add <phone> <имя>    — добавить контакт по номеру телефона
    """
    args = bot._get_command_args(message).strip()

    # ── Без аргументов: показать количество контактов ────────────────────────
    if not args:
        try:
            contacts = await bot.client.get_contacts()
            count = len(contacts)
        except Exception as exc:
            await message.reply(f"❌ Не удалось получить контакты: {exc}")
            return
        await message.reply(
            f"📒 **Контакты**\n\n"
            f"Всего в адресной книге: **{count}**\n\n"
            "`!contacts search <запрос>` — поиск\n"
            "`!contacts add <phone> <имя>` — добавить"
        )
        return

    parts = args.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    # ── search ───────────────────────────────────────────────────────────────
    if subcmd == "search":
        if not rest:
            raise UserInputError(user_message="🔍 Укажи запрос: `!contacts search <имя или номер>`")
        try:
            results = await bot.client.search_contacts(rest)
        except Exception as exc:
            await message.reply(f"❌ Ошибка поиска контактов: {exc}")
            return
        if not results:
            await message.reply(f"📭 Ничего не найдено по запросу: `{rest}`")
            return
        lines = [f"🔍 **Результаты поиска** (`{rest}`) — {len(results)}:\n"]
        for user in results[:20]:  # ограничиваем вывод до 20
            name_parts = [user.first_name or ""]
            if user.last_name:
                name_parts.append(user.last_name)
            full_name = " ".join(name_parts).strip() or "—"
            username_str = f" @{user.username}" if user.username else ""
            phone = getattr(user, "phone_number", None) or "скрыт"
            lines.append(f"• **{full_name}**{username_str} | `{user.id}` | 📞 {phone}")
        if len(results) > 20:
            lines.append(f"\n_… и ещё {len(results) - 20}_")
        await message.reply("\n".join(lines))
        return

    # ── add ──────────────────────────────────────────────────────────────────
    if subcmd == "add":
        if not rest:
            raise UserInputError(
                user_message=(
                    "📞 Укажи номер и имя: `!contacts add +79001234567 Иван`\n"
                    "Номер должен быть в международном формате."
                )
            )
        add_parts = rest.split(maxsplit=1)
        if len(add_parts) < 2:
            raise UserInputError(
                user_message=(
                    "📞 Формат: `!contacts add <phone> <имя>`\n"
                    "Пример: `!contacts add +79001234567 Иван`"
                )
            )
        phone_num, first_name = add_parts[0], add_parts[1].strip()
        if not first_name:
            raise UserInputError(user_message="📞 Укажи имя контакта.")
        try:
            added = await bot.client.add_contact(phone_num, first_name)
        except Exception as exc:
            await message.reply(f"❌ Не удалось добавить контакт: {exc}")
            return
        if added:
            name_parts = [added.first_name or ""]
            if added.last_name:
                name_parts.append(added.last_name)
            full_name = " ".join(name_parts).strip() or first_name
            await message.reply(
                f"✅ Контакт добавлен!\n\n"
                f"**Имя:** {full_name}\n"
                f"**ID:** `{added.id}`\n"
                f"**Телефон:** {phone_num}"
            )
        else:
            await message.reply(f"✅ Контакт `{first_name}` ({phone_num}) добавлен.")
        return

    # ── Неизвестная подкоманда ────────────────────────────────────────────────
    raise UserInputError(
        user_message=(
            "📒 **Контакты**\n\n"
            "`!contacts` — количество контактов\n"
            "`!contacts search <запрос>` — поиск по имени или номеру\n"
            "`!contacts add <phone> <имя>` — добавить контакт"
        )
    )


# ---------------------------------------------------------------------------
# !invite — приглашение пользователей в группу
# ---------------------------------------------------------------------------


async def handle_invite(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление приглашениями в группу. Owner-only.

    Форматы:
      !invite @username             — добавить пользователя в текущую группу
      !invite link                  — создать invite link
      !invite link revoke <url>     — отозвать invite link
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!invite` доступен только владельцу.")

    chat_id = message.chat.id
    args_raw = message.command[1:] if message.command else []

    # Справка при отсутствии аргументов
    if not args_raw:
        raise UserInputError(
            user_message=(
                "👥 **Приглашение в группу**\n\n"
                "`!invite @username` — добавить пользователя в текущую группу\n"
                "`!invite link` — создать пригласительную ссылку\n"
                "`!invite link revoke <url>` — отозвать ссылку"
            )
        )

    subcmd = args_raw[0].lower()

    # ── invite link ───────────────────────────────────────────
    if subcmd == "link":
        if len(args_raw) >= 2 and args_raw[1].lower() == "revoke":
            # Отозвать invite link
            if len(args_raw) < 3:
                raise UserInputError(user_message="❌ Укажи ссылку: `!invite link revoke <url>`")
            link_url = args_raw[2]
            try:
                revoked = await bot.client.revoke_chat_invite_link(chat_id, link_url)
                await message.reply(f"🔒 Ссылка отозвана:\n`{revoked.invite_link}`")
            except Exception as exc:
                raise UserInputError(
                    user_message=f"❌ Не удалось отозвать ссылку: `{exc}`"
                ) from exc
            return

        # Создать новую invite link
        try:
            link = await bot.client.create_chat_invite_link(chat_id)
            await message.reply(f"🔗 **Пригласительная ссылка:**\n`{link.invite_link}`")
        except Exception as exc:
            raise UserInputError(user_message=f"❌ Не удалось создать ссылку: `{exc}`") from exc
        return

    # ── add user ───────────────────────────────────────────────
    # Первый аргумент — @username или числовой user_id
    target = args_raw[0]
    try:
        await bot.client.add_chat_members(chat_id, target)
        await message.reply(f"✅ Пользователь `{target}` добавлен в чат.")
    except Exception as exc:
        raise UserInputError(user_message=f"❌ Не удалось добавить `{target}`: `{exc}`") from exc


async def handle_blocked(bot: "KraabUserbot", message: Message) -> None:
    """Управление заблокированными пользователями (userbot-only).

    Подкоманды:
      !blocked list             — список заблокированных
      !blocked add              — заблокировать автора reply-сообщения
      !blocked add @username    — заблокировать по username или user_id
      !blocked remove @username — разблокировать по username или user_id
    """
    args_raw = bot._get_command_args(message).strip()
    parts = args_raw.split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    # ── LIST ──────────────────────────────────────────────────────────────────
    if sub in {"list", "список", "ls", ""}:
        lines: list[str] = []
        try:
            async for user in bot.client.get_blocked():
                name = user.first_name or ""
                if user.last_name:
                    name = f"{name} {user.last_name}".strip()
                username_part = f" (@{user.username})" if user.username else ""
                lines.append(f"• `{user.id}` — {name}{username_part}")
        except Exception as exc:
            raise UserInputError(
                user_message=f"❌ Не удалось получить список заблокированных: {exc}"
            ) from exc

        if not lines:
            await message.reply("✅ Список заблокированных пуст.")
        else:
            text = "🚫 **Заблокированные пользователи**\n\n" + "\n".join(lines)
            await message.reply(text)
        return

    # ── ADD ───────────────────────────────────────────────────────────────────
    if sub in {"add", "ban", "block", "заблок"}:
        # Приоритет: reply > аргумент
        target_id: "int | str | None" = None

        if message.reply_to_message and not arg:
            # Блокируем автора reply
            replied = message.reply_to_message
            if replied.from_user:
                target_id = replied.from_user.id
            elif replied.sender_chat:
                target_id = replied.sender_chat.id
            else:
                raise UserInputError(user_message="❌ Не могу определить автора сообщения.")
        elif arg:
            raw = arg.lstrip("@")
            try:
                target_id = int(raw)
            except ValueError:
                target_id = raw  # username (строка)
        else:
            raise UserInputError(
                user_message=(
                    "❌ Укажи цель: ответь на сообщение или передай `@username` / `user_id`.\n"
                    "Пример: `!blocked add @username`"
                )
            )

        try:
            await bot.client.block_user(target_id)
        except Exception as exc:
            raise UserInputError(
                user_message=f"❌ Не удалось заблокировать `{target_id}`: {exc}"
            ) from exc

        await message.reply(f"🚫 Пользователь `{target_id}` заблокирован.")
        return

    # ── REMOVE ────────────────────────────────────────────────────────────────
    if sub in {"remove", "unblock", "del", "rm", "разблок"}:
        if not arg:
            raise UserInputError(
                user_message=(
                    "❌ Укажи пользователя: `!blocked remove @username` или `!blocked remove <user_id>`."
                )
            )
        raw = arg.lstrip("@")
        try:
            target_id = int(raw)
        except ValueError:
            target_id = raw  # username

        try:
            await bot.client.unblock_user(target_id)
        except Exception as exc:
            raise UserInputError(
                user_message=f"❌ Не удалось разблокировать `{target_id}`: {exc}"
            ) from exc

        await message.reply(f"✅ Пользователь `{target_id}` разблокирован.")
        return

    # ── СПРАВКА ───────────────────────────────────────────────────────────────
    await message.reply(
        "🚫 **Управление заблокированными**\n\n"
        "`!blocked list`             — список заблокированных\n"
        "`!blocked add` _(reply)_    — заблокировать автора сообщения\n"
        "`!blocked add @username`    — заблокировать по username/ID\n"
        "`!blocked remove @username` — разблокировать"
    )


async def handle_profile(bot: "KraabUserbot", message: Message) -> None:
    """!profile — управление профилем userbot-аккаунта (owner-only).

    Синтаксис:
      !profile                         — показать текущий профиль
      !profile bio <текст>             — установить bio
      !profile name <first> [last]     — изменить имя
      !profile username <username>     — изменить username
    """
    # Только владелец может менять свой профиль
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 Команда доступна только владельцу.")

    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=2)
    # parts[0] = "!profile", parts[1] = subcommand (optional), parts[2] = args
    sub = parts[1].strip().lower() if len(parts) > 1 else ""

    # --- Показ профиля ---
    if not sub:
        try:
            me = await bot.client.get_me()
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(user_message=f"❌ Не удалось получить профиль: {exc}") from exc

        first = me.first_name or ""
        last = me.last_name or ""
        full_name = f"{first} {last}".strip()
        username = f"@{me.username}" if me.username else "—"
        user_id = me.id
        bio = getattr(me, "bio", None) or "—"
        photo_count = 0
        try:
            async for _ in bot.client.get_chat_photos("me"):
                photo_count += 1
        except Exception:  # noqa: BLE001
            photo_count = 0

        lines = [
            "👤 **Профиль аккаунта**",
            "",
            f"**Имя:** {full_name}",
            f"**Username:** {username}",
            f"**ID:** `{user_id}`",
            f"**Bio:** {bio}",
            f"**Фото:** {photo_count}",
            "",
            "`!profile bio <текст>` — изменить bio",
            "`!profile name <first> [last]` — изменить имя",
            "`!profile username <username>` — изменить username",
        ]
        await message.reply("\n".join(lines))
        return

    # --- Изменение bio ---
    if sub == "bio":
        bio_text = parts[2].strip() if len(parts) > 2 else ""
        if not bio_text:
            raise UserInputError(user_message="❌ Укажи текст bio: `!profile bio <текст>`")
        try:
            await bot.client.update_profile(bio=bio_text)
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(user_message=f"❌ Не удалось обновить bio: {exc}") from exc
        await message.reply(f"✅ Bio обновлено:\n{bio_text}")
        logger.info("handle_profile_bio_updated", length=len(bio_text))
        return

    # --- Изменение имени ---
    if sub == "name":
        name_args = parts[2].strip() if len(parts) > 2 else ""
        if not name_args:
            raise UserInputError(user_message="❌ Укажи имя: `!profile name <first> [last]`")
        name_parts = name_args.split(maxsplit=1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""
        try:
            await bot.client.update_profile(
                first_name=first_name,
                last_name=last_name,
            )
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(user_message=f"❌ Не удалось обновить имя: {exc}") from exc
        full = f"{first_name} {last_name}".strip()
        await message.reply(f"✅ Имя обновлено: **{full}**")
        logger.info("handle_profile_name_updated", first=first_name, last=last_name)
        return

    # --- Изменение username ---
    if sub == "username":
        uname = parts[2].strip().lstrip("@") if len(parts) > 2 else ""
        if not uname:
            raise UserInputError(user_message="❌ Укажи username: `!profile username <username>`")
        try:
            await bot.client.update_username(uname)
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(user_message=f"❌ Не удалось обновить username: {exc}") from exc
        await message.reply(f"✅ Username обновлён: @{uname}")
        logger.info("handle_profile_username_updated", username=uname)
        return

    # --- Неизвестная подкоманда ---
    raise UserInputError(
        user_message=(
            "👤 **!profile — управление профилем**\n\n"
            "`!profile` — показать текущий профиль\n"
            "`!profile bio <текст>` — установить bio\n"
            "`!profile name <first> [last]` — изменить имя\n"
            "`!profile username <username>` — изменить username"
        )
    )


# ---------------------------------------------------------------------------
# !members — управление участниками группы (userbot-admin only)
# ---------------------------------------------------------------------------


async def handle_members(bot: "KraabUserbot", message: Message) -> None:
    """Управление участниками группы.

    Команды:
      !members                  — количество участников
      !members list [N]         — список последних N участников (по умолчанию 10)
      !members kick             — кикнуть автора сообщения (reply)
      !members ban              — забанить автора сообщения (reply)
      !members unban @username  — разбанить пользователя по @username или user_id
    """
    chat = message.chat
    # Только группы поддерживают управление участниками
    if chat.type.name not in ("GROUP", "SUPERGROUP"):
        raise UserInputError(user_message="❌ Команда `!members` работает только в группах.")

    raw_text = (message.text or "").strip()
    parts = raw_text.split(maxsplit=2)
    # parts[0] = "!members", parts[1] = подкоманда, parts[2] = аргумент
    sub = parts[1].strip().lower() if len(parts) > 1 else ""

    # ── !members (без аргументов) — количество участников ───────────────────
    if not sub:
        try:
            count = await bot.client.get_chat_members_count(chat.id)
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(
                user_message=f"❌ Не удалось получить количество участников: {exc}"
            ) from exc
        await message.reply(f"👥 Участников в `{chat.title or chat.id}`: **{count}**")
        return

    # ── !members list [N] — список участников ───────────────────────────────
    if sub == "list":
        # Опциональный лимит
        limit = 10
        if len(parts) > 2:
            try:
                limit = int(parts[2].strip())
                if limit < 1:
                    raise ValueError
                limit = min(limit, 200)  # Защита от слишком большого запроса
            except ValueError:
                raise UserInputError(user_message="❌ Укажи число участников: `!members list 20`")

        try:
            members_list = []
            async for m in bot.client.get_chat_members(chat.id, limit=limit):
                user = m.user
                if user is None or user.is_deleted:
                    continue
                name = user.first_name or ""
                if user.last_name:
                    name = f"{name} {user.last_name}".strip()
                username = f"@{user.username}" if user.username else f"id{user.id}"
                members_list.append(f"• {name} ({username})")
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(
                user_message=f"❌ Не удалось получить список участников: {exc}"
            ) from exc

        if not members_list:
            await message.reply("❌ Список участников пуст или недоступен.")
            return

        header = f"👥 **Участники** `{chat.title or chat.id}` (последние {len(members_list)}):\n\n"
        body = "\n".join(members_list)
        # Разбиваем если текст слишком длинный для Telegram (4096 символов)
        full = header + body
        if len(full) <= 4096:
            await message.reply(full)
        else:
            await message.reply(header + body[:4000] + "\n…")
        return

    # ── !members kick — кикнуть автора reply ────────────────────────────────
    if sub == "kick":
        replied = getattr(message, "reply_to_message", None)
        if replied is None or replied.from_user is None:
            raise UserInputError(
                user_message="❌ Ответь на сообщение участника которого хочешь кикнуть."
            )
        target = replied.from_user
        if target.is_bot:
            raise UserInputError(user_message="❌ Нельзя кикнуть бота этой командой.")
        try:
            # ban + немедленный unban = kick (удаляется из чата, но может вернуться)
            await bot.client.ban_chat_member(chat.id, target.id)
            await bot.client.unban_chat_member(chat.id, target.id)
        except Exception as exc:  # noqa: BLE001
            err_str = str(exc)
            if "CHAT_ADMIN_REQUIRED" in err_str or "admin" in err_str.lower():
                raise UserInputError(
                    user_message="❌ Нет прав администратора для кика участников."
                ) from exc
            raise UserInputError(user_message=f"❌ Не удалось кикнуть участника: {exc}") from exc
        name = target.first_name or str(target.id)
        await message.reply(f"👟 **{name}** кикнут из `{chat.title or chat.id}`.")
        return

    # ── !members ban — забанить автора reply ─────────────────────────────────
    if sub == "ban":
        replied = getattr(message, "reply_to_message", None)
        if replied is None or replied.from_user is None:
            raise UserInputError(
                user_message="❌ Ответь на сообщение участника которого хочешь забанить."
            )
        target = replied.from_user
        if target.is_bot:
            raise UserInputError(user_message="❌ Нельзя забанить бота этой командой.")
        try:
            await bot.client.ban_chat_member(chat.id, target.id)
        except Exception as exc:  # noqa: BLE001
            err_str = str(exc)
            if "CHAT_ADMIN_REQUIRED" in err_str or "admin" in err_str.lower():
                raise UserInputError(
                    user_message="❌ Нет прав администратора для бана участников."
                ) from exc
            raise UserInputError(user_message=f"❌ Не удалось забанить участника: {exc}") from exc
        name = target.first_name or str(target.id)
        await message.reply(f"🔨 **{name}** забанен в `{chat.title or chat.id}`.")
        return

    # ── !members unban @username|user_id — разбанить ─────────────────────────
    if sub == "unban":
        if len(parts) < 3 or not parts[2].strip():
            raise UserInputError(
                user_message="❌ Укажи пользователя: `!members unban @username` или `!members unban 12345`"
            )
        target_str = parts[2].strip()
        # Убираем @ если есть — Pyrogram принимает @username напрямую
        if target_str.startswith("@"):
            target_ref: int | str = target_str
        else:
            # Попробуем как числовой ID
            try:
                target_ref = int(target_str)
            except ValueError:
                target_ref = target_str  # Передаём как есть, Pyrogram разберётся

        try:
            await bot.client.unban_chat_member(chat.id, target_ref)
        except Exception as exc:  # noqa: BLE001
            err_str = str(exc)
            if "CHAT_ADMIN_REQUIRED" in err_str or "admin" in err_str.lower():
                raise UserInputError(
                    user_message="❌ Нет прав администратора для разбана участников."
                ) from exc
            raise UserInputError(
                user_message=f"❌ Не удалось разбанить пользователя: {exc}"
            ) from exc
        await message.reply(f"✅ Пользователь `{target_str}` разбанен в `{chat.title or chat.id}`.")
        return

    # ── Неизвестная подкоманда → справка ─────────────────────────────────────
    raise UserInputError(
        user_message=(
            "👥 **Управление участниками группы**\n\n"
            "`!members`              — количество участников\n"
            "`!members list [N]`     — список последних N участников\n"
            "`!members kick`         — кикнуть автора (reply)\n"
            "`!members ban`          — забанить автора (reply)\n"
            "`!members unban @user`  — разбанить пользователя"
        )
    )


# ---------------------------------------------------------------------------
# !log — просмотр логов Краба из Telegram
# ---------------------------------------------------------------------------

# Путь к лог-файлу Краба
# !log handler + helpers (_KRAB_LOG_PATH, _LOG_*, _read_log_tail_subprocess) —
# extracted to commands/system_commands.py (Phase 2 Wave 10, Session 27). Re-exported above.


# ---------------------------------------------------------------------------
# !convert — конвертер единиц измерения (чистая математика, без API)
# ---------------------------------------------------------------------------

# Словарь коэффициентов: все значения приведены к базовой единице.
# Формат: "единица" → (множитель_к_базе, "базовая_группа")
_CONVERT_UNITS: dict[str, tuple[float, str]] = {
    # Длина → метры
    "km": (1000.0, "m"),
    "m": (1.0, "m"),
    "cm": (0.01, "m"),
    "mm": (0.001, "m"),
    "mi": (1609.344, "m"),
    "ft": (0.3048, "m"),
    "in": (0.0254, "m"),
    "yd": (0.9144, "m"),
    # Масса → килограммы
    "kg": (1.0, "kg"),
    "g": (0.001, "kg"),
    "lb": (0.453592, "kg"),
    "oz": (0.028350, "kg"),
    # Объём → литры
    "l": (1.0, "l"),
    "ml": (0.001, "l"),
    "gal": (3.78541, "l"),
    "pt": (0.473176, "l"),
    # Скорость — база м/с (для согласованности)
    "kmh": (1.0 / 3.6, "speed"),
    "mph": (0.44704, "speed"),
    "ms": (1.0, "speed"),
    "kn": (0.514444, "speed"),
}

# Алиасы: разные варианты написания → канонический ключ
_CONVERT_ALIASES: dict[str, str] = {
    "kilometer": "km",
    "kilometers": "km",
    "kilometre": "km",
    "kilometres": "km",
    "meter": "m",
    "meters": "m",
    "metre": "m",
    "metres": "m",
    "centimeter": "cm",
    "centimeters": "cm",
    "millimeter": "mm",
    "millimeters": "mm",
    "mile": "mi",
    "miles": "mi",
    "foot": "ft",
    "feet": "ft",
    "inch": "in",
    "inches": "in",
    "yard": "yd",
    "yards": "yd",
    "kilogram": "kg",
    "kilograms": "kg",
    "gram": "g",
    "grams": "g",
    "pound": "lb",
    "pounds": "lb",
    "lbs": "lb",
    "ounce": "oz",
    "ounces": "oz",
    "liter": "l",
    "liters": "l",
    "litre": "l",
    "litres": "l",
    "milliliter": "ml",
    "milliliters": "ml",
    "gallon": "gal",
    "gallons": "gal",
    "pint": "pt",
    "pints": "pt",
    "km/h": "kmh",
    "m/s": "ms",
    "knot": "kn",
    "knots": "kn",
    # Температура
    "c": "c",
    "celsius": "c",
    "f": "f",
    "fahrenheit": "f",
    "k": "k",
    "kelvin": "k",
    "°c": "c",
    "°f": "f",
}

# Группа температурных единиц — нелинейное преобразование
_TEMP_UNITS = {"c", "f", "k"}


def _normalize_unit(raw: str) -> str:
    """Нормализует строку единицы: lower + алиасы → канонический ключ."""
    key = raw.lower().strip()
    return _CONVERT_ALIASES.get(key, key)


def _convert_temperature(value: float, src: str, dst: str) -> float:
    """Конвертация температуры между C / F / K."""
    # Шаг 1: приводим к Celsius
    if src == "c":
        celsius = value
    elif src == "f":
        celsius = (value - 32) * 5.0 / 9.0
    elif src == "k":
        celsius = value - 273.15
    else:
        raise ValueError(f"Неизвестная единица температуры: {src}")
    # Шаг 2: из Celsius в dst
    if dst == "c":
        return celsius
    if dst == "f":
        return celsius * 9.0 / 5.0 + 32
    if dst == "k":
        return celsius + 273.15
    raise ValueError(f"Неизвестная единица температуры: {dst}")


def _do_convert(value: float, src: str, dst: str) -> float:
    """
    Конвертирует value из src в dst.
    Кидает ValueError при несовместимых или неизвестных единицах.
    """
    src_n = _normalize_unit(src)
    dst_n = _normalize_unit(dst)

    # Температура — отдельная ветка (нелинейно)
    if src_n in _TEMP_UNITS or dst_n in _TEMP_UNITS:
        if src_n not in _TEMP_UNITS or dst_n not in _TEMP_UNITS:
            raise ValueError("Нельзя конвертировать температуру и другие единицы вместе.")
        return _convert_temperature(value, src_n, dst_n)

    # Обычные единицы через базовый коэффициент
    if src_n not in _CONVERT_UNITS:
        raise ValueError(f"Неизвестная единица: `{src}`")
    if dst_n not in _CONVERT_UNITS:
        raise ValueError(f"Неизвестная единица: `{dst}`")

    src_factor, src_base = _CONVERT_UNITS[src_n]
    dst_factor, dst_base = _CONVERT_UNITS[dst_n]

    if src_base != dst_base:
        raise ValueError(f"Несовместимые единицы: `{src}` ({src_base}) и `{dst}` ({dst_base})")

    # value * src_factor → базовая единица → / dst_factor → dst
    return value * src_factor / dst_factor


def _format_convert_result(result: float) -> str:
    """Форматирует число: до 6 значащих цифр, без лишних нулей."""
    if result == int(result) and abs(result) < 1e12:
        return str(int(result))
    return f"{result:.6g}"


_CONVERT_HELP = (
    "**Использование:** `!convert <число> <из> <в>`\n\n"
    "**Примеры:**\n"
    "`!convert 100 km mi` → 62.14 mi\n"
    "`!convert 72 F C` → 22.22 °C\n"
    "`!convert 5 kg lb` → 11.02 lb\n"
    "`!convert 3.5 L gal` → 0.924 gal\n\n"
    "**Поддерживаемые единицы:**\n"
    "Длина: `km m cm mm mi ft in yd`\n"
    "Масса: `kg g lb oz`\n"
    "Объём: `L mL gal pt`\n"
    "Скорость: `kmh mph m/s kn`\n"
    "Температура: `C F K`"
)


async def handle_convert(bot: "KraabUserbot", message: Message) -> None:
    """Конвертер единиц измерения без внешних API (!convert)."""
    raw_args = bot._get_command_args(message).strip()

    if not raw_args:
        await message.reply(_CONVERT_HELP)
        return

    parts = raw_args.split()
    if len(parts) != 3:
        raise UserInputError(
            user_message=("❌ Формат: `!convert <число> <из> <в>`\nНапример: `!convert 100 km mi`")
        )

    value_str, src_raw, dst_raw = parts

    # Парсим число; разрешаем запятую как десятичный разделитель
    try:
        value = float(value_str.replace(",", "."))
    except ValueError:
        raise UserInputError(user_message=f"❌ Не могу разобрать число: `{value_str}`")

    try:
        result = _do_convert(value, src_raw, dst_raw)
    except ValueError as exc:
        raise UserInputError(user_message=f"❌ {exc}")

    # Красивый символ для температурных единиц
    dst_n = _normalize_unit(dst_raw)
    if dst_n == "c":
        unit_symbol = "°C"
    elif dst_n == "f":
        unit_symbol = "°F"
    elif dst_n == "k":
        unit_symbol = "K"
    else:
        unit_symbol = dst_raw

    result_str = _format_convert_result(result)
    src_display = src_raw.upper() if _normalize_unit(src_raw) in _TEMP_UNITS else src_raw
    value_display = _format_convert_result(value)

    await message.reply(f"🔢 **{value_display} {src_display}** = **{result_str} {unit_symbol}**")


# ---------------------------------------------------------------------------
# !color — конвертер цветов: HEX ↔ RGB ↔ HSL + CSS named colors
# ---------------------------------------------------------------------------

# CSS named colors — стандартные 140 именованных цветов из CSS3
_CSS_NAMED_COLORS: dict[str, tuple[int, int, int]] = {
    "aliceblue": (240, 248, 255),
    "antiquewhite": (250, 235, 215),
    "aqua": (0, 255, 255),
    "aquamarine": (127, 255, 212),
    "azure": (240, 255, 255),
    "beige": (245, 245, 220),
    "bisque": (255, 228, 196),
    "black": (0, 0, 0),
    "blanchedalmond": (255, 235, 205),
    "blue": (0, 0, 255),
    "blueviolet": (138, 43, 226),
    "brown": (165, 42, 42),
    "burlywood": (222, 184, 135),
    "cadetblue": (95, 158, 160),
    "chartreuse": (127, 255, 0),
    "chocolate": (210, 105, 30),
    "coral": (255, 127, 80),
    "cornflowerblue": (100, 149, 237),
    "cornsilk": (255, 248, 220),
    "crimson": (220, 20, 60),
    "cyan": (0, 255, 255),
    "darkblue": (0, 0, 139),
    "darkcyan": (0, 139, 139),
    "darkgoldenrod": (184, 134, 11),
    "darkgray": (169, 169, 169),
    "darkgreen": (0, 100, 0),
    "darkgrey": (169, 169, 169),
    "darkkhaki": (189, 183, 107),
    "darkmagenta": (139, 0, 139),
    "darkolivegreen": (85, 107, 47),
    "darkorange": (255, 140, 0),
    "darkorchid": (153, 50, 204),
    "darkred": (139, 0, 0),
    "darksalmon": (233, 150, 122),
    "darkseagreen": (143, 188, 143),
    "darkslateblue": (72, 61, 139),
    "darkslategray": (47, 79, 79),
    "darkslategrey": (47, 79, 79),
    "darkturquoise": (0, 206, 209),
    "darkviolet": (148, 0, 211),
    "deeppink": (255, 20, 147),
    "deepskyblue": (0, 191, 255),
    "dimgray": (105, 105, 105),
    "dimgrey": (105, 105, 105),
    "dodgerblue": (30, 144, 255),
    "firebrick": (178, 34, 34),
    "floralwhite": (255, 250, 240),
    "forestgreen": (34, 139, 34),
    "fuchsia": (255, 0, 255),
    "gainsboro": (220, 220, 220),
    "ghostwhite": (248, 248, 255),
    "gold": (255, 215, 0),
    "goldenrod": (218, 165, 32),
    "gray": (128, 128, 128),
    "green": (0, 128, 0),
    "greenyellow": (173, 255, 47),
    "grey": (128, 128, 128),
    "honeydew": (240, 255, 240),
    "hotpink": (255, 105, 180),
    "indianred": (205, 92, 92),
    "indigo": (75, 0, 130),
    "ivory": (255, 255, 240),
    "khaki": (240, 230, 140),
    "lavender": (230, 230, 250),
    "lavenderblush": (255, 240, 245),
    "lawngreen": (124, 252, 0),
    "lemonchiffon": (255, 250, 205),
    "lightblue": (173, 216, 230),
    "lightcoral": (240, 128, 128),
    "lightcyan": (224, 255, 255),
    "lightgoldenrodyellow": (250, 250, 210),
    "lightgray": (211, 211, 211),
    "lightgreen": (144, 238, 144),
    "lightgrey": (211, 211, 211),
    "lightpink": (255, 182, 193),
    "lightsalmon": (255, 160, 122),
    "lightseagreen": (32, 178, 170),
    "lightskyblue": (135, 206, 250),
    "lightslategray": (119, 136, 153),
    "lightslategrey": (119, 136, 153),
    "lightsteelblue": (176, 196, 222),
    "lightyellow": (255, 255, 224),
    "lime": (0, 255, 0),
    "limegreen": (50, 205, 50),
    "linen": (250, 240, 230),
    "magenta": (255, 0, 255),
    "maroon": (128, 0, 0),
    "mediumaquamarine": (102, 205, 170),
    "mediumblue": (0, 0, 205),
    "mediumorchid": (186, 85, 211),
    "mediumpurple": (147, 112, 219),
    "mediumseagreen": (60, 179, 113),
    "mediumslateblue": (123, 104, 238),
    "mediumspringgreen": (0, 250, 154),
    "mediumturquoise": (72, 209, 204),
    "mediumvioletred": (199, 21, 133),
    "midnightblue": (25, 25, 112),
    "mintcream": (245, 255, 250),
    "mistyrose": (255, 228, 225),
    "moccasin": (255, 228, 181),
    "navajowhite": (255, 222, 173),
    "navy": (0, 0, 128),
    "oldlace": (253, 245, 230),
    "olive": (128, 128, 0),
    "olivedrab": (107, 142, 35),
    "orange": (255, 165, 0),
    "orangered": (255, 69, 0),
    "orchid": (218, 112, 214),
    "palegoldenrod": (238, 232, 170),
    "palegreen": (152, 251, 152),
    "paleturquoise": (175, 238, 238),
    "palevioletred": (219, 112, 147),
    "papayawhip": (255, 239, 213),
    "peachpuff": (255, 218, 185),
    "peru": (205, 133, 63),
    "pink": (255, 192, 203),
    "plum": (221, 160, 221),
    "powderblue": (176, 224, 230),
    "purple": (128, 0, 128),
    "rebeccapurple": (102, 51, 153),
    "red": (255, 0, 0),
    "rosybrown": (188, 143, 143),
    "royalblue": (65, 105, 225),
    "saddlebrown": (139, 69, 19),
    "salmon": (250, 128, 114),
    "sandybrown": (244, 164, 96),
    "seagreen": (46, 139, 87),
    "seashell": (255, 245, 238),
    "sienna": (160, 82, 45),
    "silver": (192, 192, 192),
    "skyblue": (135, 206, 235),
    "slateblue": (106, 90, 205),
    "slategray": (112, 128, 144),
    "slategrey": (112, 128, 144),
    "snow": (255, 250, 250),
    "springgreen": (0, 255, 127),
    "steelblue": (70, 130, 180),
    "tan": (210, 180, 140),
    "teal": (0, 128, 128),
    "thistle": (216, 191, 216),
    "tomato": (255, 99, 71),
    "turquoise": (64, 224, 208),
    "violet": (238, 130, 238),
    "wheat": (245, 222, 179),
    "white": (255, 255, 255),
    "whitesmoke": (245, 245, 245),
    "yellow": (255, 255, 0),
    "yellowgreen": (154, 205, 50),
}


def _rgb_to_hsl(r: int, g: int, b: int) -> tuple[int, int, int]:
    """Конвертирует RGB (0–255) в HSL (H: 0–360°, S: 0–100%, L: 0–100%)."""
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    cmax = max(rf, gf, bf)
    cmin = min(rf, gf, bf)
    delta = cmax - cmin

    # Светлота
    l_val = (cmax + cmin) / 2.0

    # Насыщенность
    if delta == 0.0:
        s_val = 0.0
    else:
        s_val = delta / (1.0 - abs(2.0 * l_val - 1.0))

    # Тон
    if delta == 0.0:
        h_val = 0.0
    elif cmax == rf:
        h_val = 60.0 * (((gf - bf) / delta) % 6)
    elif cmax == gf:
        h_val = 60.0 * (((bf - rf) / delta) + 2.0)
    else:
        h_val = 60.0 * (((rf - gf) / delta) + 4.0)

    return round(h_val), round(s_val * 100), round(l_val * 100)


def _parse_color_input(raw: str) -> tuple[int, int, int] | None:
    """
    Разбирает строку с цветом и возвращает (R, G, B) или None.

    Поддерживаемые форматы:
      - #RRGGBB или #RGB (hex)
      - rgb(R, G, B) или rgb(R,G,B)
      - CSS named color (red, blue, tomato, ...)
    """
    s = raw.strip()

    # HEX: #RRGGBB или #RGB
    hex_match = re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", s)
    if hex_match:
        h = hex_match.group(1)
        if len(h) == 3:
            # Расширяем: #ABC → #AABBCC
            h = h[0] * 2 + h[1] * 2 + h[2] * 2
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    # RGB: rgb(255, 87, 51) или rgb(255,87,51)
    rgb_match = re.fullmatch(
        r"rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)", s, re.IGNORECASE
    )
    if rgb_match:
        r_val = int(rgb_match.group(1))
        g_val = int(rgb_match.group(2))
        b_val = int(rgb_match.group(3))
        if 0 <= r_val <= 255 and 0 <= g_val <= 255 and 0 <= b_val <= 255:
            return r_val, g_val, b_val
        return None  # значения вне допустимого диапазона

    # CSS named color
    name = s.lower().replace("-", "").replace(" ", "")
    if name in _CSS_NAMED_COLORS:
        return _CSS_NAMED_COLORS[name]

    return None


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    """Конвертирует RGB в HEX строку вида #RRGGBB (верхний регистр)."""
    return f"#{r:02X}{g:02X}{b:02X}"


async def handle_color(bot: "KraabUserbot", message: Message) -> None:
    """
    !color <цвет> — конвертер цветов между форматами HEX, RGB и HSL.

    Поддерживаемые форматы ввода:
      !color #FF5733        → RGB(255, 87, 51), HSL(11°, 100%, 60%)
      !color #F53           → раскрывает до #FF5533
      !color rgb(255,87,51) → #FF5733, HSL(11°, 100%, 60%)
      !color red            → #FF0000, RGB(255, 0, 0), HSL(0°, 100%, 50%)

    Поддерживается 140 стандартных CSS named colors.
    """
    raw = bot._get_command_args(message).strip()

    if not raw:
        raise UserInputError(
            user_message=(
                "🎨 **!color — конвертер цветов**\n\n"
                "Форматы ввода:\n"
                "`!color #FF5733`        → RGB + HSL\n"
                "`!color rgb(255,87,51)` → HEX + HSL\n"
                "`!color red`            → HEX + RGB + HSL\n\n"
                "Поддерживаются: HEX (#RRGGBB, #RGB), rgb(...), CSS named colors"
            )
        )

    parsed = _parse_color_input(raw)
    if parsed is None:
        raise UserInputError(
            user_message=(
                f"❌ Не удалось распознать цвет: `{raw}`\n\n"
                "Допустимые форматы: `#FF5733`, `#F57`, `rgb(255,87,51)`, `red`"
            )
        )

    r, g, b = parsed
    hex_val = _rgb_to_hex(r, g, b)
    h_deg, s_pct, l_pct = _rgb_to_hsl(r, g, b)

    # Определяем тип ввода, чтобы не дублировать исходный формат в выводе
    s_lower = raw.strip().lower()
    is_hex = s_lower.startswith("#")
    is_rgb_fmt = s_lower.startswith("rgb(")
    is_named = not is_hex and not is_rgb_fmt

    lines: list[str] = [f"🎨 Цвет: `{raw}`\n"]

    if is_hex or is_named:
        lines.append(f"RGB: `rgb({r}, {g}, {b})`")
    if is_rgb_fmt or is_named:
        lines.append(f"HEX: `{hex_val}`")
    lines.append(f"HSL: `hsl({h_deg}°, {s_pct}%, {l_pct}%)`")

    await message.reply("\n".join(lines))


# ---------------------------------------------------------------------------
# Словарь эмодзи (~200 записей) — keyword → [emoji, ...]
# ---------------------------------------------------------------------------

_EMOJI_DB: dict[str, list[str]] = {
    # Огонь / энергия
    "fire": ["🔥"],
    "flame": ["🔥"],
    "hot": ["🔥", "🌡️"],
    # Сердце / любовь
    "heart": ["❤️", "💜", "💙", "💚", "🖤", "🤍", "🧡", "💛", "💗", "💓", "💞", "💕", "💔", "❣️"],
    "love": ["❤️", "💕", "😍", "😘", "🥰"],
    "kiss": ["😘", "💋", "😗"],
    "hug": ["🤗"],
    # Кошки
    "cat": ["🐱", "🐈", "😺", "😸", "😹", "😻", "😼", "😽", "🙀", "😿", "😾"],
    "kitten": ["🐱", "😺"],
    "meow": ["🐱", "😺"],
    # Собаки
    "dog": ["🐶", "🐕", "🦮", "🐩"],
    "puppy": ["🐶"],
    "woof": ["🐶"],
    # Смех / радость
    "laugh": ["😂", "🤣", "😄", "😁"],
    "happy": ["😊", "😄", "😁", "🙂", "😀"],
    "joy": ["😂", "🥳"],
    "smile": ["😊", "🙂", "😀", "😁"],
    "lol": ["😂", "🤣"],
    "haha": ["😂", "🤣"],
    # Грусть / слёзы
    "sad": ["😢", "😭", "😔", "😞"],
    "cry": ["😢", "😭"],
    "tear": ["😢", "😭"],
    # Злость
    "angry": ["😠", "😡", "🤬"],
    "rage": ["😡", "🤬"],
    "mad": ["😠", "😡"],
    # Удивление
    "surprise": ["😮", "😲", "🤯"],
    "shock": ["😱", "😨", "🤯"],
    "wow": ["😮", "🤩", "😲"],
    "mind": ["🤯"],
    # Испуг
    "fear": ["😨", "😰", "😱"],
    "scared": ["😨", "😱"],
    # Сон
    "sleep": ["😴", "💤"],
    "tired": ["😴", "🥱"],
    "yawn": ["🥱"],
    # Еда
    "pizza": ["🍕"],
    "burger": ["🍔"],
    "taco": ["🌮"],
    "sushi": ["🍣"],
    "ramen": ["🍜"],
    "cake": ["🎂", "🍰"],
    "coffee": ["☕", "🍵"],
    "tea": ["🍵", "🫖"],
    "beer": ["🍺", "🍻"],
    "wine": ["🍷"],
    "cocktail": ["🍹", "🍸"],
    "cookie": ["🍪"],
    "bread": ["🍞"],
    "apple": ["🍎"],
    "banana": ["🍌"],
    "strawberry": ["🍓"],
    "watermelon": ["🍉"],
    "grapes": ["🍇"],
    "mango": ["🥭"],
    "avocado": ["🥑"],
    "salad": ["🥗"],
    "chicken": ["🍗"],
    "steak": ["🥩"],
    "egg": ["🥚"],
    "icecream": ["🍦", "🍧", "🍨"],
    "chocolate": ["🍫"],
    "candy": ["🍬", "🍭"],
    "donut": ["🍩"],
    # Природа / животные
    "sun": ["☀️", "🌞"],
    "moon": ["🌙", "🌕", "🌝"],
    "star": ["⭐", "🌟", "✨", "💫"],
    "cloud": ["☁️", "⛅"],
    "rain": ["🌧️", "🌂"],
    "snow": ["❄️", "☃️", "🌨️"],
    "thunder": ["⛈️", "🌩️"],
    "rainbow": ["🌈"],
    "flower": ["🌸", "🌺", "🌼", "🌻", "🌹", "💐"],
    "rose": ["🌹"],
    "leaf": ["🍀", "🍃", "🌿"],
    "tree": ["🌳", "🌲", "🎄"],
    "mountain": ["⛰️", "🏔️"],
    "ocean": ["🌊", "🏖️"],
    "water": ["💧", "🌊"],
    "earth": ["🌍", "🌎", "🌏"],
    "fish": ["🐟", "🐠", "🐡"],
    "bird": ["🐦", "🦜", "🦅", "🦆"],
    "butterfly": ["🦋"],
    "bee": ["🐝"],
    "snake": ["🐍"],
    "frog": ["🐸"],
    "rabbit": ["🐰", "🐇"],
    "bear": ["🐻"],
    "panda": ["🐼"],
    "fox": ["🦊"],
    "wolf": ["🐺"],
    "lion": ["🦁"],
    "tiger": ["🐯"],
    "horse": ["🐴", "🦄"],
    "unicorn": ["🦄"],
    "monkey": ["🐵", "🙈", "🙉", "🙊"],
    "pig": ["🐷", "🐖"],
    "cow": ["🐮", "🐄"],
    "elephant": ["🐘"],
    "dolphin": ["🐬"],
    "shark": ["🦈"],
    "turtle": ["🐢"],
    "crab": ["🦀"],
    "lobster": ["🦞"],
    "octopus": ["🐙"],
    # Жесты / реакции
    "ok": ["👌", "✅"],
    "yes": ["✅", "👍"],
    "no": ["❌", "👎"],
    "thumbsup": ["👍"],
    "thumbsdown": ["👎"],
    "clap": ["👏"],
    "wave": ["👋"],
    "point": ["👉", "👆", "👇", "👈"],
    "muscle": ["💪"],
    "fist": ["✊", "👊"],
    "peace": ["✌️", "☮️"],
    "pray": ["🙏"],
    "eyes": ["👀"],
    "think": ["🤔"],
    "shrug": ["🤷"],
    "facepalm": ["🤦"],
    "celebrate": ["🎉", "🥳", "🎊"],
    "party": ["🎉", "🎊", "🥳"],
    # Техника
    "phone": ["📱", "☎️"],
    "computer": ["💻", "🖥️"],
    "camera": ["📷", "📸"],
    "music": ["🎵", "🎶"],
    "headphones": ["🎧"],
    "rocket": ["🚀"],
    "robot": ["🤖"],
    "alien": ["👽"],
    "ghost": ["👻"],
    "skull": ["💀"],
    "bomb": ["💣"],
    "lightning": ["⚡"],
    "magnet": ["🧲"],
    "lock": ["🔒"],
    "key": ["🔑", "🗝️"],
    "money": ["💰", "💵", "💸"],
    "gem": ["💎"],
    "crown": ["👑"],
    "trophy": ["🏆"],
    "medal": ["🥇", "🥈", "🥉"],
    "sword": ["⚔️"],
    "shield": ["🛡️"],
    "magic": ["✨", "🪄"],
    "book": ["📚", "📖"],
    "pencil": ["✏️", "📝"],
    "clock": ["🕐", "⏰", "⏱️"],
    "calendar": ["📅", "📆"],
    "mail": ["📧", "✉️"],
    "bell": ["🔔"],
    "flag": ["🚩", "🏁"],
    "search": ["🔍", "🔎"],
    "bulb": ["💡"],
    "warning": ["⚠️"],
    "forbidden": ["🚫"],
    "check": ["✅", "☑️"],
    "cross": ["❌"],
    "plus": ["➕"],
    "minus": ["➖"],
    "infinity": ["♾️"],
    # Транспорт
    "car": ["🚗", "🚙"],
    "bus": ["🚌"],
    "plane": ["✈️"],
    "ship": ["🚢"],
    "bike": ["🚲", "🛵"],
    "train": ["🚂", "🚆"],
    # Разное
    "poop": ["💩"],
    "nerd": ["🤓"],
    "cool": ["😎"],
    "sick": ["🤒", "🤧"],
    "mask": ["😷"],
    "zombie": ["🧟"],
    "vampire": ["🧛"],
    "mermaid": ["🧜"],
    "fairy": ["🧚"],
    "angel": ["😇"],
    "devil": ["😈"],
    "clown": ["🤡"],
    "santa": ["🎅"],
    "snowman": ["☃️"],
    "christmas": ["🎄", "🎅", "🎁"],
    "gift": ["🎁"],
    "balloon": ["🎈"],
    "confetti": ["🎊", "🎉"],
    "sparkles": ["✨"],
    "diamond": ["💎"],
}


def _emoji_search(query: str) -> list[str]:
    """
    Ищет эмодзи по ключевому слову.
    Возвращает дедуплицированный список, сохраняя порядок первого появления.
    Сначала точное совпадение, затем частичные.
    """
    q = query.strip().lower()
    seen: set[str] = set()
    results: list[str] = []

    # 1. Точное совпадение ключа
    if q in _EMOJI_DB:
        for em in _EMOJI_DB[q]:
            if em not in seen:
                seen.add(em)
                results.append(em)

    # 2. Частичное совпадение (ключ содержит запрос или запрос содержит ключ)
    for key, emojis in _EMOJI_DB.items():
        if key == q:
            continue  # уже обработан выше
        if q in key or key in q:
            for em in emojis:
                if em not in seen:
                    seen.add(em)
                    results.append(em)

    return results


async def handle_emoji(bot: "KraabUserbot", message: Message) -> None:
    """
    Поиск эмодзи по текстовому описанию.

    Синтаксис:
      !emoji <keyword>         — первые совпадения (до 5)
      !emoji search <keyword>  — все варианты

    Примеры:
      !emoji fire        → 🔥
      !emoji heart       → ❤️ 💜 💙 ...
      !emoji search cat  → 🐱 🐈 😺 😸 ...
    """
    raw = bot._get_command_args(message).strip()

    if not raw:
        await message.reply(
            "😊 **!emoji** — поиск эмодзи по описанию\n\n"
            "`!emoji <слово>` — первое совпадение\n"
            "`!emoji search <слово>` — все варианты\n\n"
            "_Примеры: `!emoji fire`, `!emoji heart`, `!emoji search cat`_"
        )
        return

    # Разбираем подкоманду search
    parts = raw.split(maxsplit=1)
    show_all = parts[0].lower() == "search"
    if show_all:
        query = parts[1].strip() if len(parts) > 1 else ""
    else:
        query = raw

    if not query:
        await message.reply("🔍 Укажи слово для поиска: `!emoji search <слово>`")
        return

    matches = _emoji_search(query)

    if not matches:
        await message.reply(
            f"🤷 Эмодзи для «{query}» не найдены.\n"
            "_Попробуй синоним на английском: fire, heart, cat, smile..._"
        )
        return

    if show_all:
        # Все варианты в одну строку
        line = " ".join(matches)
        await message.reply(f"🔍 `{query}` → {line}")
    else:
        # Первые 5 для краткого ответа
        preview = " ".join(matches[:5])
        if len(matches) > 5:
            suffix = f" _( +{len(matches) - 5} ещё — `!emoji search {query}`)_"
        else:
            suffix = ""
        await message.reply(f"{preview}{suffix}")


# ---------------------------------------------------------------------------
# handle_news — быстрые новости через AI
# ---------------------------------------------------------------------------

# Синонимы языков для !news ru / !news en
_NEWS_LANG_MAP: dict[str, str] = {
    "ru": "на русском языке",
    "рус": "на русском языке",
    "rus": "на русском языке",
    "en": "на английском языке",
    "eng": "на английском языке",
}

# Топик-тематики, которые пользователь может запросить
_NEWS_KNOWN_TOPICS: frozenset[str] = frozenset(
    {
        "crypto",
        "крипто",
        "криптовалюта",
        "ai",
        "ии",
        "ml",
        "tech",
        "технологии",
        "технология",
        "finance",
        "финансы",
        "финансовые",
        "science",
        "наука",
        "politics",
        "политика",
        "business",
        "бизнес",
        "sports",
        "спорт",
        "gaming",
        "игры",
        "space",
        "космос",
        "health",
        "здоровье",
        "world",
        "мир",
        "russia",
        "россия",
        "usa",
        "сша",
    }
)


async def handle_news(bot: "KraabUserbot", message: Message) -> None:
    """
    Быстрые новости через AI (web_search).

    Форматы:
      !news               — топ-5 главных новостей за сегодня
      !news <тема>        — новости по теме (crypto, AI, tech, финансы…)
      !news ru            — новости на русском
    """
    raw = bot._get_command_args(message).strip()

    # Разбираем аргументы: язык и тема
    lang_suffix = ""
    topic = "мировые события"  # дефолтная тема

    if raw:
        # Проверяем, является ли аргумент языком
        first_word = raw.split()[0].lower()
        if first_word in _NEWS_LANG_MAP:
            lang_suffix = f" {_NEWS_LANG_MAP[first_word]}"
            # Если после языка есть тема — берём её
            rest = raw[len(first_word) :].strip()
            if rest:
                topic = rest
            # Иначе тема — мировые события на указанном языке
        else:
            # Аргумент — тема
            topic = raw

    # Формируем промпт
    prompt = (
        f"Дай топ-5 главных новостей за сегодня по теме: {topic}. "
        f"Кратко, с источниками{lang_suffix}. "
        "Формат каждой новости: порядковый номер, заголовок, одно-два предложения сути, источник/URL."
    )

    # Изолированная сессия, чтобы не загрязнять основной контекст чата
    session_id = f"news_{message.chat.id}"

    # Индикатор загрузки
    display_topic = topic if topic != "мировые события" else "топ новостей"
    msg = await message.reply(f"📰 **Краб читает новости:** `{display_topic}`...")

    try:
        chunks: list[str] = []
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            disable_tools=False,  # нужен web_search
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()

        if not result:
            await msg.edit("❌ Не удалось получить новости. Попробуй позже.")
            return

        # Заголовок + тело
        header = f"📰 **{display_topic.capitalize()}**\n\n"
        full_text = header + result

        # Пагинация (Telegram лимит ~4096)
        parts = _split_text_for_telegram(full_text)
        total = len(parts)

        first = parts[0]
        if total > 1:
            first += f"\n\n_(часть 1/{total})_"
        await msg.edit(first)

        for i, part in enumerate(parts[1:], start=2):
            suffix = f"\n\n_(часть {i}/{total})_" if total > 2 else ""
            await message.reply(part + suffix)

    except Exception as exc:  # noqa: BLE001
        logger.error("handle_news_error", topic=topic, error=str(exc))
        await msg.edit(f"❌ Ошибка получения новостей: {exc}")


# !rate — extracted to commands/ai_commands.py (Phase 2 Wave 7, Session 27).
# Re-exported above (handle_rate, _RATE_CRYPTO_ALIASES, _RATE_MAX_ASSETS,
# _rate_asset_label, _build_rate_prompt).


# ---------------------------------------------------------------------------
# !say — тихая отправка сообщения от имени юзербота
# ---------------------------------------------------------------------------


async def handle_say(bot: "KraabUserbot", message: Message) -> None:
    """
    Отправляет сообщение от имени юзербота, удаляя команду из истории.

    Форматы:
      !say <текст>               — отправить в текущий чат
      !say <chat_id> <текст>     — отправить в другой чат (chat_id — число или @username)

    Полезно для «тихой» отправки без видимого !-command в истории чата.
    """
    raw = bot._get_command_args(message).strip()

    if not raw:
        raise UserInputError(
            user_message="❌ Использование: `!say <текст>` или `!say <chat_id> <текст>`"
        )

    # Определяем chat_id и текст
    # Если первый токен — число или @username — отправляем в другой чат
    parts = raw.split(maxsplit=1)
    target_chat: int | str = message.chat.id
    text = raw

    if len(parts) == 2:
        first = parts[0]
        # Числовой chat_id (может быть отрицательным)
        try:
            target_chat = int(first)
            text = parts[1]
        except ValueError:
            # @username или просто текст начинается с нечислового токена
            if first.startswith("@"):
                target_chat = first
                text = parts[1]
            # иначе — всё является текстом, отправляем в текущий чат

    if not text:
        raise UserInputError(user_message="❌ Текст сообщения не может быть пустым.")

    # Удаляем команду из истории чата (до отправки, чтобы не было паузы)
    try:
        await message.delete()
    except Exception:
        pass  # Нет прав на удаление — не критично

    # Отправляем сообщение
    try:
        await bot.client.send_message(chat_id=target_chat, text=text)
        logger.info("handle_say_sent", target_chat=target_chat, length=len(text))
    except Exception as exc:
        logger.error("handle_say_error", target_chat=target_chat, error=str(exc))
        # Если отправка в другой чат не удалась — уведомляем в текущем
        try:
            await bot.client.send_message(
                chat_id=message.chat.id,
                text=f"❌ Ошибка отправки в `{target_chat}`: {exc}",
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# handle_backup — экспорт всех persistent данных Краба в ZIP
# ---------------------------------------------------------------------------

# Файлы для резервной копии (относительно krab_runtime_state/)
_BACKUP_FILES = [
    "bookmarks.json",
    "chat_monitors.json",
    "command_aliases.json",
    "saved_stickers.json",
    "personal_todos.json",
    "code_snippets.json",
    "message_templates.json",
    "saved_quotes.json",
    "welcome_messages.json",
    "silence_schedule.json",
    "spam_filter_config.json",
    "swarm_memory.json",
    "swarm_channels.json",
]


async def handle_backup(bot: "KraabUserbot", message: Message) -> None:
    """
    Экспортирует все persistent данные Краба в ZIP-архив и отправляет в чат.

    !backup        — создать и отправить архив
    !backup list   — показать список файлов, которые войдут в архив
    """
    import tempfile
    import zipfile as _zipfile

    args = bot._get_command_args(message).strip().lower()

    # Базовая директория runtime state
    runtime_dir = pathlib.Path.home() / ".openclaw" / "krab_runtime_state"

    if args == "list":
        # Показываем какие файлы войдут в архив
        lines = ["📋 **Файлы в резервной копии:**\n"]
        found_count = 0
        missing_count = 0
        for fname in _BACKUP_FILES:
            fpath = runtime_dir / fname
            if fpath.exists():
                size_kb = fpath.stat().st_size / 1024
                lines.append(f"✅ `{fname}` ({size_kb:.1f} KB)")
                found_count += 1
            else:
                lines.append(f"⬜ `{fname}` _(отсутствует)_")
                missing_count += 1
        lines.append(f"\n**Итого:** {found_count} файлов найдено, {missing_count} отсутствуют.")
        await message.reply("\n".join(lines))
        return

    # Создаём ZIP-архив во временной директории
    status_msg = await message.reply("⏳ Создаю резервную копию данных Краба…")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            archive_name = f"krab_backup_{timestamp}.zip"
            archive_path = pathlib.Path(tmpdir) / archive_name

            included: list[str] = []
            skipped: list[str] = []

            with _zipfile.ZipFile(archive_path, "w", compression=_zipfile.ZIP_DEFLATED) as zf:
                for fname in _BACKUP_FILES:
                    fpath = runtime_dir / fname
                    if fpath.exists():
                        zf.write(fpath, arcname=fname)
                        included.append(fname)
                    else:
                        skipped.append(fname)

            if not included:
                await status_msg.edit(
                    "⚠️ Нет данных для резервной копии — ни один файл не найден.\n"
                    "Используй `!backup list` для проверки."
                )
                return

            # Размер архива
            archive_size_kb = archive_path.stat().st_size / 1024

            # Формируем подпись к документу
            caption_lines = [
                f"💾 **Krab Backup** `{timestamp}`",
                f"Файлов: {len(included)} | Размер: {archive_size_kb:.1f} KB",
            ]
            if skipped:
                caption_lines.append(f"Пропущено (нет): {', '.join(skipped)}")

            # Отправляем ZIP как документ
            await bot.client.send_document(
                chat_id=message.chat.id,
                document=str(archive_path),
                caption="\n".join(caption_lines),
                reply_to_message_id=message.id,
            )
            await status_msg.delete()

    except Exception as exc:  # noqa: BLE001
        logger.error("handle_backup_error", error=str(exc))
        await status_msg.edit(f"❌ Ошибка создания резервной копии: {str(exc)[:300]}")


# !explain — extracted to commands/ai_commands.py (Phase 2 Wave 7, Session 27).
# Re-exported above (handle_explain, _EXPLAIN_PROMPT).


# ---------------------------------------------------------------------------
# handle_id — показать ID текущего чата, себя, сообщения (если reply)
# ---------------------------------------------------------------------------


async def handle_id(bot: "KraabUserbot", message: Message) -> None:
    """Показать ID текущего чата, своего аккаунта и (если reply) сообщения и автора.

    Синтаксис:
      !id         — chat_id + свой user_id
      !id в reply — chat_id + свой user_id + message_id + user_id автора
    """
    # ID текущего чата
    chat_id = message.chat.id

    # Свой user_id
    me = await bot.client.get_me()
    my_user_id = me.id

    lines: list[str] = [
        "🆔 IDs",
        f"Chat: `{chat_id}`",
        f"User: `{my_user_id}`",
    ]

    # Если команда отправлена в reply — добавляем message_id и user_id автора
    reply = message.reply_to_message
    if reply is not None:
        lines.append(f"Message: `{reply.id}`")
        # Автор может быть user или анонимный канал/бот
        reply_from = reply.from_user
        if reply_from is not None:
            lines.append(f"Author: `{reply_from.id}`")

    await message.reply("\n".join(lines))


# ---------------------------------------------------------------------------
# handle_listen — управление режимом ответов в чате (active/mention-only/muted)
# ---------------------------------------------------------------------------


async def _handle_listen_list(bot: "KraabUserbot", message: Message) -> None:
    """Показать все чаты с явными правилами."""
    import datetime

    from ..core.chat_filter_config import chat_filter_config

    rules = chat_filter_config.list_rules()
    if not rules:
        await message.reply("📭 Нет явных правил. Все чаты используют дефолты.")
        return

    lines = ["🎛️ **Явные правила фильтра:**\n"]
    for r in rules[:30]:
        updated = datetime.datetime.fromtimestamp(r.updated_at).strftime("%Y-%m-%d %H:%M")
        lines.append(f"• `{r.chat_id}` → `{r.mode}` ({updated})")
    if len(rules) > 30:
        lines.append(f"... ещё +{len(rules) - 30}")

    await message.reply("\n".join(lines))


async def _handle_listen_stats(bot: "KraabUserbot", message: Message) -> None:
    """Показать статистику по режимам."""
    from ..core.chat_filter_config import chat_filter_config

    stats = chat_filter_config.stats()
    lines = ["📊 **Статистика фильтра:**\n"]
    lines.append(f"Всего правил: {stats['total_rules']}")
    for mode, count in sorted(stats.get("by_mode", {}).items()):
        lines.append(f"• `{mode}`: {count}")

    await message.reply("\n".join(lines))


async def handle_listen(bot: "KraabUserbot", message: Message) -> None:
    """Управление режимом ответов Краба в чате.

    Синтаксис:
      !listen                — показать текущий режим
      !listen active         — реагировать на все
      !listen mention-only   — только на @mention или reply
      !listen muted          — молчать
      !listen reset          — вернуть к дефолту
      !listen reload         — перезагрузить конфиг с диска
      !listen list           — все чаты с явными правилами
      !listen stats          — статистика по режимам
    """
    from ..core.chat_filter_config import chat_filter_config
    from ..core.command_registry import bump_command

    bump_command("listen")

    args = (bot._get_command_args(message) or "").strip().lower()
    chat_id = message.chat.id
    is_group = message.chat.type in ("group", "supergroup")

    # Специальные команды
    if args == "list":
        return await _handle_listen_list(bot, message)
    if args == "stats":
        return await _handle_listen_stats(bot, message)

    if args == "reload":
        changed = chat_filter_config.reload()
        total = chat_filter_config.stats().get("total_rules", 0)
        status = "🔄 (changed)" if changed else "✅ (no changes)"
        await message.reply(
            f"{status} Config reloaded. Total rules: {total}",
        )
        return

    # Управление режимом текущего чата
    if args in ("active", "mention-only", "muted"):
        chat_filter_config.set_mode(chat_id, args)
        mode_name = {
            "active": "все сообщения",
            "mention-only": "@mention и reply",
            "muted": "молчать",
        }[args]
        await message.reply(f"✅ Чат `{chat_id}`: {mode_name}")
        return

    if args == "reset":
        chat_filter_config.reset(chat_id)
        await message.reply(f"🔄 Чат `{chat_id}`: вернулся к дефолту")
        return

    # Показать текущий режим
    if not args:
        mode = chat_filter_config.get_mode(chat_id, is_group=is_group)
        mode_emoji = {"active": "🟢", "mention-only": "🟡", "muted": "🔴"}[mode]
        await message.reply(f"{mode_emoji} Текущий режим: `{mode}`")
        return

    # Неизвестная команда
    await message.reply(
        "❌ Неизвестный режим. Используйте: active, mention-only, muted, reset, reload, list, stats",
    )


# ── !filter — per-chat filter mode toggle (Chado §3 P2) ──────────────────────


async def handle_filter(bot: "KraabUserbot", message: Message) -> None:
    """Управление per-chat filter mode (Chado §3 P2).

    Синтаксис:
      !filter status         — показать текущий режим (=!filter без аргументов)
      !filter active         — реагировать на все сообщения
      !filter mention-only   — только на @mention или reply
      !filter muted          — молчать в этом чате
      !filter reset          — вернуть к дефолту

    Тонкий алиас !listen — делегирует ту же логику через chat_filter_config.
    """
    from ..core.chat_filter_config import chat_filter_config
    from ..core.command_registry import bump_command
    from ..core.message_priority_dispatcher import get_mode_for_chat

    bump_command("filter")

    raw = (bot._get_command_args(message) or "").strip().lower()
    # "status" — алиас для показа режима
    args = "" if raw == "status" else raw
    chat_id = message.chat.id
    is_group = message.chat.type in ("group", "supergroup")

    if args in ("active", "mention-only", "muted"):
        chat_filter_config.set_chat_mode(chat_id, args)
        mode_label = {
            "active": "все сообщения",
            "mention-only": "@mention и reply",
            "muted": "молчать",
        }[args]
        await message.reply(f"✅ Чат `{chat_id}`: режим → `{args}` ({mode_label})")
        return

    if args == "reset":
        chat_filter_config.reset(chat_id)
        await message.reply(f"🔄 Чат `{chat_id}`: режим сброшен к дефолту")
        return

    if not args:
        mode = get_mode_for_chat(chat_id, is_group=is_group)
        emoji = {"active": "🟢", "mention-only": "🟡", "muted": "🔴"}.get(mode, "⚪")
        await message.reply(
            f"{emoji} Текущий режим: `{mode}`\n\n"
            "Команды: `!filter active` · `!filter mention-only` · `!filter muted` · `!filter reset`"
        )
        return

    await message.reply(
        "❌ Неизвестный режим.\nИспользуйте: `status`, `active`, `mention-only`, `muted`, `reset`"
    )


# ── !chado — статус cross-AI синхронизации с Chado (Chado §9) ───────────────


async def handle_chado(bot: "KraabUserbot", message: Message) -> None:
    """Статус cross-AI синхронизации с Chado (Chado §9 P2).

    Субкоманды:
      !chado              — то же что !chado status
      !chado status       — last sync ts, кол-во сообщений Chado в archive.db,
                            последняя цитата, ссылка на crossteam-топик
      !chado ping         — отправить ping в Forum Topic crossteam
      !chado digest       — dry-run preview cron_chado_sync.py
    """
    from ..core.command_registry import bump_command

    bump_command("chado")

    raw = (bot._get_command_args(message) or "").strip().lower()
    sub = raw.split()[0] if raw else "status"

    if sub in ("", "status"):
        await _handle_chado_status(message)
    elif sub == "ping":
        await _handle_chado_ping(bot, message)
    elif sub == "digest":
        await _handle_chado_digest(message)
    else:
        await message.reply(
            "❌ Неизвестная субкоманда.\nИспользуйте: `!chado status` · `!chado ping` · `!chado digest`"
        )


async def _handle_chado_status(message: Message) -> None:
    """Показывает текущее состояние cross-AI sync с Chado."""
    import sqlite3
    from datetime import datetime, timezone
    from pathlib import Path

    from ..core.cross_ai_review import parse_review_bullets  # noqa: F401 — проверяем импорт

    # --- archive.db: count + last message from Chado ---
    db_path = Path.home() / ".openclaw" / "krab_memory" / "archive.db"
    chado_count = 0
    latest_quote = ""
    try:
        if db_path.exists():
            uri = f"file:{db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=1.5)
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE sender_name LIKE '%Chado%'"
                ).fetchone()
                chado_count = int(row[0]) if row else 0

                latest_row = conn.execute(
                    "SELECT text FROM messages WHERE sender_name LIKE '%Chado%'"
                    " ORDER BY date DESC LIMIT 1"
                ).fetchone()
                if latest_row and latest_row[0]:
                    latest_quote = str(latest_row[0])[:200]
            finally:
                conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("chado_status_archive_query_failed", error=str(exc))

    # --- last cross_ai_review usage (proactive_watch state) ---
    last_sync_ts = ""
    try:
        state_file = Path.home() / ".openclaw" / "krab_runtime_state" / "proactive_watch_state.json"
        if state_file.exists():
            import json as _json

            state = _json.loads(state_file.read_text())
            ts_raw = state.get("last_cross_ai_review_ts") or state.get("cross_ai_review_last_ts")
            if ts_raw:
                try:
                    dt = datetime.fromtimestamp(float(ts_raw), tz=timezone.utc)
                    last_sync_ts = dt.strftime("%Y-%m-%d %H:%M UTC")
                except Exception:  # noqa: BLE001
                    last_sync_ts = str(ts_raw)[:20]
    except Exception:  # noqa: BLE001
        pass

    # --- crossteam topic link ---
    crossteam_link = ""
    try:
        from ..core.swarm_channels import swarm_channels

        forum_id = getattr(swarm_channels, "_forum_chat_id", None)
        topics: dict = getattr(swarm_channels, "_team_topics", {})
        ct_topic = topics.get("crossteam")
        if forum_id and ct_topic:
            crossteam_link = f"t.me/c/{str(forum_id).lstrip('-100')}/{ct_topic}"
    except Exception:  # noqa: BLE001
        pass

    # --- next scheduled chado-sync ---
    next_trigger = "—"
    try:
        from ..core.scheduler import krab_scheduler

        jobs = krab_scheduler.list_jobs() if hasattr(krab_scheduler, "list_jobs") else []
        for job in jobs:
            name = (getattr(job, "name", None) or "").lower()
            if "chado" in name or "cross_ai" in name:
                next_run = getattr(job, "next_run_time", None)
                if next_run:
                    next_trigger = str(next_run)[:16]
                break
    except Exception:  # noqa: BLE001
        pass

    lines = ["🤝 **Chado Cross-AI Sync — статус**", ""]
    lines.append(f"**Последний sync:** {last_sync_ts or '—'}")
    lines.append(f"**Сообщений Chado в archive.db:** `{chado_count}`")
    if latest_quote:
        lines.append(f"**Последняя цитата:**\n_{latest_quote}_")
    else:
        lines.append("**Последняя цитата:** —")
    lines.append(f"**Crossteam топик:** {crossteam_link or '— (не настроен)'}")
    lines.append(f"**Следующий запуск sync:** {next_trigger}")

    await message.reply("\n".join(lines))


async def _handle_chado_ping(bot: "KraabUserbot", message: Message) -> None:
    """Отправляет ping в Forum Topic crossteam через swarm_channels."""
    from datetime import datetime, timezone

    from ..core.swarm_channels import swarm_channels

    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ping_text = (
        f"🤝 [Chado Ping] Cross-AI sync check\n"
        f"Инициатор: owner via `!chado ping`\n"
        f"Время: {ts}\n\n"
        "Chado, ты здесь? Синхронизация активна."
    )

    sent = False
    try:
        # Используем _resolve_destination + _send_message (публичный контракт через broadcast_delegation)
        chat_id, topic_id = swarm_channels._resolve_destination("crossteam")
        if chat_id:
            await swarm_channels._send_message(chat_id, ping_text, topic_id=topic_id)
            sent = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("chado_ping_broadcast_failed", error=str(exc))

    if sent:
        await message.reply("✅ Ping отправлен в crossteam Forum Topic.")
    else:
        await message.reply(
            "⚠️ Crossteam топик не настроен — ping не отправлен.\n"
            "Используйте `!swarm setup` для настройки Forum Topics."
        )


async def _handle_chado_digest(message: Message) -> None:
    """Dry-run preview cron_chado_sync.py."""
    import importlib.util
    from pathlib import Path

    script_path = Path(__file__).parent.parent.parent / "scripts" / "cron_chado_sync.py"

    if not script_path.exists():
        await message.reply(
            "⚠️ `scripts/cron_chado_sync.py` не найден.\n"
            "Digest недоступен — скрипт sync ещё не создан."
        )
        return

    try:
        spec = importlib.util.spec_from_file_location("cron_chado_sync", script_path)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

        if hasattr(mod, "dry_run_preview"):
            result = mod.dry_run_preview()
            preview = str(result)[:3000] if result else "— нет данных"
        else:
            preview = "⚠️ Функция `dry_run_preview()` не найдена в cron_chado_sync.py"
    except Exception as exc:  # noqa: BLE001
        logger.warning("chado_digest_dry_run_failed", error=str(exc))
        preview = f"❌ Ошибка dry-run: {exc}"

    await message.reply(f"📋 **Chado Digest (dry-run)**\n\n{preview}")


# ---------------------------------------------------------------------------
# !mem — moved to commands/memory_commands.py (Phase 2 Wave 5)
# ---------------------------------------------------------------------------


# ──────────────────────────────────────────────────────────────────────────────
# !trust — управление trusted guests allowlist (W10.1 bypass)
# ──────────────────────────────────────────────────────────────────────────────

_TRUST_HELP = """\
🔐 **!trust** — управление allowlist доверенных гостей (W10.1 bypass)

Команды (owner-only):
  `!trust add @username [user_id]` — добавить в текущем чате
  `!trust remove @username`        — удалить из текущего чата
  `!trust list`                    — список для текущего чата
  `!trust list all`                — список всех чатов

По умолчанию: `@dodik_ggt` разрешена в YMB FAMILY FOREVER и How2AI.
Trusted guest получает LLM-ответ даже без @mention Краба в группе.
"""


async def handle_trust(bot: "KraabUserbot", message: Message) -> None:
    """
    !trust add|remove|list — управление trusted_guests allowlist.

    Owner-only. Позволяет legit friends (Дашка @dodik_ggt и др.) получать
    LLM-ответы в группах, минуя W10.1 guest XOR gate.
    """
    from ..core.trusted_guests import trusted_guests  # noqa: PLC0415

    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        await message.reply("🔒 `!trust` доступен только владельцу.")
        return

    raw = str(message.text or "").strip()
    parts = raw.split(maxsplit=3)
    # parts[0] = "!trust", parts[1] = subcommand, parts[2] = @username, parts[3] = user_id
    sub = parts[1].strip().lower() if len(parts) > 1 else ""

    if not sub or sub == "help":
        await message.reply(_TRUST_HELP)
        return

    chat_id = message.chat.id

    # ── !trust list [all] ──────────────────────────────────────────────
    if sub == "list":
        scope = parts[2].strip().lower() if len(parts) > 2 else ""
        if scope == "all":
            all_data = trusted_guests.all_chats()
            if not all_data:
                await message.reply("📋 Trusted guests: пусто.")
                return
            lines = ["📋 **Trusted guests (все чаты):**", ""]
            for cid, entry in all_data.items():
                uids = entry.get("user_ids", [])
                unames = entry.get("usernames", [])
                lines.append(f"**Chat** `{cid}`:")
                for uid in uids:
                    lines.append(f"  • user_id={uid}")
                for uname in unames:
                    lines.append(f"  • {uname}")
                lines.append("")
            await message.reply("\n".join(lines).rstrip())
            return

        entries = trusted_guests.list_trusted(chat_id)
        if not entries:
            await message.reply(f"📋 Trusted guests в `{chat_id}`: пусто.")
            return
        lines = [f"📋 **Trusted guests в чате `{chat_id}`:**", ""]
        for e in entries:
            uid = e.get("user_id")
            uname = e.get("username") or "—"
            uid_str = f"user_id={uid}" if uid else "user_id=?"
            lines.append(f"  • {uname} ({uid_str})")
        await message.reply("\n".join(lines))
        return

    # ── !trust add @username [user_id] ────────────────────────────────
    if sub == "add":
        username_arg = parts[2].strip() if len(parts) > 2 else ""
        if not username_arg:
            await message.reply("❌ Формат: `!trust add @username [user_id]`")
            return
        user_id_arg = 0
        if len(parts) > 3:
            try:
                user_id_arg = int(parts[3].strip())
            except ValueError:
                await message.reply("❌ user_id должен быть числом.")
                return
        norm_uname = username_arg.lstrip("@").strip()
        trusted_guests.add_trusted(chat_id, user_id_arg, f"@{norm_uname}")
        uid_info = f", user_id={user_id_arg}" if user_id_arg else ""
        await message.reply(
            f"✅ `@{norm_uname}`{uid_info} добавлен в trusted guests чата `{chat_id}`.\n"
            f"Теперь получает LLM-ответы без @mention Краба."
        )
        return

    # ── !trust remove @username ────────────────────────────────────────
    if sub == "remove":
        username_arg = parts[2].strip() if len(parts) > 2 else ""
        if not username_arg:
            await message.reply("❌ Формат: `!trust remove @username`")
            return
        norm_uname = username_arg.lstrip("@").strip()
        trusted_guests.remove_trusted(chat_id, 0, f"@{norm_uname}")
        await message.reply(f"🗑️ `@{norm_uname}` удалён из trusted guests чата `{chat_id}`.")
        return


async def handle_proactivity(bot: "KraabUserbot", message: Message) -> None:
    """!proactivity — управление уровнем проактивности Краба.

    Синтаксис:
      !proactivity                   — показать текущий уровень и настройки
      !proactivity <level>           — переключить уровень
      !proactivity help              — справка по уровням

    Уровни:
      silent    (0) — только explicit @mention; reactions off
      reactive  (1) — mention + reply-to-krab; contextual reactions
      attentive (2) — DEFAULT: implicit triggers threshold 0.7, normal autonomy
      engaged   (3) — threshold 0.5, chatty autonomy, follow-up 5 мин
      proactive (4) — threshold 0.3, unsolicited thoughts
    """
    from ..core.proactivity import (  # noqa: PLC0415
        ProactivityLevel,
        allows_unsolicited,
        get_autonomy_mode,
        get_level,
        get_reactions_mode,
        get_trigger_threshold,
        set_level,
    )

    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    arg = parts[1].strip().lower() if len(parts) > 1 else ""

    # Без аргументов — показать статус
    if not arg or arg == "status" or arg == "статус":
        lv = get_level()
        lines = [
            f"⚡ **Proactivity Level**: `{lv.value}` ({lv.name.lower()})",
            f"🤖 Autonomy mode: `{get_autonomy_mode()}`",
            f"📊 Trigger threshold: `{get_trigger_threshold()}`",
            f"💬 Reactions mode: `{get_reactions_mode()}`",
            f"💡 Unsolicited thoughts: `{'on' if allows_unsolicited() else 'off'}`",
            "",
            "Уровни: `silent` `reactive` `attentive` `engaged` `proactive`",
        ]
        await message.reply("\n".join(lines))
        return

    # Справка
    if arg in ("help", "помощь", "?"):
        help_text = (
            "**!proactivity — уровни активности Краба:**\n\n"
            "`silent` (0) — только explicit @mention; reactions off\n"
            "`reactive` (1) — mention + reply-to-krab; contextual reactions\n"
            "`attentive` (2) — **DEFAULT**: implicit 0.7, normal autonomy\n"
            "`engaged` (3) — implicit 0.5, chatty autonomy, follow-up 5 мин\n"
            "`proactive` (4) — implicit 0.3, unsolicited thoughts\n\n"
            "Пример: `!proactivity engaged`"
        )
        await message.reply(help_text)
        return

    # Переключение уровня
    valid = {lv.name.lower() for lv in ProactivityLevel} | {
        str(lv.value) for lv in ProactivityLevel
    }
    if arg not in valid:
        await message.reply(
            f"❌ Неизвестный уровень `{arg}`.\n"
            "Доступные: `silent`, `reactive`, `attentive`, `engaged`, `proactive` (или 0–4)"
        )
        return

    set_level(arg)
    lv = get_level()
    await message.reply(
        f"✅ Proactivity переключён: **{lv.name.lower()}** ({lv.value})\n"
        f"Autonomy: `{get_autonomy_mode()}` | Threshold: `{get_trigger_threshold()}`"
    )


async def handle_e2e_smoke(bot: "KraabUserbot", message: Message) -> None:
    """!e2e-smoke — запустить E2E regression smoke tests (owner-only).

    Синтаксис:
      !e2e-smoke           — запустить все тесты
      !e2e-smoke <name>    — запустить один тест по имени
      !e2e-smoke list      — список доступных тестов
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!e2e-smoke` доступен только владельцу.")

    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""

    # Динамический импорт e2e-модуля (не в production-path)
    import importlib.util as _ilu  # noqa: PLC0415
    import pathlib as _pl  # noqa: PLC0415

    _script_path = _pl.Path(__file__).parent.parent.parent / "scripts" / "e2e_smoke_test.py"
    _spec = _ilu.spec_from_file_location("e2e_smoke_test", _script_path)
    if _spec is None or _spec.loader is None:
        await message.reply("❌ e2e_smoke_test.py не найден. Проверьте scripts/e2e_smoke_test.py")
        return
    _e2e = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_e2e)  # type: ignore[union-attr]

    TEST_CASES = _e2e.TEST_CASES  # noqa: N806

    # list
    if arg == "list":
        names = "\n".join(f"  • `{c.name}` — {c.description}" for c in TEST_CASES)
        await message.reply(f"**E2E тесты ({len(TEST_CASES)}):**\n{names}")
        return

    # Определить owner chat_id
    owner_chat_id: int | None = None
    try:
        owner_chat_id = message.from_user.id if message.from_user else None
    except Exception:
        pass
    if owner_chat_id is None and hasattr(bot, "owner_user_id"):
        owner_chat_id = bot.owner_user_id

    if not owner_chat_id:
        await message.reply("❌ Не удалось определить owner chat_id для E2E.")
        return

    # Отфильтровать тесты
    selected = TEST_CASES
    if arg and arg != "all":
        selected = [c for c in TEST_CASES if c.name == arg]
        if not selected:
            names_list = ", ".join(f"`{c.name}`" for c in TEST_CASES)
            await message.reply(f"❌ Тест `{arg}` не найден.\nДоступные: {names_list}")
            return

    await message.reply(
        f"⚙️ Запускаем E2E smoke tests ({len(selected)}/{len(TEST_CASES)})…\n"
        f"Ожидайте до {len(selected) * 65}s"
    )

    runner = _e2e.E2ESmokeRunner(chat_id=owner_chat_id, timeout=60.0, verbose=False)

    results = await runner.run_all(selected)

    passed = sum(1 for r in results if r.passed)
    total = len(results)

    lines = [f"**E2E Smoke Results: {passed}/{total} passed**\n"]
    for r in results:
        icon = "✅" if r.passed else "❌"
        snippet = (r.actual_text[:60] + "…") if len(r.actual_text) > 60 else r.actual_text
        reason = f" — {r.failure_reason}" if not r.passed else ""
        lines.append(f"{icon} `{r.case.name}` ({r.elapsed:.1f}s){reason}")
        if r.passed and snippet:
            lines.append(f"   _{snippet}_")

    # Сохранить отчёт
    try:
        report = _e2e._render_report(results, sum(r.elapsed for r in results))
        _e2e.save_report(report)
        lines.append("\nОтчёт: `docs/E2E_RESULTS_LATEST.md`")
    except Exception as exc:
        logger.warning("e2e-smoke: save report failed: %s", exc)

    await message.reply("\n".join(lines))


async def handle_setpanelauth(bot: "KraabUserbot", message: Message) -> None:
    """Установить bcrypt-пароль для Krab Panel (owner-only).

    !setpanelauth <user> <pass>  — сгенерировать хэш и вывести env-переменные
    !setpanelauth status         — текущий статус KRAB_PANEL_AUTH
    !setpanelauth off            — показать команду для отключения

    Применение: добавить в .env и перезапустить Краба.
    """
    from ..core.access_control import is_owner  # noqa: PLC0415

    if not is_owner(message):
        return

    args = bot._get_command_args(message).strip()

    if args == "status":
        auth_enabled = os.getenv("KRAB_PANEL_AUTH", "") == "1"
        username = os.getenv("KRAB_PANEL_USERNAME", "krab")
        has_hash = bool(os.getenv("KRAB_PANEL_PASSWORD_HASH", ""))
        status = "включён" if auth_enabled else "выключен"
        hash_status = "задан" if has_hash else "НЕ задан"
        await message.reply(
            f"**Panel bcrypt auth:** {status}\nUsername: `{username}`\nPassword hash: {hash_status}"
        )
        return

    if args == "off":
        await message.reply(
            "Чтобы выключить auth — удалите из `.env`:\n"
            "```\nKRAB_PANEL_AUTH=1\n"
            "KRAB_PANEL_USERNAME=...\n"
            "KRAB_PANEL_PASSWORD_HASH=...\n```\n"
            "Затем перезапустите Краба."
        )
        return

    parts = args.split(None, 1)
    if len(parts) < 2:
        await message.reply(
            "Использование: `!setpanelauth <username> <password>`\n"
            "Пример: `!setpanelauth pablito myS3cr3t`"
        )
        return

    username, password = parts[0], parts[1]

    try:
        import bcrypt  # noqa: PLC0415

        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
    except ImportError:
        await message.reply("bcrypt не установлен в окружении Краба")
        return

    env_block = (
        f"KRAB_PANEL_AUTH=1\nKRAB_PANEL_USERNAME={username}\nKRAB_PANEL_PASSWORD_HASH={hashed}"
    )
    await message.reply(
        f"Добавьте в `.env` и перезапустите Краба:\n\n```\n{env_block}\n```\n\n"
        "После перезапуска панель потребует Basic Auth с bcrypt-проверкой.\n"
        "_Сообщение с паролем будет автоматически удалено..._"
    )
    # Удалить исходное сообщение с паролем из истории чата
    try:
        await message.delete()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# !diag — одна команда с полной картиной runtime-состояния для владельца
# ---------------------------------------------------------------------------


# !diag handler + helpers (_diag_panel_base, _diag_fetch_json, _diag_fmt_section_*,
# _diag_fetch_sentry, _diag_collect_security) — extracted to commands/system_commands.py
# (Phase 2 Wave 10, Session 27). Re-exported above.
