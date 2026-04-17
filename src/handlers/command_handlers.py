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
import operator as _operator
import os
import pathlib
import re
import socket
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
from ..core.command_aliases import alias_service
from ..core.cost_analytics import cost_analytics
from ..core.exceptions import UserInputError
from ..core.inbox_service import inbox_service
from ..core.lm_studio_health import is_lm_studio_available
from ..core.logger import get_logger
from ..core.model_aliases import normalize_model_alias
from ..core.openclaw_runtime_models import get_runtime_primary_model
from ..core.openclaw_workspace import (
    append_workspace_memory_entry,
    list_workspace_memory_entries,
    recall_workspace_memory,
)
from ..core.proactive_watch import proactive_watch
from ..core.scheduler import krab_scheduler, parse_due_time, split_reminder_input
from ..core.swarm import AgentRoom
from ..core.telegram_buttons import (
    build_costs_detail_buttons,
    build_health_recheck_buttons,
    build_swarm_team_buttons,
)
from ..core.translator_runtime_profile import (
    ALLOWED_LANGUAGE_PAIRS,
    ALLOWED_TRANSLATION_MODES,
    ALLOWED_VOICE_STRATEGIES,
    default_translator_runtime_profile,
)
from ..core.weekly_digest import weekly_digest
from ..employee_templates import ROLES, get_role_prompt, list_roles, save_role
from ..integrations.hammerspoon_bridge import HammerspoonBridgeError, hammerspoon
from ..integrations.macos_automation import macos_automation
from ..mcp_client import mcp_manager
from ..memory_engine import memory_manager
from ..model_manager import model_manager
from ..openclaw_client import openclaw_client
from ..search_engine import search_brave

logger = get_logger(__name__)

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
    if message.chat.id < 0:
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
# In-memory хранилище таймеров и секундомеров
# ---------------------------------------------------------------------------

# Структура: {timer_id: {"task": asyncio.Task, "label": str, "ends_at": float, "chat_id": int}}
_active_timers: dict[int, dict] = {}
_timer_counter: int = 0  # счётчик для ID

# Структура: {chat_id: {"started_at": float, "laps": list[float]}}
_stopwatches: dict[int, dict] = {}


def _parse_duration(spec: str) -> int | None:
    """Парсит строку вида 5m, 1h30m, 90s в секунды. Возвращает None при ошибке."""
    import re
    spec = spec.strip().lower()
    # Попытка распарсить составной формат: 1h30m20s
    pattern = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")
    m = pattern.fullmatch(spec)
    if m and any(m.groups()):
        h = int(m.group(1) or 0)
        mins = int(m.group(2) or 0)
        s = int(m.group(3) or 0)
        total = h * 3600 + mins * 60 + s
        return total if total > 0 else None
    # Чистое число — трактуем как секунды
    if spec.isdigit():
        return int(spec) or None
    return None


def _fmt_duration(seconds: float) -> str:
    """Форматирует секунды в читаемую строку (1ч 5м 3с)."""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}ч")
    if m:
        parts.append(f"{m}м")
    if s or not parts:
        parts.append(f"{s}с")
    return " ".join(parts)


def _render_voice_profile(profile: dict[str, Any]) -> str:
    """Форматирует runtime voice-профиль для Telegram-ответа."""
    enabled = bool(profile.get("enabled"))
    delivery = str(profile.get("delivery") or "text+voice")
    speed = float(profile.get("speed") or 1.5)
    voice_name = str(profile.get("voice") or "ru-RU-DmitryNeural")
    input_ready = bool(profile.get("input_transcription_ready"))
    live_foundation = bool(profile.get("live_voice_foundation"))
    blocked_chats = profile.get("blocked_chats") or []
    if blocked_chats:
        blocked_preview = ", ".join(f"`{cid}`" for cid in list(blocked_chats)[:5])
        if len(blocked_chats) > 5:
            blocked_preview += f" (+{len(blocked_chats) - 5})"
    else:
        blocked_preview = "—"
    return (
        "🎙️ **Voice runtime**\n"
        f"- Озвучка ответов: `{'ВКЛ' if enabled else 'ВЫКЛ'}`\n"
        f"- Режим доставки: `{delivery}`\n"
        f"- Скорость: `{speed:.2f}x`\n"
        f"- Голос: `{voice_name}`\n"
        f"- Входящие voice/STT: `{'READY' if input_ready else 'DOWN'}`\n"
        f"- Live voice foundation: `{'READY' if live_foundation else 'DEGRADED'}`\n"
        f"- Blocked chats: {blocked_preview}\n\n"
        "Команды:\n"
        "`!voice on|off|toggle`\n"
        "`!voice speed <0.75..2.5>`\n"
        "`!voice voice <edge-tts-id>`\n"
        "`!voice delivery <text+voice|voice-only>`\n"
        "`!voice block <chat_id>` / `!voice unblock <chat_id>` / `!voice blocked`\n"
        "`!voice reset`"
    )


def _render_translator_profile(profile: dict[str, Any]) -> str:
    """Форматирует product-level translator runtime profile для Telegram-ответа."""
    language_pair = str(profile.get("language_pair") or "es-ru")
    mode = str(profile.get("translation_mode") or "bilingual")
    strategy = str(profile.get("voice_strategy") or "voice-first")
    target = str(profile.get("target_device") or "iphone_companion")
    quick_phrases = profile.get("quick_phrases") or []
    quick_phrase_count = int(profile.get("quick_phrase_count") or len(quick_phrases) or 0)
    voice_foundation = bool(profile.get("voice_foundation_ready"))
    voice_runtime = bool(profile.get("voice_runtime_enabled"))
    ordinary_calls = "ВКЛ" if bool(profile.get("ordinary_calls_enabled")) else "ВЫКЛ"
    internet_calls = "ВКЛ" if bool(profile.get("internet_calls_enabled")) else "ВЫКЛ"
    subtitles = "ВКЛ" if bool(profile.get("subtitles_enabled")) else "ВЫКЛ"
    timeline = "ВКЛ" if bool(profile.get("timeline_enabled")) else "ВЫКЛ"
    summary = "ВКЛ" if bool(profile.get("summary_enabled")) else "ВЫКЛ"
    diagnostics = "ВКЛ" if bool(profile.get("diagnostics_enabled")) else "ВЫКЛ"
    preview = ", ".join(f"`{item}`" for item in list(quick_phrases)[:3]) or "—"
    return (
        "🗣️ **Translator runtime**\n"
        f"- Языковая пара: `{language_pair}`\n"
        f"- Mode: `{mode}`\n"
        f"- Voice strategy: `{strategy}`\n"
        f"- Target device: `{target}`\n"
        f"- Ordinary calls: `{ordinary_calls}`\n"
        f"- Internet calls: `{internet_calls}`\n"
        f"- Subtitles / Timeline / Summary / Diagnostics: `{subtitles}` / `{timeline}` / `{summary}` / `{diagnostics}`\n"
        f"- Quick phrases: `{quick_phrase_count}`\n"
        f"- Voice foundation: `{'READY' if voice_foundation else 'DEGRADED'}`\n"
        f"- Voice runtime replies: `{'ВКЛ' if voice_runtime else 'ВЫКЛ'}`\n"
        f"- Preview: {preview}\n\n"
        "Команды:\n"
        "`!translator status`\n"
        "`!translator lang <es-ru|es-en|en-ru|auto-detect>`\n"
        "`!translator mode <bilingual|auto_to_ru|auto_to_en>`\n"
        "`!translator strategy <voice-first|subtitles-first>`\n"
        "`!translator ordinary <on|off>` / `!translator internet <on|off>`\n"
        "`!translator subtitles|timeline|summary|diagnostics <on|off>`\n"
        "`!translator phrase add <текст>` / `!translator phrase remove <номер>`\n"
        "`!translator reset`"
    )


def _render_translator_session_state(state: dict[str, Any]) -> str:
    """Форматирует translator session state для Telegram-ответа."""
    status = str(state.get("session_status") or "idle")
    muted = bool(state.get("translation_muted"))
    session_id = str(state.get("session_id") or "—")
    label = str(state.get("active_session_label") or "—")
    pair = str(state.get("language_pair") or state.get("last_language_pair") or "—")
    original = str(state.get("last_translated_original") or "—")
    translation = str(state.get("last_translated_translation") or "—")
    last_event = str(state.get("last_event") or "session_idle")
    updated_at = str(state.get("updated_at") or "—")
    timeline_summary = (
        state.get("timeline_summary") if isinstance(state.get("timeline_summary"), dict) else {}
    )
    preview = (
        state.get("timeline_preview") if isinstance(state.get("timeline_preview"), list) else []
    )
    preview_lines: list[str] = []
    for item in preview[:3]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "session_updated")
        ts = str(item.get("ts") or "—")
        item_translation = str(item.get("translation") or item.get("original") or "").strip()
        suffix = f" · {item_translation}" if item_translation else ""
        preview_lines.append(f"- `{ts}` `{kind}`{suffix}")
    preview_block = "\n".join(preview_lines) if preview_lines else "- `timeline пока пуст`"
    return (
        "🎧 **Translator session**\n"
        f"- Состояние: `{status}`\n"
        f"- Translation muted: `{'YES' if muted else 'NO'}`\n"
        f"- Session id: `{session_id}`\n"
        f"- Session label: `{label}`\n"
        f"- Language pair: `{pair}`\n"
        f"- Last event: `{last_event}`\n"
        f"- Updated: `{updated_at}`\n"
        f"- Timeline events: `{timeline_summary.get('total', state.get('timeline_event_count', 0))}`\n"
        f"- Line / control: `{timeline_summary.get('line_events', 0)}` / `{timeline_summary.get('control_events', 0)}`\n"
        f"- Last original: `{original}`\n"
        f"- Last translation: `{translation}`\n"
        "Recent timeline:\n"
        f"{preview_block}\n\n"
        "Команды:\n"
        "`!translator session status`\n"
        "`!translator session start [label]`\n"
        "`!translator session pause`\n"
        "`!translator session resume`\n"
        "`!translator session stop`\n"
        "`!translator session mute` / `!translator session unmute`\n"
        "`!translator session replay <original> | <translation>`\n"
        "`!translator session clear`"
    )


def _parse_toggle_arg(raw: Any, *, field_name: str) -> bool:
    """Нормализует `on/off` аргумент для командных флагов."""
    value = str(raw or "").strip().lower()
    if value == "on":
        return True
    if value == "off":
        return False
    raise UserInputError(user_message=f"❌ Для `{field_name}` поддерживаются только `on` и `off`.")


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


class _AgentRoomRouterAdapter:
    """
    Легковесный адаптер роевого запуска для userbot-команд.

    Почему отдельно:
    - AgentRoom ожидает контракт `route_query(prompt, skip_swarm=True)`;
    - userbot работает напрямую через `openclaw_client.send_message_stream`;
    - адаптер связывает эти два слоя без изменения core-логики.
    """

    def __init__(self, *, chat_id: str, system_prompt: str) -> None:
        self.chat_id = chat_id
        self.system_prompt = system_prompt

    async def route_query(self, prompt: str, skip_swarm: bool = False, **_: Any) -> str:
        """
        Выполняет один роевой шаг через OpenClaw stream.

        `skip_swarm` принят для совместимости контракта AgentRoom.
        """
        del skip_swarm
        chunks: list[str] = []
        # Увеличенный лимит: модели нужен бюджет на tool_calls JSON + финальный ответ.
        # 700 токенов слишком мало для tool call (200+ токенов на JSON аргументы).
        max_output_tokens = int(getattr(config, "SWARM_ROLE_MAX_OUTPUT_TOKENS", 4096) or 4096)
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=self.chat_id,
            system_prompt=self.system_prompt,
            force_cloud=bool(getattr(config, "FORCE_CLOUD", False)),
            max_output_tokens=max_output_tokens,
        ):
            chunks.append(str(chunk))
        return "".join(chunks).strip()


async def handle_search(bot: "KraabUserbot", message: Message) -> None:
    """
    Веб-поиск с AI-суммаризацией через OpenClaw + web_search tool.

    Форматы:
      !search <запрос>         — AI-режим: краткий ответ + источники (по умолчанию)
      !search --raw <запрос>   — сырые результаты Brave без AI
      !search --brave <запрос> — то же, что --raw

    Длинные ответы автоматически разбиваются на части (пагинация).
    """
    raw_args = bot._get_command_args(message).strip()

    # Проверяем пустой запрос
    if not raw_args or raw_args.lower() in ["search", "!search"]:
        raise UserInputError(
            user_message=(
                "🔍 Что ищем?\n"
                "`!search <запрос>` — поиск с AI-суммаризацией\n"
                "`!search --raw <запрос>` — сырые результаты Brave"
            )
        )

    # Определяем режим: --raw/--brave → без AI
    raw_mode = False
    query = raw_args
    for flag in ("--raw", "--brave"):
        if raw_args.lower().startswith(flag):
            raw_mode = True
            query = raw_args[len(flag):].strip()
            break

    if not query:
        raise UserInputError(user_message="🔍 Укажи запрос после флага.")

    # Изолированная сессия, чтобы не загрязнять основной контекст чата
    session_id = f"search_{message.chat.id}"

    if raw_mode:
        # --- Режим raw: прямой Brave-поиск без AI ---
        msg = await message.reply(f"🔍 **Ищу (raw):** `{query}`...")
        try:
            results = await search_brave(query)
            if not results:
                await msg.edit("❌ Ничего не найдено.")
                return
            # Пагинация длинных результатов
            header = f"🔍 **Результаты поиска:** `{query}`\n\n"
            parts = _split_text_for_telegram(header + results)
            await msg.edit(parts[0])
            for part in parts[1:]:
                await message.reply(part)
        except (httpx.HTTPError, OSError, ValueError, KeyError) as e:
            logger.error("handle_search_raw_error", query=query, error=str(e))
            await msg.edit(f"❌ Ошибка поиска: {e}")
        return

    # --- Режим AI: OpenClaw + web_search tool ---
    msg = await message.reply(f"🔍 **Краб ищет в сети:** `{query}`...")

    prompt = (
        f"Найди в интернете информацию по запросу: {query}. "
        "Дай краткий структурированный ответ с ключевыми фактами и источниками (URL)."
    )

    try:
        chunks: list[str] = []
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            disable_tools=False,  # обязательно использует web_search
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()

        if not result:
            await msg.edit("❌ Не удалось получить результаты поиска.")
            return

        # Заголовок + результат
        header = f"🔍 **{query}**\n\n"
        full_text = header + result

        # Пагинация: Telegram ограничивает ~4096 символов
        parts = _split_text_for_telegram(full_text)
        total = len(parts)

        # Редактируем первое сообщение
        first = parts[0]
        if total > 1:
            first += f"\n\n_(часть 1/{total})_"
        await msg.edit(first)

        # Остальные части отправляем как новые сообщения
        for i, part in enumerate(parts[1:], start=2):
            suffix = f"\n\n_(часть {i}/{total})_" if total > 2 else ""
            await message.reply(part + suffix)

    except Exception as exc:  # noqa: BLE001
        logger.error("handle_search_ai_error", query=query, error=str(exc))
        await msg.edit(f"❌ Ошибка поиска: {exc}")


async def handle_swarm(bot: "KraabUserbot", message: Message) -> None:
    """
    Запуск роевого обсуждения с поддержкой именованных команд и делегирования.

    Формат:
      !swarm <тема>                   — дефолтный room (аналитик→критик→интегратор)
      !swarm <команда> <тема>         — именованная команда (traders/coders/analysts/creative)
      !swarm teams                    — список доступных команд
      !swarm loop [N] <тема>          — итеративный режим (дефолтная команда)
      !swarm <команда> loop [N] <тема>— итеративный режим для команды
    """
    from ..core.swarm import AgentRoom
    from ..core.swarm_bus import (
        TEAM_REGISTRY,
        list_teams,
        resolve_team_name,
        swarm_bus,
    )
    from ..core.swarm_memory import swarm_memory

    args = bot._get_command_args(message).strip()
    if not args:
        # Показываем inline-кнопки выбора команды вместо текстовой справки
        await message.reply(
            "🐝 **Swarm** — выбери команду или укажи тему:\n"
            "`!swarm <тема>` — базовый room\n"
            "`!swarm <команда> <тема>` — именованная команда\n\n"
            "Быстрый выбор команды:",
            reply_markup=build_swarm_team_buttons(),
        )
        return

    # !swarm teams — справка
    if args.lower() in {"teams", "команды", "help"}:
        await message.reply(list_teams())
        return

    # !swarm task — task board operations
    if args.lower().startswith("task"):
        from ..core.swarm_task_board import swarm_task_board

        task_tokens = args.split(maxsplit=2)
        sub = task_tokens[1].lower() if len(task_tokens) > 1 else "board"

        if sub == "board":
            summary = swarm_task_board.get_board_summary()
            lines = ["📋 **Swarm Task Board**"]
            for status_name, count in sorted(summary.get("by_status", {}).items()):
                emoji = {
                    "pending": "⏳",
                    "in_progress": "🔄",
                    "done": "✅",
                    "failed": "❌",
                    "blocked": "🚫",
                }.get(status_name, "•")
                lines.append(f"  {emoji} {status_name}: {count}")
            if summary.get("by_team"):
                lines.append("**По командам:**")
                for team_name, count in sorted(summary["by_team"].items()):
                    lines.append(f"  {team_name}: {count}")
            lines.append(f"Всего: {summary.get('total', 0)}")
            await message.reply("\n".join(lines))
            return

        if sub == "list":
            team_filter = task_tokens[2].strip().lower() if len(task_tokens) > 2 else None
            tasks = swarm_task_board.list_tasks(team=team_filter, limit=10)
            if not tasks:
                await message.reply("📋 Task board пуст.")
                return
            lines = ["📋 **Tasks:**"]
            for t in tasks:
                emoji = {"pending": "⏳", "in_progress": "🔄", "done": "✅", "failed": "❌"}.get(
                    t.status, "•"
                )
                lines.append(f"{emoji} `{t.task_id[:8]}` [{t.team}] {t.title} ({t.priority})")
            await message.reply("\n".join(lines))
            return

        if sub == "create":
            # !swarm task create <team> <title>
            create_parts = (task_tokens[2] if len(task_tokens) > 2 else "").split(maxsplit=1)
            if len(create_parts) < 2:
                raise UserInputError(user_message="❌ Формат: `!swarm task create <team> <title>`")
            team_name = create_parts[0].lower()
            title = create_parts[1]
            task = swarm_task_board.create_task(
                team=team_name,
                title=title,
                description="",
                priority="medium",
                created_by="owner",
            )
            await message.reply(f"✅ Task `{task.task_id[:8]}` создан для **{team_name}**: {title}")
            return

        if sub == "done":
            tid = task_tokens[2].strip() if len(task_tokens) > 2 else ""
            if not tid:
                raise UserInputError(user_message="❌ Формат: `!swarm task done <task_id>`")
            # Ищем по prefix
            all_tasks = swarm_task_board.list_tasks(limit=200)
            match = next((t for t in all_tasks if t.task_id.startswith(tid)), None)
            if not match:
                raise UserInputError(user_message=f"❌ Task `{tid}` не найден")
            swarm_task_board.complete_task(match.task_id, result="completed by owner")
            await message.reply(f"✅ Task `{match.task_id[:8]}` → done")
            return

        if sub == "fail":
            tid = task_tokens[2].strip() if len(task_tokens) > 2 else ""
            if not tid:
                raise UserInputError(user_message="❌ Формат: `!swarm task fail <task_id>`")
            all_tasks = swarm_task_board.list_tasks(limit=200)
            match = next((t for t in all_tasks if t.task_id.startswith(tid)), None)
            if not match:
                raise UserInputError(user_message=f"❌ Task `{tid}` не найден")
            swarm_task_board.fail_task(match.task_id, reason="failed by owner")
            await message.reply(f"❌ Task `{match.task_id[:8]}` → failed")
            return

        if sub == "assign":
            # !swarm task assign <id> — помечает in_progress и подсказывает launch
            tid = task_tokens[2].strip() if len(task_tokens) > 2 else ""
            if not tid:
                raise UserInputError(user_message="❌ Формат: `!swarm task assign <task_id>`")
            all_tasks = swarm_task_board.list_tasks(limit=200)
            match = next((t for t in all_tasks if t.task_id.startswith(tid)), None)
            if not match:
                raise UserInputError(user_message=f"❌ Task `{tid}` не найден")
            if match.status == "done":
                raise UserInputError(user_message=f"Task `{match.task_id[:8]}` уже завершён")
            swarm_task_board.update_task(match.task_id, status="in_progress")
            await message.reply(
                f"🔄 Task `{match.task_id[:8]}` → **in_progress**\n\n"
                f"Для запуска swarm round:\n"
                f"`!swarm {match.team} {match.title}`"
            )
            return

        if sub in {"status", "show", "get"}:
            tid = task_tokens[2].strip() if len(task_tokens) > 2 else ""
            if not tid:
                raise UserInputError(user_message="❌ Формат: `!swarm task status <task_id>`")
            all_tasks = swarm_task_board.list_tasks(limit=200)
            match = next((t for t in all_tasks if t.task_id.startswith(tid)), None)
            if not match:
                raise UserInputError(user_message=f"❌ Task `{tid}` не найден")
            emoji = {"pending": "⏳", "in_progress": "🔄", "done": "✅", "failed": "❌"}.get(
                match.status, "•"
            )
            lines = [
                f"{emoji} **Task: {match.task_id[:12]}**",
                f"Team: {match.team}",
                f"Title: {match.title}",
                f"Status: {match.status}",
                f"Priority: {match.priority}",
                f"Created by: {match.created_by}",
                f"Created: {match.created_at}",
                f"Updated: {match.updated_at}",
            ]
            if match.result:
                lines.append(f"Result: {match.result[:200]}")
            if match.parent_task_id:
                lines.append(f"Parent: {match.parent_task_id[:12]}")
            await message.reply("\n".join(lines))
            return

        if sub == "priority":
            # !swarm task priority <id> <level>
            parts = (task_tokens[2] if len(task_tokens) > 2 else "").split(maxsplit=1)
            if len(parts) < 2:
                raise UserInputError(
                    user_message="❌ Формат: `!swarm task priority <id> low|medium|high|critical`"
                )
            tid, level = parts[0].strip(), parts[1].strip().lower()
            if level not in {"low", "medium", "high", "critical"}:
                raise UserInputError(
                    user_message="❌ Приоритеты: `low`, `medium`, `high`, `critical`"
                )
            all_tasks = swarm_task_board.list_tasks(limit=200)
            match = next((t for t in all_tasks if t.task_id.startswith(tid)), None)
            if not match:
                raise UserInputError(user_message=f"❌ Task `{tid}` не найден")
            swarm_task_board.update_task(match.task_id, priority=level)
            await message.reply(f"📌 Task `{match.task_id[:8]}` → priority **{level}**")
            return

        if sub == "count":
            # Быстрый count по статусам
            summary = swarm_task_board.get_board_summary()
            by_status = summary.get("by_status", {})
            total = summary.get("total", 0)
            parts = [f"{s}: {c}" for s, c in sorted(by_status.items())]
            await message.reply(f"📊 Tasks: {total} ({', '.join(parts)})")
            return

        if sub == "clear":
            # Очищает done/failed tasks из board
            all_tasks = swarm_task_board.list_tasks(limit=500)
            cleared = 0
            for t in all_tasks:
                if t.status in {"done", "failed"}:
                    # Удаляем через update на "archived" статус — FIFO cleanup потом удалит
                    swarm_task_board.update_task(t.task_id, status="done")
                    cleared += 1
            swarm_task_board.cleanup_old()
            await message.reply(f"🧹 Очищено {cleared} done/failed tasks")
            return

        raise UserInputError(
            user_message=(
                "📋 Task Board:\n"
                "`!swarm task board` — сводка\n"
                "`!swarm task list [team]` — список задач\n"
                "`!swarm task create <team> <title>` — создать\n"
                "`!swarm task done <id>` — завершить\n"
                "`!swarm task fail <id>` — отметить как failed\n"
                "`!swarm task assign <id>` — запустить swarm round для задачи\n"
                "`!swarm task status <id>` — детальный view\n"
                "`!swarm task priority <id> <level>` — изменить приоритет\n"
                "`!swarm task count` — быстрый count по статусам\n"
                "`!swarm task clear` — очистить done/failed"
            )
        )

    # !swarm summary — сводка текущей сессии (задачи, артефакты, раунды)
    if args.lower() in {"summary", "сводка"}:
        from ..core.swarm_artifact_store import swarm_artifact_store
        from ..core.swarm_task_board import swarm_task_board

        board = swarm_task_board.get_board_summary()
        by_status = board.get("by_status", {})
        total_tasks = board.get("total", 0)

        arts = swarm_artifact_store.list_artifacts(limit=200)
        total_rounds = len(arts)
        # суммируем время всех раундов
        total_duration = sum(a.get("duration_sec", 0) for a in arts)

        # команды, участвовавшие в сессии
        teams_seen: set[str] = {a.get("team", "?") for a in arts}

        lines = ["📊 **Swarm Summary**", ""]
        # --- задачи ---
        lines.append("**Задачи:**")
        status_emoji = {
            "done": "✅",
            "pending": "⏳",
            "in_progress": "🔄",
            "failed": "❌",
            "blocked": "🚫",
        }
        for st in ("done", "in_progress", "pending", "failed", "blocked"):
            cnt = by_status.get(st, 0)
            if cnt:
                lines.append(f"  {status_emoji.get(st, '•')} {st}: {cnt}")
        lines.append(f"  Итого задач: {total_tasks}")
        lines.append("")
        # --- артефакты ---
        lines.append("**Артефакты:**")
        lines.append(f"  📦 Раундов сохранено: {total_rounds}")
        if total_duration:
            lines.append(f"  ⏱ Суммарное время: {total_duration:.0f}с")
        if teams_seen:
            lines.append(f"  🐝 Команды: {', '.join(sorted(teams_seen))}")

        await message.reply("\n".join(lines))
        return

    # !swarm artifacts [team] — список артефактов
    if args.lower().startswith("artifacts") or args.lower().startswith("артефакт"):
        from ..core.swarm_artifact_store import swarm_artifact_store

        art_tokens = args.split(maxsplit=1)
        team_filter = art_tokens[1].strip().lower() if len(art_tokens) > 1 else None
        arts = swarm_artifact_store.list_artifacts(team=team_filter, limit=10)
        if not arts:
            await message.reply("📦 Артефактов пока нет.")
            return
        lines = ["📦 **Swarm Artifacts:**"]
        for a in arts:
            ts = a.get("timestamp_iso", "?")[:16]
            team = a.get("team", "?")
            topic = (a.get("topic") or "?")[:50]
            dur = a.get("duration_sec", 0)
            lines.append(f"  [{team}] {topic} ({dur:.0f}s, {ts})")
        await message.reply("\n".join(lines))
        return

    # !swarm report [team] — последние markdown reports
    if args.lower().startswith("report") or args.lower().startswith("отчёт"):
        from pathlib import Path

        from ..core.swarm_artifact_store import swarm_artifact_store

        report_dir = Path.home() / ".openclaw" / "krab_runtime_state" / "reports"
        if not report_dir.exists():
            await message.reply("📄 Отчётов пока нет.")
            return
        files = sorted(report_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
        if not files:
            await message.reply("📄 Отчётов пока нет.")
            return
        lines = ["📄 **Последние отчёты:**"]
        for f in files:
            name = f.name.replace(".md", "")
            size_kb = f.stat().st_size / 1024
            lines.append(f"  `{name}` ({size_kb:.1f} KB)")
        lines.append(f"\nВсего: {len(list(report_dir.glob('*.md')))} отчётов")
        await message.reply("\n".join(lines))
        return

    # !swarm listen on/off — управление team listeners
    if args.lower().startswith("listen"):
        from ..core.swarm_team_listener import is_listeners_enabled, set_listeners_enabled

        listen_tokens = args.split(maxsplit=1)
        if len(listen_tokens) > 1:
            val = listen_tokens[1].strip().lower()
            if val in {"on", "1", "true", "yes"}:
                set_listeners_enabled(True)
                await message.reply(
                    "🎧 Team listeners: **ON**\nTeam-аккаунты отвечают в ЛС и на mention."
                )
            elif val in {"off", "0", "false", "no"}:
                set_listeners_enabled(False)
                await message.reply("🔇 Team listeners: **OFF**\nTeam-аккаунты молчат.")
            else:
                await message.reply("❌ Формат: `!swarm listen on` или `!swarm listen off`")
        else:
            status = "ON ✅" if is_listeners_enabled() else "OFF 🔇"
            await message.reply(f"🎧 Team listeners: **{status}**")
        return

    # !swarm info <team> — детальная инфо о команде
    if args.lower().startswith("info"):
        info_tokens = args.split(maxsplit=1)
        team_arg = info_tokens[1].strip().lower() if len(info_tokens) > 1 else ""
        if not team_arg:
            raise UserInputError(user_message="❌ Формат: `!swarm info <team>`")
        resolved = resolve_team_name(team_arg)
        if not resolved:
            raise UserInputError(user_message=f"❌ Команда `{team_arg}` не найдена")
        roles = TEAM_REGISTRY.get(resolved, [])
        from ..core.swarm_artifact_store import swarm_artifact_store
        from ..core.swarm_task_board import swarm_task_board

        tasks = swarm_task_board.list_tasks(team=resolved, limit=5)
        arts = swarm_artifact_store.list_artifacts(team=resolved, limit=3)
        lines = [f"🐝 **Команда: {resolved}**", ""]
        lines.append("**Роли:**")
        for r in roles:
            lines.append(f"  {r.get('emoji', '•')} {r.get('title', r.get('name', '?'))}")
        if tasks:
            lines.append(f"\n**Задачи ({len(tasks)}):**")
            for t in tasks:
                emoji = {"pending": "⏳", "in_progress": "🔄", "done": "✅", "failed": "❌"}.get(
                    t.status, "•"
                )
                lines.append(f"  {emoji} {t.title[:50]} ({t.status})")
        if arts:
            lines.append(f"\n**Артефакты ({len(arts)}):**")
            for a in arts:
                lines.append(
                    f"  📦 {(a.get('topic') or '?')[:40]} ({a.get('duration_sec', 0):.0f}s)"
                )
        await message.reply("\n".join(lines))
        return

    # !swarm stats — сводная статистика по всем командам
    if args.lower().startswith("stats") or args.lower().startswith("стат"):
        from ..core.swarm_artifact_store import swarm_artifact_store
        from ..core.swarm_task_board import swarm_task_board
        from ..core.swarm_team_listener import is_listeners_enabled

        board = swarm_task_board.get_board_summary()
        arts = swarm_artifact_store.list_artifacts(limit=100)
        lines = [
            "📊 **Swarm Stats**",
            f"Tasks: {board.get('total', 0)} (done: {board.get('by_status', {}).get('done', 0)})",
            f"Artifacts: {len(arts)}",
            f"Listeners: {'ON' if is_listeners_enabled() else 'OFF'}",
        ]
        if board.get("by_team"):
            lines.append("**По командам:**")
            for team_name, count in sorted(board["by_team"].items()):
                team_arts = len([a for a in arts if a.get("team", "").lower() == team_name.lower()])
                lines.append(f"  {team_name}: {count} tasks, {team_arts} artifacts")
        await message.reply("\n".join(lines))
        return

    # !swarm memory [team] — история прогонов
    if args.lower().startswith("memory") or args.lower().startswith("память"):
        mem_tokens = args.split(maxsplit=1)
        if len(mem_tokens) > 1:
            mem_arg = mem_tokens[1].strip().lower()
            if mem_arg == "clear":
                teams = swarm_memory.all_teams()
                total = sum(swarm_memory.clear_team(t) for t in teams)
                await message.reply(f"🧹 Очищена память всех команд ({total} записей)")
            else:
                team = resolve_team_name(mem_arg) or mem_arg
                await message.reply(swarm_memory.format_history(team, count=7))
        else:
            # Показать сводку по всем командам
            teams = swarm_memory.all_teams()
            if not teams:
                await message.reply("🧠 Память свёрма пуста — ещё не было прогонов.")
            else:
                lines = ["🧠 **Память свёрма:**\n"]
                for t in teams:
                    stats = swarm_memory.get_team_stats(t)
                    lines.append(
                        f"**{t}** — {stats['total_runs']} прогонов, "
                        f"последний: {stats.get('last_run', '—')}"
                    )
                lines.append("\n`!swarm memory <команда>` — подробная история")
                await message.reply("\n".join(lines))
        return

    # !swarm schedule <team> <interval> <topic> [--workflow research|report] — создание рекуррентной задачи
    if args.lower().startswith("schedule") or args.lower().startswith("расписание"):
        import re as _re

        from ..core.swarm_scheduler import WorkflowType, parse_interval, swarm_scheduler

        # Извлекаем --workflow флаг до разбора позиционных аргументов
        workflow_match = _re.search(r"--workflow\s+(\S+)", args)
        workflow_type = WorkflowType.STANDARD
        if workflow_match:
            wf_raw = workflow_match.group(1).lower()
            try:
                workflow_type = WorkflowType(wf_raw)
            except ValueError:
                valid_wf = ", ".join(w.value for w in WorkflowType)
                await message.reply(f"❌ Неизвестный workflow: `{wf_raw}`\nДопустимые: {valid_wf}")
                return
            # Убираем --workflow ... из args для разбора позиционных аргументов
            args_clean = _re.sub(r"--workflow\s+\S+", "", args).strip()
        else:
            args_clean = args

        sched_tokens = args_clean.split(maxsplit=3)  # schedule traders 4h <BTC тема>
        if len(sched_tokens) < 4:
            await message.reply(
                "📅 Формат: `!swarm schedule <команда> <интервал> <тема> [--workflow research|report]`\n"
                "Примеры:\n"
                "  `!swarm schedule traders 4h анализ BTC`\n"
                "  `!swarm schedule analysts 6h крипторынок --workflow research`\n"
                "  `!swarm schedule coders 1d прогресс --workflow report`\n"
                "Интервалы: `30m`, `1h`, `4h`, `1d`"
            )
            return
        sched_team = resolve_team_name(sched_tokens[1])
        if not sched_team:
            await message.reply(
                f"❌ Команда '{sched_tokens[1]}' не найдена. Доступны: {', '.join(TEAM_REGISTRY)}"
            )
            return
        try:
            interval = parse_interval(sched_tokens[2])
        except ValueError as e:
            await message.reply(f"❌ {e}")
            return
        sched_topic = sched_tokens[3] if len(sched_tokens) > 3 else "общий анализ"
        try:
            job = swarm_scheduler.add_job(
                team=sched_team,
                topic=sched_topic,
                interval_sec=interval,
                workflow_type=workflow_type,
            )
            interval_h = interval / 3600
            interval_str = f"{interval_h:.1f}ч" if interval_h >= 1 else f"{interval // 60}мин"
            wf_emoji = {"standard": "🔄", "research": "🔬", "report": "📊"}.get(
                job.workflow_type, "🔄"
            )
            await message.reply(
                f"📅 Задача создана!\n"
                f"ID: `{job.job_id}`\n"
                f"Команда: **{sched_team}** каждые {interval_str}\n"
                f"Тема: _{sched_topic}_\n"
                f"Workflow: {wf_emoji} `{job.workflow_type}`"
            )
        except (RuntimeError, ValueError) as e:
            await message.reply(f"❌ {e}")
        return

    # !swarm unschedule <id> — удаление задачи
    if args.lower().startswith("unschedule") or args.lower().startswith("отмена"):
        from ..core.swarm_scheduler import swarm_scheduler

        unsched_tokens = args.split(maxsplit=1)
        if len(unsched_tokens) < 2:
            await message.reply("❌ Укажи ID задачи: `!swarm unschedule <id>`")
            return
        job_id = unsched_tokens[1].strip()
        if swarm_scheduler.remove_job(job_id):
            await message.reply(f"✅ Задача `{job_id}` удалена")
        else:
            await message.reply(f"❌ Задача `{job_id}` не найдена")
        return

    # !swarm jobs — список рекуррентных задач
    if args.lower() in {"jobs", "задачи", "schedule"}:
        from ..core.swarm_scheduler import swarm_scheduler

        await message.reply(swarm_scheduler.format_jobs())
        return

    # !swarm channels — статус swarm-групп
    if args.lower() in {"channels", "группы", "каналы"}:
        from ..core.swarm_channels import swarm_channels

        await message.reply(swarm_channels.format_status())
        return

    # !swarm setup — создание Forum-группы с топиками
    if args.lower() in {"setup", "настройка", "форум"}:
        from ..core.swarm_channels import swarm_channels

        chat = message.chat
        # Группы/супергруппы всегда имеют отрицательный chat_id
        is_group = chat.id < 0

        if is_group:
            # Вызвано в группе — пробуем создать топики напрямую
            msg = await message.reply("📡 Создаю топики...")
            try:
                topic_ids = await swarm_channels.setup_topics_in_existing(chat.id)
                if topic_ids:
                    topics_text = "\n".join(
                        f"  **{team}** → topic `{tid}`" for team, tid in topic_ids.items()
                    )
                    await msg.edit_text(
                        f"✅ **Forum Topics настроены!**\n\n{topics_text}\n\n"
                        f"Свёрм транслирует раунды в топики.\n"
                        f"Пиши в топик во время раунда — Краб подхватит как директиву."
                    )
                else:
                    await msg.edit_text(
                        "⚠️ Не удалось создать топики.\n"
                        "Убедись что **Topics** включены: Group Info → Edit → Topics.\n"
                        "После включения повтори `!swarm setup`."
                    )
            except Exception as exc:  # noqa: BLE001
                await msg.edit_text(
                    f"⚠️ Ошибка создания топиков: `{exc}`\n\n"
                    f"Включи **Topics** в настройках группы и повтори."
                )
        else:
            # Вызвано в личке — создаём новую группу
            msg = await message.reply("📡 Создаю группу **🐝 Krab Swarm**...")
            try:
                result = await swarm_channels.setup_forum()
                if result.get("topic_ids"):
                    topics_text = "\n".join(
                        f"  **{team}** → topic `{tid}`" for team, tid in result["topic_ids"].items()
                    )
                    await msg.edit_text(
                        f"✅ **Krab Swarm Forum создан!**\n\n"
                        f"Chat ID: `{result['chat_id']}`\n{topics_text}\n\n"
                        f"Свёрм транслирует раунды в топики."
                    )
                else:
                    await msg.edit_text(
                        f"⚠️ **Группа создана** (Chat ID: `{result['chat_id']}`)\n\n"
                        f"Включи **Topics**: Group Info → Edit → Topics\n"
                        f"Затем набери `!swarm setup` **в этой группе**."
                    )
            except Exception as exc:  # noqa: BLE001
                await msg.edit_text(f"❌ Ошибка: {exc}")
        return

    # !swarm setchat <team> — привязать текущую группу к команде
    if args.lower().startswith("setchat") or args.lower().startswith("привязать"):
        from ..core.swarm_channels import swarm_channels

        setchat_tokens = args.split(maxsplit=1)
        if len(setchat_tokens) < 2:
            await message.reply(
                "📡 Формат: `!swarm setchat <команда>`\n"
                "Вызывай эту команду **в группе**, которую хочешь привязать.\n"
                "Пример: в группе «Трейдеры» напиши `!swarm setchat traders`"
            )
            return
        team = resolve_team_name(setchat_tokens[1].strip())
        if not team:
            await message.reply(
                f"❌ Команда '{setchat_tokens[1]}' не найдена. Доступны: {', '.join(TEAM_REGISTRY)}"
            )
            return
        chat_id = message.chat.id
        swarm_channels.register_team_chat(team, chat_id)
        await message.reply(f"📡 Группа привязана к команде **{team}**\nChat ID: `{chat_id}`")
        return

    # !swarm research <тема> — research pipeline с обязательным web_search
    if args.lower().startswith("research") or args.lower().startswith("исследование"):
        from ..core.swarm_research_pipeline import SwarmResearchPipeline

        research_tokens = args.split(maxsplit=1)
        if len(research_tokens) < 2 or not research_tokens[1].strip():
            raise UserInputError(
                user_message=(
                    "🔬 Укажи тему исследования.\nПример: `!swarm research тренды AI 2025`"
                )
            )
        raw_topic = research_tokens[1].strip()
        msg = await message.reply(
            f"🔬 **Research Pipeline** [analysts]\nТема: `{raw_topic}`\n_web_search обязателен_"
        )
        try:
            chat_id = str(message.chat.id)
            user = message.from_user
            access_profile = bot._get_access_profile(user)
            is_allowed_sender = bot._is_allowed_sender(user)
            system_prompt = bot._build_system_prompt_for_sender(
                is_allowed_sender=is_allowed_sender,
                access_level=access_profile.level,
            )

            def _router_factory(tn: str) -> _AgentRoomRouterAdapter:
                return _AgentRoomRouterAdapter(
                    chat_id=f"swarm:{chat_id}:{tn}",
                    system_prompt=system_prompt,
                )

            pipeline = SwarmResearchPipeline()
            result_text = await pipeline.run(
                raw_topic,
                router_factory=_router_factory,
                swarm_bus=swarm_bus,
            )
            chunks = _split_text_for_telegram(result_text)
            await msg.edit(chunks[0])
            for part in chunks[1:]:
                await message.reply(part)
        except Exception as e:
            logger.error("swarm_research_error", error=str(e), exc_info=True)
            safe_err = str(e).replace("`", "'")[:500]
            try:
                await msg.edit(
                    f"❌ Ошибка Research: {safe_err}" if safe_err else "❌ Ошибка Research"
                )
            except Exception:  # noqa: BLE001
                pass
        return

    # Парсим: [team_name] [loop [N]] <topic>
    tokens = args.split()
    team_key: str | None = None
    loop_mode = False
    loop_rounds = 2

    # Проверяем первый токен на имя команды
    maybe_team = resolve_team_name(tokens[0])
    if maybe_team:
        team_key = maybe_team
        tokens = tokens[1:]

    # Проверяем loop
    if tokens and tokens[0].lower() == "loop":
        loop_mode = True
        tokens = tokens[1:]
        if tokens and tokens[0].isdigit():
            loop_rounds = min(int(tokens[0]), int(getattr(config, "SWARM_LOOP_MAX_ROUNDS", 3) or 3))
            tokens = tokens[1:]

    topic = " ".join(tokens).strip()
    if not topic:
        raise UserInputError(user_message="🐝 Укажи тему! Пример: `!swarm traders анализируй BTC`")

    team_label = f" [{team_key}]" if team_key else ""
    mode_label = f" loop×{loop_rounds}" if loop_mode else ""
    msg = await message.reply(f"🐝 **Запуск Swarm{team_label}{mode_label}...**\nТема: `{topic}`")

    try:
        chat_id = str(message.chat.id)
        user = message.from_user
        access_profile = bot._get_access_profile(user)
        is_allowed_sender = bot._is_allowed_sender(user)
        system_prompt = bot._build_system_prompt_for_sender(
            is_allowed_sender=is_allowed_sender,
            access_level=access_profile.level,
        )

        def router_factory(team_name: str) -> "_AgentRoomRouterAdapter":
            """Создаёт адаптер роутера для указанной команды."""
            return _AgentRoomRouterAdapter(
                chat_id=f"swarm:{chat_id}:{team_name}",
                system_prompt=system_prompt,
            )

        roles = TEAM_REGISTRY.get(team_key) if team_key else None
        room = AgentRoom(roles=roles)
        router = router_factory(team_key or "default")

        if loop_mode:
            result_text = await room.run_loop(
                topic,
                router,
                rounds=loop_rounds,
                max_rounds=int(getattr(config, "SWARM_LOOP_MAX_ROUNDS", 3) or 3),
                next_round_clip=int(getattr(config, "SWARM_LOOP_NEXT_ROUND_CLIP", 4000) or 4000),
                _bus=swarm_bus,
                _router_factory=router_factory,
                _team_name=team_key or "default",
            )
        else:
            result_text = await room.run_round(
                topic,
                router,
                _bus=swarm_bus,
                _router_factory=router_factory,
                _team_name=team_key or "default",
            )

        chunks = _split_text_for_telegram(result_text)
        await msg.edit(chunks[0])
        for part in chunks[1:]:
            await message.reply(part)

    except Exception as e:
        logger.error("swarm_error", error=str(e), error_type=type(e).__name__, exc_info=True)
        safe_err = str(e).replace("`", "'")[:500]
        try:
            await msg.edit(f"❌ Ошибка Swarm: {safe_err}" if safe_err else "❌ Ошибка Swarm")
        except Exception:  # noqa: BLE001
            pass


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


async def handle_remember(bot: "KraabUserbot", message: Message) -> None:
    """Запомнить факт."""
    text = bot._get_command_args(message)
    if not text:
        raise UserInputError(user_message="🧠 Что запомнить? Напиши: `!remember <текст>`")
    try:
        workspace_saved = append_workspace_memory_entry(
            text,
            source="userbot",
            author=str(getattr(getattr(message, "from_user", None), "username", "") or ""),
        )
        vector_saved = memory_manager.save_fact(text)
        success = workspace_saved or vector_saved
        if success:
            await message.reply(f"🧠 **Запомнил:** `{text}`")
        else:
            await message.reply("❌ Ошибка памяти.")
    except (ValueError, RuntimeError, OSError) as e:
        await message.reply(f"❌ Critical Memory Error: {e}")


async def handle_recall(bot: "KraabUserbot", message: Message) -> None:
    """Вспомнить факт."""
    text = bot._get_command_args(message)
    if not text:
        raise UserInputError(user_message="🧠 Что вспомнить? Напиши: `!recall <запрос>`")
    try:
        workspace_facts = recall_workspace_memory(text)
        vector_facts = memory_manager.recall(text)
        sections: list[str] = []
        if workspace_facts:
            sections.append(f"**OpenClaw workspace:**\n{workspace_facts}")
        if vector_facts and vector_facts not in workspace_facts:
            sections.append(f"**Local vector memory:**\n{vector_facts}")
        facts = "\n\n".join(section for section in sections if section).strip()
        if facts:
            await message.reply(f"🧠 **Вспомнил:**\n\n{facts}")
        else:
            await message.reply("🧠 Ничего не нашел по этому запросу.")
    except (ValueError, RuntimeError, OSError) as e:
        await message.reply(f"❌ Recalling Error: {e}")


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


async def handle_status(bot: "KraabUserbot", message: Message) -> None:
    """Компактный overview всех подсистем Краба одним сообщением (!status)."""
    import os as _os

    import psutil as _psutil

    from ..core.silence_mode import silence_manager
    from ..core.swarm_bus import TEAM_REGISTRY
    from ..core.swarm_scheduler import swarm_scheduler

    # ── 1. Telegram ─────────────────────────────────────────────────────
    tg_ok = bot.me is not None
    tg_icon = "✅" if tg_ok else "❌"

    # ── 2. OpenClaw / активная модель ───────────────────────────────────
    try:
        oc_ok = await openclaw_client.health_check()
    except Exception:
        oc_ok = False
    route_meta: dict = {}
    if hasattr(openclaw_client, "get_last_runtime_route"):
        try:
            route_meta = openclaw_client.get_last_runtime_route() or {}
        except Exception:
            route_meta = {}
    actual_model = str(route_meta.get("model") or "").strip()
    declared_primary = str(
        get_runtime_primary_model() or getattr(config, "MODEL", "") or ""
    ).strip()
    effective_model = actual_model or declared_primary or "unknown"
    # Сокращённое имя модели (после последнего "/")
    model_short = effective_model.split("/")[-1] if "/" in effective_model else effective_model
    oc_icon = "✅" if oc_ok else "❌"

    # ── 3. Scheduler ────────────────────────────────────────────────────
    try:
        sched_jobs = swarm_scheduler.list_jobs()
        job_count = len(sched_jobs)
        sched_icon = "✅"
    except Exception:
        job_count = 0
        sched_icon = "⚠️"

    # ── 4. Inbox ────────────────────────────────────────────────────────
    try:
        inbox_summary = inbox_service.get_summary()
        inbox_open = int(inbox_summary.get("open_items", 0))
    except Exception:
        inbox_open = 0

    # ── 5. Cost ─────────────────────────────────────────────────────────
    try:
        cost_report = cost_analytics.build_usage_report_dict()
        cost_month = cost_report.get("cost_month_usd", 0.0)
        budget = cost_report.get("monthly_budget_usd") or 0.0
        cost_str = f"${cost_month:.2f}/{budget:.2f}" if budget else f"${cost_month:.2f}"
    except Exception:
        cost_str = "?"

    # ── 6. Swarm teams ──────────────────────────────────────────────────
    try:
        team_count = len(TEAM_REGISTRY)
    except Exception:
        team_count = 0

    # ── 7. Translator ───────────────────────────────────────────────────
    try:
        t_state = bot.get_translator_session_state()
        t_status = t_state.get("session_status", "idle")
        t_pair = t_state.get("last_pair") or ""
        if t_status == "idle":
            translator_str = "idle"
        else:
            translator_str = f"active ({t_pair})" if t_pair else t_status
    except Exception:
        translator_str = "idle"

    # ── 8. Silence ──────────────────────────────────────────────────────
    try:
        sil = silence_manager.status()
        silence_str = "on" if sil.get("global_muted") else "off"
    except Exception:
        silence_str = "off"

    # ── 9. Uptime ───────────────────────────────────────────────────────
    try:
        elapsed = int(time.time() - bot._session_start_time)
        hours, rem = divmod(elapsed, 3600)
        mins = rem // 60
        uptime_str = f"{hours}h{mins}m" if hours else f"{mins}m"
    except Exception:
        uptime_str = "?"

    # ── 10. RAM (RSS процесса) ──────────────────────────────────────────
    try:
        rss_mb = int(_psutil.Process(_os.getpid()).memory_info().rss / 1024 / 1024)
        ram_str = f"{rss_mb}MB"
    except Exception:
        ram_str = "?"

    # ── 11. Сообщений за сессию ────────────────────────────────────────
    try:
        msg_count = bot._session_messages_processed
    except Exception:
        msg_count = 0

    # ── Сборка компактного статуса ──────────────────────────────────────
    line1 = (
        f"{tg_icon} Telegram | "
        f"{oc_icon} OpenClaw ({model_short}) | "
        f"{sched_icon} Scheduler ({job_count} jobs)"
    )
    line2 = (
        f"📬 Inbox: {inbox_open} open | "
        f"💰 Cost: {cost_str} | "
        f"🐝 Swarm: {team_count} teams"
    )
    line3 = f"🔄 Translator: {translator_str} | 🔇 Silence: {silence_str}"
    line4 = f"⏱ Uptime: {uptime_str} | 🧠 RAM: {ram_str} | 📊 Messages: {msg_count}"

    text = (
        "🦀 **Krab Status**\n"
        "━━━━━━━━━━━━\n"
        f"{line1}\n"
        f"{line2}\n"
        f"{line3}\n"
        f"{line4}"
    )

    # Доп. строка: Primary runtime если не совпадает с фактической моделью
    if declared_primary and declared_primary != effective_model:
        text += f"\n🧭 Primary runtime: `{declared_primary}`"

    # Если сообщение отправлено самим ботом — редактируем, иначе отвечаем
    me_id = getattr(bot.me, "id", None) if bot.me is not None else None
    if message.from_user and me_id and message.from_user.id == me_id:
        await message.edit(text)
    else:
        await message.reply(text)


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
            "_Подкоманды: `local`, `cloud`, `auto`, `set <model_id>`, `load <name>`, `unload`, `scan`_"
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
            "Доступные: `local`, `cloud`, `auto`, `set`, `load`, `unload`, `scan`"
        )
    )


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


