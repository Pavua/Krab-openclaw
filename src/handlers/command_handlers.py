# -*- coding: utf-8 -*-
"""
Обработчики Telegram-команд, вынесенные из userbot_bridge (Фаза 4.4).
Каждая функция принимает (bot, message) для тестируемости и уплощения register_handlers.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import TYPE_CHECKING, Any

import httpx
from pyrogram.types import Message

from ..config import config
from ..core.access_control import (
    PARTIAL_ACCESS_COMMANDS,
    AccessLevel,
    get_effective_owner_label,
    load_acl_runtime_state,
    update_acl_subject,
)
from ..core.chat_ban_cache import chat_ban_cache
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
from ..core.translator_runtime_profile import (
    ALLOWED_LANGUAGE_PAIRS,
    ALLOWED_TRANSLATION_MODES,
    ALLOWED_VOICE_STRATEGIES,
    default_translator_runtime_profile,
)
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
    timeline_summary = state.get("timeline_summary") if isinstance(state.get("timeline_summary"), dict) else {}
    preview = state.get("timeline_preview") if isinstance(state.get("timeline_preview"), list) else []
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
                part = line[i:i + limit]
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
    """Ручной веб-поиск через Brave."""
    query = bot._get_command_args(message)
    if not query or query.lower() in ["search", "!search"]:
        raise UserInputError(user_message="🔍 Что ищем? Напиши: `!search <запрос>`")
    msg = await message.reply(f"🔍 **Краб ищет в сети:** `{query}`...")
    try:
        results = await search_brave(query)
        if len(results) > 4000:
            results = results[:3900] + "..."
        await msg.edit(f"🔍 **Результаты поиска:**\n\n{results}")
    except (httpx.HTTPError, OSError, ValueError, KeyError) as e:
        await msg.edit(f"❌ Ошибка поиска: {e}")


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
        raise UserInputError(user_message=(
            "🐝 Как использовать Swarm:\n"
            "`!swarm <тема>` — базовый room\n"
            "`!swarm traders BTC анализ` — команда трейдеров\n"
            "`!swarm teams` — список команд\n"
            "`!swarm memory [команда]` — история прогонов\n"
            "`!swarm schedule traders 4h BTC` — автозапуск\n"
            "`!swarm jobs` — список задач\n"
            "`!swarm unschedule <id>` — удалить задачу\n"
            "`!swarm setup` — создать Forum-группу с топиками\n"
            "`!swarm channels` — статус групп/топиков\n"
            "`!swarm listen on|off` — team listeners\n"
            "`!swarm task create|list|done|fail|board` — task board"
        ))

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
                emoji = {"pending": "⏳", "in_progress": "🔄", "done": "✅", "failed": "❌", "blocked": "🚫"}.get(status_name, "•")
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
                emoji = {"pending": "⏳", "in_progress": "🔄", "done": "✅", "failed": "❌"}.get(t.status, "•")
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
                team=team_name, title=title, description="",
                priority="medium", created_by="owner",
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

        raise UserInputError(user_message=(
            "📋 Task Board:\n"
            "`!swarm task board` — сводка\n"
            "`!swarm task list [team]` — список задач\n"
            "`!swarm task create <team> <title>` — создать\n"
            "`!swarm task done <id>` — завершить\n"
            "`!swarm task fail <id>` — отметить как failed"
        ))

    # !swarm listen on/off — управление team listeners
    if args.lower().startswith("listen"):
        from ..core.swarm_team_listener import is_listeners_enabled, set_listeners_enabled
        listen_tokens = args.split(maxsplit=1)
        if len(listen_tokens) > 1:
            val = listen_tokens[1].strip().lower()
            if val in {"on", "1", "true", "yes"}:
                set_listeners_enabled(True)
                await message.reply("🎧 Team listeners: **ON**\nTeam-аккаунты отвечают в ЛС и на mention.")
            elif val in {"off", "0", "false", "no"}:
                set_listeners_enabled(False)
                await message.reply("🔇 Team listeners: **OFF**\nTeam-аккаунты молчат.")
            else:
                await message.reply("❌ Формат: `!swarm listen on` или `!swarm listen off`")
        else:
            status = "ON ✅" if is_listeners_enabled() else "OFF 🔇"
            await message.reply(f"🎧 Team listeners: **{status}**")
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

    # !swarm schedule <team> <topic> <interval> — создание рекуррентной задачи
    if args.lower().startswith("schedule") or args.lower().startswith("расписание"):
        from ..core.swarm_scheduler import parse_interval, swarm_scheduler
        sched_tokens = args.split(maxsplit=3)  # schedule traders "BTC" 4h
        if len(sched_tokens) < 4:
            await message.reply(
                "📅 Формат: `!swarm schedule <команда> <интервал> <тема>`\n"
                "Пример: `!swarm schedule traders 4h анализ BTC`\n"
                "Интервалы: `30m`, `1h`, `4h`, `1d`"
            )
            return
        sched_team = resolve_team_name(sched_tokens[1])
        if not sched_team:
            await message.reply(f"❌ Команда '{sched_tokens[1]}' не найдена. Доступны: {', '.join(TEAM_REGISTRY)}")
            return
        try:
            interval = parse_interval(sched_tokens[2])
        except ValueError as e:
            await message.reply(f"❌ {e}")
            return
        sched_topic = sched_tokens[3] if len(sched_tokens) > 3 else "общий анализ"
        try:
            job = swarm_scheduler.add_job(team=sched_team, topic=sched_topic, interval_sec=interval)
            interval_h = interval / 3600
            interval_str = f"{interval_h:.1f}ч" if interval_h >= 1 else f"{interval // 60}мин"
            await message.reply(
                f"📅 Задача создана!\n"
                f"ID: `{job.job_id}`\n"
                f"Команда: **{sched_team}** каждые {interval_str}\n"
                f"Тема: _{sched_topic}_"
            )
        except RuntimeError as e:
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
                        f"  **{team}** → topic `{tid}`"
                        for team, tid in result["topic_ids"].items()
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
            await message.reply(f"❌ Команда '{setchat_tokens[1]}' не найдена. Доступны: {', '.join(TEAM_REGISTRY)}")
            return
        chat_id = message.chat.id
        swarm_channels.register_team_chat(team, chat_id)
        await message.reply(f"📡 Группа привязана к команде **{team}**\nChat ID: `{chat_id}`")
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


async def handle_status(bot: "KraabUserbot", message: Message) -> None:
    """Статус системы и ресурсов."""
    ram = model_manager.get_ram_usage()
    is_ok = await openclaw_client.health_check()
    route_meta = {}
    if hasattr(openclaw_client, "get_last_runtime_route"):
        try:
            route_meta = openclaw_client.get_last_runtime_route() or {}
        except Exception:
            route_meta = {}
    actual_model = str(route_meta.get("model") or "").strip()
    actual_channel = str(route_meta.get("channel") or "").strip()
    route_status = str(route_meta.get("status") or "").strip()
    route_error = str(route_meta.get("error_code") or "").strip()
    declared_primary = str(get_runtime_primary_model() or getattr(config, "MODEL", "") or "").strip()
    effective_model = actual_model or declared_primary or "unknown"
    bar = "▓" * int(ram["percent"] / 10) + "░" * (10 - int(ram["percent"] / 10))
    text = f"""
