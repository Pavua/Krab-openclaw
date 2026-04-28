# -*- coding: utf-8 -*-
"""
Обработчики Telegram-команд, вынесенные из userbot_bridge (Фаза 4.4).
Каждая функция принимает (bot, message) для тестируемости и уплощения register_handlers.
"""

from __future__ import annotations

import ast as _ast
import asyncio
import datetime
import json  # noqa: F401  # patch surface (observability_commands tests)
import math as _math
import os
import pathlib
import re
import subprocess
import sys
from typing import TYPE_CHECKING, Any

import httpx
from pyrogram.types import Message

from ..cache_manager import (  # noqa: F401  # patch surface (state_commands)
    history_cache,
    search_cache,
)
from ..config import config  # noqa: F401  # patch surface (info_commands tests)
from ..core.access_control import AccessLevel
from ..core.command_aliases import alias_service  # noqa: F401  # re-export
from ..core.exceptions import UserInputError
from ..core.inbox_service import (
    inbox_service,  # noqa: F401  # patch surface (observability_commands)
)
from ..core.lm_studio_health import (
    is_lm_studio_available,  # noqa: F401  # patch surface (state_commands)
)
from ..core.logger import get_logger
from ..core.memory_validator import memory_validator
from ..core.model_aliases import (
    normalize_model_alias,  # noqa: F401  # patch surface (state_commands)
)
from ..core.openclaw_runtime_models import (
    get_runtime_primary_model,  # noqa: F401  # patch surface (observability_commands)
)
from ..core.openclaw_workspace import (
    append_workspace_memory_entry,
    list_workspace_memory_entries,  # noqa: F401  # patch surface (memory_admin_commands tests)
    recall_workspace_memory,  # noqa: F401  # re-export для тестов (Phase 2)
)
from ..core.proactive_watch import (
    proactive_watch,  # noqa: F401  # patch surface (observability_commands)
)
from ..core.scheduler import (  # noqa: F401  # patch surface (state_commands)
    parse_due_time,
    split_reminder_input,
)
from ..integrations.hammerspoon_bridge import (  # noqa: F401  # patch surface
    HammerspoonBridgeError,
    hammerspoon,
)
from ..integrations.macos_automation import (
    macos_automation,  # noqa: F401  # patch surface (state_commands)
)
from ..mcp_client import mcp_manager  # noqa: F401  # patch surface (fileio_commands/tests)
from ..memory_engine import memory_manager
from ..model_manager import model_manager  # noqa: F401  # patch surface (state_commands)
from ..openclaw_client import (
    openclaw_client,  # noqa: F401  # patch surface (info_commands tests + many others)
)
from ..search_engine import search_brave  # noqa: F401  # re-export для тестов (Phase 2)

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
# ---------------------------------------------------------------------------
# Phase 2 Wave 11 (Session 27): admin_commands extraction
# ---------------------------------------------------------------------------
# Re-import патч-surface для тестов которые делают
# `monkeypatch.setattr(command_handlers.chat_ban_cache, ...)` —
# Wave 11 убрал прямое использование этого модуля, но тесты в
# test_system_commands.py (chatban*, stats_panel) патчат его через
# old namespace. Dual-namespace lookup pattern, см. Session 27 fbf3262.
from src.core.chat_ban_cache import chat_ban_cache  # noqa: E402, F401, I001  # patch surface
from src.core.cost_analytics import cost_analytics  # noqa: E402, F401  # patch surface
from src.core.telegram_buttons import build_costs_detail_buttons  # noqa: E402, F401  # patch surface
from src.core.weekly_digest import weekly_digest  # noqa: E402, F401  # patch surface

# Административные команды и их private helpers:
#   !config, !set, !acl, !scope, !reasoning, !role, !notify,
#   !chatban, !block, !unblock, !blocklist, !cap, !silence (!тишина),
#   !costs, !models, !budget, !digest, !archive, !unarchive,
#   !trust, !proactivity, !setpanelauth.
# Re-exported для обратной совместимости (тесты, external imports).
from .commands.admin_commands import (  # noqa: E402, F401
    _CONFIG_GROUPS,
    _CONFIG_KEY_DESC,
    _SET_ALIASES,
    _SET_FRIENDLY,
    _TRUST_HELP,
    _costs_aggregate,
    _costs_ascii_trend,
    _costs_filter_calls,
    _get_set_value,
    _handle_costs_breakdown,
    _handle_costs_budget,
    _handle_costs_today,
    _handle_costs_trend,
    _handle_costs_week,
    _render_all_settings,
    _render_chat_ban_entries,
    _render_config_all,
    _render_config_value,
    handle_acl,
    handle_archive,
    handle_blocklist,
    handle_budget,
    handle_cap,
    handle_chatban,
    handle_cmdblock,
    handle_cmdunblock,
    handle_config,
    handle_costs,
    handle_digest,
    handle_models,
    handle_notify,
    handle_proactivity,
    handle_reasoning,
    handle_role,
    handle_scope,
    handle_set,
    handle_setpanelauth,
    handle_silence,
    handle_trust,
    handle_unarchive,
)
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