##############################################################################
# Группы ключей для !config — технические/системные настройки
##############################################################################

# (config_key, описание)
_CONFIG_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Модель и routing", [
        ("MODEL",                        "Основная модель"),
        ("FORCE_CLOUD",                  "Принудительный cloud-маршрут"),
        ("LOCAL_FALLBACK_ENABLED",       "Fallback cloud→local при ошибках"),
        ("LOCAL_PREFERRED_MODEL",        "Локальная модель (LM Studio)"),
        ("LOCAL_PREFERRED_VISION_MODEL", "Локальная vision-модель"),
        ("SINGLE_LOCAL_MODEL_MODE",      "Держать одну локальную модель"),
        ("GUARDED_IDLE_UNLOAD",          "Guarded idle-unload локальной модели"),
        ("GUARDED_IDLE_UNLOAD_GRACE_SEC","Пауза перед idle-unload (сек)"),
        ("RESTORE_PREFERRED_ON_IDLE_UNLOAD", "Восстановить preferred после unload"),
    ]),
    ("Таймауты и retry", [
        ("OPENCLAW_CHUNK_TIMEOUT_SEC",              "Таймаут chunk стриминга (сек)"),
        ("OPENCLAW_FIRST_CHUNK_TIMEOUT_SEC",         "Таймаут первого chunk (сек)"),
        ("OPENCLAW_PHOTO_FIRST_CHUNK_TIMEOUT_SEC",   "Таймаут первого chunk фото (сек)"),
        ("OPENCLAW_AUTO_RETRY_COUNT",                "Кол-во auto-retry при ошибках"),
        ("OPENCLAW_AUTO_RETRY_DELAY_SEC",            "Задержка auto-retry (сек)"),
        ("OPENCLAW_PROGRESS_NOTICE_INITIAL_SEC",     "Первый progress-notice (сек)"),
        ("OPENCLAW_PROGRESS_NOTICE_REPEAT_SEC",      "Повтор progress-notice (сек)"),
    ]),
    ("Userbot и Telegram", [
        ("USERBOT_MAX_OUTPUT_TOKENS",      "Макс. токенов ответа (текст)"),
        ("USERBOT_PHOTO_MAX_OUTPUT_TOKENS","Макс. токенов ответа (фото)"),
        ("USERBOT_FORCE_CLOUD_FOR_PHOTO",  "Cloud-маршрут для фото"),
        ("TELEGRAM_STREAM_UPDATE_INTERVAL_SEC", "Интервал stream UI (сек)"),
        ("TELEGRAM_STREAM_SHOW_REASONING",      "Показывать reasoning в stream"),
        ("TELEGRAM_REACTIONS_ENABLED",          "Реакции 👀✅❌"),
        ("TELEGRAM_MESSAGE_BATCH_WINDOW_SEC",   "Окно склейки сообщений (сек)"),
        ("TELEGRAM_SESSION_HEARTBEAT_SEC",      "Heartbeat MTProto (сек)"),
        ("TOOL_NARRATION_ENABLED",              "Tool narration в Telegram"),
    ]),
    ("Фоновые задачи", [
        ("SCHEDULER_ENABLED",              "Планировщик reminders/cron"),
        ("DEFERRED_ACTION_GUARD_ENABLED",  "Guard deferred-actions"),
        ("SWARM_AUTONOMOUS_ENABLED",       "Автономные задачи свёрма"),
        ("SILENCE_DEFAULT_MINUTES",        "Tишина по умолчанию (мин)"),
        ("OWNER_AUTO_SILENCE_MINUTES",     "Авто-тишина при owner-write (мин)"),
    ]),
    ("Доступ и безопасность", [
        ("OWNER_USERNAME",              "Username владельца (fallback)"),
        ("NON_OWNER_SAFE_MODE_ENABLED", "Safe-mode для гостей"),
        ("GUEST_TOOLS_DISABLED",        "Запрет tools для GUEST"),
        ("FORWARD_UNKNOWN_INCOMING",    "Пересылать неизвестные входящие"),
        ("AI_DISCLOSURE_ENABLED",       "Дисклеймер ИИ в начале диалога"),
        ("MANUAL_BLOCKLIST",            "Чёрный список (usernames/IDs)"),
    ]),
    ("Голос", [
        ("VOICE_MODE_DEFAULT",   "Voice-режим по умолчанию"),
        ("VOICE_REPLY_SPEED",    "Скорость TTS"),
        ("VOICE_REPLY_VOICE",    "TTS-голос"),
        ("VOICE_REPLY_DELIVERY", "Режим доставки (text+voice/voice/text)"),
    ]),
    ("История диалога", [
        ("HISTORY_WINDOW_MESSAGES",       "Окно cloud-истории (сообщений)"),
        ("LOCAL_HISTORY_WINDOW_MESSAGES", "Окно local-истории (сообщений)"),
        ("RETRY_HISTORY_WINDOW_MESSAGES", "Окно retry-истории (сообщений)"),
    ]),
    ("Сеть и прокси", [
        ("TOR_ENABLED",       "Tor SOCKS5 прокси"),
        ("TOR_SOCKS_PORT",    "Порт Tor SOCKS5"),
        ("BROWSER_FOCUS_TAB", "Фокус вкладки браузера"),
        ("LM_STUDIO_URL",     "URL LM Studio"),
        ("OPENCLAW_URL",      "URL OpenClaw Gateway"),
    ]),
    ("Прочее", [
        ("DEFAULT_WEATHER_CITY",    "Город погоды по умолчанию"),
        ("MAX_RAM_GB",              "Лимит RAM (GB)"),
        ("LOG_LEVEL",               "Уровень логирования"),
        ("GEMINI_PAID_KEY_ENABLED", "Платный Gemini API ключ"),
    ]),
]

# Плоский индекс key→описание для быстрого поиска
_CONFIG_KEY_DESC: dict[str, str] = {
    k: desc
    for _, group in _CONFIG_GROUPS
    for k, desc in group
}


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
    "language": "_TRANSLATOR_LANGUAGE",   # особый: через translator-профиль
    "autodel": "_AUTODEL_DEFAULT",         # особый: глобальный default autodel
}