🦀 **Системный статус Краба**
---------------------------
📡 **Gateway (OpenClaw):** {"✅ Online" if is_ok else "❌ Offline"}
🧠 **Фактическая модель:** `{effective_model}`
🎭 **Роль:** `{bot.current_role}`
🎙️ **Голос:** `{"ВКЛ" if bot.voice_mode else "ВЫКЛ"}`
💻 **RAM:** [{bar}] {ram["percent"]}%
"""
    if declared_primary and declared_primary != effective_model:
        text += f"🧭 **Primary runtime:** `{declared_primary}`\n"
    if actual_channel:
        text += f"🛣️ **Маршрут:** `{actual_channel}`\n"
    if route_status and (route_status != "ok" or route_error):
        suffix = f" / `{route_error}`" if route_error else ""
        text += f"⚠️ **Route status:** `{route_status}`{suffix}\n"
    if message.from_user and message.from_user.id == bot.me.id:
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
            return any(
                m.id == model_id and m.type.name.startswith("LOCAL")
                for m in models
            )
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
            lines = [f"🔍 **Доступные модели** (local={len(local_models)}, cloud={len(cloud_from_api)})\n", "☁️ **Облачные**\n"]
            for m in sorted(cloud_from_api, key=lambda x: x.id):
                loaded = " ✅" if m.id == model_manager._current_model else ""
                lines.append(f"☁️ `{m.id}` · `{_format_size_gb(getattr(m, 'size_gb', 0.0))}`{loaded}")
            lines.append("\n💻 **Локальные**\n")
            for m in sorted(local_models, key=lambda x: x.id):
                loaded = " ✅" if m.id == model_manager._current_model else ""
                lines.append(f"💻 `{m.id}` · `{_format_size_gb(getattr(m, 'size_gb', 0.0))}`{loaded}")
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
    """Очистка истории диалога."""
    openclaw_client.clear_session(str(message.chat.id))
    res = "🧹 **Память очищена. Клешни как новые!**"
    if message.from_user and message.from_user.id == bot.me.id:
        await message.edit(res)
    else:
        await message.reply(res)


async def handle_config(bot: "KraabUserbot", message: Message) -> None:
    """Просмотр текущих настроек."""
    text = f"""