# ---------------------------------------------------------------------------
# Phase 2 Wave 12 (Session 27): cli_commands extraction
# ---------------------------------------------------------------------------
# hammerspoon / HammerspoonBridgeError уже импортированы на уровне модуля (строка 43) —
# остаются как patch-surface для тестов.
from .commands.cli_commands import (  # noqa: E402, F401
    _cli_keepalive,
    _run_cli_with_progress,
    handle_claude_cli,
    handle_codex,
    handle_gemini_cli,
    handle_hs,
    handle_opencode,
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
from .commands.fileio_commands import (  # noqa: E402, F401  # Phase 2 Wave 13
    EXPORT_DEFAULT_LIMIT,
    EXPORT_MAX_LIMIT,
    EXPORT_VAULT_DIR,
    _format_sender,
    _msg_text,
    _render_export_markdown,
    _sanitize_filename,
    handle_export,
    handle_ls,
    handle_paste,
    handle_read,
    handle_write,
)
from .commands.group_admin_commands import (  # noqa: E402, F401  # Phase 2 Wave 14
    _MUTE_FOREVER_UNTIL,
    _SLOWMODE_LABELS,
    _SLOWMODE_VALID,
    _WELCOME_FILE,
    _WELCOME_TEMPLATE_VARS,
    _load_welcome_config,
    _render_welcome_text,
    _save_welcome_config,
    handle_afk,
    handle_blocked,
    handle_chatmute,
    handle_contacts,
    handle_invite,
    handle_mark,
    handle_members,
    handle_new_chat_members,
    handle_profile,
    handle_slowmode,
    handle_welcome,
)

from .commands.state_commands import (  # noqa: E402, F401  # Phase 2 Wave 16
    _format_model_info,
    _format_size_gb,
    _split_text_for_telegram,
    handle_browser,
    handle_clear,
    handle_forget,
    handle_macos,
    handle_model,
    handle_reset,
    handle_web,
)
from .commands.observability_commands import (  # noqa: E402, F401  # Phase 2 Wave 17
    _CHECKPOINTS_DIR,
    _estimate_session_tokens,
    _format_time_ago,
    handle_bookmark,
    handle_context,
    handle_inbox,
    handle_memo,
    handle_note,
    handle_watch,
)
from .commands.memory_admin_commands import (  # noqa: E402, F401  # Phase 2 Wave 18
    _ARCHIVE_DB_PATH_FOR_CLEAR,
    _collect_memory_archive_stats,
    _collect_memory_indexer_stats,
    _collect_memory_validator_stats,
    _fmt_int_ru,
    _handle_memory_clear,
    _handle_memory_rebuild,
    _handle_memory_stats,
    format_memory_stats,
    handle_memory,
)
from .commands.content_commands import (  # noqa: E402, F401  # Phase 2 Wave 15
    _BACKUP_FILES,
    _SNIPPETS_FILE,
    _TEMPLATES_FILE,
    _apply_template_vars,
    _extract_yt_url,
    _load_snippets,
    _load_templates,
    _plural_messages,
    _save_snippets,
    _save_templates,
    _YT_PROMPT_TEMPLATE,
    _YT_URL_RE,
    handle_backup,
    handle_collect,
    handle_fwd,
    handle_grep,
    handle_id,
    handle_img,
    handle_media,
    handle_ocr,
    handle_snippet,
    handle_spam,
    handle_template,
    handle_top,
    handle_yt,
)
from .commands.crypto_commands import (  # noqa: E402, F401  # Phase 2 Wave 19 (Session 28)
    _derive_key,
    _xor_crypt,
    decrypt_text,
    encrypt_text,
    handle_decrypt,
    handle_encrypt,
    handle_qr,
)
from .commands.info_commands import (  # noqa: E402, F401  # Phase 2 Wave 19+20 (Session 28)
    _CONVERT_ALIASES,
    _CONVERT_HELP,
    _CONVERT_UNITS,
    _CSS_NAMED_COLORS,
    _CURRENCY_API_URL,
    _CURRENCY_DEFAULT_TARGET,
    _CURRENCY_HTTP_TIMEOUT,
    _DEFINE_DETAILED_KEYWORDS,
    _DEFINE_EN_KEYWORDS,
    _EMOJI_DB,
    _NEWS_KNOWN_TOPICS,
    _NEWS_LANG_MAP,
    _TEMP_UNITS,
    _WTTR_TIMEOUT,
    _WTTR_URL,
    _build_define_prompt,
    _convert_temperature,
    _do_convert,
    _emoji_search,
    _fetch_wttr,
    _fmt_currency,
    _format_convert_result,
    _normalize_unit,
    _parse_color_input,
    _parse_currency_args,
    _parse_define_args,
    _rgb_to_hex,
    _rgb_to_hsl,
    fetch_exchange_rate,
    handle_color,
    handle_convert,
    handle_currency,
    handle_define,
    handle_emoji,
    handle_news,
    handle_urban,
    handle_weather,
)


# _format_size_gb + _split_text_for_telegram — extracted to commands/state_commands.py
# (Phase 2 Wave 16, Session 28). Re-exported above (см. state_commands import).


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


# handle_ls / handle_read / handle_write / handle_paste — extracted to commands/fileio_commands.py (Phase 2 Wave 13).
# Re-exported above (handle_ls, handle_read, handle_write, handle_paste).


# handle_status — extracted to commands/system_commands.py (Phase 2 Wave 10, Session 27).
# Re-exported above (handle_status).


# handle_model + _format_model_info — extracted to commands/state_commands.py (Phase 2 Wave 16, Session 28). Re-exported above.


# handle_clear + handle_forget + handle_reset — extracted to commands/state_commands.py (Phase 2 Wave 16, Session 28). Re-exported above.
# Re-exported above (handle_translator + render helpers + _parse_toggle_arg).


# handle_web — extracted to commands/state_commands.py (Phase 2 Wave 16, Session 28). Re-exported above.
# extracted to commands/system_commands.py (Phase 2 Wave 10, Session 27).


# handle_macos — extracted to commands/state_commands.py (Phase 2 Wave 16, Session 28). Re-exported above.
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


# handle_watch — extracted to commands/observability_commands.py (Phase 2 Wave 17, Session 28).
# Re-exported below.


# handle_memory — extracted to commands/memory_admin_commands.py (Phase 2 Wave 18, Session 28).
# Re-exported below (incl. _handle_memory_stats/clear/rebuild, _collect_memory_*, _fmt_int_ru, format_memory_stats, _ARCHIVE_DB_PATH_FOR_CLEAR).


# handle_inbox — extracted to commands/observability_commands.py (Phase 2 Wave 17, Session 28).
# Re-exported below.


# handle_browser — extracted to commands/state_commands.py (Phase 2 Wave 16, Session 28). Re-exported above.
# handle_hs — extracted to commands/cli_commands.py (Phase 2 Wave 12, Session 27). Re-exported above.


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


# handle_cap — extracted to commands/admin_commands.py (Phase 2 Wave 11, Session 27). Re-exported above.


# _render_stats_panel + _format_ecosystem_report + _handle_stats_ecosystem + handle_stats —
# extracted to commands/system_commands.py (Phase 2 Wave 10, Session 27). Re-exported above.


# handle_silence — extracted to commands/admin_commands.py (Phase 2 Wave 11, Session 27). Re-exported above.
# _costs_filter_calls / _costs_aggregate / _costs_ascii_trend / _handle_costs_* / handle_costs /
# handle_models / handle_budget / handle_digest — same extraction, re-exported above.


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


# handle_context / handle_memo / handle_bookmark / handle_note + helpers
# (_estimate_session_tokens, _format_time_ago, _CHECKPOINTS_DIR) — extracted to
# commands/observability_commands.py (Phase 2 Wave 17, Session 28). Re-exported below.


# handle_qr — extracted to commands/crypto_commands.py (Phase 2 Wave 19, Session 28).
# handle_weather + _fetch_wttr + _WTTR_URL/_WTTR_TIMEOUT — extracted to
# commands/info_commands.py (Phase 2 Wave 19+20, Session 28). Re-exported above.


# !hash — extracted to commands/text_utils.py (Phase 2, Session 27)
# Re-exported below: handle_hash


# !calc — extracted to commands/text_utils.py (Phase 2, Session 27)
# Re-exported below: handle_calc, safe_calc


# !b64 — extracted to commands/text_utils.py (Phase 2, Session 27)
# Re-exported below: handle_b64, _b64_encode, _b64_decode, _b64_is_valid

# !encrypt / !decrypt + helpers (_derive_key, _xor_crypt, encrypt_text,
# decrypt_text) — extracted to commands/crypto_commands.py
# (Phase 2 Wave 19, Session 28). Re-exported above.


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
# _ARCHIVE_DB_PATH_FOR_CLEAR moved to commands/memory_admin_commands.py (Wave 18) — re-exported below.


# _load_saved_quotes / _save_quotes / handle_quote moved to commands/memory_commands.py


# !define + helpers (_DEFINE_DETAILED_KEYWORDS, _DEFINE_EN_KEYWORDS,
# _parse_define_args, _build_define_prompt) — extracted to
# commands/info_commands.py (Phase 2 Wave 19+20, Session 28). Re-exported above.

# !len / !count — extracted to commands/text_utils.py (Phase 2, Session 27)
# Re-exported below: handle_len


# !currency + helpers (_CURRENCY_DEFAULT_TARGET, _CURRENCY_API_URL,
# _CURRENCY_HTTP_TIMEOUT, _parse_currency_args, fetch_exchange_rate,
# _fmt_currency) — extracted to commands/info_commands.py
# (Phase 2 Wave 19+20, Session 28). Re-exported above.


# ---------------------------------------------------------------------------
# !welcome + handle_new_chat_members — extracted to commands/group_admin_commands.py
# (Phase 2 Wave 14, Session 27). Re-exported above:
# _WELCOME_FILE, _WELCOME_TEMPLATE_VARS, _load_welcome_config, _save_welcome_config,
# _render_welcome_text, handle_welcome, handle_new_chat_members.
# ---------------------------------------------------------------------------

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
# !afk / !back — extracted to commands/group_admin_commands.py (Phase 2 Wave 14, Session 27).
# Re-exported above: handle_afk.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# !img — описание фото через AI vision
# ---------------------------------------------------------------------------


# handle_img — extracted to commands/content_commands.py (Phase 2 Wave 15).
# Re-exported above: handle_img.


# ---------------------------------------------------------------------------
# !ocr — извлечение текста из изображения через AI vision
# ---------------------------------------------------------------------------


# handle_ocr — extracted to commands/content_commands.py (Phase 2 Wave 15).
# Re-exported above: handle_ocr.


# ---------------------------------------------------------------------------
# !media — скачивание медиафайлов (фото/видео/документ)
# ---------------------------------------------------------------------------


# handle_media — extracted to commands/content_commands.py (Phase 2 Wave 15).
# Re-exported above: handle_media.


# ---------------------------------------------------------------------------
# Антиспам фильтр для групп (!spam)
# ---------------------------------------------------------------------------


# handle_spam — extracted to commands/content_commands.py (Phase 2 Wave 15).
# Re-exported above: handle_spam.


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

# _SNIPPETS_FILE — extracted to commands/content_commands.py (Phase 2 Wave 15).
# Re-exported above.


# _load_snippets — extracted to commands/content_commands.py (Phase 2 Wave 15).
# Re-exported above.


# _save_snippets — extracted to commands/content_commands.py (Phase 2 Wave 15).
# Re-exported above.


# handle_snippet — extracted to commands/content_commands.py (Phase 2 Wave 15).
# Re-exported above: handle_snippet, _load_snippets, _save_snippets.


# ---------------------------------------------------------------------------
# !tag — moved to commands/memory_commands.py (Phase 2 Wave 5)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# handle_top — лидерборд активности чата
# ---------------------------------------------------------------------------


# _plural_messages — extracted to commands/content_commands.py (Phase 2 Wave 15).
# Re-exported above.


# handle_top — extracted to commands/content_commands.py (Phase 2 Wave 15).
# Re-exported above: handle_top, _plural_messages.


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
# _YT_URL_RE, _YT_PROMPT_TEMPLATE, _extract_yt_url — extracted to commands/content_commands.py (Phase 2 Wave 15).
# Re-exported above.


# handle_yt — extracted to commands/content_commands.py (Phase 2 Wave 15).
# Re-exported above: handle_yt.


# ---------------------------------------------------------------------------
# !template — шаблоны сообщений с подстановкой переменных
# ---------------------------------------------------------------------------

# _TEMPLATES_FILE — extracted to commands/content_commands.py (Phase 2 Wave 15).
# Re-exported above.


# _load_templates — extracted to commands/content_commands.py (Phase 2 Wave 15).
# Re-exported above.


# _save_templates — extracted to commands/content_commands.py (Phase 2 Wave 15).
# Re-exported above.


# _apply_template_vars — extracted to commands/content_commands.py (Phase 2 Wave 15).
# Re-exported above.


# handle_template — extracted to commands/content_commands.py (Phase 2 Wave 15).
# Re-exported above: handle_template, _load_templates, _save_templates, _apply_template_vars.


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
# !mark — extracted to commands/group_admin_commands.py (Phase 2 Wave 14, Session 27).
# Re-exported above.
# ---------------------------------------------------------------------------
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


# _SLOWMODE_VALID, _SLOWMODE_LABELS, handle_slowmode — extracted to commands/group_admin_commands.py
# (Phase 2 Wave 14, Session 27). Re-exported above.
# ---------------------------------------------------------------------------
# !chatmute + _MUTE_FOREVER_UNTIL — extracted to commands/group_admin_commands.py
# (Phase 2 Wave 14, Session 27). Re-exported above.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# !urban — Urban Dictionary lookup через AI + web_search
# ---------------------------------------------------------------------------


# handle_urban — extracted to commands/info_commands.py
# (Phase 2 Wave 19+20, Session 28). Re-exported above.


# handle_contacts — extracted to commands/group_admin_commands.py (Phase 2 Wave 14, Session 27).
# Re-exported above.
# handle_invite — extracted to commands/group_admin_commands.py (Phase 2 Wave 14, Session 27).
# Re-exported above.
# handle_blocked — extracted to commands/group_admin_commands.py (Phase 2 Wave 14, Session 27).
# Re-exported above.
# handle_profile — extracted to commands/group_admin_commands.py (Phase 2 Wave 14, Session 27).
# Re-exported above.
# handle_members — extracted to commands/group_admin_commands.py (Phase 2 Wave 14, Session 27).
# Re-exported above.
# ---------------------------------------------------------------------------
# !log — просмотр логов Краба из Telegram
# ---------------------------------------------------------------------------

# Путь к лог-файлу Краба
# !log handler + helpers (_KRAB_LOG_PATH, _LOG_*, _read_log_tail_subprocess) —
# extracted to commands/system_commands.py (Phase 2 Wave 10, Session 27). Re-exported above.


# ---------------------------------------------------------------------------
# !convert + helpers (_CONVERT_UNITS, _CONVERT_ALIASES, _TEMP_UNITS,
#   _normalize_unit, _convert_temperature, _do_convert,
#   _format_convert_result, _CONVERT_HELP) — extracted to
# commands/info_commands.py (Phase 2 Wave 19+20, Session 28).
# !color + helpers (_CSS_NAMED_COLORS, _rgb_to_hsl, _parse_color_input,
#   _rgb_to_hex) — extracted to commands/info_commands.py.
# !emoji + _EMOJI_DB + _emoji_search — extracted to commands/info_commands.py.
# !news + _NEWS_LANG_MAP + _NEWS_KNOWN_TOPICS — extracted to commands/info_commands.py.
# Все re-exported в imports выше (Wave 19+20).
# ---------------------------------------------------------------------------


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
# _BACKUP_FILES — extracted to commands/content_commands.py (Phase 2 Wave 15).
# Re-exported above.


# handle_backup — extracted to commands/content_commands.py (Phase 2 Wave 15).
# Re-exported above: handle_backup, _BACKUP_FILES.


# !explain — extracted to commands/ai_commands.py (Phase 2 Wave 7, Session 27).
# Re-exported above (handle_explain, _EXPLAIN_PROMPT).


# ---------------------------------------------------------------------------
# handle_id — показать ID текущего чата, себя, сообщения (если reply)
# ---------------------------------------------------------------------------


# handle_id — extracted to commands/content_commands.py (Phase 2 Wave 15).
# Re-exported above: handle_id.


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


# ---------------------------------------------------------------------------
# !diag — одна команда с полной картиной runtime-состояния для владельца
# ---------------------------------------------------------------------------


# !diag handler + helpers (_diag_panel_base, _diag_fetch_json, _diag_fmt_section_*,
# _diag_fetch_sentry, _diag_collect_security) — extracted to commands/system_commands.py
# (Phase 2 Wave 10, Session 27). Re-exported above.