# (алиас → (config_key_или_метка, описание))
_SET_FRIENDLY: dict[str, tuple[str, str]] = {
    "stream_interval": ("TELEGRAM_STREAM_UPDATE_INTERVAL_SEC", "Интервал стриминга (сек)"),
    "reactions":       ("TELEGRAM_REACTIONS_ENABLED",          "Реакции 👀✅❌ (on/off)"),
    "weather_city":    ("DEFAULT_WEATHER_CITY",                "Город погоды по умолчанию"),
    "autodel":         ("_AUTODEL_DEFAULT",                    "Автоудаление ответов (сек, 0=выкл)"),
    "language":        ("_TRANSLATOR_LANGUAGE",                "Языковая пара переводчика"),
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
    lines.append("Также поддерживаются RAW ключи config: `!set TELEGRAM_STREAM_UPDATE_INTERVAL_SEC 3`")
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
            raise UserInputError(user_message="❌ `autodel` принимает число секунд (0 = выключить).")
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
            user_message=(
                "🔒 Управление правами `!scope grant/revoke` доступно только владельцу."
            )
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
            raise UserInputError(
                user_message="❌ Уровень должен быть `full` или `partial`."
            )
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
                    "❌ Формат: `!scope revoke <user_id>`\n"
                    "Пример: `!scope revoke 123456789`"
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


async def handle_voice(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление runtime voice-профилем userbot.

    Раньше это был только toggle-флаг. Теперь команда управляет целым профилем:
    enabled/speed/voice/delivery, чтобы owner мог нормально настраивать голосовой
    контур без ручной правки `.env`.
    """
    args = str(message.text or "").split()
    if len(args) == 1 or str(args[1] or "").strip().lower() in {"status", "show"}:
        await message.reply(_render_voice_profile(bot.get_voice_runtime_profile()))
        return

    sub = str(args[1] or "").strip().lower()
    if sub in {"toggle", "on", "off"}:
        if sub == "toggle":
            profile = bot.update_voice_runtime_profile(
                enabled=not bool(bot.get_voice_runtime_profile().get("enabled")),
                persist=True,
            )
        else:
            profile = bot.update_voice_runtime_profile(enabled=(sub == "on"), persist=True)
        await message.reply(_render_voice_profile(profile))
        return

    if sub == "speed":
        if len(args) < 3:
            raise UserInputError(user_message="❌ Укажи скорость: `!voice speed 1.25`")
        try:
            profile = bot.update_voice_runtime_profile(speed=float(args[2]), persist=True)
        except ValueError as exc:
            raise UserInputError(
                user_message="❌ Скорость должна быть числом, например `1.25`."
            ) from exc
        await message.reply(_render_voice_profile(profile))
        return

    if sub == "voice":
        if len(args) < 3:
            raise UserInputError(
                user_message="❌ Укажи voice-id, например `!voice voice ru-RU-SvetlanaNeural`."
            )
        profile = bot.update_voice_runtime_profile(voice=args[2], persist=True)
        await message.reply(_render_voice_profile(profile))
        return

    if sub == "delivery":
        if len(args) < 3:
            raise UserInputError(
                user_message="❌ Укажи режим: `!voice delivery text+voice` или `!voice delivery voice-only`."
            )
        delivery = str(args[2] or "").strip().lower()
        if delivery not in {"text+voice", "voice-only"}:
            raise UserInputError(
                user_message="❌ Поддерживаются только `text+voice` и `voice-only`."
            )
        profile = bot.update_voice_runtime_profile(delivery=delivery, persist=True)
        await message.reply(_render_voice_profile(profile))
        return

    if sub == "reset":
        profile = bot.update_voice_runtime_profile(
            enabled=False,
            speed=1.5,
            voice="ru-RU-DmitryNeural",
            delivery="text+voice",
            persist=True,
        )
        await message.reply(_render_voice_profile(profile))
        return

    if sub == "blocked":
        blocked = bot.get_voice_blocked_chats()
        if not blocked:
            await message.reply(
                "🎙️ **Voice blocklist** пуст.\n"
                "Добавить чат: `!voice block <chat_id>` (например, `!voice block -1001587432709`)."
            )
            return
        lines = "\n".join(f"- `{cid}`" for cid in blocked)
        await message.reply(
            f"🎙️ **Voice blocklist ({len(blocked)}):**\n{lines}\n\n"
            "Убрать: `!voice unblock <chat_id>`"
        )
        return

    if sub in {"block", "unblock"}:
        # Разрешаем два варианта:
        #   1) `!voice block <chat_id>` — явный id;
        #   2) `!voice block` без аргумента → берём id текущего чата, удобно
        #      когда owner прямо в проблемной группе пишет команду.
        target_chat_id: str
        if len(args) >= 3 and str(args[2] or "").strip():
            target_chat_id = str(args[2]).strip()
        else:
            chat_ref = getattr(message, "chat", None)
            inferred = getattr(chat_ref, "id", None) if chat_ref else None
            if inferred is None:
                raise UserInputError(
                    user_message=(
                        f"❌ Укажи chat_id: `!voice {sub} <chat_id>`\n"
                        "(например, `-1001587432709` для супергруппы)"
                    )
                )
            target_chat_id = str(inferred)

        try:
            if sub == "block":
                bot.add_voice_blocked_chat(target_chat_id, persist=True)
                prefix = "✅ Добавил в voice blocklist"
            else:
                bot.remove_voice_blocked_chat(target_chat_id, persist=True)
                prefix = "✅ Убрал из voice blocklist"
        except ValueError as exc:
            raise UserInputError(user_message=f"❌ {exc}") from exc

        profile = bot.get_voice_runtime_profile()
        await message.reply(f"{prefix}: `{target_chat_id}`\n\n{_render_voice_profile(profile)}")
        return

    raise UserInputError(user_message="❌ Неизвестная подкоманда voice. Используй `!voice status`.")


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


async def handle_translator(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление product-level translator runtime profile.

    Это не live call session-control. Команда управляет persisted owner-профилем,
    который потом читают owner UI, handoff и будущий iPhone companion flow.
    """
    # Парсим аргументы через message.command (Pyrogram заполняет его независимо от
    # prefix: "!translator status" и "Краб translator status" дают одинаковый
    # command=["translator","status"]). Fallback на split по text для тестов/совместимости.
    if getattr(message, "command", None):
        # message.command = ["translator", "sub", "arg2", "arg3"]
        # args[0]=command, args[1]=sub, args[2]=arg2, args[3]=arg3 — совместимо с дальнейшим кодом
        args = list(message.command)[:4]
        while len(args) < 4:
            args.append("")
    else:
        # fallback для тестов / нестандартных ситуаций
        raw_text = str(message.text or "").split(maxsplit=3)
        # Определяем смещение: если первое слово это command (начинается с !), offset=0 иначе 1
        if len(raw_text) >= 2 and not raw_text[0].startswith(("!", "/", ".")):
            # multi-word prefix: args[0]=prefix, args[1]=cmd, args[2]=sub, args[3]=arg2
            args = ["_cmd"] + raw_text[1:]
        else:
            args = raw_text
        while len(args) < 4:
            args.append("")
    sub = str(args[1] or "").strip().lower()
    if not sub or sub == "show":
        await message.reply(_render_translator_profile(bot.get_translator_runtime_profile()))
        return

    if sub == "status":
        # !translator status — показать и profile, и session state
        profile_text = _render_translator_profile(bot.get_translator_runtime_profile())
        session_text = _render_translator_session_state(bot.get_translator_session_state())
        await message.reply(f"{profile_text}\n\n{session_text}")
        return

    if sub in {"help", "?", "commands"}:
        await message.reply(
            "🔄 **Translator Commands:**\n"
            "`!translator` — текущий profile\n"
            "`!translator lang [pair]` — показать/сменить языковую пару\n"
            "`!translator auto` — переключить на auto-detect\n"
            "`!translator test <text>` — быстрый перевод текста\n"
            "`!translator history` — статистика переводов\n"
            "`!translator session start|stop|pause|resume|status` — управление сессией\n"
            "`!translator mode|strategy|phrase` — настройки профиля"
        )
        return

    if sub == "on":
        # !translator on → !translator session start
        profile = bot.get_translator_runtime_profile()
        label = str(args[2] or "").strip() or None
        current_state = bot.get_translator_session_state()
        state = bot.update_translator_session_state(
            session_status="active", label=label, persist=True
        )
        await message.reply(_render_translator_session_state(state))
        return

    if sub == "off":
        # !translator off → !translator session stop
        state = bot.update_translator_session_state(session_status="stopped", persist=True)
        await message.reply(_render_translator_session_state(state))
        return

    if sub == "auto":
        # !translator auto — shortcut для !translator lang auto-detect
        profile = bot.update_translator_runtime_profile(language_pair="auto-detect", persist=True)
        await message.reply(
            "🌐 Translator → **auto-detect** mode.\nЯзык будет определяться автоматически."
        )
        return

    if sub in {"lang", "language"}:
        if not str(args[2] or "").strip():
            # Без аргументов — показать текущую пару
            profile = bot.get_translator_runtime_profile()
            current = profile.get("language_pair", "es-ru")
            await message.reply(
                f"🌐 Текущая пара: **{current}**\n\n"
                f"Доступные: `es-ru`, `es-en`, `en-ru`, `auto-detect`\n"
                f"Сменить: `!translator lang auto-detect`"
            )
            return
        value = str(args[2] or "").strip().lower()
        if value not in ALLOWED_LANGUAGE_PAIRS:
            raise UserInputError(
                user_message="❌ Поддерживаются только `es-ru`, `es-en`, `en-ru`, `auto-detect`."
            )
        profile = bot.update_translator_runtime_profile(language_pair=value, persist=True)
        await message.reply(_render_translator_profile(profile))
        return

    if sub == "mode":
        if len(args) < 3:
            raise UserInputError(user_message="❌ Укажи mode: `!translator mode bilingual`.")
        value = str(args[2] or "").strip().lower()
        if value not in ALLOWED_TRANSLATION_MODES:
            raise UserInputError(
                user_message="❌ Поддерживаются только `bilingual`, `auto_to_ru`, `auto_to_en`."
            )
        profile = bot.update_translator_runtime_profile(translation_mode=value, persist=True)
        await message.reply(_render_translator_profile(profile))
        return

    if sub in {"strategy", "voice_strategy"}:
        if len(args) < 3:
            raise UserInputError(
                user_message="❌ Укажи strategy: `!translator strategy voice-first`."
            )
        value = str(args[2] or "").strip().lower()
        if value not in ALLOWED_VOICE_STRATEGIES:
            raise UserInputError(
                user_message="❌ Поддерживаются только `voice-first` и `subtitles-first`."
            )
        profile = bot.update_translator_runtime_profile(voice_strategy=value, persist=True)
        await message.reply(_render_translator_profile(profile))
        return

    toggle_fields = {
        "ordinary": "ordinary_calls_enabled",
        "internet": "internet_calls_enabled",
        "subtitles": "subtitles_enabled",
        "timeline": "timeline_enabled",
        "summary": "summary_enabled",
        "diagnostics": "diagnostics_enabled",
    }
    if sub in toggle_fields:
        if len(args) < 3:
            raise UserInputError(
                user_message=f"❌ Укажи состояние: `!translator {sub} on` или `!translator {sub} off`."
            )
        enabled = _parse_toggle_arg(args[2], field_name=f"!translator {sub}")
        profile = bot.update_translator_runtime_profile(
            **{toggle_fields[sub]: enabled},
            persist=True,
        )
        await message.reply(_render_translator_profile(profile))
        return

    if sub == "phrase":
        action = str(args[2] or "").strip().lower() if len(args) >= 3 else ""
        current = bot.get_translator_runtime_profile()
        quick_phrases = list(current.get("quick_phrases") or [])

        if action == "add":
            if len(args) < 4 or not str(args[3] or "").strip():
                raise UserInputError(
                    user_message="❌ Укажи фразу: `!translator phrase add Повтори медленнее`."
                )
            quick_phrases.append(str(args[3]).strip())
            profile = bot.update_translator_runtime_profile(
                quick_phrases=quick_phrases,
                persist=True,
            )
            await message.reply(_render_translator_profile(profile))
            return

        if action == "remove":
            if len(args) < 4:
                raise UserInputError(
                    user_message="❌ Укажи номер фразы: `!translator phrase remove 2`."
                )
            try:
                index = int(str(args[3]).strip()) - 1
            except ValueError as exc:
                raise UserInputError(
                    user_message="❌ Номер фразы должен быть целым числом."
                ) from exc
            if index < 0 or index >= len(quick_phrases):
                raise UserInputError(
                    user_message=f"❌ Нет фразы с номером `{index + 1}`. Сейчас их: `{len(quick_phrases)}`."
                )
            quick_phrases.pop(index)
            profile = bot.update_translator_runtime_profile(
                quick_phrases=quick_phrases,
                persist=True,
            )
            await message.reply(_render_translator_profile(profile))
            return

        raise UserInputError(
            user_message="❌ Используй `!translator phrase add <текст>` или `!translator phrase remove <номер>`."
        )

    if sub == "history":
        # !translator history [N] — последние N переводов из session state (default 5)
        # Парсим необязательный аргумент N
        raw_text_parts = str(message.text or "").split(None, 2)
        raw_n = raw_text_parts[2].strip() if len(raw_text_parts) > 2 else ""
        try:
            n_show = max(1, min(20, int(raw_n))) if raw_n.isdigit() else 5
        except (ValueError, TypeError):
            n_show = 5

        state = bot.get_translator_session_state()
        history: list[dict] = list(state.get("history") or [])
        recent = history[-n_show:] if history else []

        if not recent:
            await message.reply("📋 История переводов пуста.")
            return

        lines = ["📋 **Последние переводы:**", ""]
        for idx, entry in enumerate(reversed(recent), start=1):
            src = entry.get("src_lang", "?")
            tgt = entry.get("tgt_lang", "?")
            latency_s = entry.get("latency_ms", 0) / 1000
            orig = entry.get("original", "")[:120]
            trans = entry.get("translation", "")[:120]
            lines.append(f"{idx}. [{src}→{tgt}] {latency_s:.1f}s")
            lines.append(f'   "{orig}" → "{trans}"')
            lines.append("")
        await message.reply("\n".join(lines).rstrip())
        return

    if sub == "test":
        # !translator test <text> — быстрый inline перевод для тестирования
        # args split maxsplit=3 обрезает текст → берём всё после "!translator test "
        raw_text = str(message.text or "")
        test_idx = raw_text.lower().find("test")
        test_text = raw_text[test_idx + 4 :].strip() if test_idx >= 0 else ""
        if not test_text:
            raise UserInputError(user_message="❌ Формат: `!translator test Buenos días amigo`")
        try:
            from ..core.language_detect import detect_language, resolve_translation_pair
            from ..core.translator_engine import translate_text
            from ..openclaw_client import openclaw_client as _oc

            detected = detect_language(test_text)
            if not detected:
                await message.reply("❌ Не удалось определить язык.")
                return
            profile = bot.get_translator_runtime_profile()
            src, tgt = resolve_translation_pair(detected, profile.get("language_pair", "es-ru"))
            if src == tgt:
                await message.reply(f"ℹ️ Язык совпадает ({src}), перевод не нужен.")
                return
            result = await translate_text(test_text, src, tgt, openclaw_client=_oc)
            await message.reply(
                f"🔄 {src}→{tgt} ({result.latency_ms}ms)\n"
                f"**{result.original}**\n"
                f"_{result.translated}_"
            )
        except Exception as exc:
            await message.reply(f"❌ Ошибка: {str(exc)[:200]}")
        return

    if sub == "session":
        action = str(args[2] or "").strip().lower() if len(args) >= 3 else "status"
        if action in {"status", "show"}:
            await message.reply(
                _render_translator_session_state(bot.get_translator_session_state())
            )
            return
        if action == "start":
            label = str(args[3] or "").strip() if len(args) >= 4 else ""
            profile = bot.get_translator_runtime_profile()
            # Per-chat: добавляем chat_id в active_chats если не global
            current_chat_id = str(message.chat.id)
            current_state = bot.get_translator_session_state()
            active_chats = list(current_state.get("active_chats") or [])
            if current_chat_id not in active_chats:
                active_chats.append(current_chat_id)
            state = bot.update_translator_session_state(
                session_status="active",
                translation_muted=False,
                active_session_label=label,
                active_chats=active_chats,
                last_language_pair=profile.get("language_pair"),
                last_event="session_started",
                persist=True,
            )
            await message.reply(_render_translator_session_state(state))
            return
        if action == "pause":
            state = bot.update_translator_session_state(
                session_status="paused",
                last_event="session_paused",
                persist=True,
            )
            await message.reply(_render_translator_session_state(state))
            return
        if action == "resume":
            state = bot.update_translator_session_state(
                session_status="active",
                last_event="session_resumed",
                persist=True,
            )
            await message.reply(_render_translator_session_state(state))
            return
        if action == "stop":
            # Per-chat: убираем этот чат из active_chats
            current_chat_id = str(message.chat.id)
            current_state = bot.get_translator_session_state()
            active_chats = [
                c for c in (current_state.get("active_chats") or []) if c != current_chat_id
            ]
            # Если active_chats пуст — полный stop; иначе только deactivate этот чат
            if not active_chats:
                state = bot.update_translator_session_state(
                    session_status="idle",
                    translation_muted=False,
                    active_session_label="",
                    active_chats=[],
                    last_event="session_stopped",
                    persist=True,
                )
            else:
                state = bot.update_translator_session_state(
                    active_chats=active_chats,
                    last_event=f"chat_removed:{current_chat_id}",
                    persist=True,
                )
            await message.reply(_render_translator_session_state(state))
            return
        if action == "clear":
            state = bot.update_translator_session_state(
                clear_timeline=True,
                persist=True,
            )
            await message.reply(_render_translator_session_state(state))
            return
        if action in {"mute", "unmute"}:
            state = bot.update_translator_session_state(
                translation_muted=(action == "mute"),
                last_event="translation_muted" if action == "mute" else "translation_unmuted",
                persist=True,
            )
            await message.reply(_render_translator_session_state(state))
            return
        if action == "replay":
            raw = str(args[3] or "").strip() if len(args) >= 4 else ""
            if "|" not in raw:
                raise UserInputError(
                    user_message="❌ Используй `!translator session replay original | translation`."
                )
            original, translation = [part.strip() for part in raw.split("|", 1)]
            if not original or not translation:
                raise UserInputError(user_message="❌ Для replay нужны и original, и translation.")
            profile = bot.get_translator_runtime_profile()
            state = bot.update_translator_session_state(
                last_translated_original=original,
                last_translated_translation=translation,
                last_language_pair=profile.get("language_pair"),
                last_event="line_replayed",
                persist=True,
            )
            await message.reply(_render_translator_session_state(state))
            return
        raise UserInputError(
            user_message="❌ Используй `!translator session status|start|pause|resume|stop|mute|unmute|replay|clear`."
        )

    if sub == "reset":
        profile = bot.update_translator_runtime_profile(
            **dict(default_translator_runtime_profile),
            persist=True,
        )
        await message.reply(_render_translator_profile(profile))
        return

    raise UserInputError(
        user_message="❌ Неизвестная подкоманда translator. Используй `!translator status`."
    )


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


def _format_uptime_str(elapsed_sec: float) -> str:
    """Форматирует секунды в читаемый uptime: 1д 2ч 15м."""
    elapsed = int(elapsed_sec)
    days, rem = divmod(elapsed, 86400)
    hours, rem2 = divmod(rem, 3600)
    mins = rem2 // 60
    parts: list[str] = []
    if days:
        parts.append(f"{days}д")
    if hours:
        parts.append(f"{hours}ч")
    parts.append(f"{mins}м")
    return " ".join(parts)


async def handle_sysinfo(bot: "KraabUserbot", message: Message) -> None:
    """Расширенная информация о хосте: macOS, CPU, RAM, Disk, Network, Python, Krab."""
    import os
    import platform
    import socket

    import psutil

    lines: list[str] = ["🖥️ **System Info**", "─────────────"]

    # macOS версия
    try:
        mac_ver = platform.mac_ver()[0] or platform.version()
        darwin_ver = platform.release()
        lines.append(f"macOS: `{mac_ver}` (Darwin {darwin_ver})")
    except Exception:
        lines.append(f"OS: `{platform.system()} {platform.release()}`")

    # CPU: модель + load average
    try:
        cpu_model = platform.processor() or platform.machine()
        load1, load5, load15 = os.getloadavg()
        lines.append(f"CPU: `{cpu_model}` | Load: {load1:.1f}, {load5:.1f}, {load15:.1f}")
    except Exception:
        try:
            lines.append(f"CPU: Load {psutil.cpu_percent()}%")
        except Exception:
            lines.append("CPU: N/A")

    # RAM: total/used/free через psutil
    try:
        vm = psutil.virtual_memory()
        total_gb = vm.total / 1024**3
        used_gb = vm.used / 1024**3
        pct = vm.percent
        lines.append(f"RAM: {used_gb:.1f} / {total_gb:.1f} GB ({pct:.0f}%)")
    except Exception:
        lines.append("RAM: N/A")

    # Disk: total/used/free (корневой раздел)
    try:
        disk = psutil.disk_usage("/")
        d_total = disk.total / 1024**3
        d_used = disk.used / 1024**3
        d_pct = disk.percent
        lines.append(f"Disk: {d_used:.0f} / {d_total:.0f} GB ({d_pct:.0f}%)")
    except Exception:
        lines.append("Disk: N/A")

    # Network: IP адрес
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        lines.append(f"Network: `{local_ip}`")
    except Exception:
        lines.append("Network: N/A")

    # Python версия
    py_ver = platform.python_version()
    lines.append(f"Python: `{py_ver}`")

    # Krab uptime + PID
    try:
        elapsed = time.time() - bot._session_start_time
        uptime_str = _format_uptime_str(elapsed)
        krab_pid = os.getpid()
        lines.append(f"Krab: PID {krab_pid} | Uptime: {uptime_str}")
    except Exception:
        import os as _os
        lines.append(f"Krab: PID {_os.getpid()}")

    await message.reply("\n".join(lines))


async def handle_uptime(bot: "KraabUserbot", message: Message) -> None:
    """Uptime системы macOS + Краба + OpenClaw gateway."""
    import re
    import time as _t

    lines: list[str] = ["⏱️ **Uptime**", "─────────────"]

    # macOS system uptime через sysctl kern.boottime
    try:
        result = subprocess.run(
            ["sysctl", "-n", "kern.boottime"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        m = re.search(r"sec\s*=\s*(\d+)", result.stdout)
        if m:
            boot_sec = int(m.group(1))
            sys_elapsed = _t.time() - boot_sec
            lines.append(f"macOS: `{_format_uptime_str(sys_elapsed)}`")
        else:
            lines.append("macOS: N/A")
    except Exception:
        lines.append("macOS: N/A")

    # Krab uptime
    try:
        elapsed = time.time() - bot._session_start_time
        lines.append(f"Краб: `{_format_uptime_str(elapsed)}`")
    except Exception:
        lines.append("Краб: N/A")

    # OpenClaw gateway uptime — через health endpoint
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("http://127.0.0.1:18789/health")
            if resp.status_code == 200:
                data = resp.json()
                oc_uptime = data.get("uptime") or data.get("uptime_seconds")
                if oc_uptime is not None:
                    lines.append(f"OpenClaw: `{_format_uptime_str(float(oc_uptime))}`")
                else:
                    lines.append("OpenClaw: ✅ Online")
            else:
                lines.append("OpenClaw: ❌ Offline")
    except Exception:
        lines.append("OpenClaw: ❌ Недоступен")

    await message.reply("\n".join(lines))


async def handle_panel(bot: "KraabUserbot", message: Message) -> None:
    """Графическая панель управления."""
    await handle_status(bot, message)


async def handle_version(bot: "KraabUserbot", message: Message) -> None:
    """Информация о версии Краба: git commit, branch, Python, Pyrogram, OpenClaw."""
    import platform

    del bot

    lines: list[str] = ["🦀 **Krab Version**", "─────"]

    # Git commit hash (короткий 7 символов)
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(pathlib.Path(__file__).parent.parent.parent),
        )
        commit = result.stdout.strip() or "unknown"
    except Exception:
        commit = "unknown"
    lines.append(f"Commit: `{commit}`")

    # Текущая ветка
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(pathlib.Path(__file__).parent.parent.parent),
        )
        branch = result.stdout.strip() or "unknown"
    except Exception:
        branch = "unknown"
    lines.append(f"Branch: `{branch}`")

    # Дата последнего коммита (только дата YYYY-MM-DD)
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ci"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(pathlib.Path(__file__).parent.parent.parent),
        )
        commit_date = result.stdout.strip()[:10] if result.stdout.strip() else "unknown"
    except Exception:
        commit_date = "unknown"
    lines.append(f"Date: `{commit_date}`")

    # Python версия
    py_ver = platform.python_version()
    lines.append(f"Python: `{py_ver}`")

    # Pyrogram версия
    try:
        import pyrogram
        pyro_ver = pyrogram.__version__
    except Exception:
        pyro_ver = "unknown"
    lines.append(f"Pyrogram: `{pyro_ver}`")

    # OpenClaw версия через CLI
    try:
        result = subprocess.run(
            ["openclaw", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        oc_raw = (result.stdout.strip() or result.stderr.strip()).splitlines()
        oc_ver = oc_raw[0] if oc_raw else "unknown"
    except Exception:
        oc_ver = "unknown"
    lines.append(f"OpenClaw: `{oc_ver}`")

    await message.reply("\n".join(lines))


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


async def handle_restart(bot: "KraabUserbot", message: Message) -> None:
    """Перезапуск Краба через launchctl с подтверждением.

    !restart          — запросить подтверждение
    !restart confirm  — выполнить перезапуск через launchctl kickstart -k
    !restart status   — показать uptime и PID текущего процесса
    """
    from ..core.subprocess_env import clean_subprocess_env  # noqa: PLC0415

    # Метка LaunchAgent для ai.krab.core
    krab_launchd_label = "ai.krab.core"

    args = bot._get_command_args(message).strip().lower()

    # ── !restart status ──────────────────────────────────────────────────────
    if args == "status":
        pid = os.getpid()
        uptime_str = "?"
        try:
            elapsed = time.time() - bot._session_start_time
            uptime_str = _format_uptime_str(elapsed)
        except Exception:
            pass

        # Статус launchd-сервиса (Running / Stopped)
        launchd_status = "?"
        try:
            uid = os.getuid()
            proc = subprocess.run(
                ["launchctl", "print", f"gui/{uid}/{krab_launchd_label}"],
                capture_output=True,
                text=True,
                timeout=5,
                env=clean_subprocess_env(),
            )
            if "pid" in proc.stdout.lower():
                import re as _re2

                pid_match = _re2.search(r"pid\s*=\s*(\d+)", proc.stdout, _re2.IGNORECASE)
                if pid_match:
                    launchd_pid = pid_match.group(1)
                    launchd_status = f"Running (PID {launchd_pid})"
                else:
                    launchd_status = "Running"
            else:
                launchd_status = "Stopped"
        except Exception:
            launchd_status = "N/A"

        lines = [
            "🦀 **Krab Status**",
            f"PID: `{pid}`",
            f"Uptime: `{uptime_str}`",
            f"LaunchAgent: `{krab_launchd_label}` — {launchd_status}",
            "",
            "Перезапуск: `!restart confirm`",
        ]
        await message.reply("\n".join(lines))
        return

    # ── !restart confirm — выполнить перезапуск ──────────────────────────────
    if args == "confirm":
        await message.reply("🔄 Перезапускаю Краба...")
        try:
            uid = os.getuid()
            proc = subprocess.run(
                ["launchctl", "kickstart", "-k", f"gui/{uid}/{krab_launchd_label}"],
                capture_output=True,
                text=True,
                timeout=10,
                env=clean_subprocess_env(),
            )
            if proc.returncode != 0:
                # launchctl не сработал — запасной вариант через sys.exit
                logger.warning(
                    "launchctl kickstart вернул %d: %s",
                    proc.returncode,
                    (proc.stderr or proc.stdout).strip(),
                )
                sys.exit(42)
        except FileNotFoundError:
            # launchctl недоступен (например, в тестах) — запасной sys.exit
            sys.exit(42)
        except Exception as exc:
            logger.error("Ошибка при запуске launchctl: %s", exc)
            sys.exit(42)
        return

    # ── !restart (без аргументов) — запросить подтверждение ─────────────────
    await message.reply(
        "⚠️ **Перезапуск Краба**\n\n"
        "Это остановит и заново запустит процесс через launchd.\n\n"
        "Подтверди командой:\n`!restart confirm`\n\n"
        "Текущий статус: `!restart status`"
    )


async def handle_agent(bot: "KraabUserbot", message: Message) -> None:
    """Управление агентами: !agent new <name> <prompt>."""
    text = bot._get_command_args(message)
    if not text:
        raise UserInputError(
            user_message=(
                "🕵️‍♂️ Использование:\n"
                "- `!agent new <имя> <промпт>`\n"
                "- `!agent list`\n"
                "- `!agent swarm <тема>`\n"
                "- `!agent swarm loop [N] <тема>`"
            )
        )
    if text.startswith("list"):
        await message.reply(f"🕵️‍♂️ **Доступные агенты:**\n\n{list_roles()}")
        return
    if text.startswith("swarm"):
        swarm_args = text[5:].strip()
        if not swarm_args:
            raise UserInputError(user_message="🐝 Формат: `!agent swarm <тема>`")

        topic = swarm_args
        is_loop = False
        loop_rounds = 2
        if swarm_args.startswith("loop"):
            is_loop = True
            loop_payload = swarm_args[4:].strip()
            if not loop_payload:
                raise UserInputError(user_message="🐝 Формат: `!agent swarm loop [N] <тема>`")
            first, *rest = loop_payload.split(" ", 1)
            if first.isdigit():
                loop_rounds = int(first)
                topic = rest[0].strip() if rest else ""
            else:
                topic = loop_payload
            if not topic:
                raise UserInputError(user_message="🐝 Формат: `!agent swarm loop [N] <тема>`")

        max_rounds = int(getattr(config, "SWARM_LOOP_MAX_ROUNDS", 3) or 3)
        next_round_clip = int(getattr(config, "SWARM_LOOP_NEXT_ROUND_CLIP", 4000) or 4000)
        safe_rounds = max(1, min(loop_rounds, max_rounds))

        if is_loop:
            status = await message.reply(
                f"🐝 Запускаю роевой loop: {safe_rounds} раунд(а), роли аналитик → критик → интегратор..."
            )
        else:
            status = await message.reply(
                "🐝 Запускаю роевой раунд: аналитик → критик → интегратор..."
            )
        room = AgentRoom()
        role_prompt = get_role_prompt(getattr(bot, "current_role", "default"))
        room_chat_id = f"swarm:{message.chat.id}"
        router = _AgentRoomRouterAdapter(
            chat_id=room_chat_id,
            system_prompt=role_prompt,
        )
        if is_loop:
            result = await room.run_loop(
                topic,
                router,
                rounds=safe_rounds,
                max_rounds=max_rounds,
                next_round_clip=next_round_clip,
            )
        else:
            result = await room.run_round(topic, router)
        chunks = _split_text_for_telegram(result)
        await status.edit(chunks[0])
        for part in chunks[1:]:
            await message.reply(part)
        return
    if text.startswith("new"):
        parts = text[3:].strip().split(" ", 1)
        if len(parts) < 2:
            raise UserInputError(user_message="❌ Ошибка: укажите имя и промпт.")
        name = parts[0].strip()
        prompt = parts[1].strip().strip('"').strip("'")
        if save_role(name, prompt):
            await message.reply(
                f"🕵️‍♂️ **Агент создан:** `{name}`\n\nТеперь можно использовать: `стань {name}`"
            )
        else:
            await message.reply("❌ Ошибка при сохранении агента.")


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

    # Отправляем одним или двумя сообщениями (Telegram лимит 4096)
    page_limit = 4000
    combined = part1 + "\n\n" + part2
    if len(combined) <= page_limit:
        await message.reply(combined)
    else:
        await message.reply(part1)
        await message.reply(part2)


async def handle_diagnose(bot: "KraabUserbot", message: Message) -> None:
    """Диагностика системы (!diagnose)."""
    msg = await message.reply("🏥 **Запускаю диагностику системы...**")
    report = []
    report.append("**Config:**")
    report.append(f"- OPENCLAW_URL: `{config.OPENCLAW_URL}`")
    report.append(f"- LM_STUDIO_URL: `{config.LM_STUDIO_URL}`")
    if await is_lm_studio_available(config.LM_STUDIO_URL, timeout=2.0):
        report.append("- LM Studio: ✅ OK (Available)")
    else:
        report.append("- LM Studio: ❌ Offline")
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{config.OPENCLAW_URL}/health")
            if resp.status_code == 200:
                report.append("- OpenClaw: ✅ OK (Healthy)")
            else:
                report.append(f"- OpenClaw: ⚠️ Error ({resp.status_code})")
    except (httpx.RequestError, httpx.ConnectError, httpx.TimeoutException, OSError) as e:
        report.append(f"- OpenClaw: ❌ Unreachable ({str(e)})")
        report.append("  _Совет: Проверьте, запущен ли Gateway и совпадает ли порт (обычно 18792)_")
    await msg.edit("\n".join(report))


async def handle_debug(bot: "KraabUserbot", message: Message) -> None:
    """
    Отладочная информация для разработчика (!debug [sessions|tasks|gc]).

    Субкоманды:
    - `!debug`          — сводка: tasks, timers, sessions, rate limiter, last error
    - `!debug sessions` — список активных OpenClaw сессий с размером
    - `!debug tasks`    — список asyncio задач
    - `!debug gc`       — принудительный GC + статистика

    Owner-only.
    """
    import gc

    # Проверка прав: только владелец
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!debug` доступен только владельцу.")

    raw_args = bot._get_command_args(message).strip().lower()
    sub = raw_args.split()[0] if raw_args else ""

    if sub == "tasks":
        # Список asyncio задач
        tasks = list(asyncio.all_tasks())
        lines = [f"⚙️ **asyncio tasks** (`{len(tasks)}` total)", "───────────────"]
        for t in sorted(tasks, key=lambda x: x.get_name()):
            name = t.get_name()
            done_str = "done" if t.done() else "running"
            coro = t.get_coro()
            coro_name = getattr(coro, "__qualname__", None) or getattr(coro, "__name__", str(coro))
            lines.append(f"- `{name}` [{done_str}] — `{coro_name}`")
        if len(lines) > 32:
            lines = lines[:32]
            lines.append(f"…и ещё `{len(tasks) - 30}` задач")
        await message.reply("\n".join(lines))
        return

    if sub == "sessions":
        # Список OpenClaw сессий с размером
        sessions_raw: dict = {}
        try:
            sessions_raw = dict(openclaw_client._sessions)  # type: ignore[attr-defined]
        except AttributeError:
            pass
        lines = [
            f"💬 **OpenClaw sessions** (`{len(sessions_raw)}` active)",
            "───────────────────────",
        ]
        if not sessions_raw:
            lines.append("- сессий нет")
        else:
            for sid, msgs in sorted(sessions_raw.items(), key=lambda x: -len(x[1])):
                msg_list = list(msgs) if msgs is not None else []
                size = len(msg_list)
                # Грубая оценка токенов: 4 символа ~ 1 токен
                raw_text = " ".join(
                    str(m.get("content", "")) for m in msg_list if isinstance(m, dict)
                )
                approx_tokens = max(1, len(raw_text) // 4)
                lines.append(
                    f"- `{sid}` — `{size}` сообщений (~{approx_tokens:,} tok)".replace(",", "_")
                )
        await message.reply("\n".join(lines))
        return

    if sub == "gc":
        # Принудительный garbage collection
        before = gc.get_count()
        collected = gc.collect()
        after = gc.get_count()
        unreachable = gc.garbage
        lines = [
            "🗑 **Garbage Collection**",
            "─────────────────────",
            f"До:    gen0={before[0]}, gen1={before[1]}, gen2={before[2]}",
            f"После: gen0={after[0]}, gen1={after[1]}, gen2={after[2]}",
            f"Собрано объектов: `{collected}`",
            f"gc.garbage (unreachable): `{len(unreachable)}`",
        ]
        await message.reply("\n".join(lines))
        return

    # Сводка по умолчанию
    from ..core.telegram_rate_limiter import telegram_rate_limiter

    # asyncio tasks
    all_tasks = list(asyncio.all_tasks())
    task_count = len(all_tasks)
    done_count = sum(1 for t in all_tasks if t.done())

    # Pending timers из _active_timers (модуль-уровень)
    timer_count = len(_active_timers)

    # Активные OpenClaw сессии
    try:
        sessions_map: dict = dict(openclaw_client._sessions)  # type: ignore[attr-defined]
    except AttributeError:
        sessions_map = {}
    session_count = len(sessions_map)
    total_msgs = sum(len(list(v)) for v in sessions_map.values() if v is not None)

    # Rate limiter stats
    rl = telegram_rate_limiter.stats()
    rl_str = (
        f"cap {int(rl.get('max_per_sec', 0))} req/s · "
        f"в окне {int(rl.get('current_in_window', 0))} · "
        f"total {int(rl.get('total_acquired', 0))} · "
        f"waited {int(rl.get('total_waited', 0))}"
    )

    # Последняя ошибка из proactive_watch (если доступна)
    last_error_str = "—"
    try:
        err_digest = proactive_watch.get_error_digest()  # type: ignore[attr-defined]
        if err_digest:
            last_err = err_digest[-1] if isinstance(err_digest, list) else None
            if last_err and isinstance(last_err, dict):
                last_error_str = (
                    f"{last_err.get('type', '?')}: {str(last_err.get('msg', '?'))[:80]}"
                )
    except Exception:  # noqa: BLE001
        pass

    lines = [
        "🐛 **Debug Info**",
        "─────────────────",
        (
            f"asyncio tasks: `{task_count}` total "
            f"(`{done_count}` done, `{task_count - done_count}` running)"
        ),
        f"Pending timers: `{timer_count}`",
        f"OpenClaw sessions: `{session_count}` (total `{total_msgs}` messages)",
        f"Rate limiter: `{rl_str}`",
        f"Last error: `{last_error_str}`",
        "",
        "Субкоманды:",
        "`!debug sessions` — список сессий",
        "`!debug tasks`    — список задач",
        "`!debug gc`       — garbage collection",
    ]
    await message.reply("\n".join(lines))


_REMIND_HELP = (
    "⏰ **Напоминания — форматы:**\n\n"
    "**Создать:**\n"
    "- `!remind me in 30m купить молоко`\n"
    "- `!remind in 2 hours позвонить`\n"
    "- `!remind at 15:00 встреча`\n"
    "- `!remind tomorrow 9:00 зарядка`\n"
    "- `!remind через 20 минут проверить почту`\n"
    "- `!remind в 18:30 созвон`\n"
    "- `!remind 10m | выпить воды`\n\n"
    "**Управление:**\n"
    "- `!remind list` — список активных\n"
    "- `!remind cancel <id>` — отменить\n"
)


async def handle_remind(bot: "KraabUserbot", message: Message) -> None:
    """
    Управляет напоминаниями с natural language парсингом.

    Форматы создания:
    - `!remind me in 30m купить молоко`
    - `!remind in 2 hours позвонить`
    - `!remind at 15:00 встреча`
    - `!remind tomorrow 9:00 зарядка`
    - `!remind через 20 минут проверить почту`
    - `!remind в 18:30 созвон`
    - `!remind 10m | выпить воды`

    Управление:
    - `!remind list` — список pending напоминаний
    - `!remind cancel <id>` — отменить напоминание
    """
    if not bool(getattr(config, "SCHEDULER_ENABLED", False)):
        raise UserInputError(
            user_message=(
                "⏰ Scheduler сейчас выключен (`SCHEDULER_ENABLED=0`).\n"
                "Включи его (`!set SCHEDULER_ENABLED 1`) и перезапусти Krab."
            )
        )

    raw_args = bot._get_command_args(message).strip()

    # --- Субкоманда: list ---
    if raw_args.lower() in ("list", "список", "ls"):
        rows = krab_scheduler.list_reminders(chat_id=str(message.chat.id))
        if not rows:
            await message.reply("⏰ Активных напоминаний нет.")
            return
        lines = ["⏰ **Активные напоминания:**"]
        for item in rows:
            due_raw = str(item.get("due_at_iso") or "")
            try:
                from datetime import datetime as _dt
                due_label = _dt.fromisoformat(due_raw).strftime("%d.%m %H:%M")
            except Exception:  # noqa: BLE001
                due_label = due_raw
            text = str(item.get("text") or "")
            rid = str(item.get("reminder_id") or "")
            lines.append(f"- `{rid}` · `{due_label}` · {text}")
        payload = "\n".join(lines)
        chunks = _split_text_for_telegram(payload, limit=3600)
        await message.reply(chunks[0])
        for part in chunks[1:]:
            await message.reply(part)
        return

    # --- Субкоманда: cancel <id> ---
    cancel_match = re.match(r"^(?:cancel|отмена|rm|del)\s+(\S+)$", raw_args, re.IGNORECASE)
    if cancel_match:
        rid = cancel_match.group(1)
        ok = krab_scheduler.remove_reminder(rid)
        if ok:
            await message.reply(f"🗑️ Напоминание `{rid}` отменено.")
        else:
            await message.reply(f"⚠️ Напоминание `{rid}` не найдено.")
        return

    # --- Без аргументов: справка ---
    if not raw_args:
        raise UserInputError(user_message=_REMIND_HELP)

    # --- Создание напоминания ---
    time_spec, reminder_text = split_reminder_input(raw_args)
    if not time_spec or not reminder_text:
        raise UserInputError(
            user_message=(
                "⏰ Не удалось разобрать время/текст.\n\n"
                + _REMIND_HELP
            )
        )

    try:
        due_at = parse_due_time(time_spec)
    except ValueError:
        raise UserInputError(
            user_message=(
                "❌ Не удалось распознать время.\n\n"
                + _REMIND_HELP
            )
        )

    if hasattr(bot, "_sync_scheduler_runtime"):
        try:
            bot._sync_scheduler_runtime()
        except Exception as exc:  # noqa: BLE001
            logger.warning("scheduler_runtime_sync_failed_in_remind", error=str(exc))

    if not krab_scheduler.is_started:
        try:
            krab_scheduler.start()
        except RuntimeError:
            raise UserInputError(user_message="❌ Scheduler не запущен в runtime loop.")

    reminder_id = krab_scheduler.add_reminder(
        chat_id=str(message.chat.id),
        text=reminder_text,
        due_at=due_at,
    )
    due_label = due_at.astimezone().strftime("%d.%m.%Y %H:%M")
    await message.reply(
        "✅ Напоминание создано.\n"
        f"- ID: `{reminder_id}`\n"
        f"- Когда: `{due_label}`\n"
        f"- Текст: {reminder_text}\n\n"
        f"Отменить: `!remind cancel {reminder_id}`"
    )


async def handle_reminders(bot: "KraabUserbot", message: Message) -> None:
    """Показывает pending reminders текущего чата."""
    rows = krab_scheduler.list_reminders(chat_id=str(message.chat.id))
    if not rows:
        await message.reply("⏰ Активных напоминаний нет.")
        return
    lines = ["⏰ **Активные напоминания:**"]
    for item in rows:
        due = str(item.get("due_at_iso") or "")
        text = str(item.get("text") or "")
        rid = str(item.get("reminder_id") or "")
        lines.append(f"- `{rid}` · `{due}` · {text}")
    payload = "\n".join(lines)
    chunks = _split_text_for_telegram(payload, limit=3600)
    await message.reply(chunks[0])
    for part in chunks[1:]:
        await message.reply(part)


async def handle_rm_remind(bot: "KraabUserbot", message: Message) -> None:
    """Удаляет reminder по ID."""
    raw_args = bot._get_command_args(message).strip()
    if not raw_args:
        raise UserInputError(user_message="🗑️ Формат: `!rm_remind <id>`")
    ok = krab_scheduler.remove_reminder(raw_args)
    if ok:
        await message.reply(f"🗑️ Напоминание `{raw_args}` удалено.")
    else:
        await message.reply(f"⚠️ Напоминание `{raw_args}` не найдено.")


async def handle_cronstatus(bot: "KraabUserbot", message: Message) -> None:
    """Отдает runtime-статус scheduler."""
    status = krab_scheduler.get_status()
    # Тех-вывод: группа → редирект в ЛС
    await _reply_tech(
        message, bot,
        "🧭 **Scheduler status**\n"
        f"- enabled (config): `{status.get('scheduler_enabled')}`\n"
        f"- started: `{status.get('started')}`\n"
        f"- pending: `{status.get('pending_count')}`\n"
        f"- next_due_at: `{status.get('next_due_at') or '-'}`\n"
        f"- storage: `{status.get('storage_path')}`"
    )


# ---------------------------------------------------------------------------
# !cron — управление OpenClaw cron jobs из Telegram
# ---------------------------------------------------------------------------

def _cron_read_jobs() -> list[dict]:
    """Читает jobs.json напрямую (без gateway), возвращает список dict."""
    jobs_path = pathlib.Path.home() / ".openclaw" / "cron" / "jobs.json"
    try:
        data = json.loads(jobs_path.read_text(encoding="utf-8", errors="replace"))
        return data.get("jobs", []) if isinstance(data, dict) else []
    except (OSError, json.JSONDecodeError):
        return []


def _cron_write_jobs(jobs: list[dict]) -> None:
    """Записывает обновлённый список jobs обратно в jobs.json."""
    jobs_path = pathlib.Path.home() / ".openclaw" / "cron" / "jobs.json"
    payload = {"version": 1, "jobs": jobs}
    jobs_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _cron_format_schedule(job: dict) -> str:
    """Возвращает читаемую строку расписания для job."""
    schedule = job.get("schedule") or {}
    kind = str(schedule.get("kind") or "unknown").lower()
    if kind == "every":
        every_ms = int(schedule.get("everyMs") or 0)
        if every_ms <= 0:
            return "каждые ?"
        if every_ms % 3600000 == 0:
            return f"каждые {every_ms // 3600000}ч"
        if every_ms % 60000 == 0:
            return f"каждые {every_ms // 60000}м"
        return f"каждые {every_ms // 1000}с"
    if kind == "cron":
        expr = str(schedule.get("expr") or "?")
        tz = str(schedule.get("tz") or "").strip()
        return f"cron `{expr}`" + (f" ({tz})" if tz else "")
    return kind


def _cron_format_last_status(job: dict) -> str:
    """Форматирует последний статус job."""
    state = job.get("state") or {}
    status = str(state.get("lastStatus") or state.get("lastRunStatus") or "—")
    errors = int(state.get("consecutiveErrors") or 0)
    if errors > 0:
        return f"{status} ⚠️ ({errors} ош.)"
    return status


async def _cron_run_openclaw(
    *args: str,
    timeout: float = 30.0,
) -> tuple[bool, str]:
    """
    Запускает `openclaw` с переданными аргументами.
    Возвращает (success, raw_output).
    """
    from ..core.subprocess_env import clean_subprocess_env  # noqa: PLC0415

    try:
        proc = await asyncio.create_subprocess_exec(
            "openclaw",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=clean_subprocess_env(),
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
            return False, "timeout"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)

    raw = stdout.decode("utf-8", errors="replace").strip()
    return proc.returncode == 0, raw


async def handle_cron(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление OpenClaw cron jobs из Telegram.

    Команды:
      !cron list            — список всех jobs (enabled/disabled, расписание, статус)
      !cron enable <name>   — включить job по имени или id
      !cron disable <name>  — выключить job по имени или id
      !cron run <name>      — запустить job немедленно
      !cron status          — общая статистика (total, enabled, errors)
    """
    # Только владелец
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 Команда доступна только владельцу.")

    raw_args = bot._get_command_args(message).strip()
    parts = raw_args.split(maxsplit=1)
    sub = parts[0].lower() if parts else "list"
    arg = parts[1].strip() if len(parts) > 1 else ""

    # ---------- !cron status ----------
    if sub in {"status", "stat", "статус"}:
        jobs = _cron_read_jobs()
        total = len(jobs)
        enabled = sum(1 for j in jobs if j.get("enabled"))
        disabled = total - enabled
        errors = sum(
            1 for j in jobs
            if int((j.get("state") or {}).get("consecutiveErrors") or 0) > 0
        )
        lines = [
            "🗓 **OpenClaw Cron — статус**",
            f"Всего jobs: **{total}**",
            f"• включено: {enabled}",
            f"• выключено: {disabled}",
            f"• с ошибками: {errors}",
        ]
        # Тех-вывод: группа → редирект в ЛС
        await _reply_tech(message, bot, "\n".join(lines))
        return

    # ---------- !cron list ----------
    if sub in {"list", "ls", "список", ""}:
        jobs = _cron_read_jobs()
        if not jobs:
            await message.reply("🗓 Cron jobs не найдены.")
            return
        # Сортировка: сначала enabled
        jobs_sorted = sorted(jobs, key=lambda j: (not j.get("enabled"), str(j.get("name") or "")))
        lines = ["🗓 **OpenClaw Cron Jobs**\n"]
        for job in jobs_sorted:
            flag = "✅" if job.get("enabled") else "⏸"
            name = str(job.get("name") or job.get("id") or "?")
            schedule = _cron_format_schedule(job)
            last_status = _cron_format_last_status(job)
            lines.append(f"{flag} **{name}**")
            lines.append(f"   расписание: {schedule} | статус: `{last_status}`")
        # Тех-вывод: группа → редирект в ЛС
        await _reply_tech(message, bot, "\n".join(lines))
        return

    # ---------- !cron enable / !cron disable ----------
    if sub in {"enable", "вкл", "включить", "disable", "выкл", "выключить"}:
        if not arg:
            action_word = "enable" if sub in {"enable", "вкл", "включить"} else "disable"
            raise UserInputError(
                user_message=f"❌ Укажи имя или id job: `!cron {action_word} <name>`"
            )
        enable = sub in {"enable", "вкл", "включить"}
        action = "enable" if enable else "disable"

        # Ищем job в jobs.json по имени или id
        jobs = _cron_read_jobs()
        matched = [
            j for j in jobs
            if str(j.get("name") or "").lower() == arg.lower()
            or str(j.get("id") or "").lower() == arg.lower()
        ]
        if not matched:
            raise UserInputError(user_message=f"❌ Job `{arg}` не найден.")

        job_id = str(matched[0].get("id") or "")
        job_name = str(matched[0].get("name") or job_id)

        # Вызываем openclaw cron enable/disable <id>
        ok, raw = await _cron_run_openclaw("cron", action, job_id)

        if ok:
            emoji = "✅" if enable else "⏸"
            verb = "включён" if enable else "выключен"
            await message.reply(f"{emoji} Job **{job_name}** {verb}.")
        else:
            # Fallback: патчим напрямую в jobs.json (gateway может быть offline)
            logger.warning("cron_openclaw_cli_failed_patching_directly", action=action, raw=raw)
            for j in jobs:
                if str(j.get("id") or "") == job_id:
                    j["enabled"] = enable
            _cron_write_jobs(jobs)
            emoji = "✅" if enable else "⏸"
            verb = "включён" if enable else "выключен"
            await message.reply(
                f"{emoji} Job **{job_name}** {verb} (direct patch, gateway offline)."
            )
        return

    # ---------- !cron run ----------
    if sub in {"run", "запустить", "запуск"}:
        if not arg:
            raise UserInputError(user_message="❌ Укажи имя или id job: `!cron run <name>`")

        # Ищем job в jobs.json по имени или id
        jobs = _cron_read_jobs()
        matched = [
            j for j in jobs
            if str(j.get("name") or "").lower() == arg.lower()
            or str(j.get("id") or "").lower() == arg.lower()
        ]
        if not matched:
            raise UserInputError(user_message=f"❌ Job `{arg}` не найден.")

        job_id = str(matched[0].get("id") or "")
        job_name = str(matched[0].get("name") or job_id)

        msg = await message.reply(f"⏳ Запускаю job **{job_name}**…")
        ok, raw = await _cron_run_openclaw("cron", "run", job_id, timeout=60.0)

        short_raw = raw[:200] if raw else ""
        if ok:
            text = f"✅ Job **{job_name}** запущен.\n`{short_raw}`" if short_raw else f"✅ Job **{job_name}** запущен."
            await msg.edit(text)
        else:
            text = f"❌ Ошибка запуска **{job_name}**:\n`{short_raw}`" if short_raw else f"❌ Ошибка запуска **{job_name}**."
            await msg.edit(text)
        return

    # ---------- Неизвестная субкоманда ----------
    await message.reply(
        "🗓 **!cron** — управление OpenClaw cron jobs\n\n"
        "`!cron list` — список всех jobs\n"
        "`!cron enable <name>` — включить job\n"
        "`!cron disable <name>` — выключить job\n"
        "`!cron run <name>` — запустить job немедленно\n"
        "`!cron status` — общая статистика"
    )


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
    - `!memory stats` — агрегированная статистика по archive.db / indexer / validator.
    """
    del bot
    raw_args = str(message.text or "").split(maxsplit=2)
    action = raw_args[1].strip().lower() if len(raw_args) > 1 else "recent"
    source_filter = raw_args[2].strip() if len(raw_args) > 2 else ""

    if action == "stats":
        await _handle_memory_stats(message)
        return

    if action != "recent":
        raise UserInputError(
            user_message="🧠 Формат: `!memory recent [source_filter]` | `!memory stats`"
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


async def handle_audio_message(bot: "KraabUserbot", message: Message) -> None:
    """
    Обработка входящих голосовых/аудио сообщений Telegram.

    Скачивает аудио, транскрибирует через perceptor, обрабатывает как текстовый запрос.
    """
    from ..modules.perceptor import perceptor

    try:
        audio_bytes = await message.download(in_memory=True)
        if audio_bytes is None:
            await message.reply("❌ Не удалось скачать аудио.")
            return

        transcript = await perceptor.transcribe_audio(bytes(audio_bytes))
        if not transcript:
            await message.reply("❌ Не удалось распознать речь.")
            return

        await message.reply(f"_{transcript}_")

        # Обрабатываем транскрипт как обычный текстовый запрос через bot
        fake_text = transcript
        response = await bot.process_text_query(fake_text, message)
        if response:
            await message.reply(response)

    except Exception as exc:
        logger.error("handle_audio_message_error", error=str(exc))
        await message.reply(f"❌ Ошибка обработки аудио: {str(exc)[:200]}")


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

    # Проверяем доступность перед снимком
    probe = await _bb.health_check(timeout_sec=4.0)
    if not probe.get("ok"):
        err_detail = probe.get("error") or (
            "Chrome не запущен или CDP недоступен" if probe.get("blocked") else "неизвестная ошибка"
        )
        await message.reply(f"📡 **!screenshot**: браузер недоступен\n`{err_detail[:300]}`")
        return

    await message.reply("📸 Делаю снимок…")
    try:
        png_bytes = await asyncio.wait_for(_bb.screenshot(), timeout=15.0)
    except asyncio.TimeoutError:
        await message.reply("⏱ Таймаут снимка (15 с). Попробуй позже.")
        return
    except Exception as exc:
        await message.reply(f"❌ Ошибка: `{str(exc)[:300]}`")
        return

    if not png_bytes:
        await message.reply("❌ Снимок пустой — возможно вкладок нет или CDP не отвечает.")
        return

    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as _tmp:
        _tmp.write(png_bytes)
        _tmp_path = _tmp.name
    try:
        await message.reply_photo(_tmp_path, caption="📸 Screenshot")
    except Exception as _photo_err:
        logger.warning("reply_photo_failed", error=str(_photo_err))
        try:
            await message.reply_document(_tmp_path, caption="📸 Screenshot (fallback)")
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


def _render_stats_panel(bot: "KraabUserbot") -> str:
    """
    Собирает компактный runtime-статус Краба из четырёх подсистем.

    Источники (все singleton'ы читаются sync):
    - telegram_rate_limiter.stats() — sliding-window capacity cap;
    - chat_ban_cache.list_entries() — persisted short-circuit ban list;
    - chat_capability_cache.list_entries() — TTL cache per-chat permissions;
    - silence_manager.status() — per-chat и глобальный mute;
    - bot.get_voice_runtime_profile() — флаг delivery и blocked chats.

    Бонус: голосовой runtime-профиль отображается только если bot отдал словарь,
    чтобы тесты могли подменить профиль через stub без Pyrogram client'а.
    """
    import time as _time

    import psutil as _psutil

    from ..core.chat_ban_cache import chat_ban_cache
    from ..core.chat_capability_cache import chat_capability_cache
    from ..core.silence_mode import silence_manager
    from ..core.telegram_rate_limiter import telegram_rate_limiter

    lines: list[str] = ["📊 **Krab Stats**", "─────────────"]

    # 0. Компактная сводка: uptime, сообщения, модель, translator, swarm, inbox, RAM ──
    try:
        elapsed = int(_time.time() - bot._session_start_time)
        hours, rem = divmod(elapsed, 3600)
        mins = rem // 60
        uptime_str = f"{hours}ч {mins}м" if hours else f"{mins}м"
    except Exception:
        uptime_str = "?"

    try:
        msg_count = bot._session_messages_processed
    except Exception:
        msg_count = 0

    # Текущая модель из runtime models config
    try:
        from ..core.openclaw_runtime_models import get_runtime_primary_model as _grpm

        model_name = _grpm() or "?"
        provider = (model_name.split("/")[0]) if "/" in model_name else "?"
        model_short = model_name.split("/")[-1] if "/" in model_name else model_name
    except Exception:
        model_short = "?"
        provider = "?"

    # Статус транслятора
    try:
        t_state = bot.get_translator_session_state()
        t_status = t_state.get("session_status", "idle")
        t_pair = t_state.get("last_pair") or ""
        translator_str = f"{t_status}" + (f" ({t_pair})" if t_pair else "")
    except Exception:
        translator_str = "idle"

    # Swarm rounds за сегодня
    try:
        import datetime as _dt

        from ..core.swarm_artifact_store import swarm_artifact_store as _sas

        today_str = _dt.date.today().isoformat()
        all_arts = _sas.list_artifacts(limit=200)
        swarm_today = sum(
            1 for a in all_arts if str(a.get("timestamp_iso", "")).startswith(today_str)
        )
    except Exception:
        swarm_today = 0

    # Inbox summary
    try:
        from ..core.inbox_service import inbox_service as _inbox

        isummary = _inbox.get_summary()
        inbox_open = isummary.get("open_items", 0)
        inbox_attention = isummary.get("attention_items", 0)
        inbox_str = f"{inbox_open} open" + (
            f" ({inbox_attention} attention)" if inbox_attention else ""
        )
    except Exception:
        inbox_str = "?"

    # RAM usage
    try:
        import os as _os

        rss_mb = int(_psutil.Process(_os.getpid()).memory_info().rss / 1024 / 1024)
        ram_str = f"{rss_mb} MB"
    except Exception:
        ram_str = "?"

    lines.append(f"⏱ Uptime: {uptime_str}")
    lines.append(f"💬 Сообщений: {msg_count}")
    lines.append(f"🤖 Модель: {model_short} ({provider})")
    lines.append(f"🔄 Translator: {translator_str}")
    lines.append(f"🐝 Swarm: {swarm_today} rounds today")
    lines.append(f"📬 Inbox: {inbox_str}")
    lines.append(f"🧠 RAM: {ram_str}")
    lines.append("")

    # 1. Telegram API rate limiter ────────────────────────────────────
    rl = telegram_rate_limiter.stats()
    lines.append("🌐 **Telegram API rate limiter**")
    lines.append(
        f"- Cap: `{int(rl.get('max_per_sec', 0))} req/s` "
        f"(окно `{float(rl.get('window_sec', 1.0)):.1f}s`) · "
        f"В окне сейчас: `{int(rl.get('current_in_window', 0))}`"
    )
    lines.append(
        f"- Всего acquire: `{int(rl.get('total_acquired', 0))}` · "
        f"ждали: `{int(rl.get('total_waited', 0))}` "
        f"(сумма `{float(rl.get('total_wait_sec', 0.0)):.3f}s`)"
    )
    lines.append("")

    # 2. Chat ban cache ───────────────────────────────────────────────
    ban_entries = chat_ban_cache.list_entries()
    lines.append(f"🚫 **Chat ban cache** (`{len(ban_entries)}` active)")
    if not ban_entries:
        lines.append("- записей нет")
    else:
        for entry in ban_entries[:3]:
            chat_id = entry.get("chat_id", "?")
            code = entry.get("last_error_code") or entry.get("error_code") or "?"
            lines.append(f"- `{chat_id}` · {code}")
        if len(ban_entries) > 3:
            lines.append(f"- …ещё `{len(ban_entries) - 3}`")
    lines.append("")

    # 3. Chat capability cache ────────────────────────────────────────
    cap_entries = chat_capability_cache.list_entries()
    voice_forbidden = sum(1 for entry in cap_entries if entry.get("voice_allowed") is False)
    slow_mode_active = 0
    for entry in cap_entries:
        slow = entry.get("slow_mode_seconds")
        try:
            if slow is not None and int(slow) > 0:
                slow_mode_active += 1
        except (TypeError, ValueError):
            continue
    lines.append(f"🎛 **Chat capability cache** (`{len(cap_entries)}` cached)")
    lines.append(f"- Voice запрещён явно: `{voice_forbidden}`")
    lines.append(f"- Slow mode > 0: `{slow_mode_active}`")
    lines.append("")

    # 4. Silence mode ─────────────────────────────────────────────────
    silence = silence_manager.status()
    global_muted = bool(silence.get("global_muted"))
    muted_chats_raw = silence.get("muted_chats") or {}
    muted_chats_count = len(muted_chats_raw) if isinstance(muted_chats_raw, dict) else 0
    global_remaining_min = silence.get("global_remaining_min") or 0
    lines.append("🔇 **Silence mode**")
    lines.append(
        f"- Глобально: `{'ВКЛ' if global_muted else 'ВЫКЛ'}`"
        + (f" · осталось `{global_remaining_min} мин`" if global_muted else "")
    )
    lines.append(f"- Заглушённых чатов: `{muted_chats_count}`")
    lines.append("")

    # 5. Voice runtime (bonus) ────────────────────────────────────────
    try:
        profile = bot.get_voice_runtime_profile()
    except Exception:  # pragma: no cover — defensive
        profile = None
    if isinstance(profile, dict):
        blocked = profile.get("blocked_chats") or []
        try:
            blocked_count = len(blocked)
        except TypeError:
            blocked_count = 0
        delivery = str(profile.get("delivery") or "text+voice")
        enabled = bool(profile.get("enabled"))
        lines.append("🎙 **Voice runtime**")
        lines.append(
            f"- Озвучка: `{'ВКЛ' if enabled else 'ВЫКЛ'}` · "
            f"Delivery: `{delivery}` · Blocklist: `{blocked_count}`"
        )

    # 6. FinOps + Translator + Swarm (session 5) ──────────────────────
    lines.append("")
    try:
        from ..core.cost_analytics import cost_analytics as _ca

        report = _ca.build_usage_report_dict()
        cost = report.get("cost_session_usd", 0)
        calls = sum(m.get("calls", 0) for m in (report.get("by_model") or {}).values())
        tool_calls = report.get("total_tool_calls", 0)
        lines.append(f"💰 **FinOps** · ${cost:.4f} · {calls} calls · {tool_calls} tools")
    except Exception:
        pass
    try:
        translator_state = bot.get_translator_session_state()
        t_status = translator_state.get("session_status", "idle")
        t_stats = translator_state.get("stats") or {}
        t_count = t_stats.get("total_translations", 0)
        lines.append(f"🔄 **Translator** · {t_status} · {t_count} переводов")
    except Exception:
        pass
    try:
        from ..core.swarm_task_board import swarm_task_board

        board = swarm_task_board.get_board_summary()
        lines.append(
            f"🐝 **Swarm** · {board.get('total', 0)} tasks · done: {board.get('by_status', {}).get('done', 0)}"
        )
    except Exception:
        pass

    return "\n".join(lines).rstrip()


async def handle_stats(bot: "KraabUserbot", message: Message) -> None:
    """Агрегированный runtime-статус Краба (B.9 session 4 tail)."""
    panel = _render_stats_panel(bot)
    await message.reply(panel)


async def handle_who(bot: "KraabUserbot", message: Message) -> None:
    """
    Информация о пользователе или чате.

    Варианты использования:
      !who                — в ответ на сообщение: инфо об авторе
      !who @username      — инфо по username или user_id
      !who                — без reply и без аргументов: инфо о текущем чате
    """

    def _fmt_status(user) -> str:
        """Форматирует статус пользователя."""
        from pyrogram.enums import UserStatus

        status = getattr(user, "status", None)
        if status is None:
            return "неизвестен"
        status_map = {
            UserStatus.ONLINE: "🟢 online",
            UserStatus.OFFLINE: "⚪ offline",
            UserStatus.RECENTLY: "🕐 недавно в сети",
            UserStatus.LAST_WEEK: "📅 на прошлой неделе",
            UserStatus.LAST_MONTH: "📆 в прошлом месяце",
            UserStatus.LONG_AGO: "⏳ давно",
        }
        return status_map.get(status, str(status))

    args = bot._get_command_args(message).strip()

    # Определяем цель: reply / аргумент / текущий чат
    target_user_id = None
    show_chat = False

    if message.reply_to_message and not args:
        # Ответ на сообщение — берём автора
        replied = message.reply_to_message
        if replied.from_user:
            target_user_id = replied.from_user.id
        elif replied.sender_chat:
            target_user_id = replied.sender_chat.id
            show_chat = True
        else:
            await message.reply("❓ Не могу определить отправителя сообщения.")
            return
    elif args:
        # Аргумент: @username или числовой ID
        raw = args.lstrip("@")
        try:
            target_user_id = int(raw)
        except ValueError:
            target_user_id = raw
    else:
        # Без аргументов и без reply — текущий чат
        show_chat = True
        target_user_id = message.chat.id

    if show_chat:
        # Инфо о чате
        try:
            chat = await bot.client.get_chat(target_user_id)
        except Exception as exc:
            await message.reply(f"❌ Ошибка: не удалось получить инфо о чате: {exc}")
            return

        chat_type = str(getattr(chat, "type", "")).replace("ChatType.", "")
        members = getattr(chat, "members_count", None)
        description = getattr(chat, "description", None) or "—"
        username = f"@{chat.username}" if getattr(chat, "username", None) else "отсутствует"

        lines = [
            "💬 **Chat Info**",
            "─────────────",
            f"**Название:** {chat.title or chat.first_name or '—'}",
            f"**Username:** {username}",
            f"**ID:** `{chat.id}`",
            f"**Тип:** {chat_type}",
        ]
        if members is not None:
            lines.append(f"**Участников:** {members}")
        lines.append(f"**Описание:** {description}")

        await message.reply("\n".join(lines))
        return

    # Инфо о пользователе
    try:
        user = await bot.client.get_users(target_user_id)
    except Exception as exc:
        await message.reply(f"❌ Ошибка: не удалось получить инфо о пользователе: {exc}")
        return

    # Общие чаты — только для реальных пользователей (не ботов)
    common_count: int | str = "—"
    if not getattr(user, "is_bot", False):
        try:
            common_chats = await bot.client.get_common_chats(user.id)
            common_count = len(common_chats)
        except Exception:
            common_count = "—"

    # Bio — берём из get_chat (там полное описание)
    bio = "—"
    try:
        chat_info = await bot.client.get_chat(user.id)
        bio = getattr(chat_info, "bio", None) or "—"
    except Exception:
        pass

    # Телефон — доступен только для контактов
    phone = getattr(user, "phone_number", None) or "скрыт"

    name_parts = [user.first_name or ""]
    if user.last_name:
        name_parts.append(user.last_name)
    full_name = " ".join(name_parts).strip() or "—"

    username_str = f"@{user.username}" if user.username else "—"
    is_bot = "да" if getattr(user, "is_bot", False) else "нет"
    is_premium = "да" if getattr(user, "is_premium", False) else "нет"
    is_verified = "да" if getattr(user, "is_verified", False) else "нет"
    is_restricted = "да" if getattr(user, "is_restricted", False) else "нет"
    is_scam = " ⚠️ SCAM" if getattr(user, "is_scam", False) else ""
    is_fake = " ⚠️ FAKE" if getattr(user, "is_fake", False) else ""

    lines = [
        f"👤 **User Info**{is_scam}{is_fake}",
        "─────────────",
        f"**Имя:** {full_name}",
        f"**Username:** {username_str}",
        f"**ID:** `{user.id}`",
        f"**Телефон:** {phone}",
        f"**Статус:** {_fmt_status(user)}",
        f"**Бот:** {is_bot}",
        f"**Premium:** {is_premium}",
        f"**Verified:** {is_verified}",
    ]
    if is_restricted == "да":
        lines.append("**Restricted:** да")
    lines.append(f"**Bio:** {bio}")
    if not getattr(user, "is_bot", False):
        lines.append(f"**Общих чатов:** {common_count}")

    await message.reply("\n".join(lines))


async def handle_chatinfo(bot: "KraabUserbot", message: Message) -> None:
    """Подробная информация о чате.

    Синтаксис:
      !chatinfo              — текущий чат
      !chatinfo <chat_id>   — другой чат по ID или @username
    """
    args = bot._get_command_args(message).strip()

    # Определяем целевой чат
    if args:
        raw = args.lstrip("@")
        try:
            target: int | str = int(raw)
        except ValueError:
            target = raw  # username без @
    else:
        target = message.chat.id

    # Получаем объект чата
    try:
        chat = await bot.client.get_chat(target)
    except Exception as exc:
        raise UserInputError(
            user_message=f"❌ Не удалось получить инфо о чате `{target}`: {exc}"
        ) from exc

    # Тип чата — убираем префикс "ChatType."
    chat_type = str(getattr(chat, "type", "")).replace("ChatType.", "").lower()

    # Участники — пробуем members_count из объекта или отдельный запрос
    members_count = getattr(chat, "members_count", None)
    if members_count is None:
        try:
            members_count = await bot.client.get_chat_members_count(chat.id)
        except Exception:
            members_count = None

    # Username
    username = f"@{chat.username}" if getattr(chat, "username", None) else "—"

    # Название / имя
    title = getattr(chat, "title", None) or getattr(chat, "first_name", None) or "—"

    # Описание
    description = (getattr(chat, "description", None) or "").strip() or "—"

    # Дата создания
    dc_date = getattr(chat, "date", None)
    created_str: str
    if dc_date:
        try:
            import datetime as _dt
            if isinstance(dc_date, (int, float)):
                dt = _dt.datetime.fromtimestamp(dc_date, tz=_dt.timezone.utc)
            else:
                dt = dc_date
            created_str = dt.strftime("%Y-%m-%d")
        except Exception:
            created_str = str(dc_date)
    else:
        created_str = "—"

    # Linked chat (привязанный канал/группа)
    linked_chat = getattr(chat, "linked_chat", None)
    if linked_chat:
        lc_username = getattr(linked_chat, "username", None)
        lc_id = getattr(linked_chat, "id", None)
        linked_str = f"@{lc_username}" if lc_username else str(lc_id or "—")
    else:
        linked_str = "—"

    # Количество администраторов
    admins_count: int | str = "—"
    try:
        admins = [m async for m in bot.client.get_chat_members(chat.id, filter="administrators")]
        admins_count = len(admins)
    except Exception:
        pass

    # Базовые права участников (permissions)
    perms = getattr(chat, "permissions", None)
    perm_lines: list[str] = []
    if perms:
        _perm_map = [
            ("can_send_messages", "Писать сообщения"),
            ("can_send_media_messages", "Медиа"),
            ("can_send_polls", "Опросы"),
            ("can_add_web_page_previews", "Превью"),
            ("can_change_info", "Изменять инфо"),
            ("can_invite_users", "Приглашать"),
            ("can_pin_messages", "Закреплять"),
        ]
        for attr, label in _perm_map:
            val = getattr(perms, attr, None)
            if val is not None:
                icon = "✅" if val else "❌"
                perm_lines.append(f"  {icon} {label}")

    # Формируем ответ
    lines: list[str] = [
        "📊 **Chat Info**",
        "─────────────",
        f"**Название:** {title}",
        f"**ID:** `{chat.id}`",
        f"**Тип:** {chat_type}",
    ]
    if members_count is not None:
        lines.append(f"**Участников:** {members_count:,}")
    lines.append(f"**Username:** {username}")
    lines.append(f"**Создан:** {created_str}")
    if description != "—":
        # Обрезаем длинное описание
        desc_display = description[:200] + "…" if len(description) > 200 else description
        lines.append(f"**Описание:** {desc_display}")
    lines.append(f"**Linked chat:** {linked_str}")
    if isinstance(admins_count, int):
        lines.append(f"**Администраторов:** {admins_count}")
    if perm_lines:
        lines.append("**Права участников:**")
        lines.extend(perm_lines)

    await message.reply("\n".join(lines))


async def handle_history(bot: "KraabUserbot", message: Message) -> None:
    """Статистика текущего чата за последние 1000 сообщений.

    Синтаксис:
      !history   — статистика текущего чата
    """
    import datetime as _dt
    from collections import Counter

    chat_id = message.chat.id
    limit = 1000

    # Счётчики по типам
    total = 0
    text_count = 0
    photo_count = 0
    video_count = 0
    voice_count = 0
    doc_count = 0
    other_count = 0

    weekday_counts: Counter = Counter()  # {0..6: int} — день недели
    dates_seen: set = set()              # уникальные даты для среднего

    first_dt: _dt.datetime | None = None
    last_dt: _dt.datetime | None = None

    try:
        async for msg in bot.client.get_chat_history(chat_id, limit=limit):
            total += 1

            # Тип сообщения
            if msg.text:
                text_count += 1
            elif msg.photo:
                photo_count += 1
            elif msg.video or msg.video_note:
                video_count += 1
            elif msg.voice or msg.audio:
                voice_count += 1
            elif msg.document:
                doc_count += 1
            else:
                other_count += 1

            # Дата
            if msg.date:
                msg_dt = msg.date
                if isinstance(msg_dt, (int, float)):
                    msg_dt = _dt.datetime.fromtimestamp(msg_dt, tz=_dt.timezone.utc)
                weekday_counts[msg_dt.weekday()] += 1
                dates_seen.add(msg_dt.date())

                if first_dt is None or msg_dt < first_dt:
                    first_dt = msg_dt
                if last_dt is None or msg_dt > last_dt:
                    last_dt = msg_dt

    except Exception as exc:
        raise UserInputError(
            user_message=f"❌ Не удалось получить историю чата: {exc}"
        ) from exc

    if total == 0:
        await message.reply("📈 В этом чате нет сообщений (в пределах 1000).")
        return

    # Самый активный день недели
    _weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    if weekday_counts:
        busiest_wd, busiest_count = weekday_counts.most_common(1)[0]
        busiest_name = _weekday_names[busiest_wd]
        # Сколько таких дней встретилось в выборке
        busiest_days_in_sample = sum(
            1 for d in dates_seen if d.weekday() == busiest_wd
        ) or 1
        avg_on_busiest = round(busiest_count / busiest_days_in_sample)
        most_active_str = f"{busiest_name} (avg {avg_on_busiest} msgs)"
    else:
        most_active_str = "—"

    # Среднее в день
    days_span = len(dates_seen) or 1
    avg_per_day = round(total / days_span)

    # Форматирование дат
    first_str = first_dt.strftime("%Y-%m-%d") if first_dt else "—"
    last_str = last_dt.strftime("%Y-%m-%d") if last_dt else "—"

    lines = [
        "📈 Chat History Stats",
        "─────────────",
        f"Messages: {total:,}",
        (
            f"Text: {text_count:,} | Photo: {photo_count:,} | Video: {video_count:,}"
            f" | Voice: {voice_count:,} | Docs: {doc_count:,} | Other: {other_count:,}"
        ),
        f"Most active: {most_active_str}",
        f"Average: {avg_per_day:,} msgs/day",
        f"First: {first_str} | Last: {last_str}",
    ]
    await message.reply("\n".join(lines))


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

    if args == "статус":
        await message.reply(silence_manager.format_status())
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


async def handle_costs(bot: "KraabUserbot", message: Message) -> None:
    """!costs — текущий cost report прямо в Telegram (owner-only)."""
    # Проверка: только владелец
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 Команда доступна только владельцу.")

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
    """!digest — немедленно сгенерировать и отправить weekly digest (owner-only)."""
    # Проверка: только владелец
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 Команда доступна только владельцу.")

    await message.reply("⏳ Генерирую digest, подожди...")

    try:
        result = await weekly_digest.generate_digest()
    except Exception as exc:  # noqa: BLE001
        logger.warning("handle_digest_failed", error=str(exc))
        await message.reply(f"❌ Ошибка генерации digest: {exc}")
        return

    if not result.get("ok"):
        err = result.get("error", "неизвестная ошибка")
        await message.reply(f"❌ Digest не удался: {err}")
        return

    rounds = result.get("total_rounds", 0)
    cost = result.get("cost_week_usd", 0.0)
    attention = result.get("attention_count", 0)

    # Digest уже доставлен через telegram_callback если он настроен;
    # иначе выводим итоговую сводку
    if not weekly_digest._telegram_callback:
        await message.reply(
            f"✅ **Weekly Digest сгенерирован**\n"
            f"Swarm rounds: {rounds}\n"
            f"Cost (7д): ${cost:.4f}\n"
            f"Attention items: {attention}\n\n"
            "_Для автодоставки в чат настрой telegram_callback._"
        )
    else:
        await message.reply(
            f"✅ Digest отправлен.\n"
            f"Rounds: {rounds} | Cost 7д: ${cost:.4f} | Attention: {attention}"
        )


async def handle_health(bot: "KraabUserbot", message: Message) -> None:
    """
    Глубокая диагностика всех подсистем Краба (!health).

    Каждая строка — ✅ OK / ⚠️ Warning / ❌ Error.
    Owner-only команда.
    """
    from ..core.swarm_bus import TEAM_REGISTRY
    from ..core.swarm_scheduler import swarm_scheduler
    from ..core.telegram_rate_limiter import telegram_rate_limiter

    lines: list[str] = ["🏥 **Health Check**", "─────────────────"]

    # 1. Telegram: проверяем, что me доступен (userbot подключён)
    try:
        telegram_ok = bot.me is not None
        lines.append("✅ Telegram: connected" if telegram_ok else "❌ Telegram: не инициализирован")
    except Exception as exc:
        lines.append(f"❌ Telegram: ошибка ({exc})")

    # 2. OpenClaw gateway — health check + текущая модель маршрута
    try:
        oc_ok = await openclaw_client.health_check()
        route_meta: dict[str, Any] = {}
        if hasattr(openclaw_client, "get_last_runtime_route"):
            route_meta = openclaw_client.get_last_runtime_route() or {}
        model = str(route_meta.get("model") or "").strip()
        if not model:
            from ..core.openclaw_runtime_models import get_runtime_primary_model

            model = str(get_runtime_primary_model() or getattr(config, "MODEL", "") or "unknown")
        if oc_ok:
            lines.append(f"✅ OpenClaw: up ({model})")
        else:
            lines.append(f"❌ OpenClaw: offline ({model})")
    except Exception as exc:
        lines.append(f"❌ OpenClaw: ошибка ({exc})")

    # 3. Swarm scheduler — флаг ENABLED и количество jobs
    try:
        sched_enabled = getattr(config, "SCHEDULER_ENABLED", False)
        jobs = swarm_scheduler.list_jobs()
        job_count = len(jobs)
        if sched_enabled:
            lines.append(f"✅ Scheduler: enabled ({job_count} jobs)")
        else:
            lines.append(f"⚠️ Scheduler: disabled ({job_count} jobs)")
    except Exception as exc:
        lines.append(f"❌ Scheduler: ошибка ({exc})")

    # 4. Proactive Watch — фоновая asyncio-задача жива?
    try:
        pw_task = getattr(bot, "_proactive_watch_task", None)
        pw_running = pw_task is not None and not pw_task.done()
        if pw_running:
            lines.append("✅ Proactive Watch: running")
        else:
            lines.append("⚠️ Proactive Watch: не запущен")
    except Exception as exc:
        lines.append(f"❌ Proactive Watch: ошибка ({exc})")

    # 5. Inbox — attention items (warning/error severity)
    try:
        inbox_summary = inbox_service.get_summary()
        attention = int(inbox_summary.get("attention_items", 0))
        open_items = int(inbox_summary.get("open_items", 0))
        if attention > 0:
            lines.append(f"⚠️ Inbox: {attention} attention items ({open_items} open)")
        else:
            lines.append(f"✅ Inbox: чисто ({open_items} open)")
    except Exception as exc:
        lines.append(f"❌ Inbox: ошибка ({exc})")

    # 6. Swarm teams — из TEAM_REGISTRY
    try:
        team_count = len(TEAM_REGISTRY)
        if team_count > 0:
            lines.append(f"✅ Swarm: {team_count} teams ready ({', '.join(TEAM_REGISTRY)})")
        else:
            lines.append("❌ Swarm: команды не зарегистрированы")
    except Exception as exc:
        lines.append(f"❌ Swarm: ошибка ({exc})")

    # 7. Voice — проверяем конфигурацию через runtime профиль бота
    try:
        voice_profile: dict[str, Any] = (
            bot.get_voice_runtime_profile() if hasattr(bot, "get_voice_runtime_profile") else {}
        )
        voice_name = str(voice_profile.get("voice") or getattr(config, "VOICE_REPLY_VOICE", ""))
        voice_enabled = bool(voice_profile.get("enabled"))
        if voice_name:
            status_str = "ВКЛ" if voice_enabled else "ВЫКЛ"
            lines.append(f"✅ Voice: configured ({voice_name}, {status_str})")
        else:
            lines.append("⚠️ Voice: не настроен")
    except Exception as exc:
        lines.append(f"❌ Voice: ошибка ({exc})")

    # 8. LM Studio — availability check (короткий таймаут)
    try:
        lm_ok = await is_lm_studio_available(config.LM_STUDIO_URL, timeout=2.0)
        if lm_ok:
            lines.append("✅ LM Studio: online")
        else:
            lines.append("❌ LM Studio: offline")
    except Exception as exc:
        lines.append(f"❌ LM Studio: ошибка ({exc})")

    # 9. Rate Limiter — текущая нагрузка sliding window
    try:
        rl_stats = telegram_rate_limiter.stats()
        current = int(rl_stats.get("current_in_window", 0))
        cap = int(rl_stats.get("max_per_sec", 20))
        if current >= cap:
            lines.append(f"⚠️ Rate Limiter: {current}/{cap} rps (перегрузка)")
        else:
            lines.append(f"✅ Rate Limiter: {current}/{cap} rps")
    except Exception as exc:
        lines.append(f"❌ Rate Limiter: ошибка ({exc})")

    report = "\n".join(lines)
    if message.from_user and message.from_user.id == bot.me.id:
        await message.edit(report)
    else:
        await message.reply(report, reply_markup=build_health_recheck_buttons())


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


async def handle_pin(bot: "KraabUserbot", message: Message) -> None:
    """
    Закрепляет сообщение в чате (!pin в ответ на сообщение).

    Owner-only. Опциональный флаг `silent` подавляет системное уведомление.
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!pin` доступен только владельцу.")

    target = message.reply_to_message
    if target is None:
        raise UserInputError(user_message="📌 Ответь на сообщение, которое хочешь закрепить.")

    # Флаг silent — подавляет системное уведомление о закреплении
    args = bot._get_command_args(message).strip().lower()
    silent = args == "silent"

    try:
        await bot.client.pin_chat_message(
            chat_id=message.chat.id,
            message_id=target.id,
            disable_notification=silent,
        )
        note = " (без уведомления)" if silent else ""
        reply = f"📌 Сообщение закреплено{note}."
    except Exception as exc:
        reply = f"❌ Не удалось закрепить: `{exc}`"

    if message.from_user and message.from_user.id == bot.me.id:
        await message.edit(reply)
    else:
        await message.reply(reply)


async def handle_unpin(bot: "KraabUserbot", message: Message) -> None:
    """
    Открепляет сообщение в чате (!unpin).

    - `!unpin` в ответ на сообщение — открепляет конкретное сообщение.
    - `!unpin all` — открепляет все сообщения в чате.
    Owner-only.
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!unpin` доступен только владельцу.")

    args = bot._get_command_args(message).strip().lower()

    try:
        if args == "all":
            # Открепляем все сообщения в чате
            await bot.client.unpin_all_chat_messages(chat_id=message.chat.id)
            reply = "📌 Все сообщения откреплены."
        else:
            # Открепляем конкретное сообщение (reply) или последнее закреплённое
            target = message.reply_to_message
            if target is None:
                raise UserInputError(
                    user_message=(
                        "📌 Ответь на сообщение, которое хочешь открепить, "
                        "или используй `!unpin all`."
                    )
                )
            await bot.client.unpin_chat_message(
                chat_id=message.chat.id,
                message_id=target.id,
            )
            reply = "📌 Сообщение откреплено."
    except UserInputError:
        raise
    except Exception as exc:
        reply = f"❌ Не удалось открепить: `{exc}`"

    if message.from_user and message.from_user.id == bot.me.id:
        await message.edit(reply)
    else:
        await message.reply(reply)


async def handle_archive(bot: "KraabUserbot", message: Message) -> None:
    """
    Архивация и разархивация чатов. Owner-only.

    Форматы:
      !archive          — архивировать текущий чат
      !unarchive        — разархивировать текущий чат
      !archive list     — показать список архивированных чатов (до 20)
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
                title = getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(chat.id)
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


async def handle_monitor(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление мониторингом чатов на ключевые слова.

    Формат:
      !monitor add <chat_id|@username> [keyword1] [keyword2] ...
      !monitor remove <chat_id>
      !monitor list

    Ключевые слова могут быть:
      - обычный текст (поиск без учёта регистра)
      - re:<pattern>  — Python regex (например: re:крипт|биткоин)
    """
    from ..core.chat_monitor import chat_monitor_service

    args_raw = message.command[1:] if message.command else []
    if not args_raw:
        await message.reply(
            "📡 **Chat Monitor**\n\n"
            "`!monitor add <chat_id> [keywords...]` — начать мониторинг\n"
            "`!monitor remove <chat_id>` — остановить мониторинг\n"
            "`!monitor list` — активные мониторинги\n\n"
            "Regex поддерживается: `re:pattern`"
        )
        return

    subcmd = args_raw[0].lower()

    # ── list ──────────────────────────────────────────────────
    if subcmd == "list":
        monitors = chat_monitor_service.list_monitors()
        if not monitors:
            await message.reply("📡 Активных мониторингов нет.")
            return
        lines = ["📡 **Активные мониторинги:**\n"]
        for entry in monitors:
            kw_str = (
                ", ".join(f"`{k}`" for k in entry.keywords)
                if entry.keywords
                else "_(все сообщения)_"
            )
            lines.append(
                f"• **{entry.chat_title}** (`{entry.chat_id}`)\n  Ключевые слова: {kw_str}"
            )
        await message.reply("\n".join(lines))
        return

    # ── remove ────────────────────────────────────────────────
    if subcmd == "remove":
        if len(args_raw) < 2:
            raise UserInputError(user_message="❌ Формат: `!monitor remove <chat_id>`")
        target_id = args_raw[1]
        removed = chat_monitor_service.remove(target_id)
        if removed:
            await message.reply(f"🗑️ Мониторинг `{target_id}` удалён.")
        else:
            await message.reply(f"⚠️ Мониторинг для `{target_id}` не найден.")
        return

    # ── add ───────────────────────────────────────────────────
    if subcmd == "add":
        if len(args_raw) < 2:
            raise UserInputError(
                user_message="❌ Формат: `!monitor add <chat_id|@username> [keywords...]`"
            )
        target_raw = args_raw[1]
        keywords = args_raw[2:]  # может быть пустым — мониторим все сообщения

        # Резолвим chat_id и получаем title
        chat_id_resolved: int | str = target_raw
        chat_title = target_raw
        try:
            chat = await bot.client.get_chat(target_raw)
            chat_id_resolved = chat.id
            chat_title = (
                getattr(chat, "title", None) or getattr(chat, "first_name", None) or target_raw
            )
        except Exception as e:
            logger.warning("monitor_resolve_chat_error", target=target_raw, error=str(e))
            # Если не смогли резолвить — используем как есть (числовой id)
            if target_raw.lstrip("-").isdigit():
                chat_id_resolved = int(target_raw)

        entry = chat_monitor_service.add(
            chat_id=chat_id_resolved,
            chat_title=chat_title,
            keywords=list(keywords),
        )
        kw_str = (
            ", ".join(f"`{k}`" for k in entry.keywords) if entry.keywords else "_(все сообщения)_"
        )
        await message.reply(
            f"✅ **Мониторинг запущен**\n"
            f"Чат: **{entry.chat_title}** (`{entry.chat_id}`)\n"
            f"Ключевые слова: {kw_str}"
        )
        return

    raise UserInputError(
        user_message="❌ Неизвестная подкоманда. Используй: `add`, `remove`, `list`"
    )


async def handle_schedule(bot: "KraabUserbot", message: Message) -> None:
    """
    Отложенные сообщения через MTProto schedule_date.

    Форматы:
        !schedule HH:MM <текст>   — отправить в указанное время сегодня/завтра
        !schedule +Nm <текст>     — через N минут
        !schedule +Nh <текст>     — через N часов
        !schedule list            — список запланированных
        !schedule cancel <id>     — отменить по ID
    """
    from ..core.message_scheduler import (
        _MIN_SCHEDULE_SECONDS,
        _now_local,
        format_scheduled_list,
        msg_scheduler_store,
        parse_schedule_spec,
        split_schedule_input,
    )

    raw_args = bot._get_command_args(message).strip()
    if not raw_args:
        raise UserInputError(
            user_message=(
                "📅 **Отложенные сообщения**\n\n"
                "Форматы:\n"
                "`!schedule HH:MM <текст>` — в указанное время\n"
                "`!schedule +Nm <текст>` — через N минут\n"
                "`!schedule +Nh <текст>` — через N часов\n"
                "`!schedule list` — список запланированных\n"
                "`!schedule cancel <id>` — отменить\n\n"
                "Пример: `!schedule +30m Позвонить Маше`"
            )
        )

    spec, rest = split_schedule_input(raw_args)
    chat_id = str(message.chat.id)

    # --- list ---
    if spec in {"list", "список"}:
        records = msg_scheduler_store.list_pending(chat_id=chat_id)
        await message.reply(format_scheduled_list(records))
        return

    # --- cancel ---
    if spec in {"cancel", "отмена"}:
        record_id = rest.strip()
        if not record_id:
            raise UserInputError(user_message="❌ Укажи ID: `!schedule cancel <id>`")

        rec = msg_scheduler_store.get(record_id)
        if rec is None or rec.chat_id != chat_id:
            await message.reply(f"⚠️ Запись `{record_id}` не найдена в этом чате.")
            return

        # Удаляем scheduled message через Pyrogram
        try:
            await bot.client.delete_messages(
                chat_id=int(chat_id),
                message_ids=rec.tg_message_id,
                revoke=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "schedule_cancel_delete_failed",
                record_id=record_id,
                tg_msg_id=rec.tg_message_id,
                error=str(exc),
            )

        msg_scheduler_store.mark_cancelled(record_id)
        await message.reply(f"🗑️ Запланированное сообщение `{record_id}` отменено.")
        return

    # --- schedule new message ---
    if not rest:
        raise UserInputError(
            user_message=("❌ Укажи текст сообщения.\nПример: `!schedule +30m Позвонить Маше`")
        )

    try:
        schedule_time = parse_schedule_spec(spec)
    except ValueError as exc:
        raise UserInputError(
            user_message=(
                f"❌ Не удалось распознать время: `{spec}`\n\n"
                "Поддерживаемые форматы:\n"
                "• `HH:MM` — конкретное время (напр. `14:30`)\n"
                "• `+Nm` — через N минут (напр. `+30m`)\n"
                "• `+Nh` — через N часов (напр. `+2h`)"
            )
        ) from exc

    # Проверяем минимальный отступ
    delay_sec = (schedule_time - _now_local()).total_seconds()
    if delay_sec < _MIN_SCHEDULE_SECONDS:
        raise UserInputError(
            user_message=(
                f"⚠️ Минимальное время отсрочки — {_MIN_SCHEDULE_SECONDS} секунд.\n"
                "Telegram не принимает scheduled messages ближе к текущему моменту."
            )
        )

    # Отправляем через Pyrogram с schedule_date
    try:
        sent = await bot.client.send_message(
            chat_id=int(chat_id),
            text=rest,
            schedule_date=schedule_time,
        )
    except Exception as exc:
        logger.error("schedule_send_failed", chat_id=chat_id, error=str(exc))
        raise UserInputError(
            user_message=f"❌ Ошибка при создании отложенного сообщения: {exc}"
        ) from exc

    # Сохраняем локальную запись
    record_id = msg_scheduler_store.add(
        chat_id=chat_id,
        text=rest,
        schedule_time=schedule_time,
        tg_message_id=sent.id,
    )

    when_str = schedule_time.strftime("%d.%m.%Y в %H:%M")
    await message.reply(
        f"📅 Сообщение запланировано на **{when_str}**\n"
        f"ID: `{record_id}` · Telegram msg: `{sent.id}`\n"
        f"Отмена: `!schedule cancel {record_id}`"
    )


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

# Ключ в _runtime_state бота для хранения настроек autodel по чатам
_AUTODEL_STATE_KEY = "autodel_settings"


async def _delete_after(client: Any, chat_id: int, message_id: int, delay: float) -> None:
    """Удаляет сообщение после задержки (внутренняя утилита для autodel)."""
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id, message_ids=[message_id])
    except Exception as exc:
        logger.debug("autodel_failed", chat_id=chat_id, msg_id=message_id, error=str(exc))


def schedule_autodel(client: Any, chat_id: int, message_id: int, delay: float) -> None:
    """
    Планирует отложенное удаление сообщения Краба.
    Вызывается из LLM flow после отправки ответа, если autodel включён для чата.
    """
    asyncio.create_task(
        _delete_after(client, chat_id, message_id, delay),
        name=f"autodel_{chat_id}_{message_id}",
    )


def get_autodel_delay(bot: "KraabUserbot", chat_id: int) -> float | None:
    """
    Возвращает задержку autodel для чата (в секундах) или None если выключен.
    Читает из _runtime_state бота.
    """
    state: dict = getattr(bot, "_runtime_state", {}) or {}
    settings: dict = state.get(_AUTODEL_STATE_KEY, {})
    delay = settings.get(str(chat_id))
    if delay is None or delay <= 0:
        return None
    return float(delay)


def _set_autodel_delay(bot: "KraabUserbot", chat_id: int, delay: float) -> None:
    """Сохраняет настройку autodel в _runtime_state бота."""
    if not hasattr(bot, "_runtime_state") or bot._runtime_state is None:
        bot._runtime_state = {}
    settings: dict = bot._runtime_state.setdefault(_AUTODEL_STATE_KEY, {})
    if delay <= 0:
        settings.pop(str(chat_id), None)
    else:
        settings[str(chat_id)] = delay


async def handle_del(bot: "KraabUserbot", message: Message) -> None:
    """
    !del [N] — удаляет последние N сообщений Краба в текущем чате.

    По умолчанию N=1. Максимум 100 за раз.
    Включает само сообщение с командой !del.
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🚫 Только owner может удалять сообщения.")

    raw = bot._get_command_args(message).strip()
    try:
        n = int(raw) if raw else 1
    except ValueError:
        raise UserInputError(user_message="❌ Использование: `!del [N]` — N должно быть числом.")

    if n < 1 or n > 100:
        raise UserInputError(user_message="❌ N должно быть от 1 до 100.")

    chat_id = message.chat.id
    bot_id = bot.me.id

    # Удаляем саму команду !del сразу
    try:
        await message.delete()
    except Exception:
        pass

    # Собираем историю и ищем сообщения Краба
    collected: list[int] = []
    try:
        async for msg in bot.client.get_chat_history(chat_id, limit=200):
            if msg.from_user and msg.from_user.id == bot_id:
                collected.append(msg.id)
                if len(collected) >= n:
                    break
    except Exception as exc:
        logger.warning("handle_del_history_error", error=str(exc))

    if not collected:
        return

    try:
        await bot.client.delete_messages(chat_id, message_ids=collected)
        logger.info("handle_del_done", chat_id=chat_id, count=len(collected))
    except Exception as exc:
        logger.warning("handle_del_failed", error=str(exc))


async def handle_purge(bot: "KraabUserbot", message: Message) -> None:
    """
    !purge — удаляет ВСЕ сообщения Краба в текущем чате за последний час.

    Проходит историю за 60 минут, собирает ID сообщений бота и удаляет пачками.
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🚫 Только owner может использовать !purge.")

    chat_id = message.chat.id
    bot_id = bot.me.id
    cutoff = time.time() - 3600  # 1 час назад

    # Удаляем саму команду
    try:
        await message.delete()
    except Exception:
        pass

    collected: list[int] = []
    try:
        async for msg in bot.client.get_chat_history(chat_id, limit=500):
            if msg.date and msg.date.timestamp() < cutoff:
                break
            if msg.from_user and msg.from_user.id == bot_id:
                collected.append(msg.id)
    except Exception as exc:
        logger.warning("handle_purge_history_error", error=str(exc))

    if not collected:
        return

    # Удаляем пачками по 100 (лимит Telegram)
    chunk_size = 100
    deleted_total = 0
    for i in range(0, len(collected), chunk_size):
        chunk = collected[i : i + chunk_size]
        try:
            await bot.client.delete_messages(chat_id, message_ids=chunk)
            deleted_total += len(chunk)
        except Exception as exc:
            logger.warning("handle_purge_chunk_error", error=str(exc))

    logger.info("handle_purge_done", chat_id=chat_id, deleted=deleted_total)


async def handle_autodel(bot: "KraabUserbot", message: Message) -> None:
    """
    !autodel <секунды> — автоудаление ответов Краба через N секунд.
    !autodel 0          — выключить автоудаление.
    !autodel status     — показать текущую настройку.

    Каждый ответ Краба в этом чате будет удалён через N секунд после отправки.
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🚫 Только owner может управлять автоудалением.")

    chat_id = message.chat.id
    raw = bot._get_command_args(message).strip()

    if not raw or raw.lower() == "status":
        delay = get_autodel_delay(bot, chat_id)
        if delay:
            await message.reply(
                f"⏱ Автоудаление: **включено** — через `{int(delay)}` сек.\n"
                f"`!autodel 0` — выключить."
            )
        else:
            await message.reply("⏱ Автоудаление: **выключено**.\n`!autodel <секунды>` — включить.")
        return

    try:
        delay = float(raw)
    except ValueError:
        raise UserInputError(
            user_message="❌ Использование: `!autodel <секунды>` или `!autodel status`."
        )

    if delay < 0:
        raise UserInputError(user_message="❌ Секунды не могут быть отрицательными.")

    _set_autodel_delay(bot, chat_id, delay)

    if delay == 0:
        await message.reply("⏱ Автоудаление **выключено**.")
    else:
        await message.reply(
            f"⏱ Автоудаление **включено**: ответы Краба будут удалены через `{int(delay)}` сек."
        )


# ─────────────────────────────────────────────────────────────────────────────
# !summary / !catchup — суммаризация истории чата через LLM
# ─────────────────────────────────────────────────────────────────────────────

_SUMMARY_DEFAULT_N = 50
_SUMMARY_MAX_N = 500
# Максимум символов истории, передаваемых в LLM
_SUMMARY_MAX_HISTORY_CHARS = 24_000
# Порог редактирования streaming-сообщения (каждые N новых символов)
_SUMMARY_EDIT_THRESHOLD = 200


def _format_chat_history_for_llm(messages: list) -> str:
    """
    Форматирует список Pyrogram Message в читаемый текст для LLM.

    Формат строки: [HH:MM] Имя: текст
    Медиа без подписи помечается как [тип медиа].
    История приходит из get_chat_history новые-первые, разворачиваем в хронологию.
    """
    lines: list[str] = []
    for msg in reversed(messages):
        # Определяем имя отправителя
        sender = "Unknown"
        if getattr(msg, "from_user", None):
            u = msg.from_user
            name_parts = [
                p for p in [getattr(u, "first_name", None), getattr(u, "last_name", None)] if p
            ]
            if name_parts:
                sender = " ".join(name_parts)
            elif getattr(u, "username", None):
                sender = f"@{u.username}"
            else:
                sender = str(u.id)
        elif getattr(msg, "sender_chat", None):
            sender = getattr(msg.sender_chat, "title", None) or str(msg.sender_chat.id)

        # Текст сообщения
        text: str = getattr(msg, "text", None) or getattr(msg, "caption", None) or ""
        if not text:
            if getattr(msg, "photo", None):
                text = "[фото]"
            elif getattr(msg, "video", None):
                text = "[видео]"
            elif getattr(msg, "voice", None) or getattr(msg, "audio", None):
                text = "[голосовое/аудио]"
            elif getattr(msg, "document", None):
                text = "[документ]"
            elif getattr(msg, "sticker", None):
                text = "[стикер]"
            else:
                text = "[медиа]"

        # Время
        ts = ""
        date = getattr(msg, "date", None)
        if date:
            ts = date.strftime("%H:%M")

        lines.append(f"[{ts}] {sender}: {text}")

    return "\n".join(lines)


async def handle_summary(bot: "KraabUserbot", message: Message) -> None:
    """
    Суммаризирует историю чата через LLM.

    Синтаксис:
      !summary [N]               — последние N сообщений текущего чата (default 50)
      !summary <chat_id> [N]     — другой чат (userbot видит всё)
      !catchup                   — алиас для !summary 100

    Примеры:
      !summary
      !summary 100
      !summary -1001234567890 200
    """
    raw_args = bot._get_command_args(message).strip()
    parts = raw_args.split() if raw_args else []

    # Парсим аргументы: опциональный chat_id и N
    target_chat_id: int = message.chat.id
    n: int = _SUMMARY_DEFAULT_N

    if parts:
        first = parts[0]
        # chat_id: начинается с '-100' или длинное число (>6 цифр)
        is_chat_id = first.startswith("-100") or (
            first.lstrip("-").isdigit() and len(first.lstrip("-")) > 6
        )
        if is_chat_id:
            try:
                target_chat_id = int(first)
            except ValueError:
                raise UserInputError(user_message=f"❌ Некорректный chat_id: `{first}`")
            if len(parts) >= 2 and parts[1].isdigit():
                n = max(1, min(int(parts[1]), _SUMMARY_MAX_N))
        elif first.isdigit():
            # Только число — N для текущего чата
            n = max(1, min(int(first), _SUMMARY_MAX_N))
        else:
            raise UserInputError(
                user_message=(
                    "📋 **Суммаризация чата**\n\n"
                    "`!summary [N]` — последние N сообщений текущего чата\n"
                    "`!summary <chat_id> [N]` — другой чат\n"
                    "`!catchup` — алиас для !summary 100"
                )
            )

    # Отправляем плейсхолдер
    status_msg = await message.reply(f"📋 Собираю последние {n} сообщений...")

    # Читаем историю через pyrogram
    try:
        raw_messages = [m async for m in bot.client.get_chat_history(target_chat_id, limit=n)]
    except Exception as exc:
        logger.warning("handle_summary_fetch_failed", chat_id=target_chat_id, error=str(exc))
        await status_msg.edit(f"❌ Не удалось получить историю чата: {exc}")
        return

    if not raw_messages:
        await status_msg.edit("📭 История чата пуста или недоступна.")
        return

    # Форматируем историю
    history_text = _format_chat_history_for_llm(raw_messages)

    # Обрезаем если слишком длинно (эвристика по символам)
    if len(history_text) > _SUMMARY_MAX_HISTORY_CHARS:
        history_text = "[...]\n" + history_text[-_SUMMARY_MAX_HISTORY_CHARS:]

    actual_n = len(raw_messages)
    chat_label = str(target_chat_id) if target_chat_id != message.chat.id else "текущего чата"

    prompt = (
        f"Суммаризируй этот чат (последние {actual_n} сообщений из {chat_label}).\n"
        "Выдели ключевые темы, решения и важные факты. "
        "Будь кратким и структурированным. Отвечай на языке чата.\n\n"
        f"История:\n{history_text}"
    )

    # Обновляем плейсхолдер перед стримингом
    header = f"📋 **Сводка чата** (последние {actual_n} сообщений)\n─────────────\n"
    await status_msg.edit(header + "⏳ Генерирую...")

    chunks: list[str] = []
    last_edit_len = 0

    try:
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            # Изолированная сессия — не портит основной контекст чата
            chat_id=f"summary_{message.chat.id}",
            disable_tools=True,
        ):
            chunks.append(str(chunk))
            total = "".join(chunks)
            if len(total) - last_edit_len >= _SUMMARY_EDIT_THRESHOLD:
                last_edit_len = len(total)
                preview = total
                max_preview = 4000 - len(header)
                if len(preview) > max_preview:
                    preview = preview[-max_preview:]
                try:
                    await status_msg.edit(header + preview)
                except Exception:  # noqa: BLE001
                    pass  # промежуточные ошибки редактирования игнорируем

    except Exception as exc:
        logger.warning("handle_summary_llm_failed", error=str(exc))
        await status_msg.edit(f"❌ Ошибка суммаризации: {exc}")
        return

    # Финальное редактирование с полным текстом
    final_text = "".join(chunks).strip()
    result = header + final_text
    if len(result) > 4096:
        result = result[:4090] + "..."
    try:
        await status_msg.edit(result)
    except Exception:  # noqa: BLE001
        # Если редактирование не удалось — пишем новым сообщением
        await message.reply(result)


async def handle_catchup(bot: "KraabUserbot", message: Message) -> None:
    """!catchup — алиас для !summary 100 (быстро догнать пропущенное)."""
    # Подменяем _get_command_args чтобы handle_summary получил "100"
    original_get_args = bot._get_command_args

    def _patched_args(_msg: Message) -> str:  # noqa: ARG001
        return "100"

    bot._get_command_args = _patched_args  # type: ignore[method-assign]
    try:
        await handle_summary(bot, message)
    finally:
        bot._get_command_args = original_get_args  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# !translate — быстрый перевод текста без voice
# ---------------------------------------------------------------------------

# Поддерживаемые языки для быстрого указания через аргумент
_TRANSLATE_LANG_ALIASES: dict[str, str] = {
    "ru": "ru",
    "en": "en",
    "es": "es",
    "fr": "fr",
    "de": "de",
    "it": "it",
    "pt": "pt",
    "uk": "uk",
    "рус": "ru",
    "рu": "ru",
    "eng": "en",
    "spa": "es",
}


async def handle_translate(bot: "KraabUserbot", message: Message) -> None:
    """
    !translate [lang] <текст> — быстрый перевод без voice.

    Формы вызова:
      !translate <текст>         — автоопределение направления (ru→en, en→ru, es→ru)
                                   или по language_pair профиля если задана
      !translate en <текст>      — перевод на указанный язык
      !translate (reply)         — перевод текста ответного сообщения
      !translate auto            — включить/выключить автоперевод входящих в чате
    """
    from ..core.translator_engine import translate_text
    from ..openclaw_client import openclaw_client as _oc

    # --- Парсинг аргументов ---
    raw_text = str(message.text or "").strip()
    # Убираем команду: !translate или Краб translate
    parts = raw_text.split(maxsplit=1)
    after_cmd = parts[1].strip() if len(parts) > 1 else ""

    # Специальный subcommand: !translate auto → toggle автоперевода
    if after_cmd.lower() in ("auto", "авто"):
        await handle_translate_auto(bot, message)
        return

    # Определяем target lang и текст для перевода
    tgt_lang_override: str | None = None
    text_to_translate: str = ""

    if after_cmd:
        # Проверяем первое слово — может быть указание языка
        first_word, _, rest = after_cmd.partition(" ")
        if first_word.lower() in _TRANSLATE_LANG_ALIASES:
            tgt_lang_override = _TRANSLATE_LANG_ALIASES[first_word.lower()]
            text_to_translate = rest.strip()
        else:
            text_to_translate = after_cmd.strip()

    # Если текст не указан — берём из reply
    if not text_to_translate:
        reply_msg = getattr(message, "reply_to_message", None)
        if reply_msg is not None:
            text_to_translate = str(getattr(reply_msg, "text", None) or "").strip()
        if not text_to_translate:
            raise UserInputError(
                user_message=(
                    "❌ Укажи текст для перевода:\n"
                    "`!translate <текст>`\n"
                    "`!translate en <текст>`\n"
                    "`!translate auto` — автоперевод входящих в чате\n"
                    "Или ответь командой на сообщение."
                )
            )

    # --- Определяем языковую пару ---
    profile = bot.get_translator_runtime_profile()
    pair = profile.get("language_pair", "")

    if tgt_lang_override:
        # Пользователь явно указал целевой язык — автоопределяем source через language_detect
        try:
            from ..core.language_detect import detect_language

            detected = detect_language(text_to_translate)
            src_lang = detected if detected and detected != tgt_lang_override else "auto"
            if src_lang == "auto" or not src_lang:
                # Берём src из профиля если авто не сработал
                src_lang = pair.split("-")[0] if "-" in pair else "auto"
        except Exception:
            src_lang = pair.split("-")[0] if "-" in pair else "auto"
        tgt_lang = tgt_lang_override
        if src_lang == tgt_lang:
            # Если совпадает — переводим на другой (из профиля tgt)
            tgt_lang = pair.split("-")[1] if len(pair.split("-")) >= 2 else "ru"
    elif pair and pair not in ("", "auto-detect"):
        # Профиль задан — используем пару из профиля; language_detect определяет src
        try:
            from ..core.language_detect import detect_language, resolve_translation_pair

            detected = detect_language(text_to_translate)
            if detected:
                src_lang, tgt_lang = resolve_translation_pair(detected, pair)
            else:
                src_lang, tgt_lang = (pair.split("-") + ["ru"])[:2]
        except Exception:
            src_lang, tgt_lang = (pair.split("-") + ["ru"])[:2]
    else:
        # Нет профиля или auto-detect — автоопределяем направление по языку текста
        # Правила: ru→en, en→ru, es→ru, остальное→ru
        try:
            from ..core.language_detect import auto_detect_direction, detect_language

            detected = detect_language(text_to_translate)
            if detected:
                src_lang, tgt_lang = auto_detect_direction(detected)
            else:
                # Детекция не удалась — fallback: переводим на русский
                src_lang, tgt_lang = "auto", "ru"
        except Exception:
            src_lang, tgt_lang = "auto", "ru"

    # Если src == tgt — принудительно меняем tgt на ru или на en
    if src_lang == tgt_lang:
        tgt_lang = "en" if src_lang == "ru" else "ru"

    # --- Перевод ---
    try:
        result = await translate_text(
            text_to_translate,
            src_lang,
            tgt_lang,
            openclaw_client=_oc,
            chat_id="translate_cmd",
        )
    except Exception as exc:
        logger.exception("handle_translate: ошибка при переводе")
        await message.reply(f"❌ Ошибка перевода: {str(exc)[:200]}")
        return

    if not result.translated:
        await message.reply("❌ Пустой ответ от модели.")
        return

    await message.reply(
        f"🔄 {result.src_lang}→{result.tgt_lang} ({result.latency_ms}ms)\n"
        f"**{result.original}**\n"
        f"_{result.translated}_"
    )


async def handle_translate_auto(bot: "KraabUserbot", message: Message) -> None:
    """
    !translate auto — toggle автоперевода входящих сообщений в текущем чате.

    Когда включён: все входящие текстовые сообщения переводятся автоматически
    (направление определяется через auto_detect_direction: ru→en, en→ru, es→ru).
    Повторный вызов выключает автоперевод.
    """
    chat_id = str(message.chat.id)
    is_enabled = bot.is_auto_translate_enabled(chat_id)

    if is_enabled:
        bot.remove_auto_translate_chat(chat_id)
        await message.reply("🔄 Автоперевод в этом чате выключен.")
        logger.info("auto_translate_disabled", chat_id=chat_id)
    else:
        bot.add_auto_translate_chat(chat_id)
        await message.reply(
            "🔄 Автоперевод входящих включён в этом чате.\n"
            "Направление: ru→en, en→ru, es→ru.\n"
            "Для выключения: `!translate auto`"
        )
        logger.info("auto_translate_enabled", chat_id=chat_id)


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


async def handle_react(bot: "KraabUserbot", message: Message) -> None:
    """
    !react <emoji> — поставить реакцию на сообщение.

    Должна быть ответом на сообщение (reply). Ставит указанный emoji
    как реакцию на то сообщение, которому адресован reply.
    Только для owner.

    Примеры:
        !react 👍          — лайк
        !react ❤️          — сердечко
        !react 🔥          — огонь
    """
    if not bool(getattr(config, "TELEGRAM_REACTIONS_ENABLED", True)):
        await message.reply("⚠️ Реакции отключены (TELEGRAM_REACTIONS_ENABLED=0).")
        return

    raw_args = bot._get_command_args(message).strip()
    if not raw_args:
        raise UserInputError(
            user_message="🎭 Формат: `!react <emoji>` (в reply на нужное сообщение)\n"
                         "Пример: `!react 👍`"
        )

    emoji = raw_args.strip()

    # Определяем целевое сообщение: reply → target, иначе само сообщение
    target = message.reply_to_message if message.reply_to_message else message
    chat_id_int = int(target.chat.id)
    msg_id_int = int(target.id)

    try:
        await bot.client.send_reaction(
            chat_id=chat_id_int,
            message_id=msg_id_int,
            emoji=emoji,
        )
        # Тихо удаляем команду (best-effort) — не захламляем чат
        try:
            await message.delete()
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        err_text = str(exc)[:200]
        logger.warning(
            "handle_react_failed",
            chat_id=chat_id_int,
            message_id=msg_id_int,
            emoji=emoji,
            error=err_text,
        )
        await message.reply(f"❌ Не удалось поставить реакцию `{emoji}`: {err_text}")


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
        getattr(chat, "title", None)
        or getattr(chat, "first_name", None)
        or str(chat.id)
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


async def handle_alias(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление пользовательскими алиасами команд.

    Использование:
      !alias set <имя> <команда>   — создать алиас (напр. !alias set t !translate)
      !alias list                  — показать все алиасы
      !alias del <имя>             — удалить алиас
    """
    del bot
    raw = str(message.text or "").split(maxsplit=2)
    # raw[0] = "!alias", raw[1] = subcommand, raw[2] = остаток

    if len(raw) < 2:
        await message.reply(
            "**Алиасы команд**\n\n"
            "`!alias set <имя> <команда>` — создать алиас\n"
            "`!alias list` — список алиасов\n"
            "`!alias del <имя>` — удалить алиас\n\n"
            "Пример: `!alias set t !translate` → затем `!t привет` = `!translate привет`"
        )
        return

    sub = raw[1].lower()

    if sub == "list":
        await message.reply(alias_service.format_list())

    elif sub == "set":
        if len(raw) < 3:
            raise UserInputError(user_message="Формат: `!alias set <имя> <команда>`")
        # raw[2] = "<имя> <команда>"
        parts = raw[2].split(None, 1)
        if len(parts) < 2:
            raise UserInputError(
                user_message="Формат: `!alias set <имя> <команда>`\n"
                             "Пример: `!alias set t !translate`"
            )
        alias_name, alias_cmd = parts[0], parts[1]
        ok, msg = alias_service.add(alias_name, alias_cmd)
        await message.reply(msg)

    elif sub in ("del", "delete", "rm", "remove"):
        if len(raw) < 3:
            raise UserInputError(user_message="Формат: `!alias del <имя>`")
        alias_name = raw[2].strip()
        ok, msg = alias_service.remove(alias_name)
        await message.reply(msg)

    else:
        raise UserInputError(
            user_message=f"Неизвестная подкоманда `{sub}`.\n"
                         "Доступно: `set`, `list`, `del`"
        )


async def handle_ask(bot: "KraabUserbot", message: Message) -> None:
    """
    !ask [вопрос] — задаёт вопрос AI о конкретном сообщении (reply).

    Использование:
      !ask кратко         — суммаризировать сообщение
      !ask переведи       — перевести
      !ask                — объяснить сообщение (вопрос по умолчанию)
    """
    question = bot._get_command_args(message).strip()

    # Получаем исходное сообщение — только из reply
    replied = message.reply_to_message
    if replied is None:
        raise UserInputError(
            user_message=(
                "💬 Ответь на сообщение командой `!ask [вопрос]`.\n"
                "Пример: `!ask кратко` (в reply на длинный текст)"
            )
        )

    # Извлекаем текст из reply-сообщения
    source_text = (replied.text or replied.caption or "").strip()
    if not source_text:
        raise UserInputError(
            user_message="❌ Исходное сообщение не содержит текста."
        )

    # Вопрос по умолчанию если не указан
    if not question:
        question = "Объясни это сообщение"

    # Изолированная сессия, чтобы не загрязнять основной контекст чата
    session_id = f"ask_{message.chat.id}"

    # Системный промпт: роль аналитика без лишнего контекста
    system_prompt = (
        "Ты — Краб, персональный AI-ассистент. "
        "Пользователь прислал фрагмент текста и задал вопрос о нём. "
        "Отвечай кратко и по делу. "
        "Используй язык вопроса (если вопрос на русском — отвечай по-русски)."
    )

    # Формируем промпт: текст + вопрос
    prompt = f"Текст:\n\"\"\"\n{source_text}\n\"\"\"\n\nВопрос: {question}"

    # Отправляем статус и запускаем стриминг
    msg = await message.reply("🤔 Думаю...")

    try:
        chunks: list[str] = []
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            system_prompt=system_prompt,
            disable_tools=True,  # !ask не нужны tool_calls — только ответ
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()
        if not result:
            await msg.edit("❌ AI вернул пустой ответ.")
            return

        # Разбиваем длинный ответ на куски для Telegram
        parts = _split_text_for_telegram(result)
        await msg.edit(parts[0])
        for part in parts[1:]:
            await message.reply(part)

    except Exception as exc:  # noqa: BLE001
        logger.error("handle_ask_error", error=str(exc))
        await msg.edit(f"❌ Ошибка: {exc}")



# ---------------------------------------------------------------------------
# !fix — исправление грамматики, орфографии и пунктуации через AI
# ---------------------------------------------------------------------------


async def handle_fix(bot: "KraabUserbot", message: Message) -> None:
    """
    !fix [текст] — исправляет грамматику, орфографию и пунктуацию через AI.

    Использование:
      !fix Привет как дела   — исправить текст из аргументов команды
      !fix                   — исправить текст из reply-сообщения
    """
    args_text = bot._get_command_args(message).strip()

    # Если нет аргументов — берём текст из reply
    if not args_text:
        replied = message.reply_to_message
        if replied is None:
            raise UserInputError(
                user_message=(
                    "✏️ Укажи текст после команды или ответь на сообщение:\n"
                    "`!fix Привет как дела` — исправит текст\n"
                    "`!fix` (в reply) — исправит текст ответного сообщения"
                )
            )
        source_text = (replied.text or replied.caption or "").strip()
        if not source_text:
            raise UserInputError(
                user_message="❌ Исходное сообщение не содержит текста."
            )
    else:
        source_text = args_text

    # Изолированная сессия — не загрязняем основной контекст чата
    session_id = f"fix_{message.chat.id}"

    # Промпт: только исправленный текст без объяснений
    prompt = (
        "Исправь грамматику, орфографию и пунктуацию. "
        "Верни ТОЛЬКО исправленный текст.\n\n"
        f"{source_text}"
    )

    # Статусное сообщение пока AI обрабатывает
    msg = await message.reply("✏️ Исправляю...")

    try:
        chunks: list[str] = []
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            disable_tools=True,       # только текстовый ответ, без tool_calls
            max_output_tokens=512,    # короткий вывод — только исправленный текст
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()
        if not result:
            await msg.edit("❌ AI вернул пустой ответ.")
            return

        # Разбиваем длинный ответ на куски для Telegram (редко нужно, но на всякий случай)
        parts = _split_text_for_telegram(result)
        await msg.edit(parts[0])
        for part in parts[1:]:
            await message.reply(part)

    except Exception as exc:  # noqa: BLE001
        logger.error("handle_fix_error", error=str(exc))
        await msg.edit(f"❌ Ошибка: {exc}")


# ---------------------------------------------------------------------------
# !rewrite — переписывание / улучшение текста через LLM
# ---------------------------------------------------------------------------

# Поддерживаемые режимы и их промпты
_REWRITE_MODES: dict[str, tuple[str, str]] = {
    "formal": (
        "formal",
        "Перепиши текст в официальном / формальном стиле. "
        "Сохраняй смысл, избегай сленга и разговорных выражений.",
    ),
    "casual": (
        "casual",
        "Перепиши текст в разговорном / неформальном стиле. "
        "Можно использовать живой язык, сокращения, избегать бюрократических оборотов.",
    ),
    "short": (
        "short",
        "Сократи текст: убери воду, оставь только суть. "
        "Итог должен быть заметно короче оригинала.",
    ),
    # режим по умолчанию — ключ пустая строка
    "": (
        "default",
        "Улучши текст: сделай его чётче, читабельнее и грамотнее. "
        "Исправь стиль и формулировки, сохрани смысл и язык оригинала.",
    ),
}


async def handle_rewrite(bot: "KraabUserbot", message: Message) -> None:
    """
    !rewrite [режим] [текст] — переписывает текст через LLM.

    Режимы:
      !rewrite <текст>          — улучшить / переписать
      !rewrite formal <текст>   — формальный стиль
      !rewrite casual <текст>   — разговорный стиль
      !rewrite short <текст>    — сократить

    Также работает в reply — если текст не указан, берётся из ответного сообщения.
    """
    args = bot._get_command_args(message).strip()

    # Определяем режим — первое слово, если оно совпадает с известным
    mode_key = ""
    text_to_rewrite = ""

    if args:
        first_word = args.split()[0].lower()
        if first_word in _REWRITE_MODES:
            mode_key = first_word
            text_to_rewrite = args[len(first_word):].strip()
        else:
            text_to_rewrite = args

    # Если текст не передан аргументом — пробуем reply
    if not text_to_rewrite:
        replied = message.reply_to_message
        if replied is None:
            raise UserInputError(
                user_message=(
                    "✏️ Использование:\n"
                    "- `!rewrite <текст>` — улучшить текст\n"
                    "- `!rewrite formal <текст>` — формальный стиль\n"
                    "- `!rewrite casual <текст>` — разговорный стиль\n"
                    "- `!rewrite short <текст>` — сократить\n\n"
                    "Или ответь на сообщение командой `!rewrite [режим]`."
                )
            )
        text_to_rewrite = (replied.text or replied.caption or "").strip()
        if not text_to_rewrite:
            raise UserInputError(
                user_message="❌ Исходное сообщение не содержит текста."
            )

    _mode_label, mode_instruction = _REWRITE_MODES[mode_key]

    # Системный промпт
    system_prompt = (
        "Ты — Краб, персональный AI-ассистент. "
        "Твоя задача — редактировать тексты по инструкции пользователя. "
        "Возвращай ТОЛЬКО переписанный текст без пояснений, заголовков и лишних слов. "
        "Сохраняй язык оригинала (если текст на русском — отвечай по-русски, "
        "если на английском — по-английски)."
    )

    # Промпт = инструкция + текст
    prompt = f"{mode_instruction}\n\nТекст:\n\"\"\"\n{text_to_rewrite}\n\"\"\""

    # Изолированная сессия, чтобы не загрязнять основной контекст чата
    session_id = f"rewrite_{message.chat.id}"

    msg = await message.reply("✏️ Переписываю...")

    try:
        chunks: list[str] = []
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            system_prompt=system_prompt,
            disable_tools=True,  # только текстовый ответ, tool_calls не нужны
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()
        if not result:
            await msg.edit("❌ AI вернул пустой ответ.")
            return

        # Разбиваем длинный ответ на куски для Telegram
        parts = _split_text_for_telegram(result)
        await msg.edit(parts[0])
        for part in parts[1:]:
            await message.reply(part)

    except Exception as exc:  # noqa: BLE001
        logger.error("handle_rewrite_error", error=str(exc))
        await msg.edit(f"❌ Ошибка: {exc}")


# ---------------------------------------------------------------------------
# !report — структурированный отчёт
# ---------------------------------------------------------------------------

def _collect_daily_report_data() -> dict:
    """Собирает данные для дневного отчёта из доступных источников."""
    import time as _time

    from ..core.swarm_artifact_store import swarm_artifact_store

    data: dict = {}

    # --- Расходы за сегодня ---
    try:
        today_start = _time.mktime(datetime.date.today().timetuple())
        today_calls = [r for r in cost_analytics._calls if r.timestamp >= today_start]
        data["cost_today_usd"] = round(sum(r.cost_usd for r in today_calls), 4)
        data["cost_month_usd"] = round(cost_analytics.get_monthly_cost_usd(), 4)
        data["calls_today"] = len(today_calls)
        data["tokens_today"] = sum(r.input_tokens + r.output_tokens for r in today_calls)
    except Exception:  # noqa: BLE001
        data["cost_today_usd"] = 0.0
        data["cost_month_usd"] = 0.0
        data["calls_today"] = 0
        data["tokens_today"] = 0

    # --- Swarm rounds за сегодня ---
    try:
        all_arts = swarm_artifact_store.list_artifacts(limit=500)
        today_str = datetime.date.today().isoformat()
        today_arts = [a for a in all_arts if str(a.get("timestamp_iso", "")).startswith(today_str)]
        data["swarm_rounds_today"] = len(today_arts)
        data["swarm_teams_today"] = sorted({a.get("team", "?") for a in today_arts})
        data["swarm_duration_today"] = sum(a.get("duration_sec", 0) for a in today_arts)
    except Exception:  # noqa: BLE001
        data["swarm_rounds_today"] = 0
        data["swarm_teams_today"] = []
        data["swarm_duration_today"] = 0

    # --- Errors/warnings из inbox ---
    try:
        summary = inbox_service.get_summary()
        data["inbox_open"] = summary.get("open", 0)
        data["inbox_errors"] = summary.get("error", 0)
        data["inbox_warnings"] = summary.get("warning", 0)
    except Exception:  # noqa: BLE001
        data["inbox_open"] = 0
        data["inbox_errors"] = 0
        data["inbox_warnings"] = 0

    return data


def _render_daily_report(data: dict) -> str:
    """Форматирует дневной отчёт в markdown."""
    today = datetime.date.today().isoformat()
    lines = [
        f"📊 **Daily Report — {today}**",
        "",
        "**💰 Расходы**",
        f"  • Сегодня: ${data['cost_today_usd']:.4f} ({data['calls_today']} вызовов, {data['tokens_today']:,} токенов)",
        f"  • Месяц: ${data['cost_month_usd']:.4f}",
        "",
        "**🐝 Swarm**",
        f"  • Раундов сегодня: {data['swarm_rounds_today']}",
    ]
    if data["swarm_teams_today"]:
        lines.append(f"  • Команды: {', '.join(data['swarm_teams_today'])}")
    if data["swarm_duration_today"]:
        lines.append(f"  • Суммарное время: {data['swarm_duration_today']:.0f}с")
    lines += [
        "",
        "**⚠️ Inbox**",
        f"  • Открытых: {data['inbox_open']} (🔴 ошибок: {data['inbox_errors']}, 🟡 warnings: {data['inbox_warnings']})",
    ]
    return "\n".join(lines)


async def handle_poll(bot: "KraabUserbot", message: Message) -> None:
    """
    Быстрое создание опросов в чате.

    Синтаксис:
      !poll <вопрос> | <вариант1> | <вариант2> [| ...]
      !poll anonymous <вопрос> | <вариант1> | <вариант2> [| ...]
    Минимум 2, максимум 10 вариантов.
    """
    raw = bot._get_command_args(message).strip()

    if not raw or raw.lower() in {"help", "помощь"}:
        raise UserInputError(
            user_message=(
                "📊 **!poll — создание опроса**\n\n"
                "`!poll Вопрос? | Вариант 1 | Вариант 2`\n"
                "`!poll anonymous Вопрос? | Вариант 1 | Вариант 2`\n\n"
                "Минимум 2, максимум 10 вариантов. Разделитель — `|`."
            )
        )

    # Определяем режим анонимности
    is_anonymous = False
    if raw.lower().startswith("anonymous "):
        is_anonymous = True
        raw = raw[len("anonymous "):].strip()

    # Разбираем вопрос и варианты
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 3:
        raise UserInputError(
            user_message="❌ Нужно минимум 2 варианта. Синтаксис: `!poll Вопрос? | Вариант 1 | Вариант 2`"
        )

    question = parts[0]
    options = parts[1:]

    if len(options) > 10:
        raise UserInputError(user_message="❌ Максимум 10 вариантов ответа.")

    if not question:
        raise UserInputError(user_message="❌ Вопрос не может быть пустым.")

    # Удаляем исходное сообщение с командой
    try:
        await message.delete()
    except Exception:  # noqa: BLE001
        pass

    await bot.client.send_poll(
        chat_id=message.chat.id,
        question=question,
        options=options,
        is_anonymous=is_anonymous,
    )
    logger.info("handle_poll_sent", question=question, options_count=len(options), anonymous=is_anonymous)


async def handle_quiz(bot: "KraabUserbot", message: Message) -> None:
    """
    Создание квиза (опрос с правильным ответом).

    Синтаксис:
      !quiz <вопрос> | <правильный ответ> | <неправильный1> [| ...]
    Первый вариант всегда правильный. Минимум 2, максимум 10 вариантов.
    """
    raw = bot._get_command_args(message).strip()

    if not raw or raw.lower() in {"help", "помощь"}:
        raise UserInputError(
            user_message=(
                "🧠 **!quiz — создание квиза**\n\n"
                "`!quiz Вопрос? | Правильный ответ | Неправильный 1 | Неправильный 2`\n\n"
                "Первый вариант — правильный. Минимум 2, максимум 10 вариантов."
            )
        )

    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 3:
        raise UserInputError(
            user_message="❌ Нужно минимум 2 варианта. Синтаксис: `!quiz Вопрос? | Правильный | Неправильный`"
        )

    question = parts[0]
    options = parts[1:]

    if len(options) > 10:
        raise UserInputError(user_message="❌ Максимум 10 вариантов ответа.")

    if not question:
        raise UserInputError(user_message="❌ Вопрос не может быть пустым.")

    # Удаляем исходное сообщение с командой
    try:
        await message.delete()
    except Exception:  # noqa: BLE001
        pass

    await bot.client.send_poll(
        chat_id=message.chat.id,
        question=question,
        options=options,
        type="quiz",
        correct_option_id=0,  # первый вариант — правильный
        is_anonymous=False,
    )
    logger.info("handle_quiz_sent", question=question, options_count=len(options))


async def handle_dice(bot: "KraabUserbot", message: Message) -> None:
    """
    Отправка анимированных Telegram dice (кубик/дартс/футбол/баскетбол/боулинг/слот).

    Синтаксис:
      !dice            → 🎲 кубик (по умолчанию)
      !dice dart       → 🎯 дартс
      !dice ball       → ⚽ футбол
      !dice basket     → 🏀 баскетбол
      !dice bowl       → 🎳 боулинг
      !dice slot       → 🎰 слот-машина
    """
    # Карта alias → эмодзи
    _DICE_ALIASES: dict[str, str] = {
        "": "🎲",
        "dice": "🎲",
        "dart": "🎯",
        "darts": "🎯",
        "ball": "⚽",
        "football": "⚽",
        "soccer": "⚽",
        "basket": "🏀",
        "basketball": "🏀",
        "bowl": "🎳",
        "bowling": "🎳",
        "slot": "🎰",
        "slots": "🎰",
        "casino": "🎰",
    }

    raw = bot._get_command_args(message).strip().lower()

    emoji = _DICE_ALIASES.get(raw)
    if emoji is None:
        raise UserInputError(
            user_message=(
                "🎲 **!dice — анимированные кубики**\n\n"
                "`!dice` — 🎲 кубик\n"
                "`!dice dart` — 🎯 дартс\n"
                "`!dice ball` — ⚽ футбол\n"
                "`!dice basket` — 🏀 баскетбол\n"
                "`!dice bowl` — 🎳 боулинг\n"
                "`!dice slot` — 🎰 слот-машина"
            )
        )

    # Удаляем команду (best-effort)
    try:
        await message.delete()
    except Exception:  # noqa: BLE001
        pass

    await bot.client.send_dice(chat_id=message.chat.id, emoji=emoji)
    logger.info("handle_dice_sent", emoji=emoji, chat_id=message.chat.id)


async def handle_report(bot: "KraabUserbot", message: Message) -> None:
    """
    Структурированный отчёт через LLM.

    Синтаксис:
      !report daily   — дневной отчёт (cost, swarm rounds, ошибки)
      !report weekly  — недельный отчёт через WeeklyDigest
      !report <тема>  — кастомный отчёт через LLM по заданной теме

    Owner-only команда.
    """
    # Проверка прав
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 Команда доступна только владельцу.")

    args = bot._get_command_args(message).strip()

    if not args or args.lower() in {"help", "помощь"}:
        raise UserInputError(
            user_message=(
                "📊 **!report — генерация отчётов**\n\n"
                "`!report daily` — дневной отчёт (cost, swarm, ошибки)\n"
                "`!report weekly` — недельный отчёт через WeeklyDigest\n"
                "`!report <тема>` — кастомный отчёт через LLM по любой теме"
            )
        )

    # --- daily ---
    if args.lower() in {"daily", "день", "дневной"}:
        status_msg = await message.reply("⏳ Собираю данные за сегодня...")
        try:
            data = _collect_daily_report_data()
            report_text = _render_daily_report(data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("handle_report_daily_failed", error=str(exc))
            await status_msg.edit(f"❌ Ошибка сбора данных: {exc}")
            return
        await status_msg.edit(report_text)
        return

    # --- weekly ---
    if args.lower() in {"weekly", "неделя", "недельный"}:
        status_msg = await message.reply("⏳ Генерирую недельный отчёт...")
        try:
            result = await weekly_digest.generate_digest()
        except Exception as exc:  # noqa: BLE001
            logger.warning("handle_report_weekly_failed", error=str(exc))
            await status_msg.edit(f"❌ Ошибка генерации недельного отчёта: {exc}")
            return

        if not result.get("ok"):
            err = result.get("error", "неизвестная ошибка")
            await status_msg.edit(f"❌ Недельный отчёт не удался: {err}")
            return

        rounds = result.get("total_rounds", 0)
        cost = result.get("cost_week_usd", 0.0)
        attention = result.get("attention_count", 0)
        calls = result.get("calls_count", 0)
        tokens = result.get("total_tokens", 0)

        lines = [
            "📊 **Weekly Report**",
            "",
            "**🐝 Swarm**",
            f"  • Раундов за неделю: {rounds}",
            "",
            "**💰 Расходы (7 дней)**",
            f"  • Cost: ${cost:.4f}",
            f"  • Вызовов: {calls}",
            f"  • Токенов: {tokens:,}",
            "",
            "**⚠️ Inbox (attention)**",
            f"  • Требуют внимания: {attention}",
        ]
        await status_msg.edit("\n".join(lines))
        return

    # --- кастомный отчёт через LLM ---
    topic = args
    status_msg = await message.reply(f"⏳ Генерирую отчёт по теме: **{topic}**...")

    # Собираем контекст системных данных для LLM
    try:
        daily_data = _collect_daily_report_data()
        context_block = (
            f"Текущие системные данные Краба (на {datetime.date.today().isoformat()}):\n"
            f"- Расходы сегодня: ${daily_data['cost_today_usd']:.4f} ({daily_data['calls_today']} вызовов)\n"
            f"- Расходы за месяц: ${daily_data['cost_month_usd']:.4f}\n"
            f"- Swarm раундов сегодня: {daily_data['swarm_rounds_today']}\n"
            f"- Команды сегодня: {', '.join(daily_data['swarm_teams_today']) or 'нет'}\n"
            f"- Открытых inbox-items: {daily_data['inbox_open']} "
            f"(ошибок: {daily_data['inbox_errors']}, warnings: {daily_data['inbox_warnings']})\n"
        )
    except Exception:  # noqa: BLE001
        context_block = ""

    prompt = (
        f"Ты — аналитик Telegram userbot Краб. Напиши структурированный отчёт по теме: **{topic}**.\n\n"
        f"{context_block}\n"
        "Требования к отчёту:\n"
        "- Оформи в виде markdown с секциями\n"
        "- Выдели ключевые метрики, выводы, рекомендации\n"
        "- Будь конкретным и кратким\n"
        "- Отвечай на русском языке\n"
    )

    header = f"📊 **Отчёт: {topic}**\n─────────────────\n"
    await status_msg.edit(header + "⏳ LLM генерирует...")

    chunks: list[str] = []
    last_edit_len = 0
    edit_threshold = 200

    try:
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=f"report_{message.chat.id}_{int(datetime.datetime.now().timestamp())}",
            disable_tools=True,
        ):
            chunks.append(str(chunk))
            total = "".join(chunks)
            if len(total) - last_edit_len >= edit_threshold:
                last_edit_len = len(total)
                preview = total
                max_preview = 4000 - len(header)
                if len(preview) > max_preview:
                    preview = preview[-max_preview:]
                try:
                    await status_msg.edit(header + preview)
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("handle_report_llm_failed", topic=topic, error=str(exc))
        await status_msg.edit(f"❌ Ошибка генерации отчёта: {exc}")
        return

    # Финальное обновление
    final_text = "".join(chunks)
    max_len = 4000 - len(header)
    if len(final_text) > max_len:
        final_text = final_text[-max_len:]
    try:
        await status_msg.edit(header + final_text)
    except Exception:  # noqa: BLE001
        pass


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
            f"🔍 Ничего не найдено для `{display_query}` "
            f"в последних {scanned} сообщениях."
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
# !timer — таймер с обратным отсчётом
# ---------------------------------------------------------------------------


async def handle_timer(bot: "KraabUserbot", message: Message) -> None:
    """Управление таймерами: !timer <время>, !timer list, !timer cancel [id]."""
    global _timer_counter  # noqa: PLW0603

    args = bot._get_command_args(message).strip()

    # --- список активных таймеров ---
    if args in ("list", "список", "ls"):
        if not _active_timers:
            await message.reply("⏱ Нет активных таймеров.")
            return
        lines = ["⏱ **Активные таймеры:**"]
        now = time.monotonic()
        for tid, info in sorted(_active_timers.items()):
            remaining = max(0.0, info["ends_at"] - now)
            label = info.get("label") or ""
            label_part = f" — {label}" if label else ""
            lines.append(f"• `#{tid}` {_fmt_duration(remaining)} осталось{label_part}")
        await message.reply("\n".join(lines))
        return

    # --- отмена таймера ---
    if args.startswith(("cancel", "отмена", "stop")):
        parts = args.split(maxsplit=1)
        if len(parts) < 2:
            # Отменить все
            if not _active_timers:
                await message.reply("⏱ Нет активных таймеров.")
                return
            for info in _active_timers.values():
                info["task"].cancel()
            count = len(_active_timers)
            _active_timers.clear()
            await message.reply(f"✅ Отменено таймеров: {count}.")
            return
        id_str = parts[1].lstrip("#")
        if not id_str.isdigit():
            await message.reply("❌ Укажи ID таймера: `!timer cancel 3`")
            return
        tid = int(id_str)
        if tid not in _active_timers:
            await message.reply(f"❌ Таймер `#{tid}` не найден.")
            return
        _active_timers[tid]["task"].cancel()
        del _active_timers[tid]
        await message.reply(f"✅ Таймер `#{tid}` отменён.")
        return

    # --- новый таймер: парсим первое слово как длительность, остальное — метка ---
    parts = args.split(maxsplit=1)
    if not parts or not args:
        await message.reply(
            "⏱ Использование:\n"
            "`!timer 5m` — таймер на 5 минут\n"
            "`!timer 1h30m Обед` — таймер с меткой\n"
            "`!timer list` — список\n"
            "`!timer cancel [id]` — отмена"
        )
        return

    seconds = _parse_duration(parts[0])
    if seconds is None:
        await message.reply(
            f"❌ Не могу распарсить время: `{parts[0]}`\n"
            "Примеры: `5m`, `1h30m`, `90s`, `3600`"
        )
        return

    label = parts[1] if len(parts) > 1 else ""
    chat_id = message.chat.id

    _timer_counter += 1
    tid = _timer_counter

    async def _timer_callback(t_id: int, secs: int, c_id: int, lbl: str) -> None:
        """Ждёт нужное время, затем отправляет уведомление в чат.
        Если таймер был запущен в группе — уведомление идёт в Saved Messages,
        чтобы не засорять групповой чат техническими нотификациями.
        """
        try:
            await asyncio.sleep(secs)
        except asyncio.CancelledError:
            return
        label_part = f" — {lbl}" if lbl else ""
        # Группы имеют отрицательный chat_id — перенаправляем к себе
        if c_id < 0:
            target = "me"
            text = f"⏰ **Таймер истёк!**{label_part} (#{t_id})\n_(из группы `{c_id}`)_"
        else:
            target = c_id
            text = f"⏰ **Таймер истёк!**{label_part} (#{t_id})"
        try:
            await bot.client.send_message(target, text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("timer_notify_failed", timer_id=t_id, error=str(exc))
        finally:
            _active_timers.pop(t_id, None)

    task = asyncio.create_task(_timer_callback(tid, seconds, chat_id, label))
    _active_timers[tid] = {
        "task": task,
        "label": label,
        "ends_at": time.monotonic() + seconds,
        "chat_id": chat_id,
    }

    label_part = f" — {label}" if label else ""
    await message.reply(
        f"⏱ Таймер `#{tid}` запущен на **{_fmt_duration(seconds)}**{label_part}."
    )


# ---------------------------------------------------------------------------
# !stopwatch — секундомер
# ---------------------------------------------------------------------------


async def handle_stopwatch(bot: "KraabUserbot", message: Message) -> None:
    """Управление секундомером: !stopwatch start|stop|lap."""
    args = bot._get_command_args(message).strip().lower()
    chat_id = message.chat.id

    if args in ("start", "старт", "go"):
        if chat_id in _stopwatches:
            elapsed = time.monotonic() - _stopwatches[chat_id]["started_at"]
            await message.reply(
                f"⚡ Секундомер уже запущен ({_fmt_duration(elapsed)} прошло). "
                "Используй `!stopwatch stop` для остановки."
            )
            return
        _stopwatches[chat_id] = {"started_at": time.monotonic(), "laps": []}
        await message.reply("▶️ Секундомер запущен.")
        return

    if args in ("stop", "стоп", "end"):
        if chat_id not in _stopwatches:
            await message.reply("⏱ Секундомер не запущен. Используй `!stopwatch start`.")
            return
        sw = _stopwatches.pop(chat_id)
        elapsed = time.monotonic() - sw["started_at"]
        laps = sw["laps"]
        lines = [f"⏹ Секундомер остановлен. **Итого: {_fmt_duration(elapsed)}**"]
        if laps:
            lines.append("")
            for i, lap_ts in enumerate(laps, 1):
                lap_elapsed = lap_ts - sw["started_at"]
                lines.append(f"  Круг {i}: {_fmt_duration(lap_elapsed)}")
        await message.reply("\n".join(lines))
        return

    if args in ("lap", "круг", "split"):
        if chat_id not in _stopwatches:
            await message.reply("⏱ Секундомер не запущен. Используй `!stopwatch start`.")
            return
        sw = _stopwatches[chat_id]
        now = time.monotonic()
        sw["laps"].append(now)
        elapsed = now - sw["started_at"]
        lap_num = len(sw["laps"])
        if lap_num > 1:
            prev = sw["laps"][-2]
            split = now - prev
            await message.reply(
                f"🔵 Круг {lap_num}: **{_fmt_duration(elapsed)}** "
                f"(+{_fmt_duration(split)} с прошлого)"
            )
        else:
            await message.reply(f"🔵 Круг {lap_num}: **{_fmt_duration(elapsed)}**")
        return

    # status / без аргументов
    if args in ("", "status", "статус", "time"):
        if chat_id not in _stopwatches:
            await message.reply("⏱ Секундомер не запущен. Используй `!stopwatch start`.")
            return
        sw = _stopwatches[chat_id]
        elapsed = time.monotonic() - sw["started_at"]
        laps = sw["laps"]
        lines = [f"⏱ Текущее время: **{_fmt_duration(elapsed)}**"]
        if laps:
            lines.append(f"Кругов: {len(laps)}")
        await message.reply("\n".join(lines))
        return

    await message.reply(
        "⏱ Использование секундомера:\n"
        "`!stopwatch start` — запустить\n"
        "`!stopwatch stop` — остановить\n"
        "`!stopwatch lap` — отметить круг\n"
        "`!stopwatch` — текущее время"
    )


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


async def handle_todo(bot: "KraabUserbot", message: Message) -> None:
    """
    Персональный менеджер задач в Telegram.

    Синтаксис:
      !todo                   — показать все задачи
      !todo list              — то же самое
      !todo add <текст>       — добавить задачу
      !todo done <id>         — отметить выполненной
      !todo del <id>          — удалить задачу
      !todo clear done        — очистить выполненные
    """
    from ..core.personal_todo import personal_todo_service

    args = bot._get_command_args(message).strip()

    # Без аргументов или "list" — показать список
    if not args or args.lower() in ("list",):
        await message.reply(personal_todo_service.render())
        return

    parts = args.split(maxsplit=1)
    sub = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    # --- add ---
    if sub == "add":
        if not rest:
            raise UserInputError(user_message="📋 Формат: `!todo add <текст задачи>`")
        item = personal_todo_service.add(rest)
        await message.reply(f"✅ Задача добавлена: **{item['id']}. {item['text']}**")
        return

    # --- done ---
    if sub == "done":
        if not rest.isdigit():
            raise UserInputError(user_message="📋 Формат: `!todo done <id>`")
        todo_id = int(rest)
        item = personal_todo_service.mark_done(todo_id)
        if item is None:
            raise UserInputError(user_message=f"📋 Задача #{todo_id} не найдена.")
        await message.reply(f"✅ Отмечено выполненным: ~{item['text']}~")
        return

    # --- del ---
    if sub in ("del", "delete", "rm"):
        if not rest.isdigit():
            raise UserInputError(user_message="📋 Формат: `!todo del <id>`")
        todo_id = int(rest)
        deleted = personal_todo_service.delete(todo_id)
        if not deleted:
            raise UserInputError(user_message=f"📋 Задача #{todo_id} не найдена.")
        await message.reply(f"🗑 Задача #{todo_id} удалена.")
        return

    # --- clear done ---
    if sub == "clear" and rest.lower() == "done":
        count = personal_todo_service.clear_done()
        if count == 0:
            await message.reply("📋 Нет выполненных задач для очистки.")
        else:
            await message.reply(f"🗑 Очищено {count} выполненных задач.")
        return

    # Неизвестная подкоманда
    raise UserInputError(
        user_message=(
            "📋 **Todo — менеджер задач**\n\n"
            "`!todo` / `!todo list` — показать все задачи\n"
            "`!todo add <текст>` — добавить задачу\n"
            "`!todo done <id>` — отметить выполненной\n"
            "`!todo del <id>` — удалить задачу\n"
            "`!todo clear done` — очистить выполненные"
        )
    )


# ---------------------------------------------------------------------------
# !weather — погода через OpenClaw + web_search
# ---------------------------------------------------------------------------

async def handle_weather(bot: "KraabUserbot", message: Message) -> None:
    """
    Показывает текущую погоду для города через LLM + web_search.

    Форматы:
    - !weather          — погода в городе по умолчанию (DEFAULT_WEATHER_CITY)
    - !weather <город>  — погода в указанном городе
    """
    # Определяем город: из аргументов или из конфига
    city = bot._get_command_args(message).strip()
    if not city:
        city = config.DEFAULT_WEATHER_CITY

    # Изолированная сессия, чтобы не загрязнять основной контекст чата
    session_id = f"weather_{message.chat.id}"

    prompt = (
        f"Какая сейчас погода в {city}? "
        "Дай краткий ответ: температура, облачность, осадки. "
        "Используй актуальные данные из веб-поиска."
    )

    msg = await message.reply(f"🌤 Смотрю погоду в **{city}**...")

    try:
        chunks: list[str] = []
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            disable_tools=False,  # LLM должен использовать web_search
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()
        if not result:
            await msg.edit("❌ Не удалось получить данные о погоде.")
            return

        # Разбиваем длинный ответ если нужно
        parts = _split_text_for_telegram(result)
        await msg.edit(parts[0])
        for part in parts[1:]:
            await message.reply(part)

    except Exception as exc:  # noqa: BLE001
        logger.error("handle_weather_error", error=str(exc))
        await msg.edit(f"❌ Ошибка получения погоды: {exc}")


# ---------------------------------------------------------------------------
# !hash — хэширование текста (MD5, SHA1, SHA256)
# ---------------------------------------------------------------------------

async def handle_hash(bot: "KraabUserbot", message: Message) -> None:
    """
    Хэширует текст и возвращает MD5 / SHA1 / SHA256.

    Синтаксис:
      !hash <текст>         — все три хэша
      !hash md5 <текст>     — только MD5
      !hash sha1 <текст>    — только SHA1
      !hash sha256 <текст>  — только SHA256
      !hash (reply)         — хэши текста из ответного сообщения
    """
    import hashlib

    # Алгоритмы, поддерживаемые как первый аргумент
    _known_algos = {"md5", "sha1", "sha256"}

    raw_args = bot._get_command_args(message).strip()

    # Определяем алгоритм-фильтр и текст для хэширования
    algo_filter: str | None = None
    text: str = ""

    if raw_args:
        parts = raw_args.split(maxsplit=1)
        if parts[0].lower() in _known_algos:
            # Первый токен — алгоритм
            algo_filter = parts[0].lower()
            text = parts[1].strip() if len(parts) > 1 else ""
        else:
            text = raw_args

    # Если текст пустой — берём из reply-сообщения
    if not text and message.reply_to_message:
        replied = message.reply_to_message
        text = (replied.text or replied.caption or "").strip()

    if not text:
        raise UserInputError(
            user_message=(
                "🔐 Укажи текст: `!hash <текст>`, `!hash md5 <текст>` "
                "или ответь командой на сообщение."
            )
        )

    # Вычисляем хэши
    encoded = text.encode("utf-8")
    hashes = {
        "md5": hashlib.md5(encoded).hexdigest(),
        "sha1": hashlib.sha1(encoded).hexdigest(),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }

    # Формируем ответ
    if algo_filter:
        # Только один алгоритм
        result = f"🔐 `{algo_filter.upper()}`\n─────\n`{hashes[algo_filter]}`"
    else:
        # Все три
        result = (
            "🔐 Hash\n"
            "─────\n"
            f"MD5:    `{hashes['md5']}`\n"
            f"SHA1:   `{hashes['sha1']}`\n"
            f"SHA256: `{hashes['sha256']}`"
        )

    await message.reply(result)

# ---------------------------------------------------------------------------
# !calc — безопасный калькулятор (compile + ограниченный namespace)
# ---------------------------------------------------------------------------

# Разрешённые бинарные операции
_CALC_BINOPS: dict[type, object] = {
    _ast.Add: _operator.add,
    _ast.Sub: _operator.sub,
    _ast.Mult: _operator.mul,
    _ast.Div: _operator.truediv,
    _ast.Mod: _operator.mod,
    _ast.Pow: _operator.pow,
    _ast.FloorDiv: _operator.floordiv,
}

# Разрешённые унарные операции
_CALC_UNOPS: dict[type, object] = {
    _ast.UAdd: _operator.pos,
    _ast.USub: _operator.neg,
}

# Разрешённые функции и константы
_CALC_NAMESPACE: dict[str, object] = {
    "sqrt": _math.sqrt,
    "sin": _math.sin,
    "cos": _math.cos,
    "tan": _math.tan,
    "log": _math.log,
    "log2": _math.log2,
    "log10": _math.log10,
    "abs": abs,
    "round": round,
    "pi": _math.pi,
    "e": _math.e,
    "inf": _math.inf,
}


def _calc_eval_node(node: _ast.AST) -> float | int:
    """Рекурсивно вычисляет AST-узел. Разрешены только безопасные операции."""
    if isinstance(node, _ast.Expression):
        return _calc_eval_node(node.body)
    if isinstance(node, _ast.Constant):
        # bool — подкласс int, но недопустим в математических выражениях
        if isinstance(node.value, bool):
            raise UserInputError(user_message=f"❌ Недопустимый литерал: {node.value!r}")
        if isinstance(node.value, (int, float)):
            return node.value
        raise UserInputError(user_message=f"❌ Недопустимый литерал: {node.value!r}")
    if isinstance(node, _ast.BinOp):
        op_fn = _CALC_BINOPS.get(type(node.op))
        if op_fn is None:
            raise UserInputError(user_message="❌ Недопустимая операция.")
        left = _calc_eval_node(node.left)
        right = _calc_eval_node(node.right)
        try:
            return op_fn(left, right)  # type: ignore[operator]
        except ZeroDivisionError:
            raise UserInputError(user_message="❌ Деление на ноль.")
    if isinstance(node, _ast.UnaryOp):
        op_fn = _CALC_UNOPS.get(type(node.op))
        if op_fn is None:
            raise UserInputError(user_message="❌ Недопустимая унарная операция.")
        return op_fn(_calc_eval_node(node.operand))  # type: ignore[operator]
    if isinstance(node, _ast.Call):
        # Разрешены только функции из _CALC_NAMESPACE
        if not isinstance(node.func, _ast.Name):
            raise UserInputError(user_message="❌ Недопустимый вызов функции.")
        fn = _CALC_NAMESPACE.get(node.func.id)
        if fn is None:
            raise UserInputError(user_message=f"❌ Функция не поддерживается: {node.func.id}()")
        if node.keywords:
            raise UserInputError(user_message="❌ Именованные аргументы не поддерживаются.")
        args = [_calc_eval_node(a) for a in node.args]
        try:
            return fn(*args)  # type: ignore[operator]
        except (ValueError, TypeError) as exc:
            raise UserInputError(user_message=f"❌ Ошибка вычисления: {exc}")
    if isinstance(node, _ast.Name):
        val = _CALC_NAMESPACE.get(node.id)
        if val is None or not isinstance(val, (int, float)):
            raise UserInputError(user_message=f"❌ Неизвестная переменная: {node.id}")
        return val  # type: ignore[return-value]
    raise UserInputError(user_message=f"❌ Недопустимая конструкция: {type(node).__name__}")


def safe_calc(expression: str) -> float | int:
    """
    Безопасно вычисляет математическое выражение через AST.
    Не использует eval/exec напрямую — только разбор AST и whitelisted операции.
    """
    expression = expression.strip()
    if not expression:
        raise UserInputError(user_message="❌ Пустое выражение.")
    if len(expression) > 200:
        raise UserInputError(user_message="❌ Выражение слишком длинное (макс. 200 символов).")
    try:
        tree = compile(expression, "<calc>", "eval", _ast.PyCF_ONLY_AST)
    except SyntaxError as exc:
        raise UserInputError(user_message=f"❌ Синтаксическая ошибка: {exc.msg}")
    return _calc_eval_node(tree)


async def handle_calc(bot: "KraabUserbot", message: Message) -> None:
    """
    !calc <выражение> — безопасный калькулятор.

    Примеры:
      !calc 2+2*3       → = 8
      !calc sqrt(144)   → = 12.0
      !calc sin(pi/2)   → = 1.0
    """
    expr = bot._get_command_args(message).strip()
    if not expr:
        raise UserInputError(
            user_message=(
                "🧮 **Калькулятор**\n\n"
                "Использование: `!calc <выражение>`\n\n"
                "Примеры:\n"
                "`!calc 2+2*3` → `= 8`\n"
                "`!calc sqrt(144)` → `= 12.0`\n"
                "`!calc sin(pi/2)` → `= 1.0`\n\n"
                "Поддерживаются: `+`, `-`, `*`, `/`, `**`, `%`, `//`\n"
                "Функции: `sqrt`, `sin`, `cos`, `tan`, `log`, `log2`, `log10`, `abs`, `round`\n"
                "Константы: `pi`, `e`"
            )
        )
    result = safe_calc(expr)
    # Красиво форматируем: целые числа без .0
    if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
        formatted = str(int(result))
    else:
        formatted = str(result)
    await message.reply(f"= {formatted}")


# ---------------------------------------------------------------------------
# !b64 — кодирование/декодирование Base64
# ---------------------------------------------------------------------------

import base64 as _base64  # noqa: E402


def _b64_is_valid(text: str) -> bool:
    """Проверяет, является ли строка валидным Base64 (паддинг добавляется автоматически)."""
    # Убираем пробельные символы — допустимы в некоторых форматах
    stripped = text.strip().replace("\n", "").replace(" ", "")
    if not stripped:
        return False
    # Паддим до кратности 4
    padded = stripped + "=" * ((4 - len(stripped) % 4) % 4)
    try:
        _base64.b64decode(padded, validate=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def _b64_encode(text: str) -> str:
    """Кодирует текст в Base64 (UTF-8)."""
    return _base64.b64encode(text.encode("utf-8")).decode("ascii")


def _b64_decode(b64: str) -> str:
    """Декодирует Base64 в строку (UTF-8, с мягким паддингом)."""
    stripped = b64.strip().replace("\n", "").replace(" ", "")
    padded = stripped + "=" * ((4 - len(stripped) % 4) % 4)
    return _base64.b64decode(padded).decode("utf-8")


async def handle_b64(bot: "KraabUserbot", message: Message) -> None:
    """
    Команда !b64 — Base64 кодирование и декодирование.

    Форматы:
      !b64 encode <текст>   — закодировать в Base64
      !b64 decode <base64>  — декодировать из Base64
      !b64 (reply)          — автоопределение: если reply — валидный Base64, декодирует,
                              иначе кодирует
      !b64 <текст>          — кодирует произвольный текст (без явной подкоманды)
    """
    args = bot._get_command_args(message).strip()

    # --- явный режим encode ---
    if args.lower().startswith("encode "):
        payload = args[len("encode "):].strip()
        if not payload:
            raise UserInputError(user_message="❌ Укажи текст для кодирования: `!b64 encode <текст>`")
        result = _b64_encode(payload)
        await message.reply(f"🔐 **Base64 (encode):**\n`{result}`")
        return

    # --- явный режим decode ---
    if args.lower().startswith("decode "):
        payload = args[len("decode "):].strip()
        if not payload:
            raise UserInputError(user_message="❌ Укажи Base64 для декодирования: `!b64 decode <base64>`")
        try:
            result = _b64_decode(payload)
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(user_message=f"❌ Невалидный Base64: {exc}") from exc
        await message.reply(f"🔓 **Base64 (decode):**\n`{result}`")
        return

    # --- автоопределение по reply ---
    reply_text: str | None = None
    if message.reply_to_message and message.reply_to_message.text:
        reply_text = message.reply_to_message.text

    if not args and reply_text:
        if _b64_is_valid(reply_text):
            try:
                result = _b64_decode(reply_text)
                await message.reply(f"🔓 **Base64 (decode):**\n`{result}`")
            except Exception as exc:  # noqa: BLE001
                raise UserInputError(user_message=f"❌ Невалидный Base64: {exc}") from exc
        else:
            result = _b64_encode(reply_text)
            await message.reply(f"🔐 **Base64 (encode):**\n`{result}`")
        return

    # --- нет явной подкоманды, есть текст — кодируем ---
    if args:
        result = _b64_encode(args)
        await message.reply(f"🔐 **Base64 (encode):**\n`{result}`")
        return

    # --- справка ---
    raise UserInputError(
        user_message=(
            "🔐 **Base64 — справка**\n\n"
            "`!b64 encode <текст>` — закодировать\n"
            "`!b64 decode <base64>` — декодировать\n"
            "`!b64 <текст>` — закодировать (короткий вариант)\n"
            "`!b64` в reply — автоопределение по содержимому"
        )
    )


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


def _get_local_ip() -> str:
    """Определить локальный IP через UDP-сокет (без отправки пакетов)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:  # noqa: BLE001
        return "н/д"


async def _get_public_ip() -> str:
    """Получить публичный IP через api.ipify.org."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get("https://api.ipify.org?format=json")
        resp.raise_for_status()
        return resp.json()["ip"]


# ---------------------------------------------------------------------------
# handle_ip — публичный и локальный IP
# ---------------------------------------------------------------------------


async def handle_ip(bot: "KraabUserbot", message: Message) -> None:
    """
    Команда !ip — показать IP-адреса.

    !ip          — публичный + локальный
    !ip local    — только локальный (без HTTP-запроса)
    """
    args = bot._get_command_args(message).strip().lower()

    local_ip = _get_local_ip()

    if args == "local":
        # Только локальный IP
        text = (
            "🌐 **IP Info**\n"
            "─────\n"
            f"Local: `{local_ip}`"
        )
        await message.reply(text)
        return

    # Публичный + локальный
    try:
        public_ip = await _get_public_ip()
    except Exception as exc:  # noqa: BLE001
        raise UserInputError(user_message=f"❌ Не удалось получить публичный IP: {exc}") from exc

    text = (
        "🌐 **IP Info**\n"
        "─────\n"
        f"Public: `{public_ip}`\n"
        f"Local: `{local_ip}`"
    )
    await message.reply(text)


# ---------------------------------------------------------------------------
# handle_dns — DNS lookup (A, AAAA, MX записи)
# ---------------------------------------------------------------------------


async def handle_dns(bot: "KraabUserbot", message: Message) -> None:
    """
    Команда !dns <domain> — DNS lookup.

    Показывает A, AAAA и MX записи для домена.
    """
    domain = bot._get_command_args(message).strip()
    if not domain:
        raise UserInputError(user_message="❌ Укажи домен: `!dns example.com`")

    lines: list[str] = [f"🔍 **DNS: {domain}**", "─────"]

    # A записи (IPv4)
    try:
        a_results = await asyncio.get_event_loop().run_in_executor(
            None, lambda: socket.getaddrinfo(domain, None, socket.AF_INET)
        )
        a_addrs = sorted({r[4][0] for r in a_results})
        for addr in a_addrs:
            lines.append(f"A     `{addr}`")
    except socket.gaierror:
        lines.append("A     н/д")

    # AAAA записи (IPv6)
    try:
        aaaa_results = await asyncio.get_event_loop().run_in_executor(
            None, lambda: socket.getaddrinfo(domain, None, socket.AF_INET6)
        )
        aaaa_addrs = sorted({r[4][0] for r in aaaa_results})
        for addr in aaaa_addrs:
            lines.append(f"AAAA  `{addr}`")
    except socket.gaierror:
        pass  # IPv6 может отсутствовать — не показываем

    # MX записи через host (доступна на macOS/Linux)
    try:
        proc = await asyncio.create_subprocess_exec(
            "host", "-t", "MX", domain,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        mx_lines = stdout.decode(errors="replace").splitlines()
        for mx_line in mx_lines:
            if "mail is handled" in mx_line or "MX" in mx_line:
                parts = mx_line.split()
                if len(parts) >= 2:
                    mx_record = " ".join(parts[-2:]).rstrip(".")
                    lines.append(f"MX    `{mx_record}`")
    except Exception:  # noqa: BLE001
        pass  # MX необязательны

    await message.reply("\n".join(lines))


# ---------------------------------------------------------------------------
# handle_ping — ping одного хоста (1 пакет)
# ---------------------------------------------------------------------------


async def handle_ping(bot: "KraabUserbot", message: Message) -> None:
    """
    Команда !ping <host> — ping хоста (1 пакет, показать latency).

    Использует системный ping через subprocess.
    """
    host = bot._get_command_args(message).strip()
    if not host:
        raise UserInputError(user_message="❌ Укажи хост: `!ping example.com`")

    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", "3", host,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        output = stdout.decode(errors="replace")
    except asyncio.TimeoutError:
        raise UserInputError(user_message=f"❌ `{host}` — timeout (10 сек)") from None
    except Exception as exc:  # noqa: BLE001
        raise UserInputError(user_message=f"❌ Ошибка ping: {exc}") from exc

    # Парсим latency из строки вида: time=12.3 ms
    latency: str | None = None
    for line in output.splitlines():
        if "time=" in line:
            for part in line.split():
                if part.startswith("time="):
                    latency = part[len("time="):]
                    break
            if latency:
                break

    if proc.returncode == 0 and latency:
        text = (
            f"🏓 **Ping: {host}**\n"
            "─────\n"
            f"Latency: `{latency} ms`\n"
            "Status: ✅ доступен"
        )
    elif proc.returncode == 0:
        text = (
            f"🏓 **Ping: {host}**\n"
            "─────\n"
            "Status: ✅ доступен"
        )
    else:
        text = (
            f"🏓 **Ping: {host}**\n"
            "─────\n"
            "Status: ❌ недоступен"
        )

    await message.reply(text)


# ---------------------------------------------------------------------------
# !rand — генератор случайных значений
# ---------------------------------------------------------------------------

async def handle_rand(bot: "KraabUserbot", message: Message) -> None:
    """
    Генератор случайных значений.

    Синтаксис:
      !rand                   — число 1–100
      !rand N                 — число 1–N
      !rand N M               — число N–M
      !rand coin              — орёл/решка 🪙
      !rand dice              — кубик 1–6 🎲
      !rand pick a, b, c      — случайный выбор из списка
      !rand pass [N]          — пароль длиной N символов (default 16)
      !rand uuid              — UUID4
    """
    import random
    import secrets
    import string
    import uuid as _uuid_mod

    args = bot._get_command_args(message).strip()

    # --- без аргументов: 1–100 ---
    if not args:
        n = random.randint(1, 100)
        await message.reply(f"🎲 {n}")
        return

    parts = args.split(maxsplit=1)
    sub = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    # --- coin ---
    if sub == "coin":
        result = random.choice(["Орёл 🦅", "Решка 🪙"])
        await message.reply(result)
        return

    # --- dice ---
    if sub == "dice":
        n = random.randint(1, 6)
        await message.reply(f"🎲 {n}")
        return

    # --- pick ---
    if sub == "pick":
        if not rest:
            raise UserInputError(user_message="🎲 Формат: `!rand pick item1, item2, item3`")
        items = [item.strip() for item in rest.split(",") if item.strip()]
        if len(items) < 2:
            raise UserInputError(user_message="🎲 Нужно минимум 2 варианта, разделённых запятой.")
        chosen = random.choice(items)
        await message.reply(f"🎲 Выбрано: **{chosen}**")
        return

    # --- pass ---
    if sub == "pass":
        length = 16
        if rest:
            if not rest.isdigit():
                raise UserInputError(user_message="🎲 Формат: `!rand pass [длина]`")
            length = int(rest)
            if not (4 <= length <= 128):
                raise UserInputError(user_message="🎲 Длина пароля: от 4 до 128 символов.")
        # secrets для криптографической случайности
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        await message.reply(f"🔑 `{password}`")
        return

    # --- uuid ---
    if sub == "uuid":
        uid = str(_uuid_mod.uuid4())
        await message.reply(f"`{uid}`")
        return

    # --- !rand N или !rand N M ---
    # Попытка распарсить числа
    try:
        first = int(sub)
    except ValueError:
        raise UserInputError(
            user_message=(
                "🎲 **!rand** — генератор случайных значений\n"
                "`!rand` — число 1–100\n"
                "`!rand N` — число 1–N\n"
                "`!rand N M` — число N–M\n"
                "`!rand coin` — орёл/решка\n"
                "`!rand dice` — кубик 1–6\n"
                "`!rand pick a, b, c` — выбор из списка\n"
                "`!rand pass [N]` — пароль (default 16 символов)\n"
                "`!rand uuid` — UUID4"
            )
        )

    if not rest:
        # !rand N → 1..N
        if first < 1:
            raise UserInputError(user_message="🎲 N должен быть ≥ 1.")
        n = random.randint(1, first)
        await message.reply(f"🎲 {n}")
        return

    # !rand N M → N..M
    try:
        second = int(rest.split()[0])
    except ValueError:
        raise UserInputError(user_message="🎲 Формат: `!rand N M` — оба аргумента должны быть целыми числами.")
    lo, hi = min(first, second), max(first, second)
    n = random.randint(lo, hi)
    await message.reply(f"🎲 {n}")


# ---------------------------------------------------------------------------
# !quote — случайные и сохранённые цитаты
# ---------------------------------------------------------------------------

# Встроенный набор мотивационных цитат (~50 шт.) на русском и английском
_BUILTIN_QUOTES: list[str] = [
    "Успех — это не конечная точка, провал — не смертельный исход. Важна лишь смелость продолжать. — Уинстон Черчилль",
    "В середине каждой трудности лежит возможность. — Альберт Эйнштейн",
    "Жизнь — это то, что происходит с тобой, пока ты строишь другие планы. — Джон Леннон",
    "Будь собой — все остальные роли уже заняты. — Оскар Уайльд",
    "Единственный способ делать великую работу — любить то, что ты делаешь. — Стив Джобс",
    "Не важно, как медленно ты идёшь, главное — не останавливаться. — Конфуций",
    "Верь, что можешь, — и ты уже на полпути. — Теодор Рузвельт",
    "Жизнь прожита не зря, если ты зажёг хотя бы одну свечу во тьме. — Ромен Роллан",
    "Сначала они тебя игнорируют, потом смеются над тобой, потом борются с тобой. Потом ты побеждаешь. — Махатма Ганди",
    "Человек рождён для счастья, как птица для полёта. — Владимир Короленко",
    "Всё, что нас не убивает, делает нас сильнее. — Фридрих Ницше",
    "Чтобы дойти до цели, надо прежде всего идти. — Оноре де Бальзак",
    "Мечтай, как будто ты будешь жить вечно. Живи, как будто ты умрёшь сегодня. — Джеймс Дин",
    "Не бойся медленно продвигаться вперёд. Бойся стоять на месте. — Китайская пословица",
    "Смелость — это не отсутствие страха, а решимость победить его. — Нельсон Мандела",
    "Каждый день — это новая возможность изменить свою жизнь. — Сэр Пол Маккартни",
    "Лучшее время, чтобы посадить дерево, было 20 лет назад. Второе лучшее время — сейчас. — Китайская пословица",
    "Ты не можешь вернуться назад и изменить начало, но ты можешь начать сейчас и изменить конец. — К.С. Льюис",
    "Величайшая слава в жизни — не в том, чтобы никогда не падать, а в том, чтобы каждый раз подниматься. — Нельсон Мандела",
    "Стремись не к тому, чтобы добиться успеха, а к тому, чтобы твоя жизнь имела смысл. — Альберт Эйнштейн",
    "Делай, что можешь, тем, что имеешь, там, где ты есть. — Теодор Рузвельт",
    "Счастье — это когда то, что ты думаешь, то, что ты говоришь, и то, что ты делаешь, находятся в гармонии. — Махатма Ганди",
    "Измени своё мышление, и ты изменишь свой мир. — Норман Пил",
    "Опыт — это то, что ты получаешь, когда не получаешь того, чего хотел. — Рэнди Пауш",
    "Проблема людей в том, что они слишком долго думают, прежде чем начать. — Конфуций",
    "Начни где стоишь. Используй что имеешь. Делай что можешь. — Артур Эш",
    "It does not matter how slowly you go as long as you do not stop. — Confucius",
    "In the middle of difficulty lies opportunity. — Albert Einstein",
    "The only way to do great work is to love what you do. — Steve Jobs",
    "Success is not final, failure is not fatal: It is the courage to continue that counts. — Winston Churchill",
    "Believe you can and you\'re halfway there. — Theodore Roosevelt",
    "The future belongs to those who believe in the beauty of their dreams. — Eleanor Roosevelt",
    "It always seems impossible until it\'s done. — Nelson Mandela",
    "You are never too old to set another goal or to dream a new dream. — C.S. Lewis",
    "The only limit to our realization of tomorrow will be our doubts of today. — Franklin D. Roosevelt",
    "Act as if what you do makes a difference. It does. — William James",
    "Hardships often prepare ordinary people for an extraordinary destiny. — C.S. Lewis",
    "Keep your eyes on the stars and your feet on the ground. — Theodore Roosevelt",
    "Life is what happens when you\'re busy making other plans. — John Lennon",
    "Happiness is when what you think, what you say, and what you do are in harmony. — Mahatma Gandhi",
    "Be the change you wish to see in the world. — Mahatma Gandhi",
    "The best time to plant a tree was 20 years ago. The second best time is now. — Chinese Proverb",
    "Dream as if you\'ll live forever. Live as if you\'ll die today. — James Dean",
    "Don\'t watch the clock; do what it does. Keep going. — Sam Levenson",
    "You miss 100% of the shots you don\'t take. — Wayne Gretzky",
    "The secret of getting ahead is getting started. — Mark Twain",
    "Whether you think you can or you think you can\'t, you\'re right. — Henry Ford",
    "Twenty years from now you will be more disappointed by the things you didn\'t do. — Mark Twain",
    "The way to get started is to quit talking and begin doing. — Walt Disney",
    "Innovation distinguishes between a leader and a follower. — Steve Jobs",
]

# Путь к файлу с пользовательскими цитатами
_SAVED_QUOTES_PATH = (
    pathlib.Path.home() / ".openclaw" / "krab_runtime_state" / "saved_quotes.json"
)


def _load_saved_quotes() -> list[dict]:
    """Загружает сохранённые цитаты из JSON-файла."""
    if not _SAVED_QUOTES_PATH.exists():
        return []
    try:
        data = json.loads(_SAVED_QUOTES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_quotes(quotes: list[dict]) -> None:
    """Сохраняет список цитат в JSON-файл."""
    _SAVED_QUOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SAVED_QUOTES_PATH.write_text(
        json.dumps(quotes, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def handle_quote(bot: "KraabUserbot", message: Message) -> None:
    """
    Команда цитат.

    Синтаксис:
      !quote              — случайная встроенная мотивационная цитата
      !quote save         — сохранить цитируемое сообщение (reply)
      !quote my           — случайная из сохранённых
      !quote list         — список всех сохранённых цитат
    """
    import random

    args = bot._get_command_args(message).strip().lower()

    # --- !quote save ---
    if args == "save":
        reply = message.reply_to_message
        if not reply:
            await message.reply("💬 Ответь на сообщение, которое хочешь сохранить как цитату.")
            return
        text = (reply.text or reply.caption or "").strip()
        if not text:
            await message.reply("💬 Сообщение не содержит текста.")
            return
        # Автор: имя пользователя или «неизвестно»
        sender = reply.from_user
        if sender:
            author = sender.first_name or ""
            if sender.last_name:
                author = f"{author} {sender.last_name}".strip()
            if not author and sender.username:
                author = f"@{sender.username}"
        else:
            author = "Неизвестно"
        saved = _load_saved_quotes()
        entry = {
            "text": text,
            "author": author,
            "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        saved.append(entry)
        _save_quotes(saved)
        await message.reply(f"✅ Цитата сохранена (#{len(saved)}):\n\n_{text}_\n— **{author}**")
        return

    # --- !quote my ---
    if args == "my":
        saved = _load_saved_quotes()
        if not saved:
            await message.reply(
                "📭 У тебя пока нет сохранённых цитат. Используй `!quote save` в reply."
            )
            return
        entry = random.choice(saved)
        text = entry.get("text", "")
        author = entry.get("author", "Неизвестно")
        await message.reply(f"💬 _{text}_\n— **{author}**")
        return

    # --- !quote list ---
    if args == "list":
        saved = _load_saved_quotes()
        if not saved:
            await message.reply("📭 Нет сохранённых цитат. Используй `!quote save` в reply.")
            return
        lines = []
        for i, entry in enumerate(saved, 1):
            text = entry.get("text", "")
            author = entry.get("author", "?")
            # Обрезаем длинный текст для списка
            preview = text[:80] + "…" if len(text) > 80 else text
            lines.append(f"{i}. _{preview}_ — **{author}**")
        reply_text = "📚 **Сохранённые цитаты:**\n\n" + "\n".join(lines)
        await message.reply(reply_text)
        return

    # --- неизвестная подкоманда — справка ---
    if args and args not in ("save", "my", "list"):
        await message.reply(
            "💬 **!quote** — цитаты\n\n"
            "`!quote` — случайная мотивационная цитата\n"
            "`!quote save` — сохранить цитируемое сообщение (reply)\n"
            "`!quote my` — случайная из сохранённых\n"
            "`!quote list` — все сохранённые цитаты"
        )
        return

    # --- !quote (без аргументов) — случайная встроенная цитата ---
    quote = random.choice(_BUILTIN_QUOTES)
    await message.reply(f"💬 _{quote}_")


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


async def handle_len(bot: "KraabUserbot", message: Message) -> None:
    """
    !len <текст> / !count <текст> — подсчёт символов, слов и строк.

    Варианты:
      !len <текст>   — статистика переданного текста
      !len (reply)   — статистика текста ответного сообщения
      !count <текст> — алиас для !len
    """
    raw_args = bot._get_command_args(message).strip()

    # Если аргументов нет — берём из reply
    if not raw_args and message.reply_to_message:
        replied = message.reply_to_message
        raw_args = (replied.text or replied.caption or "").strip()

    if not raw_args:
        raise UserInputError(
            user_message=(
                "📏 **!len — статистика текста**\n\n"
                "`!len <текст>` — символы, слова, строки\n"
                "`!count <текст>` — то же самое\n"
                "_Или ответь командой на любое сообщение._"
            )
        )

    # Подсчёт статистики
    chars = len(raw_args)
    words = len(raw_args.split())
    lines = len(raw_args.splitlines()) or 1  # минимум 1 строка

    # Склонение существительных
    def _plural_chars(n: int) -> str:
        if 11 <= n % 100 <= 19:
            return "символов"
        r = n % 10
        if r == 1:
            return "символ"
        if 2 <= r <= 4:
            return "символа"
        return "символов"

    def _plural_words(n: int) -> str:
        if 11 <= n % 100 <= 19:
            return "слов"
        r = n % 10
        if r == 1:
            return "слово"
        if 2 <= r <= 4:
            return "слова"
        return "слов"

    def _plural_lines(n: int) -> str:
        if 11 <= n % 100 <= 19:
            return "строк"
        r = n % 10
        if r == 1:
            return "строка"
        if 2 <= r <= 4:
            return "строки"
        return "строк"

    result = (
        f"📏 Текст: {chars} {_plural_chars(chars)}, "
        f"{words} {_plural_words(words)}, "
        f"{lines} {_plural_lines(lines)}"
    )
    await message.reply(result)


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
        template
        .replace("{name}", name)
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


# ---------------------------------------------------------------------------
# !sed — IRC-style regex замена
# ---------------------------------------------------------------------------

# Допустимые флаги в конце выражения s/old/new/flags
_SED_VALID_FLAGS = frozenset("gi")


def _parse_sed_expr(expr: str) -> tuple[str, str, int]:
    """Парсит выражение вида s/old/new/[flags].

    Возвращает (pattern, replacement, re_flags).
    Поддерживает альтернативный разделитель (любой символ после 's').
    Бросает ValueError при некорректном формате.
    """
    if len(expr) < 2 or expr[0] != "s":
        raise ValueError("Выражение должно начинаться с 's'")

    sep = expr[1]  # разделитель — обычно /
    parts = expr[2:].split(sep)

    if len(parts) < 2:
        raise ValueError("Недостаточно частей в s-выражении")

    pattern = parts[0]
    replacement = parts[1]
    raw_flags = parts[2] if len(parts) > 2 else ""

    # Проверяем флаги
    unknown = set(raw_flags) - _SED_VALID_FLAGS
    if unknown:
        raise ValueError(f"Неизвестные флаги: {''.join(sorted(unknown))}")

    re_flags = 0
    count = 1  # по умолчанию — первое совпадение

    if "i" in raw_flags:
        re_flags |= re.IGNORECASE
    if "g" in raw_flags:
        count = 0  # 0 = все совпадения

    # Компилируем паттерн — чтобы поймать ошибку до применения
    try:
        compiled = re.compile(pattern, re_flags)
    except re.error as exc:
        raise ValueError(f"Ошибка в regex: {exc}") from exc

    return compiled, replacement, count


async def handle_sed(bot: "KraabUserbot", message: Message) -> None:
    """Обработчик !sed s/old/new/[flags] — IRC-style regex замена.

    В reply на сообщение:
    - Своё сообщение → редактирует через edit_text
    - Чужое сообщение → отвечает «✏️ Исправление: <текст>»
    """
    # message.command содержит список частей после команды: ['sed', 's/old/new/g']
    parts = message.command
    if not parts or len(parts) < 2:
        await message.reply(
            "✏️ **!sed — IRC-style замена**\n\n"
            "Использование: `!sed s/old/new/[флаги]` в reply на сообщение\n\n"
            "Флаги:\n"
            "`g` — заменить все совпадения (глобально)\n"
            "`i` — без учёта регистра\n\n"
            "Примеры:\n"
            "`!sed s/опечатка/слово/` — первое вхождение\n"
            "`!sed s/foo/bar/g` — все вхождения\n"
            "`!sed s/Hello/Привет/i` — без учёта регистра"
        )
        return

    expr = parts[1]

    try:
        compiled, replacement, count = _parse_sed_expr(expr)
    except ValueError as exc:
        raise UserInputError(user_message=f"❌ {exc}") from exc

    # Определяем целевое сообщение: reply или само сообщение
    target = message.reply_to_message
    if target is None:
        raise UserInputError(user_message="❌ Используй `!sed` в reply на сообщение")

    # Исходный текст целевого сообщения
    original_text = target.text or target.caption or ""
    if not original_text:
        raise UserInputError(user_message="❌ Целевое сообщение не содержит текста")

    # Применяем замену
    new_text, n_subs = compiled.subn(replacement, original_text, count=count)

    if n_subs == 0:
        await message.reply("⚠️ Паттерн не найден в тексте")
        return

    # Определяем, является ли целевое сообщение нашим
    me = await bot.client.get_me()
    is_own = target.from_user and target.from_user.id == me.id

    if is_own:
        # Редактируем своё сообщение
        try:
            await bot.client.edit_message_text(
                chat_id=target.chat.id,
                message_id=target.id,
                text=new_text,
            )
            # Удаляем команду-триггер, чтобы не засорять чат
            try:
                await message.delete()
            except Exception:
                pass
        except Exception as exc:
            await message.reply(f"❌ Не удалось отредактировать: {exc}")
    else:
        # Чужое сообщение — отправляем исправление как reply
        await message.reply(f"✏️ Исправление:\n{new_text}")


# ---------------------------------------------------------------------------
# Утилита для сравнения текстов (!diff)
# ---------------------------------------------------------------------------


def _build_diff_output(old_text: str, new_text: str) -> str:
    """
    Сравнивает two texts и возвращает unified diff в Telegram-формате.

    Строки старого текста помечаются «- », нового — «+ », общие — «  ».
    Заголовки @@ из unified_diff опускаются.
    """
    import difflib

    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    # lineterm="" убирает \n из строк difflib — управляем переносами сами
    diff_lines: list[str] = []
    for line in difflib.unified_diff(old_lines, new_lines, lineterm=""):
        if line.startswith("---") or line.startswith("+++"):
            # Заголовки файлов не нужны
            continue
        if line.startswith("@@"):
            # Разделитель блоков — вставляем пустую строку для читаемости
            if diff_lines:
                diff_lines.append("")
            continue
        # Убираем завершающий \n если он есть (splitlines убрал, но diff мог добавить)
        diff_lines.append(line.rstrip("\n"))

    return "\n".join(diff_lines)


async def handle_diff(bot: "KraabUserbot", message: Message) -> None:
    """
    !diff — сравнение двух текстов в unified-формате (как git diff).

    Использование:
      В reply на сообщение + текст в команде:
        !diff <новый текст>  — сравнивает текст reply (старый) с аргументом (новый)

    Вывод:
      - строка   → только в старом
      + строка   → только в новом
        строка   → общая
    """
    args = bot._get_command_args(message).strip()

    # Определяем тексты для сравнения
    reply = message.reply_to_message
    reply_text: str | None = None
    if reply:
        reply_text = (reply.text or reply.caption or "").strip() or None

    if reply_text is None:
        raise UserInputError(
            user_message=(
                "📊 **!diff — сравнение текстов**\n\n"
                "Используй в reply на сообщение:\n"
                "`!diff <новый текст>` — сравнивает текст reply с твоим текстом\n\n"
                "_Старый текст — сообщение, на которое отвечаешь._\n"
                "_Новый текст — аргумент команды._"
            )
        )

    if not args:
        raise UserInputError(
            user_message=(
                "❌ Укажи новый текст: `!diff <текст>`\n"
                "_Старый текст берётся из reply-сообщения._"
            )
        )

    old_text = reply_text
    new_text = args

    diff_body = _build_diff_output(old_text, new_text)

    if not diff_body.strip():
        await message.reply("✅ Тексты идентичны — различий нет.")
        return

    separator = "─" * 5
    header = f"📊 **Diff**\n{separator}"
    full_output = f"{header}\n```\n{diff_body}\n```"

    # Telegram ограничивает длину — режем если нужно
    if len(full_output) > 3900:
        diff_body_trimmed = diff_body[: 3800 - len(header)]
        full_output = f"{header}\n```\n{diff_body_trimmed}\n…(обрезано)```"

    await message.reply(full_output)


# ---------------------------------------------------------------------------
# Управление стикерами (!sticker)
# ---------------------------------------------------------------------------

_STICKERS_FILE = pathlib.Path.home() / ".openclaw" / "krab_runtime_state" / "saved_stickers.json"


def _load_stickers() -> dict[str, str]:
    """Загружает словарь {name: file_id} из JSON-файла."""
    try:
        if _STICKERS_FILE.exists():
            return json.loads(_STICKERS_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_stickers(data: dict[str, str]) -> None:
    """Сохраняет словарь {name: file_id} в JSON-файл."""
    _STICKERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STICKERS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def handle_sticker(bot: "KraabUserbot", message: Message) -> None:
    """
    !sticker save <name> — сохранить стикер (в ответ на стикер)
    !sticker <name>      — отправить сохранённый стикер
    !sticker list        — показать список сохранённых стикеров
    !sticker del <name>  — удалить стикер из коллекции
    """
    raw_args = bot._get_command_args(message).strip()
    parts = raw_args.split(None, 1)

    # --- !sticker list ---
    if not parts or parts[0].lower() == "list":
        stickers = _load_stickers()
        if not stickers:
            await message.reply("📭 Нет сохранённых стикеров. Используй `!sticker save <name>` в ответ на стикер.")
            return
        lines = [f"• `{name}`" for name in sorted(stickers)]
        await message.reply("🗂 **Сохранённые стикеры:**\n" + "\n".join(lines))
        return

    subcommand = parts[0].lower()

    # --- !sticker save <name> ---
    if subcommand == "save":
        if len(parts) < 2 or not parts[1].strip():
            raise UserInputError(user_message="❌ Укажи имя: `!sticker save <name>`")
        name = parts[1].strip().lower()

        # Ищем стикер в replied сообщении
        replied = message.reply_to_message
        if replied is None or replied.sticker is None:
            raise UserInputError(user_message="❌ Ответь на стикер командой `!sticker save <name>`")

        file_id = replied.sticker.file_id
        stickers = _load_stickers()
        stickers[name] = file_id
        _save_stickers(stickers)
        await message.reply(f"✅ Стикер `{name}` сохранён!")
        return

    # --- !sticker del <name> ---
    if subcommand == "del":
        if len(parts) < 2 or not parts[1].strip():
            raise UserInputError(user_message="❌ Укажи имя: `!sticker del <name>`")
        name = parts[1].strip().lower()
        stickers = _load_stickers()
        if name not in stickers:
            raise UserInputError(user_message=f"❌ Стикер `{name}` не найден.")
        del stickers[name]
        _save_stickers(stickers)
        await message.reply(f"🗑 Стикер `{name}` удалён.")
        return

    # --- !sticker <name> — отправить стикер ---
    name = parts[0].lower()
    stickers = _load_stickers()
    if name not in stickers:
        raise UserInputError(
            user_message=f"❌ Стикер `{name}` не найден. Список: `!sticker list`"
        )
    file_id = stickers[name]
    await bot.client.send_sticker(message.chat.id, file_id)
    # Удаляем исходную команду, чтобы не засорять чат
    try:
        await message.delete()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# !tts — текст в голосовое сообщение (macOS say)
# ---------------------------------------------------------------------------

# Поддерживаемые языки и voice macOS say
_TTS_VOICES: dict[str, str] = {
    "ru": "Milena",
    "en": "Samantha",
    "es": "Monica",
}

# Алиасы кодов языков
_TTS_LANG_ALIASES: dict[str, str] = {
    "ru": "ru",
    "en": "en",
    "es": "es",
    "russian": "ru",
    "english": "en",
    "spanish": "es",
}


async def handle_tts(bot: "KraabUserbot", message: Message) -> None:
    """
    !tts [lang] <текст> — голосовое сообщение через macOS say + ffmpeg.

    Варианты вызова:
      !tts Привет, мир          — русский (Milena, по умолчанию)
      !tts en Hello world       — английский (Samantha)
      !tts es Hola mundo        — испанский (Monica)
      !tts (reply на сообщение) — озвучивает текст ответного сообщения

    Пайплайн: say -v <Voice> -o speech.aiff <text> → ffmpeg → OGG/Opus → send_voice
    """
    import tempfile

    from ..core.subprocess_env import clean_subprocess_env

    raw = bot._get_command_args(message).strip()

    # Определяем язык и текст
    lang = "ru"
    text = raw

    if raw:
        parts = raw.split(None, 1)
        first = parts[0].lower()
        if first in _TTS_LANG_ALIASES:
            lang = _TTS_LANG_ALIASES[first]
            text = parts[1].strip() if len(parts) > 1 else ""

    # Если текста нет — проверяем reply на сообщение
    if not text:
        replied = getattr(message, "reply_to_message", None)
        if replied is not None:
            replied_text = (
                getattr(replied, "text", None) or getattr(replied, "caption", None) or ""
            )
            text = replied_text.strip()

    if not text:
        raise UserInputError(
            user_message=(
                "🎙️ **TTS — текст в голос (macOS say)**\n\n"
                "Использование:\n"
                "`!tts <текст>` — русский (Milena)\n"
                "`!tts en <текст>` — английский (Samantha)\n"
                "`!tts es <текст>` — испанский (Monica)\n"
                "`!tts` в ответ на сообщение — озвучить его\n\n"
                "_Поддерживаемые языки: ru, en, es_"
            )
        )

    voice_name = _TTS_VOICES.get(lang, _TTS_VOICES["ru"])

    # Временные файлы: say генерирует AIFF, ffmpeg конвертирует в OGG/Opus
    with tempfile.TemporaryDirectory(prefix="krab_tts_") as tmpdir:
        aiff_path = os.path.join(tmpdir, "speech.aiff")
        ogg_path = os.path.join(tmpdir, "speech.ogg")

        try:
            # Шаг 1: macOS say → AIFF
            say_proc = await asyncio.create_subprocess_exec(
                "/usr/bin/say",
                "-v", voice_name,
                "-o", aiff_path,
                text,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=clean_subprocess_env(),
            )
            await say_proc.wait()
            if say_proc.returncode != 0:
                logger.error("tts_say_failed", voice=voice_name, returncode=say_proc.returncode)
                raise UserInputError(
                    user_message=f"❌ macOS say завершился с ошибкой (код {say_proc.returncode})."
                )

            # Шаг 2: AIFF → OGG/Opus (Telegram voice message)
            ffmpeg_proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-i", aiff_path,
                "-c:a", "libopus",
                "-b:a", "32k",
                "-vbr", "on",
                "-compression_level", "10",
                ogg_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=clean_subprocess_env(),
            )
            await ffmpeg_proc.wait()

            if not os.path.exists(ogg_path) or os.path.getsize(ogg_path) == 0:
                logger.error("tts_ffmpeg_failed", aiff=aiff_path, ogg=ogg_path)
                raise UserInputError(user_message="❌ ffmpeg не смог конвертировать аудио.")

            # Шаг 3: отправляем голосовое сообщение
            logger.info(
                "tts_sending_voice",
                lang=lang,
                voice=voice_name,
                text_len=len(text),
                ogg_size=os.path.getsize(ogg_path),
            )
            await bot.client.send_voice(message.chat.id, ogg_path)

        except UserInputError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("tts_error", error=str(exc), error_type=type(exc).__name__)
            raise UserInputError(
                user_message=f"❌ TTS ошибка: {str(exc)[:200]}"
            ) from exc


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
                "🖼 Это сообщение не содержит фото. "
                "Ответь командой на сообщение с фотографией."
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
                "📄 Это сообщение не содержит фото. "
                "Ответь командой на сообщение с изображением."
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
                    f"❌ Неизвестное действие: `{action}`.\n"
                    f"Доступны: `ban`, `mute`, `delete`"
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
_EVAL_FORBIDDEN_NAMES = frozenset({
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
})

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


# ---------------------------------------------------------------------------
# !json — форматирование и валидация JSON
# ---------------------------------------------------------------------------


def _json_extract_text(message: Message, args: str) -> str | None:
    """Извлекает текст: сначала из args, затем из reply."""
    if args:
        return args
    if message.reply_to_message:
        return message.reply_to_message.text or message.reply_to_message.caption or None
    return None


async def handle_json(bot: "KraabUserbot", message: Message) -> None:
    """
    !json <текст>            — форматирует (pretty print) JSON с отступами 2
    !json (reply)            — форматирует текст ответного сообщения
    !json validate <текст>   — проверяет валидность, показывает ошибку с позицией
    !json minify <текст>     — минифицирует JSON (убирает пробелы)
    """
    raw_args = bot._get_command_args(message).strip()

    # Определяем подкоманду
    sub: str | None = None
    payload: str = raw_args

    lower = raw_args.lower()
    if lower.startswith("validate ") or lower == "validate":
        sub = "validate"
        payload = raw_args[len("validate"):].strip()
    elif lower.startswith("minify ") or lower == "minify":
        sub = "minify"
        payload = raw_args[len("minify"):].strip()

    # Если payload пуст — пробуем взять из reply
    if not payload:
        payload = _json_extract_text(message, "") or ""

    if not payload:
        raise UserInputError(
            user_message=(
                "🔧 **JSON-утилита**\n\n"
                "`!json <текст>` — форматировать (pretty print)\n"
                "`!json` в reply — форматировать текст ответа\n"
                "`!json validate <текст>` — проверить валидность\n"
                "`!json minify <текст>` — минифицировать"
            )
        )

    # --- validate ---
    if sub == "validate":
        try:
            json.loads(payload)
            await message.reply("✅ JSON валиден.")
        except json.JSONDecodeError as exc:
            await message.reply(
                f"❌ JSON невалиден: {exc.msg}: line {exc.lineno} column {exc.colno}"
            )
        return

    # --- minify ---
    if sub == "minify":
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise UserInputError(
                user_message=f"❌ JSON невалиден: {exc.msg}: line {exc.lineno} column {exc.colno}"
            ) from exc
        minified = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
        await message.reply(f"```json\n{minified}\n```")
        return

    # --- format (default) ---
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise UserInputError(
            user_message=f"❌ JSON невалиден: {exc.msg}: line {exc.lineno} column {exc.colno}"
        ) from exc
    pretty = json.dumps(parsed, ensure_ascii=False, indent=2)
    await message.reply(f"```json\n{pretty}\n```")


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
        raise UserInputError(
            user_message=f"❌ Сниппет `{name}` не найден. Список: `!snippet list`"
        )
    code = snippets[name].get("code", "")
    created = snippets[name].get("created_at", "")
    header = f"📄 **{name}**" + (f" _(сохранён {created[:10]})_" if created else "")
    await message.reply(f"{header}\n```\n{code}\n```")


# ---------------------------------------------------------------------------
# !tag — теги на сообщения
# ---------------------------------------------------------------------------

_TAGS_FILE = pathlib.Path.home() / ".openclaw" / "krab_runtime_state" / "message_tags.json"


def _load_tags() -> dict[str, dict[str, list[str]]]:
    """Загружает теги из JSON. Формат: {chat_id: {message_id: [tags]}}."""
    try:
        if _TAGS_FILE.exists():
            return json.loads(_TAGS_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_tags(data: dict[str, dict[str, list[str]]]) -> None:
    """Сохраняет теги в JSON-файл."""
    _TAGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TAGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_msg_link(chat_id: int, message_id: int) -> str:
    """Формирует ссылку на сообщение Telegram."""
    if chat_id < 0:
        # Супергруппы/каналы: -100XXXXXXXXXX → t.me/c/XXXXXXXXXX/id
        numeric = str(chat_id).lstrip("-")
        if numeric.startswith("100"):
            numeric = numeric[3:]
        return f"https://t.me/c/{numeric}/{message_id}"
    return f"https://t.me/c/{chat_id}/{message_id}"


async def handle_tag(bot: "KraabUserbot", message: Message) -> None:
    """
    !tag <тег>              — в reply → добавляет тег к сообщению
    !tag list               — все теги (уникальные) с количеством
    !tag find <тег>         — сообщения с тегом (ссылки)
    !tag del <тег>          — удалить тег с сообщения (в reply)
    """
    raw_args = bot._get_command_args(message).strip()
    parts = raw_args.split(None, 1)

    chat_id = message.chat.id

    # --- !tag list ---
    if not parts or parts[0].lower() == "list":
        tags_data = _load_tags()
        chat_key = str(chat_id)
        chat_tags = tags_data.get(chat_key, {})
        # Собираем все теги с подсчётом
        counter: dict[str, int] = {}
        for tag_list in chat_tags.values():
            for t in tag_list:
                counter[t] = counter.get(t, 0) + 1
        if not counter:
            await message.reply("🏷 Тегов нет. Используй `!tag <тег>` в reply на сообщение.")
            return
        lines = [f"• `{t}` — {n} сообщ." for t, n in sorted(counter.items())]
        await message.reply("🏷 **Теги в этом чате:**\n" + "\n".join(lines))
        return

    subcommand = parts[0].lower()

    # --- !tag find <тег> ---
    if subcommand == "find":
        if len(parts) < 2 or not parts[1].strip():
            raise UserInputError(user_message="❌ Укажи тег: `!tag find <тег>`")
        needle = parts[1].strip().lower()
        tags_data = _load_tags()
        chat_key = str(chat_id)
        chat_tags = tags_data.get(chat_key, {})
        matches = [
            int(msg_id)
            for msg_id, tag_list in chat_tags.items()
            if needle in [t.lower() for t in tag_list]
        ]
        if not matches:
            await message.reply(f"🔍 Нет сообщений с тегом `{needle}`.")
            return
        links = [_make_msg_link(chat_id, mid) for mid in sorted(matches)]
        header = f"🔍 Сообщения с тегом `{needle}` ({len(links)}):"
        await message.reply(header + "\n" + "\n".join(links))
        return

    # --- !tag del <тег> ---
    if subcommand == "del":
        if len(parts) < 2 or not parts[1].strip():
            raise UserInputError(user_message="❌ Укажи тег: `!tag del <тег>` в reply на сообщение")
        tag = parts[1].strip()
        replied = message.reply_to_message
        if replied is None:
            raise UserInputError(user_message="❌ Ответь на сообщение командой `!tag del <тег>`")
        msg_id = str(replied.id)
        chat_key = str(chat_id)
        tags_data = _load_tags()
        tag_list = tags_data.get(chat_key, {}).get(msg_id, [])
        if tag not in tag_list:
            raise UserInputError(user_message=f"❌ Тег `{tag}` не найден на этом сообщении.")
        tag_list.remove(tag)
        if tag_list:
            tags_data[chat_key][msg_id] = tag_list
        else:
            del tags_data[chat_key][msg_id]
            if not tags_data[chat_key]:
                del tags_data[chat_key]
        _save_tags(tags_data)
        await message.reply(f"🗑 Тег `{tag}` удалён с сообщения.")
        return

    # --- !tag <тег> в reply — добавить тег ---
    tag = parts[0].strip()
    replied = message.reply_to_message
    if replied is None:
        raise UserInputError(user_message="❌ Ответь на сообщение командой `!tag <тег>`")
    msg_id = str(replied.id)
    chat_key = str(chat_id)
    tags_data = _load_tags()
    chat_tags = tags_data.setdefault(chat_key, {})
    tag_list = chat_tags.setdefault(msg_id, [])
    if tag in tag_list:
        await message.reply(f"ℹ️ Тег `{tag}` уже есть на этом сообщении.")
        return
    tag_list.append(tag)
    _save_tags(tags_data)
    await message.reply(f"🏷 Тег `{tag}` добавлен.")


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
    top_n = 10    # сколько участников показать
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
        "bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "buff.ly",
        "short.link", "rb.gy", "cutt.ly", "is.gd", "v.gd", "tiny.cc",
        "shorturl.at", "clck.ru", "vk.cc",
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
    title_match = re.search(
        r"<title[^>]*>([^<]{1,300})</title>", html, re.IGNORECASE | re.DOTALL
    )
    if title_match:
        result["title"] = re.sub(r"\s+", " ", title_match.group(1)).strip()

    # og:title перекрывает <title>
    og_title = re.search(
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']{1,300})["\']',
        html, re.IGNORECASE,
    )
    if og_title:
        result["title"] = og_title.group(1).strip()

    # og:description или meta description
    og_desc = re.search(
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']{1,500})["\']',
        html, re.IGNORECASE,
    )
    if not og_desc:
        og_desc = re.search(
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']{1,500})["\']',
            html, re.IGNORECASE,
        )
    if og_desc:
        result["description"] = og_desc.group(1).strip()

    # og:image
    og_img = re.search(
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']{1,500})["\']',
        html, re.IGNORECASE,
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


# ---------------------------------------------------------------------------
# !regex — тестирование регулярных выражений
# ---------------------------------------------------------------------------


def _format_regex_result(pattern_src: str, text: str) -> str:
    """Форматирует результат regex-матча в читаемый вид для Telegram."""
    import re as _re

    # Компилируем паттерн (ошибки обрабатываем снаружи)
    compiled = _re.compile(pattern_src)
    matches = list(compiled.finditer(text))

    if not matches:
        return f"🔍 Regex: `/{pattern_src}/`\n\n⚠️ Совпадений не найдено"

    lines: list[str] = [
        f"🔍 Regex: `/{pattern_src}/`",
        f"Matches: {len(matches)}",
    ]

    for i, m in enumerate(matches[:10], start=1):
        # Обрезаем длинные совпадения до 60 символов
        matched_text = m.group(0)
        if len(matched_text) > 60:
            matched_text = matched_text[:57] + "..."
        start, end = m.span()
        lines.append(f'{i}. `"{matched_text}"` ({start}:{end})')

        # Именованные группы приоритетнее позиционных
        if m.lastindex or m.groupdict():
            groups: list[str] = []
            named = m.groupdict()
            if named:
                for name, val in named.items():
                    groups.append(f'{name}="{val}"')
            else:
                for j, val in enumerate(m.groups(), start=1):
                    groups.append(f'group{j}="{val}"')
            if groups:
                lines.append(f"   Groups: {', '.join(groups)}")

    if len(matches) > 10:
        lines.append(f"   … и ещё {len(matches) - 10} совпадений")

    return "\n".join(lines)


async def handle_regex(bot: "KraabUserbot", message: Message) -> None:
    """
    Тестирует регулярное выражение прямо в Telegram.

    Синтаксис:
      !regex <паттерн> <текст>  — тест паттерна на тексте
      !regex <паттерн>          — тест паттерна на тексте reply-сообщения
      !regex (reply с паттерном) — паттерн из reply, текст reply как текст?
                                  нет — паттерн из команды, текст из reply

    Формат ответа:
      🔍 Regex: /паттерн/
      Matches: 3
      1. "match1" (0:5)
      2. "match2" (10:15)
         Groups: group1="...", group2="..."
    """
    import re

    raw_args = bot._get_command_args(message).strip()

    # --- Определяем паттерн и текст ---
    pattern_src: str = ""
    text: str = ""

    if raw_args:
        # Пробуем разбить: первый "токен" — паттерн, остальное — текст
        # Паттерн может содержать пробелы внутри /…/, но обычно без них
        # Формат: !regex <pattern> <text>  или  !regex <pattern> (+ reply)
        parts = raw_args.split(maxsplit=1)
        pattern_src = parts[0]
        if len(parts) > 1:
            text = parts[1].strip()

    # Если текст не задан — берём из reply
    if not text and message.reply_to_message:
        reply = message.reply_to_message
        text = (reply.text or reply.caption or "").strip()

    # Нет ни паттерна, ни текста → справка
    if not pattern_src:
        raise UserInputError(
            user_message=(
                "🔍 **!regex — тестирование регулярных выражений**\n\n"
                "Использование:\n"
                "`!regex <паттерн> <текст>` — тест на тексте\n"
                "`!regex <паттерн>` (reply) — паттерн, текст из reply\n\n"
                "Примеры:\n"
                r"`!regex \d+ abc123def456`" + " → 2 совпадения\n"
                r"`!regex (foo|bar) foo baz bar`" + " → 2 совпадения\n"
                r"`!regex (?P<name>\w+) Hello World`" + " → именованные группы"
            )
        )

    if not text:
        raise UserInputError(
            user_message=(
                "❌ Укажи текст для проверки или ответь командой на сообщение.\n"
                "Пример: `!regex \\d+ текст 123`"
            )
        )

    # Компилируем паттерн с проверкой ошибок
    try:
        re.compile(pattern_src)
    except re.error as exc:
        raise UserInputError(
            user_message=f"❌ Невалидный regex: `{exc}`"
        ) from exc

    # Формируем и отправляем результат
    result = _format_regex_result(pattern_src, text)
    await message.reply(result)


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
        replied_text = (
            message.reply_to_message.text
            or message.reply_to_message.caption
            or ""
        )
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

_TEMPLATES_FILE = pathlib.Path.home() / ".openclaw" / "krab_runtime_state" / "message_templates.json"


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
            f" Переменные: {', '.join(f'`{{{v}}}`' for v in vars_found)}"
            if vars_found
            else ""
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
        raise UserInputError(
            user_message=f"❌ Шаблон `{name}` не найден. Список: `!template list`"
        )
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
        rest = args[len("convert "):].strip()
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
            f"🕐 **{display_name}** ({tz_name})\n"
            f"`{_time_format_dt(dt)}` {offset_fmt}"
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
        raise UserInputError(
            user_message=f"❌ Ошибка установки slowmode: {exc}"
        ) from exc

    label = _SLOWMODE_LABELS.get(seconds, f"{seconds} сек")
    if seconds == 0:
        await message.reply(f"✅ Slowmode **выключен** в `{chat.title or chat.id}`.")
    else:
        await message.reply(
            f"🐢 Slowmode установлен: **{label}** в `{chat.title or chat.id}`."
        )


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
                _raw.functions.account.UpdateNotifySettings(
                    peer=notify_peer, settings=settings
                )
            )
            await message.reply(
                "🔕 Уведомления в этом чате **отключены**.\n"
                "`!chatmute on` — включить обратно."
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
                _raw.functions.account.UpdateNotifySettings(
                    peer=notify_peer, settings=settings
                )
            )
            await message.reply("🔔 Уведомления в этом чате **включены**.")
        except Exception as exc:
            raise UserInputError(
                user_message=f"❌ Не удалось включить уведомления: {exc}"
            ) from exc

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
            raise UserInputError(
                user_message="🔍 Укажи запрос: `!contacts search <имя или номер>`"
            )
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
                raise UserInputError(
                    user_message="❌ Укажи ссылку: `!invite link revoke <url>`"
                )
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
            raise UserInputError(
                user_message=f"❌ Не удалось создать ссылку: `{exc}`"
            ) from exc
        return

    # ── add user ───────────────────────────────────────────────
    # Первый аргумент — @username или числовой user_id
    target = args_raw[0]
    try:
        await bot.client.add_chat_members(chat_id, target)
        await message.reply(f"✅ Пользователь `{target}` добавлен в чат.")
    except Exception as exc:
        raise UserInputError(
            user_message=f"❌ Не удалось добавить `{target}`: `{exc}`"
        ) from exc


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
            raise UserInputError(
                user_message="❌ Укажи текст bio: `!profile bio <текст>`"
            )
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
            raise UserInputError(
                user_message="❌ Укажи имя: `!profile name <first> [last]`"
            )
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
            raise UserInputError(
                user_message="❌ Укажи username: `!profile username <username>`"
            )
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
        raise UserInputError(
            user_message="❌ Команда `!members` работает только в группах."
        )

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
        await message.reply(
            f"👥 Участников в `{chat.title or chat.id}`: **{count}**"
        )
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
                raise UserInputError(
                    user_message="❌ Укажи число участников: `!members list 20`"
                )

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
            raise UserInputError(
                user_message=f"❌ Не удалось кикнуть участника: {exc}"
            ) from exc
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
            raise UserInputError(
                user_message=f"❌ Не удалось забанить участника: {exc}"
            ) from exc
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
        await message.reply(
            f"✅ Пользователь `{target_str}` разбанен в `{chat.title or chat.id}`."
        )
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
_KRAB_LOG_PATH = pathlib.Path.home() / ".openclaw" / "krab_runtime_state" / "krab_main.log"

# Максимальный размер файла для чтения целиком (5 MB)
_LOG_MAX_INLINE_SIZE = 5 * 1024 * 1024

# Лимит строк для вывода в Telegram (без document)
_LOG_TEXT_MAX_LINES = 200


async def handle_log(bot: "KraabUserbot", message: Message) -> None:
    """Просмотр логов Краба из Telegram.

    Форматы:
      !log [N]              — последние N строк лога (default 20)
      !log errors           — только ошибки (строки с ERROR/error/CRITICAL/WARNING)
      !log search <запрос>  — поиск по логам (grep-like)

    Длинный вывод отправляется как документ (.txt файл).
    """
    args = bot._get_command_args(message).strip()

    # Определяем переменную окружения или дефолтный путь к логу
    raw_env = os.environ.get("KRAB_LOG_FILE")
    if raw_env and raw_env.lower() != "none":
        log_path = pathlib.Path(raw_env).expanduser()
    else:
        base_env = os.environ.get("KRAB_RUNTIME_STATE_DIR")
        if base_env:
            log_path = pathlib.Path(base_env).expanduser() / "krab_main.log"
        else:
            log_path = _KRAB_LOG_PATH

    # Определяем цель: в группе — редирект в ЛС
    _log_target = "me" if message.chat.id < 0 else None
    if message.chat.id < 0:
        try:
            await message.reply("📬 Ответ в ЛС (тех-команда).")
        except Exception:  # noqa: BLE001
            pass

    async def _send_log_text(text: str) -> None:
        """Отправляет текст лога в правильный чат."""
        if _log_target:
            try:
                await bot.client.send_message(_log_target, text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("tech_dm_redirect_failed", error=str(exc))
        else:
            await message.reply(text)

    async def _send_log_document(filepath: pathlib.Path, caption: str) -> None:
        """Отправляет документ лога в правильный чат."""
        target = _log_target or message.chat.id
        await bot.client.send_document(target, str(filepath), caption=caption)

    # Лог-файл должен существовать
    if not log_path.exists():
        await _send_log_text(
            f"📋 Лог-файл не найден: `{log_path}`\n"
            "Убедись, что Краб запущен и лог активен."
        )
        return

    # --- Разбор подкоманды ---
    mode = "tail"
    n_lines = 20
    query: str | None = None

    if args:
        lower = args.lower()
        if lower == "errors":
            mode = "errors"
        elif lower.startswith("search "):
            mode = "search"
            query = args[7:].strip()
            if not query:
                raise UserInputError(
                    user_message="🔍 Укажи запрос: `!log search <текст>`"
                )
        else:
            # Пробуем распарсить как число
            try:
                n_lines = max(1, min(int(args), 1000))
            except ValueError:
                raise UserInputError(
                    user_message=(
                        "📋 **!log — просмотр логов Краба**\n\n"
                        "`!log [N]`              — последние N строк (default 20)\n"
                        "`!log errors`           — только ошибки\n"
                        "`!log search <запрос>`  — поиск по логам"
                    )
                )

    # --- Чтение лог-файла ---
    try:
        file_size = log_path.stat().st_size
        if file_size > _LOG_MAX_INLINE_SIZE:
            # Большой файл — читаем через tail subprocess
            lines = _read_log_tail_subprocess(log_path, max(n_lines, 500))
        else:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, IOError) as e:
        await _send_log_text(f"❌ Ошибка чтения лога: {e}")
        return

    # --- Фильтрация ---
    if mode == "errors":
        _error_keywords = {"error", "critical", "warning"}
        result_lines = [
            ln for ln in lines
            if any(kw in ln.lower() for kw in _error_keywords)
        ]
        header = "⚠️ **Ошибки в логах Краба**"
        if not result_lines:
            await _send_log_text("✅ Ошибок в логах нет.")
            return
    elif mode == "search":
        assert query is not None
        result_lines = [ln for ln in lines if query.lower() in ln.lower()]
        header = f"🔍 **Поиск в логах:** `{query}`"
        if not result_lines:
            await _send_log_text(f"🔍 По запросу `{query}` ничего не найдено.")
            return
    else:
        # tail режим
        result_lines = lines[-n_lines:]
        header = f"📋 **Последние {n_lines} строк лога Краба**"

    # --- Формируем текст ---
    body = "\n".join(result_lines)
    full_text = f"{header}\n\n```\n{body}\n```"

    # --- Отправка: короткий текст → reply/DM, длинный → document ---
    if len(full_text) <= 3900 and len(result_lines) <= _LOG_TEXT_MAX_LINES:
        parts = _split_text_for_telegram(full_text)
        for part in parts:
            await _send_log_text(part)
    else:
        # Отправляем как документ
        now = datetime.datetime.now()
        filename = now.strftime("krab_log_%Y-%m-%d_%H-%M.txt")
        tmpdir = pathlib.Path(config.BASE_DIR) / ".runtime" / "log_exports"
        tmpdir.mkdir(parents=True, exist_ok=True)
        filepath = tmpdir / filename

        # Считаем строки — без markdown-обёртки
        export_text = f"{header}\n\n{body}"
        try:
            filepath.write_text(export_text, encoding="utf-8")
            await _send_log_document(filepath, caption=f"📋 {header} ({len(result_lines)} строк)")
        except (OSError, IOError) as e:
            await _send_log_text(f"❌ Ошибка создания файла: {e}")
        finally:
            try:
                filepath.unlink(missing_ok=True)
            except OSError:
                pass


def _read_log_tail_subprocess(log_path: pathlib.Path, n: int) -> list[str]:
    """Читает последние N строк большого лог-файла через subprocess tail."""
    try:
        from .core.subprocess_env import clean_subprocess_env  # type: ignore[import]
        env = clean_subprocess_env()
    except (ImportError, Exception):
        env = None

    try:
        result = subprocess.run(
            ["tail", "-n", str(n), str(log_path)],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        return result.stdout.splitlines()
    except (subprocess.TimeoutExpired, OSError):
        # Fallback: читаем весь файл и берём хвост
        return log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]


# ---------------------------------------------------------------------------
# WHOIS lookup
# ---------------------------------------------------------------------------

# Поля, которые извлекаем из whois-вывода: (ключ_результата, [варианты_regex])
_WHOIS_FIELD_PATTERNS: list[tuple[str, list[str]]] = [
    ("registrar", [r"Registrar:\s*(.+)", r"registrar:\s*(.+)"]),
    ("created", [
        r"Creation Date:\s*(.+)",
        r"Created Date:\s*(.+)",
        r"created:\s*(.+)",
        r"Domain Registration Date:\s*(.+)",
    ]),
    ("expires", [
        r"Registry Expiry Date:\s*(.+)",
        r"Expir(?:y|ation) Date:\s*(.+)",
        r"expires:\s*(.+)",
        r"paid-till:\s*(.+)",
    ]),
    ("nameservers", [
        r"Name Server:\s*(.+)",
        r"nserver:\s*(.+)",
        r"Nameservers:\s*(.+)",
    ]),
]


def _parse_whois_output(raw: str) -> dict[str, str | list[str]]:
    """
    Извлекает ключевые WHOIS-поля из сырого вывода.

    Возвращает словарь с полями: registrar, created, expires, nameservers.
    Nameservers — список строк.
    """
    result: dict[str, str | list[str]] = {}
    nameservers: list[str] = []

    for field_key, patterns in _WHOIS_FIELD_PATTERNS:
        if field_key == "nameservers":
            # Собираем все уникальные NS-записи
            for pattern in patterns:
                for m in re.finditer(pattern, raw, re.IGNORECASE | re.MULTILINE):
                    ns = m.group(1).strip().lower().rstrip(".")
                    if ns and ns not in nameservers:
                        nameservers.append(ns)
        else:
            # Берём первое совпадение
            if field_key in result:
                continue
            for pattern in patterns:
                m = re.search(pattern, raw, re.IGNORECASE | re.MULTILINE)
                if m:
                    value = m.group(1).strip()
                    # Обрезаем до даты: берём только первые 10 символов ISO-даты
                    if field_key in ("created", "expires") and "T" in value:
                        value = value.split("T")[0]
                    elif field_key in ("created", "expires"):
                        # Некоторые реестры пишут дату с пробелом
                        value = value.split(" ")[0]
                    result[field_key] = value
                    break

    result["nameservers"] = nameservers  # type: ignore[assignment]
    return result


async def handle_whois(bot: "KraabUserbot", message: Message) -> None:
    """
    !whois <домен> — WHOIS lookup: регистратор, дата создания, истечения, NS.

    Использует системную утилиту whois (macOS built-in).
    Парсит вывод для ключевых полей.
    """
    from ..core.subprocess_env import clean_subprocess_env  # noqa: PLC0415

    domain = bot._get_command_args(message).strip().lower()

    # Убираем протокол и путь если пользователь вставил URL
    domain = re.sub(r"^https?://", "", domain)
    domain = domain.split("/")[0].strip()

    if not domain:
        raise UserInputError(
            user_message=(
                "🔍 **!whois — WHOIS lookup**\n\n"
                "`!whois <домен>` — информация о домене\n\n"
                "_Пример: `!whois example.com`_"
            )
        )

    status_msg = await message.reply(f"🔍 WHOIS: `{domain}`...")

    try:
        proc = await asyncio.create_subprocess_exec(
            "whois",
            domain,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=clean_subprocess_env(),
        )
        try:
            stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=20.0)
        except asyncio.TimeoutError:
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
            await status_msg.edit(f"❌ WHOIS timeout для `{domain}` (>20 сек).")
            return
    except FileNotFoundError:
        await status_msg.edit("❌ Утилита `whois` не найдена на этом хосте.")
        return
    except Exception as exc:  # noqa: BLE001
        logger.error("handle_whois_exec_error", domain=domain, error=str(exc))
        await status_msg.edit(f"❌ Ошибка запуска whois: {exc}")
        return

    raw = stdout.decode("utf-8", errors="replace")

    # Некоторые домены возвращают «No match» или аналогичное
    _not_found_signals = (
        "no match",
        "not found",
        "no entries found",
        "object does not exist",
        "no data found",
        "this query returned 0 objects",
        "domain not found",
    )
    raw_lower = raw.lower()
    if any(sig in raw_lower for sig in _not_found_signals) and len(raw) < 500:
        await status_msg.edit(f"❌ Домен `{domain}` не найден в WHOIS.")
        return

    fields = _parse_whois_output(raw)

    registrar = fields.get("registrar") or "—"
    created = fields.get("created") or "—"
    expires = fields.get("expires") or "—"
    ns_list: list[str] = fields.get("nameservers", [])  # type: ignore[assignment]
    nameservers_str = ", ".join(ns_list) if ns_list else "—"

    reply = (
        f"🔍 WHOIS: `{domain}`\n"
        f"─────\n"
        f"Registrar: {registrar}\n"
        f"Created: {created}\n"
        f"Expires: {expires}\n"
        f"Nameservers: {nameservers_str}"
    )

    await status_msg.edit(reply)


# ---------------------------------------------------------------------------
# !convert — конвертер единиц измерения (чистая математика, без API)
# ---------------------------------------------------------------------------

# Словарь коэффициентов: все значения приведены к базовой единице.
# Формат: "единица" → (множитель_к_базе, "базовая_группа")
_CONVERT_UNITS: dict[str, tuple[float, str]] = {
    # Длина → метры
    "km":  (1000.0,   "m"),
    "m":   (1.0,      "m"),
    "cm":  (0.01,     "m"),
    "mm":  (0.001,    "m"),
    "mi":  (1609.344, "m"),
    "ft":  (0.3048,   "m"),
    "in":  (0.0254,   "m"),
    "yd":  (0.9144,   "m"),
    # Масса → килограммы
    "kg":  (1.0,      "kg"),
    "g":   (0.001,    "kg"),
    "lb":  (0.453592, "kg"),
    "oz":  (0.028350, "kg"),
    # Объём → литры
    "l":   (1.0,      "l"),
    "ml":  (0.001,    "l"),
    "gal": (3.78541,  "l"),
    "pt":  (0.473176, "l"),
    # Скорость — база м/с (для согласованности)
    "kmh": (1.0 / 3.6,     "speed"),
    "mph": (0.44704,        "speed"),
    "ms":  (1.0,            "speed"),
    "kn":  (0.514444,       "speed"),
}

# Алиасы: разные варианты написания → канонический ключ
_CONVERT_ALIASES: dict[str, str] = {
    "kilometer": "km", "kilometers": "km", "kilometre": "km", "kilometres": "km",
    "meter": "m", "meters": "m", "metre": "m", "metres": "m",
    "centimeter": "cm", "centimeters": "cm",
    "millimeter": "mm", "millimeters": "mm",
    "mile": "mi", "miles": "mi",
    "foot": "ft", "feet": "ft",
    "inch": "in", "inches": "in",
    "yard": "yd", "yards": "yd",
    "kilogram": "kg", "kilograms": "kg",
    "gram": "g", "grams": "g",
    "pound": "lb", "pounds": "lb", "lbs": "lb",
    "ounce": "oz", "ounces": "oz",
    "liter": "l", "liters": "l", "litre": "l", "litres": "l",
    "milliliter": "ml", "milliliters": "ml",
    "gallon": "gal", "gallons": "gal",
    "pint": "pt", "pints": "pt",
    "km/h": "kmh",
    "m/s": "ms",
    "knot": "kn", "knots": "kn",
    # Температура
    "c": "c", "celsius": "c",
    "f": "f", "fahrenheit": "f",
    "k": "k", "kelvin": "k",
    "°c": "c", "°f": "f",
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
        raise ValueError(
            f"Несовместимые единицы: `{src}` ({src_base}) и `{dst}` ({dst_base})"
        )

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
            user_message=(
                "❌ Формат: `!convert <число> <из> <в>`\n"
                "Например: `!convert 100 km mi`"
            )
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

    await message.reply(
        f"🔢 **{value_display} {src_display}** = **{result_str} {unit_symbol}**"
    )


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
_NEWS_KNOWN_TOPICS: frozenset[str] = frozenset({
    "crypto", "крипто", "криптовалюта",
    "ai", "ии", "ml",
    "tech", "технологии", "технология",
    "finance", "финансы", "финансовые",
    "science", "наука",
    "politics", "политика",
    "business", "бизнес",
    "sports", "спорт",
    "gaming", "игры",
    "space", "космос",
    "health", "здоровье",
    "world", "мир",
    "russia", "россия",
    "usa", "сша",
})


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
            rest = raw[len(first_word):].strip()
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


# ---------------------------------------------------------------------------
# !rate — курсы криптовалют и акций через AI + web_search
# ---------------------------------------------------------------------------

# Известные крипто-тикеры для более точного AI-запроса
_RATE_CRYPTO_ALIASES: dict[str, str] = {
    "btc": "Bitcoin (BTC)",
    "eth": "Ethereum (ETH)",
    "sol": "Solana (SOL)",
    "bnb": "BNB (Binance Coin)",
    "xrp": "XRP (Ripple)",
    "ada": "Cardano (ADA)",
    "doge": "Dogecoin (DOGE)",
    "ton": "Toncoin (TON)",
    "usdt": "Tether (USDT)",
    "usdc": "USD Coin (USDC)",
    "avax": "Avalanche (AVAX)",
    "link": "Chainlink (LINK)",
    "dot": "Polkadot (DOT)",
    "ltc": "Litecoin (LTC)",
    "shib": "Shiba Inu (SHIB)",
}

# Максимальное количество тикеров за один запрос
_RATE_MAX_ASSETS = 5


def _rate_asset_label(ticker: str) -> str:
    """Возвращает читаемое название актива по тикеру (крипто) или тикер в верхнем регистре (акции)."""
    return _RATE_CRYPTO_ALIASES.get(ticker.lower(), ticker.upper())


def _build_rate_prompt(assets: list[str]) -> str:
    """Формирует промпт для AI-запроса текущего курса активов."""
    labels = [_rate_asset_label(a) for a in assets]
    if len(labels) == 1:
        asset_str = labels[0]
        return (
            f"Найди текущую цену {asset_str}. "
            "Покажи: цену в USD, изменение за 24ч, капитализацию. "
            "Используй актуальные данные из веб-поиска. "
            "Ответ дай кратко, без лишних вступлений."
        )
    else:
        asset_str = ", ".join(labels)
        return (
            f"Найди текущие цены следующих активов: {asset_str}. "
            "Для каждого актива покажи: цену в USD, изменение за 24ч, капитализацию. "
            "В конце добавь краткое сравнение. "
            "Используй актуальные данные из веб-поиска. "
            "Ответ дай кратко, без лишних вступлений."
        )


async def handle_rate(bot: "KraabUserbot", message: Message) -> None:
    """
    Курсы криптовалют и акций через AI + web_search.

    Форматы:
      !rate btc          — текущая цена Bitcoin (цена, 24h%, капитализация)
      !rate eth          — Ethereum
      !rate AAPL         — акция Apple
      !rate btc eth      — сравнение двух активов
      !rate btc eth sol  — сравнение нескольких активов (до 5)
    """
    raw_args = bot._get_command_args(message).strip()

    # Проверяем пустой запрос
    if not raw_args:
        raise UserInputError(
            user_message=(
                "📈 Укажи тикер:\n"
                "`!rate btc` — Bitcoin\n"
                "`!rate eth` — Ethereum\n"
                "`!rate AAPL` — акция Apple\n"
                "`!rate btc eth` — сравнение активов"
            )
        )

    # Парсим список тикеров (разделители: пробел или запятая)
    assets = [a.strip() for a in re.split(r"[\s,]+", raw_args) if a.strip()]

    if not assets:
        raise UserInputError(user_message="📈 Укажи хотя бы один тикер.")

    # Ограничиваем количество активов
    if len(assets) > _RATE_MAX_ASSETS:
        assets = assets[:_RATE_MAX_ASSETS]

    # Изолированная сессия (не загрязняем основной контекст чата)
    session_id = f"rate_{message.chat.id}"

    # Индикатор загрузки
    labels_preview = ", ".join(_rate_asset_label(a) for a in assets)
    msg = await message.reply(f"📈 Смотрю курс: **{labels_preview}**...")

    prompt = _build_rate_prompt(assets)

    try:
        chunks: list[str] = []
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            disable_tools=False,  # AI использует web_search для актуальных данных
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()
        if not result:
            await msg.edit("❌ Не удалось получить данные о курсе.")
            return

        # Заголовок + ответ AI
        header = f"📈 **{labels_preview}**\n\n"
        full_text = header + result

        # Пагинация для длинных ответов (Telegram лимит ~4096)
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
        logger.error("handle_rate_error", assets=assets, error=str(exc))
        await msg.edit(f"❌ Ошибка получения курса: {exc}")


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
        lines.append(
            f"\n**Итого:** {found_count} файлов найдено, {missing_count} отсутствуют."
        )
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


# ---------------------------------------------------------------------------
# !explain — объяснение кода через AI
# ---------------------------------------------------------------------------

_EXPLAIN_PROMPT = (
    "Объясни этот код простым языком. Что он делает, зачем, как работает."
)


async def handle_explain(bot: "KraabUserbot", message: Message) -> None:
    """
    Объяснение фрагмента кода через AI.

    Форматы:
      !explain <код>   — объясняет переданный код
      !explain         — reply на сообщение с кодом → объясняет код из reply
    """
    raw_args = bot._get_command_args(message).strip()

    # Получаем код: из аргументов или из reply-сообщения
    code = raw_args
    if not code:
        replied = getattr(message, "reply_to_message", None)
        if replied:
            code = (replied.text or replied.caption or "").strip()

    if not code:
        raise UserInputError(
            user_message=(
                "💡 Пришли код для объяснения:\n"
                "`!explain <код>` — вставь код напрямую\n"
                "Или ответь на сообщение с кодом командой `!explain`"
            )
        )

    # Изолированная сессия — не смешиваем с основным диалогом чата
    session_id = f"explain_{message.chat.id}"

    msg = await message.reply("💡 **Анализирую код...**")

    prompt = f"{_EXPLAIN_PROMPT}\n\n```\n{code}\n```"

    try:
        chunks: list[str] = []
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            disable_tools=True,
            max_output_tokens=1024,
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()

        if not result:
            await msg.edit("❌ AI не смог объяснить этот код.")
            return

        # Пагинация при длинном ответе
        header = "💡 **Объяснение кода**\n\n"
        parts = _split_text_for_telegram(header + result)
        total = len(parts)

        first = parts[0]
        if total > 1:
            first += f"\n\n_(часть 1/{total})_"
        await msg.edit(first)

        for i, part in enumerate(parts[1:], start=2):
            suffix = f"\n\n_(часть {i}/{total})_" if total > 2 else ""
            await message.reply(part + suffix)

    except Exception as exc:  # noqa: BLE001
        logger.error("handle_explain_error", error=str(exc))
        await msg.edit(f"❌ Ошибка объяснения: {exc}")


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