⚙️ **Конфигурация Краба**
----------------------
👤 **Владелец (effective):** `{get_effective_owner_label()}`
🧷 **Fallback owner_username:** `{config.OWNER_USERNAME}`
🎯 **Триггеры:** `{", ".join(config.TRIGGER_PREFIXES)}`
🧠 **Память (RAM):** `{config.MAX_RAM_GB}GB`
"""
    await message.reply(text)


async def handle_set(bot: "KraabUserbot", message: Message) -> None:
    """Изменение настроек на лету."""
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        raise UserInputError(user_message="⚙️ `!set <KEY> <VAL>`")
    key = str(args[1] or "").upper()
    if config.update_setting(key, args[2]):
        extra = ""
        if key == "SCHEDULER_ENABLED" and hasattr(bot, "_sync_scheduler_runtime"):
            try:
                bot._sync_scheduler_runtime()
                state = "ON" if bool(getattr(config, "SCHEDULER_ENABLED", False)) else "OFF"
                extra = f"\n⏰ Scheduler runtime: `{state}`"
            except Exception as exc:  # noqa: BLE001
                extra = f"\n⚠️ Scheduler sync warning: `{str(exc)[:120]}`"
        await message.reply(f"✅ `{key}` обновлено!{extra}")
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
                "❌ Неизвестное действие ACL.\n"
                "Используй: `status`, `list`, `grant`, `revoke`."
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
        raise UserInputError(
            user_message="❌ Можно изменять только уровни `full` и `partial`."
        )

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
        await message.reply("🔔 Tool notifications: **ON**\nБуду показывать какой инструмент вызываю.")
    elif args in {"off", "0", "false", "no"}:
        _cfg.update_setting("TOOL_NARRATION_ENABLED", "0")
        await message.reply("🔕 Tool notifications: **OFF**\nИнструменты молча, только финальный ответ.")
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
            raise UserInputError(user_message="❌ Скорость должна быть числом, например `1.25`.") from exc
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
        lines.append(
            f"- `{chat_id}` · {code} · expires={expires} · hits={hits}"
        )
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
    sub = (args[1].strip().lower() if len(args) >= 2 else "status")

    if sub in {"", "status", "show", "list"}:
        entries = chat_ban_cache.list_entries()
        await message.reply(_render_chat_ban_entries(entries))
        return

    if sub == "clear":
        if len(args) < 3 or not args[2].strip():
            raise UserInputError(
                user_message="❌ Укажи chat_id: `!chatban clear <chat_id>`"
            )
        target = args[2].strip()
        removed = chat_ban_cache.clear(target)
        if removed:
            await message.reply(
                f"✅ Убрал `{target}` из chat ban cache. "
                f"Краб снова будет обрабатывать сообщения оттуда."
            )
        else:
            await message.reply(
                f"ℹ️ `{target}` не был в chat ban cache (уже снят или не помечен)."
            )
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
    args = str(message.text or "").split(maxsplit=3)
    if len(args) == 1 or str(args[1] or "").strip().lower() in {"status", "show"}:
        await message.reply(_render_translator_profile(bot.get_translator_runtime_profile()))
        return

    sub = str(args[1] or "").strip().lower()

    if sub in {"lang", "language"}:
        if len(args) < 3:
            raise UserInputError(
                user_message="❌ Укажи языковую пару: `!translator lang es-ru`."
            )
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
            raise UserInputError(
                user_message="❌ Укажи mode: `!translator mode bilingual`."
            )
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
                raise UserInputError(user_message="❌ Номер фразы должен быть целым числом.") from exc
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

    if sub == "session":
        action = str(args[2] or "").strip().lower() if len(args) >= 3 else "status"
        if action in {"status", "show"}:
            await message.reply(_render_translator_session_state(bot.get_translator_session_state()))
            return
        if action == "start":
            label = str(args[3] or "").strip() if len(args) >= 4 else ""
            profile = bot.get_translator_runtime_profile()
            state = bot.update_translator_session_state(
                session_status="active",
                translation_muted=False,
                active_session_label=label,
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
            state = bot.update_translator_session_state(
                session_status="idle",
                translation_muted=False,
                active_session_label="",
                last_event="session_stopped",
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
                raise UserInputError(
                    user_message="❌ Для replay нужны и original, и translation."
                )
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
            **default_translator_runtime_profile(),
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


async def handle_sysinfo(bot: "KraabUserbot", message: Message) -> None:
    """Расширенная информация о хосте."""
    import platform

    import psutil

    text = f"🖥️ **System:** `{platform.system()}`\n🔥 **CPU:** `{psutil.cpu_percent()}%`"
    await message.reply(text)


async def handle_panel(bot: "KraabUserbot", message: Message) -> None:
    """Графическая панель управления."""
    await handle_status(bot, message)


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
            + (
                f" (`{status.get('clipboard_preview')}`)"
                if status.get("clipboard_preview")
                else ""
            )
        )
        warnings = status.get("warnings") or []
        if warnings:
            lines.append("- Warnings: " + "; ".join(str(item) for item in warnings[:3]))
        reminder_lists = status.get("reminder_lists") or []
        note_folders = status.get("note_folders") or []
        calendars = status.get("calendars") or []
        if reminder_lists:
            lines.append("- Reminders lists: " + ", ".join(f"`{item}`" for item in reminder_lists[:5]))
        if note_folders:
            lines.append("- Notes folders: " + ", ".join(f"`{item}`" for item in note_folders[:5]))
        if calendars:
            lines.append("- Calendars: " + ", ".join(f"`{item}`" for item in calendars[:6]))
        await message.reply("\n".join(lines))
        return

    if sub == "reminders":
        if len(parts) < 2:
            raise UserInputError(user_message="🍎 Формат: `!mac reminders list` или `!mac reminders add <время> | <текст>`")
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
                raise UserInputError(user_message="🍎 Формат: `!mac reminders add <время> | <текст>`")
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
        raise UserInputError(user_message="🍎 Формат: `!mac reminders list` или `!mac reminders add <время> | <текст>`")

    if sub == "notes":
        if len(parts) < 2:
            raise UserInputError(user_message="🍎 Формат: `!mac notes list` или `!mac notes add <заголовок> | <текст>`")
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
                raise UserInputError(user_message="🍎 Формат: `!mac notes add <заголовок> | <текст>`")
            raw_title, raw_body = payload.split("|", 1)
            title = raw_title.strip()
            body = raw_body.strip()
            if not title or not body:
                raise UserInputError(user_message="🍎 Заголовок и текст заметки не должны быть пустыми.")
            created = await macos_automation.create_note(title=title, body=body)
            await message.reply(
                "✅ Заметка создана в Notes.\n"
                f"- ID: `{created['id']}`\n"
                f"- Папка: `{created['folder_name']}`\n"
                f"- Заголовок: `{title}`"
            )
            return
        raise UserInputError(user_message="🍎 Формат: `!mac notes list` или `!mac notes add <заголовок> | <текст>`")

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
                raise UserInputError(user_message="🍎 Формат: `!mac calendar add <время> | <название>`")
            start_at = parse_due_time(time_spec)
            created = await macos_automation.create_calendar_event(title=event_title, start_at=start_at, duration_minutes=30)
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
            raise UserInputError(user_message="🍎 Формат: `!mac clip get` или `!mac clip set <текст>`")
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
            raise UserInputError(user_message="🍎 Формат: `!mac notify <текст>` или `!mac notify <заголовок> | <текст>`")
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
        app_arg = args[len("focus"):].strip()
        if not app_arg:
            raise UserInputError(user_message="🍎 Формат: `!mac focus <имя приложения>`")
        result = await macos_automation.focus_app(app_arg)
        await message.reply(f"🪟 Фокус: `{result['app_name']}`")
        return

    if sub == "type":
        text_arg = args[len("type"):].strip()
        if not text_arg:
            raise UserInputError(user_message="🍎 Формат: `!mac type <текст>`")
        result = await macos_automation.type_text(text_arg)
        await message.reply(f"⌨️ Напечатано {result['text_length']} символов в `{result['app_name']}`")
        return

    if sub == "typeclip":
        text_arg = args[len("typeclip"):].strip()
        if not text_arg:
            raise UserInputError(user_message="🍎 Формат: `!mac typeclip <текст>` (через clipboard, поддерживает Unicode)")
        result = await macos_automation.type_text_via_clipboard(text_arg)
        await message.reply(f"📋→⌨️ Вставлено {result['text_length']} символов в `{result['app_name']}`")
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
        key_arg = args[len("key"):].strip()
        if not key_arg:
            raise UserInputError(user_message="🍎 Формат: `!mac key <клавиша>` (return/tab/escape/...)")
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
    """Мягкая перезагрузка процесса."""
    await message.reply("🔄 Перезапускаюсь...")
    sys.exit(42)


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
                raise UserInputError(
                    user_message="🐝 Формат: `!agent swarm loop [N] <тема>`"
                )
            first, *rest = loop_payload.split(" ", 1)
            if first.isdigit():
                loop_rounds = int(first)
                topic = rest[0].strip() if rest else ""
            else:
                topic = loop_payload
            if not topic:
                raise UserInputError(
                    user_message="🐝 Формат: `!agent swarm loop [N] <тема>`"
                )

        max_rounds = int(getattr(config, "SWARM_LOOP_MAX_ROUNDS", 3) or 3)
        next_round_clip = int(getattr(config, "SWARM_LOOP_NEXT_ROUND_CLIP", 4000) or 4000)
        safe_rounds = max(1, min(loop_rounds, max_rounds))

        if is_loop:
            status = await message.reply(
                f"🐝 Запускаю роевой loop: {safe_rounds} раунд(а), роли аналитик → критик → интегратор..."
            )
        else:
            status = await message.reply("🐝 Запускаю роевой раунд: аналитик → критик → интегратор...")
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
    """Справка по командам (v7.2 categories)."""
    text = """🦀 **Команды Краба**

