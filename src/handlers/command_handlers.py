# -*- coding: utf-8 -*-
"""
Обработчики Telegram-команд, вынесенные из userbot_bridge (Фаза 4.4).
Каждая функция принимает (bot, message) для тестируемости и уплощения register_handlers.
"""

from __future__ import annotations

import asyncio  # noqa: F401  # patch surface (test_screenshot_fallback patches command_handlers.asyncio)
import json  # noqa: F401  # patch surface (observability_commands tests)
import os  # noqa: F401  # patch surface (test_screenshot_fallback patches command_handlers.os.unlink)
import pathlib  # noqa: F401  # patch surface (test_backup_command etc.)
import subprocess  # noqa: F401  # patch surface (test_*_command patches command_handlers.subprocess)
from typing import TYPE_CHECKING, Any

import httpx  # noqa: F401, E402  # patch surface (test_handle_link patches command_handlers.httpx)
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
    handle_replay,
    handle_restart,
    handle_stats,
    handle_status,
    handle_sysinfo,
    handle_uptime,
    handle_version,
)

# ---------------------------------------------------------------------------
# tor_commands — !tor (status/ip/newid/fetch). Owner-only.
# ---------------------------------------------------------------------------
from .commands.tor_commands import handle_tor  # noqa: E402, F401

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
from .commands.observability_commands import (  # noqa: E402, F401  # Phase 2 Wave 17 + Wave 25-D + Wave 55-D
    _CHECKPOINTS_DIR,
    _count_today_calls,
    _estimate_session_tokens,
    _format_time_ago,
    _probe_anthropic_vertex,
    _probe_gemini_cli,
    _probe_vertex_gemini,
    handle_bookmark,
    handle_context,
    handle_inbox,
    handle_memo,
    handle_metrics,
    handle_note,
    handle_quota,
    handle_routes,
    handle_skills,
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
from .commands.diagnostic_commands import (  # noqa: E402, F401  # Phase 2 Wave 21 (Session 28)
    _EVAL_ALLOWED_NODES,
    _EVAL_FORBIDDEN_NAMES,
    _EVAL_NAMESPACE,
    _SHORT_DOMAINS,
    _TIME_CITY_MAP,
    _TIME_DEFAULT_CITIES,
    _TYPING_ACTION_MAP,
    _TYPING_DEFAULT_SECONDS,
    _TYPING_LABEL_MAP,
    _TYPING_MAX_SECONDS,
    _URL_RE,
    _eval_check_node,
    _expand_url,
    _fetch_link_meta,
    _format_link_preview,
    _handle_chado_digest,
    _handle_chado_ping,
    _handle_chado_status,
    _handle_listen_list,
    _handle_listen_stats,
    _is_short_url,
    _time_format_dt,
    _time_lookup_tz,
    handle_bench,
    handle_chado,
    handle_e2e_smoke,
    handle_eval,
    handle_filter,
    handle_help,
    handle_link,
    handle_listen,
    handle_run,
    handle_say,
    handle_screenshot,
    handle_time,
    handle_typing,
    safe_eval,
)
from .commands.curator_commands import (  # noqa: E402, F401  # Wave 14-I Step 1/4 (Session 33)
    handle_curator,
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


# async def handle_help — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# async def handle_screenshot — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# async def handle_bench — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


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
# _EVAL_ALLOWED_NODES — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.

# Запрещённые имена в !eval
# _EVAL_FORBIDDEN_NAMES — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.

# Безопасное пространство имён для !eval
# _EVAL_NAMESPACE — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# def _eval_check_node — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# def safe_eval — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# async def handle_eval — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# async def handle_run — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


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
# _URL_RE — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.

# Набор коротких доменов (для _is_short_url)
# _SHORT_DOMAINS — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# def _is_short_url — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# async def _fetch_link_meta — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# async def _expand_url — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# def _format_link_preview — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# async def handle_link — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# Маппинг: имя города (нижний регистр) → IANA timezone
# _TIME_CITY_MAP — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.

# Города по умолчанию для `!time` без аргументов
# _TIME_DEFAULT_CITIES — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# def _time_format_dt — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# def _time_lookup_tz — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# async def handle_time — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.

# _TYPING_ACTION_MAP — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.

# _TYPING_LABEL_MAP — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.

# _TYPING_DEFAULT_SECONDS — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.
# _TYPING_MAX_SECONDS — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# async def handle_typing — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# async def handle_say — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# async def _handle_listen_list — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# async def _handle_listen_stats — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# async def handle_listen — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# async def handle_filter — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# async def handle_chado — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# async def _handle_chado_status — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# async def _handle_chado_ping — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# async def _handle_chado_digest — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.


# async def handle_e2e_smoke — extracted to commands/diagnostic_commands.py (Phase 2 Wave 21, Session 28). Re-exported above.