**Core**
`!status` — статус системы
`!clear` — очистить историю диалога
`!config` — текущие настройки
`!set <KEY> <VAL>` — изменить настройку
`!restart` — перезапуск бота
`!help` — эта справка

**AI / Model**
`!model` — статус маршрутизации
`!model local` — принудительно локальная модель
`!model cloud` — принудительно облачная модель
`!model auto` — автоматический выбор
`!model set <model_id>` — выбрать конкретную модель (из `!model scan`)
`!model load <name>` — загрузить модель
`!model unload` — выгрузить модель
`!model scan` — список доступных моделей

**Tools**
`!search <query>` — веб-поиск
`!remember <text>` — запомнить факт
`!recall <query>` — вспомнить факт
`!acl ...` / `!access ...` — управление full/partial доступом (owner-only)
`!role [name|list]` — смена личности
`!remind <время> | <текст>` — поставить напоминание
`!reminders` — список активных напоминаний
`!rm_remind <id>` — удалить напоминание
`!cronstatus` — статус scheduler
`!watch status|now` — proactive watch / owner-digest
`!memory recent` — последние записи общей памяти
`!inbox [list|status|ack|done|cancel|approve|reject|task|approval]` — owner-visible inbox / escalation

**System**
`!ls [path]` — список файлов
`!read <path>` — чтение файла
`!write <file> <content>` — запись файла
`!sysinfo` — информация о хосте
`!mac ...` — управление macOS (clipboard / notify / apps / Finder / Notes / Reminders / Calendar)
`!screenshot` — снимок текущей вкладки Chrome; `!screenshot ocr [lang]` — OCR; `!screenshot health` — статус CDP
`!cap` — просмотр/toggle capabilities матрицы; `!cap <name> on|off`; `!cap reset`
`!diagnose` — диагностика подключений

**Dev**
`!agent new <name> <prompt>` — создать агента
`!agent list` — список агентов
`!agent swarm <тема>` — роевой раунд (аналитик/критик/интегратор)
`!agent swarm loop [N] <тема>` — несколько роевых раундов (итеративная доработка)
`!voice ...` — голосовой runtime-профиль (on/off/speed/voice/delivery)
`!translator ...` — product runtime-профиль переводчика (языки/mode/strategy/flags/phrases)
`!reasoning [show|clear]` — owner-only просмотр скрытой reasoning-trace последнего ответа
`!web` — управление браузером
`!panel` — панель управления (soon)
"""
    await message.reply(text)


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


async def handle_remind(bot: "KraabUserbot", message: Message) -> None:
    """
    Добавляет reminder-задачу в runtime scheduler.

    Форматы:
    - `!remind 10m | купить воду`
    - `!remind через 20 минут проверить почту`
    - `!remind в 18:30 созвон`
    """
    if not bool(getattr(config, "SCHEDULER_ENABLED", False)):
        raise UserInputError(
            user_message=(
                "⏰ Scheduler сейчас выключен (`SCHEDULER_ENABLED=0`).\n"
                "Включи его (`!set SCHEDULER_ENABLED 1`) и перезапусти Krab."
            )
        )

    raw_args = bot._get_command_args(message)
    if not raw_args:
        raise UserInputError(
            user_message=(
                "⏰ Формат:\n"
                "`!remind <время> | <текст>`\n\n"
                "Примеры:\n"
                "- `!remind 10m | выпить воды`\n"
                "- `!remind через 20 минут проверить почту`\n"
                "- `!remind в 18:30 созвон`"
            )
        )

    time_spec, reminder_text = split_reminder_input(raw_args)
    if not time_spec or not reminder_text:
        raise UserInputError(
            user_message=(
                "⏰ Не удалось разобрать время/текст.\n"
                "Используй формат: `!remind <время> | <текст>`"
            )
        )

    try:
        due_at = parse_due_time(time_spec)
    except ValueError:
        raise UserInputError(
            user_message=(
                "❌ Не удалось распознать время.\n"
                "Поддерживается: `10m`, `через 20 минут`, `в 18:30`, `2026-03-05 09:00`."
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
        f"- Текст: {reminder_text}"
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
    await message.reply(
        "🧭 **Scheduler status**\n"
        f"- enabled (config): `{status.get('scheduler_enabled')}`\n"
        f"- started: `{status.get('started')}`\n"
        f"- pending: `{status.get('pending_count')}`\n"
        f"- next_due_at: `{status.get('next_due_at') or '-'}`\n"
        f"- storage: `{status.get('storage_path')}`"
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
        suffix = "\n- Память: записано в workspace memory" if result.get("wrote_memory") else "\n- Память: запись пропущена"
        await message.reply(str(result.get("digest") or "watch digest unavailable") + suffix)
        return

    raise UserInputError(user_message="🛰️ Формат: `!watch status` или `!watch now`")


async def handle_memory(bot: "KraabUserbot", message: Message) -> None:
    """
    Короткий просмотр общей памяти OpenClaw без поиска по словам.

    Пока сознательно ограничиваемся read-only режимом:
    - `!remember` уже отвечает за запись фактов;
    - эта команда нужна для последних записей и owner-digest слоёв.
    """
    del bot
    raw_args = str(message.text or "").split(maxsplit=2)
    action = raw_args[1].strip().lower() if len(raw_args) > 1 else "recent"
    source_filter = raw_args[2].strip() if len(raw_args) > 2 else ""

    if action != "recent":
        raise UserInputError(user_message="🧠 Формат: `!memory recent [source_filter]`")

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
            approval_suffix = f" · scope `{approval_scope}`" if approval_scope and item["kind"] == "approval_request" else ""
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
            raise UserInputError(user_message="📥 Формат: `!inbox taskfrom <source_id> | <title> | <body>`")
        source_item_id, title, body = [part.strip() for part in raw_args[2].split("|", maxsplit=2)]
        if not source_item_id or not title or not body:
            raise UserInputError(user_message="📥 Для taskfrom нужны source_id, заголовок и описание.")
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
            raise UserInputError(user_message="📥 Формат: `!inbox approval <scope> | <title> | <body>`")
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
            raise UserInputError(user_message="📥 Формат: `!inbox approvalfrom <source_id> | <scope> | <title> | <body>`")
        source_item_id, scope, title, body = [part.strip() for part in raw_args[2].split("|", maxsplit=3)]
        if not source_item_id or not scope or not title or not body:
            raise UserInputError(user_message="📥 Для approvalfrom нужны source_id, scope, заголовок и описание.")
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
        raise UserInputError(user_message="📥 Укажи item id: `!inbox ack|done|cancel|approve|reject <id> [| note]`")
    target_payload = raw_args[2].strip()
    target_id, note = [part.strip() for part in target_payload.split("|", maxsplit=1)] if "|" in target_payload else (target_payload, "")
    if not target_id:
        raise UserInputError(user_message="📥 Укажи корректный item id: `!inbox ack|done|cancel|approve|reject <id> [| note]`")
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
            raise UserInputError(user_message=f"📥 Item `{target_id}` не является approval-request.")
        raise UserInputError(user_message=f"📥 Item `{target_id}` не найден.")
    await message.reply(
        "✅ Inbox item обновлён.\n"
        f"- ID: `{target_id}`\n"
        f"- Новый статус: `{target_status}`"
        + (f"\n- Note: {note}" if note else "")
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
        lines = [f"{i + 1}. {t.get('title') or t.get('url')}\n   {t['url']}" for i, t in enumerate(tabs)]
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

    _HELP = (
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
            app    = " ".join(parts[2:]) if len(parts) > 2 else ""
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
        status = "✅ CDP ready" if probe.get("ok") else ("🚫 blocked" if probe.get("blocked") else "⚠️ degraded")
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
        err_detail = probe.get("error") or ("Chrome не запущен или CDP недоступен" if probe.get("blocked") else "неизвестная ошибка")
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
            f"❌ `{cap_name}` — неизвестная capability.\n"
            f"Доступные: `{'`, `'.join(valid)}`"
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
    from ..core.chat_ban_cache import chat_ban_cache
    from ..core.chat_capability_cache import chat_capability_cache
    from ..core.silence_mode import silence_manager
    from ..core.telegram_rate_limiter import telegram_rate_limiter

    lines: list[str] = ["📊 **Krab Runtime Stats**", ""]

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
            code = (
                entry.get("last_error_code")
                or entry.get("error_code")
                or "?"
            )
            lines.append(f"- `{chat_id}` · {code}")
        if len(ban_entries) > 3:
            lines.append(f"- …ещё `{len(ban_entries) - 3}`")
    lines.append("")

    # 3. Chat capability cache ────────────────────────────────────────
    cap_entries = chat_capability_cache.list_entries()
    voice_forbidden = sum(
        1 for entry in cap_entries if entry.get("voice_allowed") is False
    )
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
    muted_chats_count = (
        len(muted_chats_raw) if isinstance(muted_chats_raw, dict) else 0
    )
    global_remaining_min = silence.get("global_remaining_min") or 0
    lines.append("🔇 **Silence mode**")
    lines.append(
        f"- Глобально: `{'ВКЛ' if global_muted else 'ВЫКЛ'}`"
        + (
            f" · осталось `{global_remaining_min} мин`"
            if global_muted
            else ""
        )
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

    return "\n".join(lines).rstrip()


async def handle_stats(bot: "KraabUserbot", message: Message) -> None:
    """Агрегированный runtime-статус Краба (B.9 session 4 tail)."""
    panel = _render_stats_panel(bot)
    await message.reply(panel)


async def handle_silence(bot: "KraabUserbot", message: Message) -> None:
    """!тишина — управление режимом тишины.

    Синтаксис:
      !тишина           — toggle текущего чата (30 мин)
      !тишина 15        — mute текущего чата на 15 минут
      !тишина стоп      — снять mute текущего чата
      !тишина глобально — глобальный mute (60 мин)
      !тишина глобально 30 — глобальный mute на 30 мин
      !тишина статус    — показать все активные mutes
    """
    from ..core.silence_mode import silence_manager

    chat_id = str(message.chat.id)
    raw = (message.text or "").strip()
    # Убираем префикс команды
    parts = raw.split(maxsplit=1)
    args = parts[1].strip().lower() if len(parts) > 1 else ""

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
        minutes = int(rest) if rest.isdigit() else int(getattr(config, "SILENCE_DEFAULT_MINUTES", 60))
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
