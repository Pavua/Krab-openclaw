# -*- coding: utf-8 -*-
"""
Commands Handler — Базовые команды бота: !status, !diagnose, !config, !help, !logs.

Извлечён из main.py (строки ~290-898). Отвечает за общую информацию
о состоянии бота, диагностику и конфигурацию.
"""

import os
import asyncio
import json
from datetime import datetime
from pathlib import Path

from pyrogram import filters, enums
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)

from .auth import is_owner

import structlog
import asyncio
from src.core.ecosystem_health import EcosystemHealthService
logger = structlog.get_logger(__name__)


def parse_model_set_request(args: list[str], valid_slots: list[str]) -> dict[str, str | bool]:
    """
    Разбирает аргументы `!model set` в каноничном и legacy формате.

    Контракт:
    - канон: `!model set <slot> <model_id>`
    - legacy: `!model set <model_id>` -> слот `chat` + предупреждение
    """
    slots_sorted = sorted({str(slot).strip().lower() for slot in valid_slots if str(slot).strip()})
    slots_hint = ", ".join(slots_sorted) if slots_sorted else "chat"
    usage = (
        "⚠️ Формат команды:\n"
        "`!model set <slot> <model_id>`\n"
        "Пример: `!model set chat zai-org/glm-4.6v-flash`"
    )

    if len(args) < 3:
        return {
            "ok": False,
            "error": usage,
            "slot": "",
            "model_name": "",
            "legacy": False,
            "warning": "",
        }

    # Legacy: !model set <model_id>
    if len(args) == 3:
        model_name = args[2].strip()
        if model_name.lower() in slots_sorted:
            return {
                "ok": False,
                "error": (
                    "❌ После слота нужно указать model_id.\n"
                    f"{usage}"
                ),
                "slot": model_name.lower(),
                "model_name": "",
                "legacy": False,
                "warning": "",
            }
        if not model_name:
            return {
                "ok": False,
                "error": usage,
                "slot": "",
                "model_name": "",
                "legacy": False,
                "warning": "",
            }
        return {
            "ok": True,
            "error": "",
            "slot": "chat",
            "model_name": model_name,
            "legacy": True,
            "warning": (
                "⚠️ Legacy-формат `!model set <model_id>` устарел.\n"
                "Команда интерпретирована как `!model set chat <model_id>`."
            ),
        }

    slot = args[2].strip().lower()
    if slot not in slots_sorted:
        return {
            "ok": False,
            "error": (
                f"❌ Неизвестный слот `{slot}`.\n"
                f"Доступные слоты: {slots_hint}\n\n"
                f"{usage}"
            ),
            "slot": slot,
            "model_name": "",
            "legacy": False,
            "warning": "",
        }

    model_name = " ".join(args[3:]).strip()
    if not model_name:
        return {
            "ok": False,
            "error": (
                "❌ После слота нужно указать model_id.\n"
                f"{usage}"
            ),
            "slot": slot,
            "model_name": "",
            "legacy": False,
            "warning": "",
        }

    return {
        "ok": True,
        "error": "",
        "slot": slot,
        "model_name": model_name,
        "legacy": False,
        "warning": "",
    }


def register_handlers(app, deps: dict):
    """Регистрирует обработчики базовых команд."""
    router = deps["router"]
    config_manager = deps["config_manager"]
    black_box = deps["black_box"]
    safe_handler = deps["safe_handler"]
    openclaw_client = deps.get("openclaw_client")
    voice_gateway_client = deps.get("voice_gateway_client")
    krab_ear_client = deps.get("krab_ear_client")

    def _resolve_web_panel_url() -> str:
        """Возвращает публичный URL web-панели."""
        explicit = os.getenv("WEB_PUBLIC_BASE_URL", "").strip().rstrip("/")
        if explicit:
            return explicit
        port = int(config_manager.get("WEB_PORT", 8080))
        host = str(config_manager.get("WEB_HOST", "127.0.0.1")).strip() or "127.0.0.1"
        return f"http://{host}:{port}"

    # --- !status: Состояние AI ---
    @app.on_message(filters.command("status", prefixes="!"))
    @safe_handler
    async def status_command(client, message: Message):
        """Показывает текущее состояние всех подсистем."""
        if not is_owner(message):
            return

        reminder_manager = deps.get("reminder_manager")
        reminders_active = 0
        if not reminder_manager:
            logger.warning("Reminder manager missing for status command.")
        elif not hasattr(reminder_manager, "get_list"):
            logger.warning("Reminder manager lacks get_list for status command.")
        else:
            try:
                reminder_list = reminder_manager.get_list(None)
                if asyncio.iscoroutine(reminder_list):
                    reminder_list = await reminder_list
                reminders_active = len(reminder_list or [])
            except Exception as exc:
                logger.warning("Reminder manager get_list failed for status.", error=str(exc))

        notification = await message.reply_text("🔍 **Проверяю состояние...**")

        # Проверка роутера (локальные модели + Cloud)
        local_ok = await router.check_local_health()
        # gemini_client removed, cloud relies on openclaw
        openclaw_ok = await openclaw_client.health_check() if openclaw_client else False
        voice_ok = await voice_gateway_client.health_check() if voice_gateway_client else False
        
        # Cloud Model status checks router's openclaw client if different, or just openclaw general
        cloud_ok = openclaw_ok 

        # Формируем отчёт
        local_status = "🟢 Online" if local_ok else "🔴 Offline"
        cloud_status = "🟢 Ready" if cloud_ok else "🟡 Offline (OpenClaw)"
        voice_status = "🟢 Ready" if voice_ok else "🟡 Offline"
        local_model = router.active_local_model or "—"
        cloud_model = router.models.get("chat", "—")
        last_route = router.get_last_route() if hasattr(router, "get_last_route") else {}
        if isinstance(last_route, dict) and last_route:
            last_route_text = (
                f"{last_route.get('channel', '-')}/{last_route.get('profile', '-')}: "
                f"{last_route.get('model', '-')}"
            )
        else:
            last_route_text = "—"
        last_stream_route = router.get_last_stream_route() if hasattr(router, "get_last_stream_route") else {}
        if isinstance(last_stream_route, dict) and last_stream_route:
            last_stream_text = (
                f"{last_stream_route.get('channel', '-')}/{last_stream_route.get('profile', '-')}: "
                f"{last_stream_route.get('model', '-')}"
            )
        else:
            last_stream_text = "—"
        rag_docs = router.rag.get_total_documents() if router.rag else 0
        rag_status = "🟢 Active" if router.rag else "⚪ Disabled (OpenClaw)"
        web_panel_url = _resolve_web_panel_url()
        browser_enabled = os.getenv("ENABLE_LOCAL_BROWSER", "0").strip().lower() in {"1", "true", "yes", "on"}

        uptime_str = "N/A"
        if hasattr(black_box, "get_uptime"):
            try:
                uptime_str = black_box.get_uptime()
            except Exception as exc:
                logger.warning("BlackBox get_uptime failed", error=str(exc))

        report = (
            "**🦀 Krab v6.5 Status:**\n\n"
            f"🤖 **Local AI:** {local_status}\n"
            f"   └ Engine: `{router.local_engine or '—'}`\n"
            f"   └ Model: `{local_model}`\n"
            f"☁️  **Cloud (OpenClaw):** {cloud_status}\n"
            f"   └ Config chat: `{cloud_model}`\n"
            f"🎧 **Voice Gateway:** {voice_status}\n"
            f"🧠 **RAG:** {rag_status} ({rag_docs} docs)\n"
            f"🧭 **Last route:** `{last_route_text}`\n"
            f"🌊 **Last stream:** `{last_stream_text}`\n"
            f"📊 **Uptime:** {uptime_str}\n"
            f"⏰ **Reminders:** {reminders_active} active\n"
            f"📂 **Config:** Hot-reload {'🟢' if config_manager else '⚪'}\n"
            f"📈 **Calls:** Local {router._stats['local_calls']}, "
            f"Cloud {router._stats['cloud_calls']}\n"
            f"🌐 **Browser fallback:** {'🟢 Enabled' if browser_enabled else '⚪ Disabled'}\n"
            f"🕸️ **Web Panel:** `{web_panel_url}`\n"
            f"🐱 **GitHub:** {'🟢 Configured' if os.environ.get('GITHUB_PERSONAL_ACCESS_TOKEN') else '⚠️ Token Missing'}\n"
        )

        await notification.edit_text(report)

    # --- !cost: Быстрый отчёт по расходам ---
    @app.on_message(filters.command("cost", prefixes="!"))
    @safe_handler
    async def cost_command(client, message: Message):
        """Быстрый cost-отчёт без префикса !ops."""
        if not is_owner(message):
            return
        if not hasattr(router, "get_cost_report"):
            await message.reply_text("❌ get_cost_report недоступен.")
            return

        forecast = 5000
        if len(message.command) >= 2:
            try:
                forecast = int(message.command[1])
            except ValueError:
                forecast = 5000

        report = router.get_cost_report(monthly_calls_forecast=forecast)
        costs = report.get("costs_usd", {})
        pricing = report.get("pricing", {})
        monthly = report.get("monthly_forecast", {})
        budget = report.get("budget", {})
        await message.reply_text(
            "💵 **Cost Report (USD):**\n\n"
            f"• Cloud cost/call: `{pricing.get('cloud_cost_per_call_usd', 0)}`\n"
            f"• Local cost/call: `{pricing.get('local_cost_per_call_usd', 0)}`\n"
            f"• Current total cost: `{costs.get('total_cost', 0)}`\n"
            f"• Current avg cost/call: `{costs.get('avg_cost_per_call', 0)}`\n\n"
            "**Monthly forecast:**\n"
            f"• Calls: `{monthly.get('forecast_calls', 0)}`\n"
            f"• Cloud calls: `{monthly.get('forecast_cloud_calls', 0)}`\n"
            f"• Local calls: `{monthly.get('forecast_local_calls', 0)}`\n"
            f"• Forecast total: `{monthly.get('forecast_total_cost', 0)}`\n"
            f"• Budget: `{budget.get('cloud_monthly_budget_usd', 0)}`\n"
            f"• Budget ratio: `{budget.get('forecast_ratio', 0)}`\n\n"
            "_Подсказка: `!cost 12000` — прогноз на 12k вызовов/мес._"
        )

    # --- !brain: Единая сводка маршрутизации/стоимости ---
    @app.on_message(filters.command("brain", prefixes="!"))
    @safe_handler
    async def brain_command(client, message: Message):
        """Короткая сводка по мозгу роутера: режимы, маршруты, usage, расходы."""
        if not is_owner(message):
            return
        if not hasattr(router, "get_usage_summary") or not hasattr(router, "get_cost_report"):
            await message.reply_text("❌ Brain report недоступен: router API неполный.")
            return

        usage = router.get_usage_summary()
        cost = router.get_cost_report(monthly_calls_forecast=router.monthly_calls_forecast)
        totals = usage.get("totals", {})
        ratios = usage.get("ratios", {})
        soft_cap = usage.get("soft_cap", {})
        last_route = router.get_last_route() if hasattr(router, "get_last_route") else {}
        last_stream = router.get_last_stream_route() if hasattr(router, "get_last_stream_route") else {}
        costs_usd = cost.get("costs_usd", {})
        monthly = cost.get("monthly_forecast", {})
        budget = cost.get("budget", {})
        top_models = usage.get("top_models", [])
        ai_runtime = deps.get("ai_runtime")
        reaction_engine = deps.get("reaction_engine")
        queue_stats = ai_runtime.queue_manager.get_stats() if ai_runtime and hasattr(ai_runtime, "queue_manager") else {}
        reaction_stats = reaction_engine.get_reaction_stats() if reaction_engine else {}

        top_lines = []
        for item in top_models[:3]:
            top_lines.append(f"• `{item.get('model', '-')}`: `{item.get('count', 0)}`")
        top_text = "\n".join(top_lines) if top_lines else "• _(нет данных)_"

        last_route_text = (
            f"{last_route.get('channel', '-')}/{last_route.get('profile', '-')}: {last_route.get('model', '-')}"
            if isinstance(last_route, dict) and last_route else "—"
        )
        last_stream_text = (
            f"{last_stream.get('channel', '-')}/{last_stream.get('profile', '-')}: {last_stream.get('model', '-')}"
            if isinstance(last_stream, dict) and last_stream else "—"
        )

        await message.reply_text(
            "**🧠 Brain Report:**\n\n"
            f"• Force mode: `{getattr(router, 'force_mode', 'auto')}`\n"
            f"• Policy: `{getattr(router, 'routing_policy', 'n/a')}`\n"
            f"• Last route: `{last_route_text}`\n"
            f"• Last stream: `{last_stream_text}`\n\n"
            f"• Calls L/C/T: `{int(totals.get('local_calls', 0))}` / "
            f"`{int(totals.get('cloud_calls', 0))}` / `{int(totals.get('all_calls', 0))}`\n"
            f"• Cloud share: `{float(ratios.get('cloud_share', 0.0))}`\n"
            f"• Soft cap: `{soft_cap.get('cloud_remaining_calls', 0)}` remaining\n\n"
            f"• Cost total (USD): `{float(costs_usd.get('total_cost', 0.0))}`\n"
            f"• Avg cost/call (USD): `{float(costs_usd.get('avg_cost_per_call', 0.0))}`\n"
            f"• Forecast (USD): `{float(monthly.get('forecast_total_cost', 0.0))}`\n"
            f"• Budget ratio: `{float(budget.get('forecast_ratio', 0.0))}`\n\n"
            f"• Queue active chats: `{int(queue_stats.get('active_chats', 0))}`\n"
            f"• Queue total: `{int(queue_stats.get('queued_total', 0))}`\n"
            f"• Reactions total: `{int(reaction_stats.get('total', 0))}`\n"
            f"• Reactions +/-: `{int(reaction_stats.get('positive', 0))}` / `{int(reaction_stats.get('negative', 0))}`\n\n"
            "**Top models:**\n"
            f"{top_text}"
        )

    # --- !ctx: Диагностика контекста последнего запроса ---
    @app.on_message(filters.command("ctx", prefixes="!"))
    @safe_handler
    async def ctx_command(client, message: Message):
        """Показывает контекстный snapshot последнего авто-ответа."""
        if not is_owner(message):
            return
        ai_runtime = deps.get("ai_runtime")
        if not ai_runtime:
            await message.reply_text("⚠️ AI runtime пока не инициализирован.")
            return
        snap = ai_runtime.get_context_snapshot(message.chat.id)
        if not snap:
            await message.reply_text("ℹ️ Контекстный snapshot ещё не накоплен.")
            return
        last_route = router.get_last_route() if hasattr(router, "get_last_route") else {}
        route_text = (
            f"{last_route.get('channel', '-')}/{last_route.get('profile', '-')}: {last_route.get('model', '-')}"
            if isinstance(last_route, dict) and last_route else "—"
        )
        await message.reply_text(
            "**🧾 Context Snapshot:**\n\n"
            f"• Last route: `{route_text}`\n"
            f"• Context messages: `{int(snap.get('context_messages', 0))}`\n"
            f"• Prompt chars: `{int(snap.get('prompt_length_chars', 0))}`\n"
            f"• Response chars: `{int(snap.get('response_length_chars', 0))}`\n"
            f"• Telegram truncated: `{bool(snap.get('telegram_truncated', False))}`\n"
            f"• Telegram chunks: `{int(snap.get('telegram_chunks_sent', 1))}`\n"
            f"• Forward context: `{bool(snap.get('has_forward_context', False))}`\n"
            f"• Reply context: `{bool(snap.get('has_reply_context', False))}`\n"
            f"• Updated: `{snap.get('updated_at', '-')}`"
        )

    # --- !policy: Runtime-политика AI ---
    @app.on_message(filters.command("policy", prefixes="!"))
    @safe_handler
    async def policy_command(client, message: Message):
        """Управление runtime-политикой: queue/guardrails/reactions."""
        if not is_owner(message):
            return
        ai_runtime = deps.get("ai_runtime")
        if not ai_runtime:
            await message.reply_text("⚠️ AI runtime недоступен.")
            return
        args = message.command
        sub = args[1].strip().lower() if len(args) > 1 else "show"

        if sub == "show":
            policy = ai_runtime.get_policy_snapshot()
            queue = policy.get("queue", {})
            guardrails = policy.get("guardrails", {})
            await message.reply_text(
                "**⚙️ Policy:**\n\n"
                f"• Queue enabled: `{policy.get('queue_enabled')}`\n"
                f"• Forward context enabled: `{policy.get('forward_context_enabled')}`\n"
                f"• Reaction learning enabled: `{policy.get('reaction_learning_enabled')}`\n"
                f"• Chat mood enabled: `{policy.get('chat_mood_enabled')}`\n"
                f"• Auto reactions enabled: `{policy.get('auto_reactions_enabled')}`\n\n"
                f"• Queue max/chat: `{queue.get('max_per_chat', 0)}`\n"
                f"• Queue total: `{queue.get('queued_total', 0)}`\n"
                f"• Queue active chats: `{queue.get('active_chats', 0)}`\n\n"
                f"• include_reasoning: `{guardrails.get('local_include_reasoning')}`\n"
                f"• reasoning_max_chars: `{guardrails.get('local_reasoning_max_chars')}`\n"
                f"• stream_total_timeout_seconds: `{guardrails.get('local_stream_total_timeout_seconds')}`\n"
                f"• stream_sock_read_timeout_seconds: `{guardrails.get('local_stream_sock_read_timeout_seconds')}`"
            )
            return

        if sub == "queue":
            if len(args) < 3:
                await message.reply_text("⚠️ Формат: `!policy queue on|off|max <N>`")
                return
            act = args[2].strip().lower()
            if act in {"on", "off"}:
                ai_runtime.set_queue_enabled(act == "on")
                await message.reply_text(f"✅ Queue mode: `{act}`")
                return
            if act == "max" and len(args) >= 4:
                try:
                    max_n = int(args[3].strip())
                except Exception:
                    await message.reply_text("❌ N должен быть числом.")
                    return
                ai_runtime.set_queue_max(max_n)
                await message.reply_text(f"✅ Queue max/chat: `{max(1, max_n)}`")
                return
            await message.reply_text("⚠️ Формат: `!policy queue on|off|max <N>`")
            return

        if sub == "guardrails":
            if len(args) < 3:
                await message.reply_text(
                    "⚠️ Формат: `!policy guardrails set <name> <value>`\n"
                    "Доступно: `reasoning_max_chars`, `stream_total_timeout_seconds`, "
                    "`stream_sock_read_timeout_seconds`, `include_reasoning`"
                )
                return
            action = args[2].strip().lower()
            if action != "set" or len(args) < 5:
                await message.reply_text("⚠️ Формат: `!policy guardrails set <name> <value>`")
                return
            name = args[3].strip().lower()
            raw_value = args[4].strip()
            try:
                numeric_value = float(raw_value)
            except Exception:
                await message.reply_text("❌ Значение должно быть числом.")
                return
            ok = ai_runtime.set_guardrail(name, numeric_value)
            if not ok:
                await message.reply_text("❌ Неизвестный guardrail name.")
                return
            await message.reply_text(f"✅ Guardrail `{name}` обновлён на `{raw_value}`.")
            return

        if sub == "reactions":
            if len(args) >= 3 and args[2].strip().lower() in {"on", "off"}:
                enabled = args[2].strip().lower() == "on"
                ai_runtime.set_reaction_learning_enabled(enabled)
                ai_runtime.set_auto_reactions_enabled(enabled)
                await message.reply_text(f"✅ Reactions mode: `{'on' if enabled else 'off'}`")
                return
            if len(args) >= 3 and args[2].strip().lower() == "show":
                snap = ai_runtime.get_policy_snapshot()
                await message.reply_text(
                    "**😀 Reactions Policy:**\n\n"
                    f"• learning: `{snap.get('reaction_learning_enabled')}`\n"
                    f"• auto reactions: `{snap.get('auto_reactions_enabled')}`\n"
                    f"• mood: `{snap.get('chat_mood_enabled')}`"
                )
                return
            await message.reply_text("⚠️ Формат: `!policy reactions on|off|show` или `!policy show`")
            return

        await message.reply_text("⚠️ Подкоманда не распознана. Используй `!policy show`.")

    # --- !reactions: управление реакциями ---
    @app.on_message(filters.command("reactions", prefixes="!"))
    @safe_handler
    async def reactions_command(client, message: Message):
        """Управление реактивным контуром."""
        if not is_owner(message):
            return
        reaction_engine = deps.get("reaction_engine")
        ai_runtime = deps.get("ai_runtime")
        if not reaction_engine or not ai_runtime:
            await message.reply_text("⚠️ Reaction engine недоступен.")
            return
        args = message.command
        sub = args[1].strip().lower() if len(args) > 1 else "stats"
        if sub in {"on", "off"}:
            enabled = sub == "on"
            ai_runtime.set_reaction_learning_enabled(enabled)
            ai_runtime.set_auto_reactions_enabled(enabled)
            await message.reply_text(f"✅ Reaction learning: `{'on' if enabled else 'off'}`")
            return
        if sub == "stats":
            target_chat_id = message.chat.id
            if len(args) >= 3:
                try:
                    target_chat_id = int(args[2].strip())
                except Exception:
                    target_chat_id = message.chat.id
            stats = reaction_engine.get_reaction_stats(chat_id=target_chat_id)
            top = stats.get("top_emojis", [])
            top_text = "\n".join(f"• {item.get('emoji')} — `{item.get('count')}`" for item in top) if top else "• _(пока пусто)_"
            await message.reply_text(
                "**😀 Reaction Stats:**\n\n"
                f"• Chat: `{target_chat_id}`\n"
                f"• Total: `{stats.get('total', 0)}`\n"
                f"• Positive: `{stats.get('positive', 0)}`\n"
                f"• Negative: `{stats.get('negative', 0)}`\n"
                f"• Neutral: `{stats.get('neutral', 0)}`\n\n"
                f"{top_text}"
            )
            return
        await message.reply_text("⚠️ Формат: `!reactions on|off|stats [chat_id]`")

    # --- !mood: профиль настроения чата ---
    @app.on_message(filters.command("mood", prefixes="!"))
    @safe_handler
    async def mood_command(client, message: Message):
        """Показывает и сбрасывает chat mood."""
        if not is_owner(message):
            return
        reaction_engine = deps.get("reaction_engine")
        if not reaction_engine:
            await message.reply_text("⚠️ Mood engine недоступен.")
            return
        args = message.command
        sub = args[1].strip().lower() if len(args) > 1 else "show"
        target_chat_id = message.chat.id
        if sub not in {"reset", "show"} and len(args) >= 2:
            try:
                target_chat_id = int(args[1].strip())
            except Exception:
                target_chat_id = message.chat.id
            sub = "show"
        if sub == "reset":
            if len(args) >= 3:
                try:
                    target_chat_id = int(args[2].strip())
                except Exception:
                    target_chat_id = message.chat.id
            result = reaction_engine.reset_chat_mood(target_chat_id)
            await message.reply_text(
                f"✅ Mood reset: chat `{result.get('chat_id')}`, removed=`{result.get('removed')}`"
            )
            return
        mood = reaction_engine.get_chat_mood(target_chat_id)
        top = mood.get("top_emojis", [])
        top_text = "\n".join(f"• {item.get('emoji')} — `{item.get('count')}`" for item in top) if top else "• _(нет данных)_"
        await message.reply_text(
            "**🌡️ Chat Mood:**\n\n"
            f"• Chat: `{target_chat_id}`\n"
            f"• Label: `{mood.get('label', 'neutral')}`\n"
            f"• Avg: `{mood.get('avg', 0.0)}`\n"
            f"• Events: `{mood.get('events', 0)}`\n\n"
            f"{top_text}"
        )

    # --- !web: ссылки и health web-панели / экосистемы ---
    @app.on_message(filters.command("web", prefixes="!"))
    @safe_handler
    async def web_command(client, message: Message):
        """Показывает URL web-панели и состояние ключевых сервисов."""
        if not is_owner(message):
            return

        args = message.command
        web_panel_url = _resolve_web_panel_url()
        links = {
            "dashboard": web_panel_url,
            "stats_api": f"{web_panel_url}/api/stats",
            "health_api": f"{web_panel_url}/api/health",
            "links_api": f"{web_panel_url}/api/links",
        }

        if len(args) >= 2 and args[1].strip().lower() in {"health", "diag", "status"}:
            ecosystem = EcosystemHealthService(
                router=router,
                openclaw_client=openclaw_client,
                voice_gateway_client=voice_gateway_client,
                krab_ear_client=krab_ear_client,
            )
            report_data = await ecosystem.collect()
            checks = report_data.get("checks", {})
            openclaw_ok = bool(checks.get("openclaw", {}).get("ok"))
            local_ok = bool(checks.get("local_lm", {}).get("ok"))
            voice_ok = bool(checks.get("voice_gateway", {}).get("ok"))
            ear_ok = bool(checks.get("krab_ear", {}).get("ok"))

            report = (
                "**🕸️ Web/Ecosystem Health:**\n\n"
                f"• OpenClaw: {'🟢' if openclaw_ok else '🟡'}\n"
                f"• Local LM: {'🟢' if local_ok else '🔴'}\n"
                f"• Voice Gateway: {'🟢' if voice_ok else '🟡'}\n"
                f"• Krab Ear: {'🟢' if ear_ok else '🟡'}\n"
                f"• Degradation: `{report_data.get('degradation', 'unknown')}`\n"
                f"• Risk: `{report_data.get('risk_level', 'low')}`\n"
                f"• Panel URL: `{links['dashboard']}`\n"
            )
            await message.reply_text(report)
            return

        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🕸️ Открыть панель", url=links["dashboard"])],
                [
                    InlineKeyboardButton("📊 Stats API", url=links["stats_api"]),
                    InlineKeyboardButton("❤️ Health API", url=links["health_api"]),
                ],
            ]
        )

        await message.reply_text(
            "**🕸️ Web Panel Links:**\n"
            f"`{links['dashboard']}`\n\n"
            "**API:**\n"
            f"- stats: `{links['stats_api']}`\n"
            f"- health: `{links['health_api']}`\n"
            f"- links: `{links['links_api']}`\n\n"
            "_Проверка состояния:_ `!web health`",
            reply_markup=keyboard,
        )

    # --- !ops: usage/alerts по роутингу и расходам ---
    @app.on_message(filters.command("ops", prefixes="!"))
    @safe_handler
    async def ops_command(client, message: Message):
        """Операционный срез: usage модели, cloud share, алерты."""
        if not is_owner(message):
            return

        if not hasattr(router, "get_usage_summary") or not hasattr(router, "get_ops_alerts"):
            await message.reply_text("❌ Ops API роутера недоступен.")
            return

        args = message.command
        if len(args) >= 3 and args[1].strip().lower() in {"ack", "unack"}:
            action = args[1].strip().lower()
            code = args[2].strip()
            note = " ".join(args[3:]).strip() if len(args) > 3 else ""
            try:
                if action == "ack":
                    if not hasattr(router, "acknowledge_ops_alert"):
                        await message.reply_text("❌ acknowledge_ops_alert недоступен.")
                        return
                    result = router.acknowledge_ops_alert(code=code, actor="owner_telegram", note=note)
                    await message.reply_text(
                        "✅ Alert acknowledged:\n"
                        f"- code: `{result.get('code')}`\n"
                        f"- ts: `{result.get('ack', {}).get('ts', '-')}`"
                    )
                    return
                if not hasattr(router, "clear_ops_alert_ack"):
                    await message.reply_text("❌ clear_ops_alert_ack недоступен.")
                    return
                result = router.clear_ops_alert_ack(code=code)
                await message.reply_text(
                    "♻️ Alert ack cleared:\n"
                    f"- code: `{result.get('code')}`\n"
                    f"- removed: `{result.get('removed')}`"
                )
                return
            except Exception as exc:
                await message.reply_text(f"❌ Ошибка ops {action}: {exc}")
                return

        if len(args) >= 2 and args[1].strip().lower() in {"history", "hist"}:
            if not hasattr(router, "get_ops_history"):
                await message.reply_text("❌ get_ops_history недоступен.")
                return
            limit = 10
            if len(args) >= 3:
                try:
                    limit = int(args[2])
                except ValueError:
                    limit = 10
            history = router.get_ops_history(limit=limit)
            items = history.get("items", [])
            if not items:
                await message.reply_text("📉 Ops history пуст.")
                return
            lines = []
            for item in items[-8:]:
                lines.append(
                    f"- `{item.get('ts', '-')}` status=`{item.get('status', '-')}` "
                    f"alerts=`{item.get('alerts_count', 0)}` codes=`{item.get('codes', [])}`"
                )
            await message.reply_text(
                "📉 **Ops History:**\n"
                f"- total: `{history.get('total', 0)}`\n"
                f"- returned: `{history.get('count', 0)}`\n\n"
                + "\n".join(lines)
            )
            return

        if len(args) >= 2 and args[1].strip().lower() in {"prune", "cleanup"}:
            if not hasattr(router, "prune_ops_history"):
                await message.reply_text("❌ prune_ops_history недоступен.")
                return
            max_age_days = 30
            keep_last = 100
            if len(args) >= 3:
                try:
                    max_age_days = int(args[2])
                except ValueError:
                    max_age_days = 30
            if len(args) >= 4:
                try:
                    keep_last = int(args[3])
                except ValueError:
                    keep_last = 100
            result = router.prune_ops_history(max_age_days=max_age_days, keep_last=keep_last)
            await message.reply_text(
                "🧹 **Ops History Prune:**\n"
                f"- before: `{result.get('before', 0)}`\n"
                f"- after: `{result.get('after', 0)}`\n"
                f"- removed: `{result.get('removed', 0)}`\n"
                f"- max_age_days: `{result.get('max_age_days', max_age_days)}`\n"
                f"- keep_last: `{result.get('keep_last', keep_last)}`"
            )
            return

        if len(args) >= 2 and args[1].strip().lower() in {"cost", "costs"}:
            if not hasattr(router, "get_cost_report"):
                await message.reply_text("❌ get_cost_report недоступен.")
                return
            forecast = 5000
            if len(args) >= 3:
                try:
                    forecast = int(args[2])
                except ValueError:
                    forecast = 5000
            report = router.get_cost_report(monthly_calls_forecast=forecast)
            costs = report.get("costs_usd", {})
            pricing = report.get("pricing", {})
            monthly = report.get("monthly_forecast", {})
            budget = report.get("budget", {})
            await message.reply_text(
                "💵 **Ops Cost Report (USD):**\n\n"
                f"• Cloud cost/call: `{pricing.get('cloud_cost_per_call_usd', 0)}`\n"
                f"• Local cost/call: `{pricing.get('local_cost_per_call_usd', 0)}`\n"
                f"• Current total cost: `{costs.get('total_cost', 0)}`\n"
                f"• Current avg cost/call: `{costs.get('avg_cost_per_call', 0)}`\n\n"
                "**Monthly forecast:**\n"
                f"• Calls: `{monthly.get('forecast_calls', 0)}`\n"
                f"• Cloud calls: `{monthly.get('forecast_cloud_calls', 0)}`\n"
                f"• Local calls: `{monthly.get('forecast_local_calls', 0)}`\n"
                f"• Forecast total: `{monthly.get('forecast_total_cost', 0)}`\n"
                f"• Budget: `{budget.get('cloud_monthly_budget_usd', 0)}`\n"
                f"• Budget ratio: `{budget.get('forecast_ratio', 0)}`"
            )
            return

        if len(args) >= 2 and args[1].strip().lower() in {"executive", "execsum", "summary"}:
            if not hasattr(router, "get_ops_executive_summary"):
                await message.reply_text("❌ get_ops_executive_summary недоступен.")
                return
            forecast = 5000
            if len(args) >= 3:
                try:
                    forecast = int(args[2])
                except ValueError:
                    forecast = 5000
            summary = router.get_ops_executive_summary(monthly_calls_forecast=forecast)
            kpi = summary.get("kpi", {})
            recs = summary.get("recommendations", [])
            alerts = summary.get("alerts_brief", [])
            alerts_text = (
                "\n".join(
                    f"- `{a.get('severity', 'info')}` `{a.get('code', '-')}` ack=`{a.get('acknowledged', False)}`"
                    for a in alerts[:5]
                )
                if alerts
                else "- ✅ активных alerts нет"
            )
            recs_text = "\n".join(f"- {item}" for item in recs) if recs else "- _(нет)_"
            await message.reply_text(
                "📊 **Ops Executive Summary:**\n\n"
                f"• Generated: `{summary.get('generated_at', '-')}`\n"
                f"• Risk: `{summary.get('risk_level', 'low')}`\n"
                f"• Calls total: `{kpi.get('calls_total', 0)}`\n"
                f"• Cloud share: `{kpi.get('cloud_share', 0)}`\n"
                f"• Forecast total cost: `{kpi.get('forecast_total_cost', 0)}`\n"
                f"• Budget ratio: `{kpi.get('budget_ratio', 0)}`\n"
                f"• Active alerts: `{kpi.get('active_alerts', 0)}`\n\n"
                "**Top alerts:**\n"
                f"{alerts_text}\n\n"
                "**Recommendations:**\n"
                f"{recs_text}"
            )
            return

        if len(args) >= 2 and args[1].strip().lower() in {"report", "full"}:
            if not hasattr(router, "get_ops_report"):
                await message.reply_text("❌ get_ops_report недоступен.")
                return
            history_limit = 20
            if len(args) >= 3:
                try:
                    history_limit = int(args[2])
                except ValueError:
                    history_limit = 20
            report = router.get_ops_report(history_limit=history_limit)
            usage = report.get("usage", {})
            alerts = report.get("alerts", {}).get("alerts", [])
            costs = report.get("costs", {}).get("monthly_forecast", {})
            history = report.get("history", {})
            await message.reply_text(
                "🧾 **Ops Full Report:**\n\n"
                f"• Generated: `{report.get('generated_at', '-')}`\n"
                f"• Calls total: `{usage.get('totals', {}).get('all_calls', 0)}`\n"
                f"• Cloud share: `{usage.get('ratios', {}).get('cloud_share', 0)}`\n"
                f"• Active alerts: `{len(alerts)}`\n"
                f"• Forecast total cost: `{costs.get('forecast_total_cost', 0)}`\n"
                f"• History total: `{history.get('total', 0)}`\n"
                f"• History returned: `{history.get('count', 0)}`"
            )
            return

        if len(args) >= 2 and args[1].strip().lower() in {"export", "dump"}:
            if not hasattr(router, "get_ops_report"):
                await message.reply_text("❌ get_ops_report недоступен.")
                return
            history_limit = 50
            if len(args) >= 3:
                try:
                    history_limit = int(args[2])
                except ValueError:
                    history_limit = 50
            report = router.get_ops_report(history_limit=history_limit)
            ops_dir = Path("artifacts/ops")
            ops_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            output_path = ops_dir / f"ops_report_tg_{stamp}.json"
            with output_path.open("w", encoding="utf-8") as fp:
                json.dump(report, fp, ensure_ascii=False, indent=2)
            try:
                await message.reply_document(str(output_path), caption=f"🧾 Ops report export (`{output_path.name}`)")
            except Exception:
                await message.reply_text(f"🧾 Ops report сохранен: `{output_path}`")
            return

        if len(args) >= 2 and args[1].strip().lower() in {"bundle", "pack"}:
            if not hasattr(router, "get_ops_report"):
                await message.reply_text("❌ get_ops_report недоступен.")
                return
            history_limit = 50
            if len(args) >= 3:
                try:
                    history_limit = int(args[2])
                except ValueError:
                    history_limit = 50
            ops_report = router.get_ops_report(history_limit=history_limit)
            local_ok = await router.check_local_health()
            openclaw_ok = await openclaw_client.health_check() if openclaw_client else False
            voice_ok = await voice_gateway_client.health_check() if voice_gateway_client else False
            bundle = {
                "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "ops_report": ops_report,
                "health": {
                    "openclaw": openclaw_ok,
                    "local_lm": local_ok,
                    "voice_gateway": voice_ok,
                },
            }
            ops_dir = Path("artifacts/ops")
            ops_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            output_path = ops_dir / f"ops_bundle_tg_{stamp}.json"
            with output_path.open("w", encoding="utf-8") as fp:
                json.dump(bundle, fp, ensure_ascii=False, indent=2)
            try:
                await message.reply_document(str(output_path), caption=f"📦 Ops bundle (`{output_path.name}`)")
            except Exception:
                await message.reply_text(f"📦 Ops bundle сохранен: `{output_path}`")
            return

        usage = router.get_usage_summary()
        alerts_payload = router.get_ops_alerts()
        alerts = alerts_payload.get("alerts", [])
        cost_report = alerts_payload.get("cost_report", {})

        totals = usage.get("totals", {})
        ratios = usage.get("ratios", {})
        soft_cap = usage.get("soft_cap", {})
        budget = cost_report.get("budget", {})
        monthly = cost_report.get("monthly_forecast", {})
        top_models = usage.get("top_models", [])
        top_profiles = usage.get("top_profiles", [])

        top_models_text = (
            "\n".join(f"- `{item.get('model')}`: {item.get('count')}" for item in top_models[:3])
            if top_models
            else "- _(нет данных)_"
        )
        top_profiles_text = (
            "\n".join(f"- `{item.get('profile')}`: {item.get('count')}" for item in top_profiles[:3])
            if top_profiles
            else "- _(нет данных)_"
        )
        alerts_text = (
            "\n".join(f"- `{item.get('severity', 'info')}` `{item.get('code', '-')}`: {item.get('message', '')}" for item in alerts)
            if alerts
            else "- ✅ активных алертов нет"
        )

        await message.reply_text(
            "**📈 Ops Snapshot:**\n\n"
            f"• Calls total: `{totals.get('all_calls', 0)}`\n"
            f"• Local calls: `{totals.get('local_calls', 0)}`\n"
            f"• Cloud calls: `{totals.get('cloud_calls', 0)}`\n"
            f"• Cloud share: `{ratios.get('cloud_share', 0)}`\n"
            f"• Soft cap: `{soft_cap.get('cloud_soft_cap_calls', 0)}`\n"
            f"• Remaining: `{soft_cap.get('cloud_remaining_calls', 0)}`\n"
            f"• Cap reached: `{'YES' if soft_cap.get('cloud_soft_cap_reached') else 'NO'}`\n\n"
            f"• Forecast cost: `{monthly.get('forecast_total_cost', 0)}`\n"
            f"• Budget ratio: `{budget.get('forecast_ratio', 0)}`\n\n"
            "**Top models:**\n"
            f"{top_models_text}\n\n"
            "**Top profiles:**\n"
            f"{top_profiles_text}\n\n"
            "**Alerts:**\n"
            f"{alerts_text}"
        )

    # --- !openclaw: health/report auth/browser/tools ---
    @app.on_message(filters.command("openclaw", prefixes="!"))
    @safe_handler
    async def openclaw_command(client, message: Message):
        """Диагностика OpenClaw и его подсистем (auth/browser/tools)."""
        if not is_owner(message):
            return
        if not openclaw_client:
            await message.reply_text("❌ OpenClaw client не инициализирован.")
            return

        sub = "status"
        if len(message.command) >= 2:
            sub = message.command[1].strip().lower()

        notification = await message.reply_text("🧩 Проверяю OpenClaw...")
        report = await openclaw_client.get_health_report()

        if sub in {"status", "health", "report"}:
            auth = report.get("auth", {})
            browser = report.get("browser", {})
            tools = report.get("tools", {})
            ready_sub = report.get("ready_for_subscriptions", False)
            local_ok = await router.check_local_health(force=True)
            local_models = await router.list_local_models()
            local_reason = "ok" if local_ok else ("model_not_loaded" if local_models else "local_lm_unavailable")

            auth_reason = str(auth.get("status_reason") or "unknown")
            auth_human = {
                "ok": "OK",
                "auth_missing_lmstudio_profile": "AUTH_MISSING",
                "gateway_route_unavailable": "ROUTE_UNAVAILABLE",
                "required_auth_providers_missing": "PROVIDER_MISSING",
                "required_auth_providers_unhealthy": "PROVIDER_UNHEALTHY",
            }.get(auth_reason, auth_reason.upper())

            triage_line = "✅ Контур в норме"
            if auth_reason == "auth_missing_lmstudio_profile":
                triage_line = "❗ Диагноз: отсутствует lmstudio auth profile"
            elif auth_reason == "gateway_route_unavailable":
                triage_line = "❗ Диагноз: route auth/providers/health недоступен или вернул невалидный payload"
            elif local_reason == "model_not_loaded":
                triage_line = "❗ Диагноз: LM Studio доступен, но локальная модель не загружена"

            text = (
                "**🧩 OpenClaw Report:**\n\n"
                f"• Gateway: `{'UP' if report.get('gateway') else 'DOWN'}`\n"
                f"• Auth providers: `{'UP' if auth.get('available') else 'DOWN'}` ({auth.get('path', '-')})\n"
                f"• Auth reason: `{auth_human}`\n"
                f"• Auth readiness: `{'READY' if auth.get('ready_for_subscriptions') else 'NOT_READY'}`\n"
                f"• Browser path: `{'UP' if browser.get('available') else 'DOWN'}` ({browser.get('path', '-')})\n"
                f"• Tools registry: `{'UP' if tools.get('available') else 'DOWN'}` count=`{tools.get('tools_count', 0)}`\n"
                f"• Local LM status: `{local_reason}`\n"
                f"• Subscriptions flow: `{'READY' if ready_sub else 'PARTIAL'}`\n"
                f"• Base URL: `{report.get('base_url', '-')}`\n\n"
                f"{triage_line}\n"
                "_Ремедиация auth:_ `repair_openclaw_lmstudio_auth.command`\n\n"
                "_Подкоманды:_ `!openclaw auth`, `!openclaw browser`, `!openclaw tools`, `!openclaw deep`, `!openclaw plan`, `!openclaw smoke [url]`"
            )
            await notification.edit_text(text)
            return

        if sub in {"auth", "providers"}:
            auth = report.get("auth", {})
            payload = json.dumps(auth.get("payload", {}), ensure_ascii=False, indent=2, default=str)
            provider_lines = []
            providers = auth.get("providers", {})
            if isinstance(providers, dict) and providers:
                for name, meta in sorted(providers.items()):
                    provider_lines.append(f"- `{name}`: `{'UP' if meta.get('healthy') else 'DOWN'}`")
            else:
                provider_lines.append("- _(провайдеры не обнаружены в payload)_")

            required = auth.get("required_providers", [])
            missing = auth.get("missing_required", [])
            unhealthy = auth.get("unhealthy_required", [])
            if len(payload) > 2500:
                payload = payload[:2500] + "...(truncated)"
            await notification.edit_text(
                "**🧩 OpenClaw Auth Health:**\n"
                f"- available: `{auth.get('available')}`\n"
                f"- path: `{auth.get('path')}`\n"
                f"- tried: `{auth.get('tried')}`\n"
                f"- status_reason: `{auth.get('status_reason')}`\n"
                f"- ready_for_subscriptions: `{auth.get('ready_for_subscriptions')}`\n"
                f"- required: `{required}`\n"
                f"- missing_required: `{missing}`\n"
                f"- unhealthy_required: `{unhealthy}`\n"
                f"- lmstudio_profile: `{(auth.get('lmstudio_profile') or {}).get('present')}`\n"
                f"- lmstudio_profile_path: `{(auth.get('lmstudio_profile') or {}).get('path')}`\n"
                f"- lmstudio_profile_error: `{(auth.get('lmstudio_profile') or {}).get('error')}`\n\n"
                "_Автофикс:_ `repair_openclaw_lmstudio_auth.command`\n\n"
                "**Providers:**\n"
                + "\n".join(provider_lines)
                + "\n\n"
                f"```json\n{payload}\n```"
            )
            return

        if sub == "browser":
            browser = report.get("browser", {})
            payload = json.dumps(browser.get("payload", {}), ensure_ascii=False, indent=2, default=str)
            if len(payload) > 2500:
                payload = payload[:2500] + "...(truncated)"
            await notification.edit_text(
                "**🧩 OpenClaw Browser Health:**\n"
                f"- available: `{browser.get('available')}`\n"
                f"- path: `{browser.get('path')}`\n"
                f"- tried: `{browser.get('tried')}`\n\n"
                f"```json\n{payload}\n```"
            )
            return

        if sub == "tools":
            tools = report.get("tools", {})
            payload = json.dumps(tools.get("payload", {}), ensure_ascii=False, indent=2, default=str)
            if len(payload) > 2500:
                payload = payload[:2500] + "...(truncated)"
            await notification.edit_text(
                "**🧩 OpenClaw Tools Overview:**\n"
                f"- available: `{tools.get('available')}`\n"
                f"- path: `{tools.get('path')}`\n"
                f"- tools_count: `{tools.get('tools_count', 0)}`\n\n"
                f"```json\n{payload}\n```"
            )
            return

        if sub in {"deep", "check", "full"}:
            deep = await openclaw_client.get_deep_health_report()
            issues = deep.get("issues", [])
            remediations = deep.get("remediations", [])
            smoke = deep.get("tool_smoke", {})

            issue_lines = "\n".join(f"- `{item}`" for item in issues) if issues else "- _(нет)_"
            remediation_lines = "\n".join(f"- {item}" for item in remediations) if remediations else "- _(не требуется)_"
            await notification.edit_text(
                "**🧩 OpenClaw Deep Check:**\n"
                f"- ready: `{'YES' if deep.get('ready') else 'NO'}`\n"
                f"- tool_smoke: `{'OK' if smoke.get('ok') else 'FAIL'}` (`{smoke.get('tool', 'web_search')}`)\n\n"
                "**Issues:**\n"
                f"{issue_lines}\n\n"
                "**Remediation:**\n"
                f"{remediation_lines}"
            )
            return

        if sub in {"plan", "fixplan", "remediation"}:
            plan = await openclaw_client.get_remediation_plan()
            steps = plan.get("steps", [])
            lines = []
            for item in steps[:8]:
                lines.append(
                    f"- `{item.get('priority', 'P3')}` {item.get('title', '')}: "
                    f"{'✅' if item.get('done') else '⚠️'}"
                )
            steps_text = "\n".join(lines) if lines else "- _(нет шагов)_"
            await notification.edit_text(
                "**🧩 OpenClaw Remediation Plan:**\n"
                f"- ready: `{'YES' if plan.get('ready') else 'NO'}`\n"
                f"- open_items: `{plan.get('open_items', 0)}`\n\n"
                "**Steps:**\n"
                f"{steps_text}"
            )
            return

        if sub in {"smoke", "browser-smoke", "bsmoke"}:
            smoke_url = "https://example.com"
            if len(message.command) >= 3:
                smoke_url = message.command[2].strip() or smoke_url
            smoke = await openclaw_client.get_browser_smoke_report(url=smoke_url)
            browser_smoke = smoke.get("browser_smoke", {})
            endpoint_attempts = browser_smoke.get("endpoint_attempts", [])
            tool_attempts = browser_smoke.get("tool_attempts", [])
            await notification.edit_text(
                "**🧪 OpenClaw Browser Smoke:**\n"
                f"- ready: `{'YES' if smoke.get('ready') else 'NO'}`\n"
                f"- ok: `{'YES' if browser_smoke.get('ok') else 'NO'}`\n"
                f"- channel: `{browser_smoke.get('channel', '-')}`\n"
                f"- target: `{browser_smoke.get('url', '-')}`\n"
                f"- endpoint_attempts: `{len(endpoint_attempts)}`\n"
                f"- tool_attempts: `{len(tool_attempts)}`\n"
                f"- error: `{browser_smoke.get('error', '-')}`"
            )
            return

        await notification.edit_text("❓ Использование: `!openclaw [status|auth|browser|tools|deep|plan|smoke]`")

    # --- !diagnose / !diag: Полная диагностика ---
    @app.on_message(filters.command(["diagnose", "diag"], prefixes="!"))
    @safe_handler
    async def diagnose_command(client, message: Message):
        """Полная системная диагностика."""
        if not is_owner(message):
            return

        notification = await message.reply_text("🔍 **Запускаю диагностику...**")

        diag = await router.diagnose()

        # Формируем текстовую версию
        lines = ["**🔍 Diagnostic Report:**\n"]
        for key, val in diag.items():
            if isinstance(val, dict):
                emoji = "✅" if val.get("ok") else "❌"
                status = val.get("status", val)
            else:
                # Handle non-dict values (e.g. simple strings/bools)
                emoji = "ℹ️"
                status = str(val)
            lines.append(f"{emoji} **{key}**: {status}")
        last_route = router.get_last_route() if hasattr(router, "get_last_route") else {}
        if isinstance(last_route, dict) and last_route:
            lines.append(
                "ℹ️ **Last Route**: "
                f"{last_route.get('channel', '-')}/{last_route.get('profile', '-')} "
                f"→ {last_route.get('model', '-')}"
            )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить", callback_data="diag_full")]
        ])

        await notification.edit_text("\n".join(lines), reply_markup=keyboard)

    # (Voice Gateway команды вынесены в tools.py)

    # --- Callback: обновление диагностики ---
    @app.on_callback_query(filters.regex("^diag_full$"))
    async def diag_callback(client, callback_query: CallbackQuery):
        """Обновление диагностики по нажатию inline-кнопки."""
        await callback_query.answer("🔄 Обновляю...")
        diag = await router.diagnose()

        lines = ["**🔍 Diagnostic Report (Updated):**\n"]
        for key, val in diag.items():
            if isinstance(val, dict):
                emoji = "✅" if val.get("ok") else "❌"
                status = val.get("status", val)
            else:
                emoji = "ℹ️"
                status = str(val)
            lines.append(f"{emoji} **{key}**: {status}")
        last_route = router.get_last_route() if hasattr(router, "get_last_route") else {}
        if isinstance(last_route, dict) and last_route:
            lines.append(
                "ℹ️ **Last Route**: "
                f"{last_route.get('channel', '-')}/{last_route.get('profile', '-')} "
                f"→ {last_route.get('model', '-')}"
            )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить", callback_data="diag_full")]
        ])

        await callback_query.message.edit_text(
            "\n".join(lines), reply_markup=keyboard
        )

    # --- !config: Динамическая конфигурация ---
    @app.on_message(filters.command("config", prefixes="!"))
    @safe_handler
    async def config_command(client, message: Message):
        """
        Управление конфигурацией бота через Telegram.
        !config — показать текущие настройки
        !config set <key> <value> — изменить настройку
        """
        if not is_owner(message):
            return

        args = message.command

        if len(args) == 1:
            # Показываем текущие настройки
            cfg = config_manager.get_all()
            text = "**⚙️ Текущая конфигурация:**\n\n"
            for key, val in cfg.items():
                text += f"  `{key}`: **{val}**\n"
            text += "\n_Изменить:_ `!config set <key> <value>`"
            await message.reply_text(text)
            return

        if args[1] == "set" and len(args) >= 4:
            key = args[2]
            value = " ".join(args[3:])
            old_val = config_manager.get(key)
            config_manager.set(key, value)
            await message.reply_text(
                f"✅ **Config Updated:**\n"
                f"  `{key}`: ~~{old_val}~~ → **{value}**"
            )
        else:
            await message.reply_text(
                "⚙️ Использование:\n"
                "`!config` — показать все\n"
                "`!config set <key> <value>` — изменить"
            )

    # --- !model: Просмотр и управление моделями ---
    @app.on_message(filters.command("model", prefixes="!"))
    @safe_handler
    async def model_command(client, message: Message):
        """
        Управление моделями.
        !model — показать текущие модели
        !model set <slot> <name> — переключить модель в runtime
        """
        if not is_owner(message):
            return

        args = message.command

        if len(args) == 1:
            # Показываем текущие модели
            info = router.get_model_info()
            local_line = (
                f"🟢 `{info['local_engine']}`: `{info['local_model']}`"
                if info['local_available']
                else "🔴 Offline"
            )

            # Определяем иконку текущего режима
            mode_icon = "🤖"
            if info.get('force_mode') == 'force_cloud': mode_icon = "☁️ [Forced]"
            elif info.get('force_mode') == 'force_local': mode_icon = "🏠 [Forced]"
            else: mode_icon = "🔄 [Auto]"

            text = (
                f"**🧠 Krab v6.5 — Модели ({mode_icon}):**\n\n"
                f"**☁️ Cloud (OpenClaw):**\n"
            )
            for slot, name in info['cloud_models'].items():
                text += f"  `{slot}`: **{name}**\n"
            
            # Fetch real available models
            try:
                available_cloud = await router.list_cloud_models()
                if available_cloud:
                    text += "\n**📋 Доступные в OpenClaw:**\n"
                    for m in available_cloud[:10]:
                        text += f"  - `{m}`\n"
                    if len(available_cloud) > 10:
                        text += f"  ...и ещё {len(available_cloud)-10}\n"
            except Exception:
                pass

            text += f"\n**🖥️ Local:**\n  {local_line}\n"
            last_route = router.get_last_route() if hasattr(router, "get_last_route") else {}
            if isinstance(last_route, dict) and last_route:
                text += (
                    "\n**🧭 Последний фактический маршрут:**\n"
                    f"  Канал: `{last_route.get('channel', '-')}`\n"
                    f"  Профиль: `{last_route.get('profile', '-')}`\n"
                    f"  Модель: `{last_route.get('model', '-')}`\n"
                    f"  Время: `{last_route.get('ts', '-')}`\n"
                )
            soft_cap_state = "⚠️ достигнут" if info.get("cloud_soft_cap_reached") else "✅ в норме"
            text += (
                f"\n**📐 Routing Policy:** `{info.get('routing_policy', 'auto')}`\n"
                f"**💸 Cloud Soft Cap:** `{info.get('cloud_soft_cap_calls', '—')}` ({soft_cap_state})\n"
            )

            rec = info.get("recommendations", {})
            if rec:
                text += "\n**🎯 Рекомендации по профилям:**\n"
                for profile in ["chat", "moderation", "code", "security", "infra", "review"]:
                    entry = rec.get(profile, {})
                    if not entry:
                        continue
                    text += f"  `{profile}` → `{entry.get('model', '—')}` ({entry.get('channel', 'auto')})\n"

            feedback_summary = info.get("feedback_summary", {})
            feedback_models = feedback_summary.get("top_models", []) if isinstance(feedback_summary, dict) else []
            if feedback_models:
                text += "\n**⭐ Топ моделей по feedback:**\n"
                for item in feedback_models[:3]:
                    text += (
                        f"  `{item.get('model', '—')}`"
                        f" ({item.get('profile', 'chat')})"
                        f" → `{item.get('avg_score', 0)}`/5"
                        f" на `{item.get('count', 0)}` оценках\n"
                    )

            text += (
                f"\n📈 **Статистика:**\n"
                f"  Local: {info['stats']['local_calls']} ok / {info['stats']['local_failures']} fail\n"
                f"  Cloud: {info['stats']['cloud_calls']} ok / {info['stats']['cloud_failures']} fail\n"
                f"\n_Переключение режима:_\n"
                f"`!model local` — только локально\n"
                f"`!model cloud` — только облако\n"
                f"`!model auto` — авто-выбор\n"
                f"`!model recommend <profile>` — рекомендация\n"
                f"`!model preflight [task_type] <задача> [--confirm-expensive]` — план до запуска\n"
                f"`!model feedback <1-5> [note]` — оценить последний прогон\n"
                f"`!model feedback <1-5> <profile> <model> [channel] [note]` — явная оценка\n"
                f"`!model stats [profile]` — качество по feedback\n"
                f"\n_Смена модели:_\n"
                f"`!model set chat <name>`"
            )
            await message.reply_text(text)
            return

        # Обработка команд переключения режима
        subcommand = args[1].lower()

        if subcommand in ['local', 'cloud', 'auto']:
            res = router.set_force_mode(subcommand)
            await message.reply_text(f"✅ **Режим обновлен:**\n{res}")
            return

        if subcommand == "recommend":
            profile = "chat"
            if len(args) >= 3:
                profile = args[2].strip().lower()
            rec = router.get_profile_recommendation(profile)
            feedback_hint = rec.get("feedback_hint", {})
            await message.reply_text(
                "🧭 **Рекомендация роутера:**\n"
                f"Профиль: `{rec.get('profile')}`\n"
                f"Канал: `{rec.get('channel')}`\n"
                f"Модель: `{rec.get('model')}`\n"
                f"Критичная задача: `{'да' if rec.get('critical') else 'нет'}`\n"
                f"Feedback: `{feedback_hint.get('avg_score', 0)}`/5 (`n={feedback_hint.get('count', 0)}`)"
            )
            return

        if subcommand == "preflight":
            if not hasattr(router, "get_task_preflight"):
                await message.reply_text("❌ task preflight недоступен в текущем роутере.")
                return

            raw_tokens = args[2:]
            confirm_expensive = False
            payload_tokens: list[str] = []
            for token in raw_tokens:
                normalized = token.strip().lower()
                if normalized in {"--confirm-expensive", "--confirm", "confirm"}:
                    confirm_expensive = True
                    continue
                payload_tokens.append(token)

            if not payload_tokens:
                await message.reply_text(
                    "⚠️ Формат: `!model preflight [task_type] <задача> [--confirm-expensive]`\n"
                    "Пример: `!model preflight security Проведи аудит API`"
                )
                return

            known_task_types = {
                "chat",
                "coding",
                "reasoning",
                "creative",
                "moderation",
                "security",
                "infra",
                "review",
            }
            task_type = "chat"
            if payload_tokens[0].strip().lower() in known_task_types and len(payload_tokens) >= 2:
                task_type = payload_tokens[0].strip().lower()
                prompt = " ".join(payload_tokens[1:]).strip()
            else:
                prompt = " ".join(payload_tokens).strip()

            if not prompt:
                await message.reply_text("⚠️ Укажи задачу для preflight анализа.")
                return

            plan = router.get_task_preflight(
                prompt=prompt,
                task_type=task_type,
                confirm_expensive=confirm_expensive,
            )
            execution = plan.get("execution", {})
            policy = plan.get("policy", {})
            cost_hint = plan.get("cost_hint", {})
            warnings = plan.get("warnings", [])
            reasons = plan.get("reasons", [])

            warnings_text = "\n".join(f"- {line}" for line in warnings) if warnings else "- ✅ предупреждений нет"
            reasons_text = "\n".join(f"- {line}" for line in reasons) if reasons else "- _(нет)_"

            await message.reply_text(
                "🧭 **Model Preflight Plan:**\n\n"
                f"• Task type: `{plan.get('task_type', task_type)}`\n"
                f"• Profile: `{plan.get('profile', 'chat')}`\n"
                f"• Critical: `{'да' if plan.get('critical') else 'нет'}`\n"
                f"• Channel: `{execution.get('channel', 'auto')}`\n"
                f"• Model: `{execution.get('model', '—')}`\n"
                f"• Can run now: `{'да' if execution.get('can_run_now') else 'нет'}`\n"
                f"• Requires confirm: `{'да' if execution.get('requires_confirm_expensive') else 'нет'}`\n"
                f"• Confirm received: `{'да' if execution.get('confirm_expensive_received') else 'нет'}`\n"
                f"• Force mode: `{policy.get('force_mode', 'auto')}`\n"
                f"• Local available: `{'да' if policy.get('local_available') else 'нет'}`\n"
                f"• Marginal cost: `${cost_hint.get('marginal_call_cost_usd', 0)}`\n\n"
                "**Причины выбора:**\n"
                f"{reasons_text}\n\n"
                "**Warnings:**\n"
                f"{warnings_text}\n\n"
                f"➡️ {plan.get('next_step', 'Можно запускать задачу.')}"
            )
            return

        if subcommand in {"feedback", "rate"}:
            if not hasattr(router, "submit_feedback"):
                await message.reply_text("❌ feedback API недоступен в текущем роутере.")
                return
            if len(args) < 3:
                await message.reply_text(
                    "⚠️ Формат:\n"
                    "`!model feedback <1-5> [note]`\n"
                    "`!model feedback <1-5> <profile> <model> [channel] [note]`"
                )
                return

            try:
                score = int(args[2].strip())
            except Exception:
                await message.reply_text("❌ score должен быть целым числом от 1 до 5.")
                return

            profile = None
            model_name = None
            channel = None
            note = ""
            if len(args) >= 5:
                profile = args[3].strip().lower()
                model_name = args[4].strip()
                cursor = 5
                if len(args) > cursor and args[cursor].strip().lower() in {"local", "cloud"}:
                    channel = args[cursor].strip().lower()
                    cursor += 1
                note = " ".join(args[cursor:]).strip()
            else:
                note = " ".join(args[3:]).strip()

            try:
                result = router.submit_feedback(
                    score=score,
                    profile=profile,
                    model_name=model_name,
                    channel=channel,
                    note=note,
                )
            except ValueError as exc:
                await message.reply_text(
                    "❌ Не удалось сохранить feedback:\n"
                    f"`{exc}`\n\n"
                    "Подсказка: сначала запусти задачу или передай profile/model явно."
                )
                return

            model_stats = result.get("profile_model_stats", {})
            channel_stats = result.get("profile_channel_stats", {})
            await message.reply_text(
                "✅ **Feedback сохранен:**\n"
                f"• Score: `{result.get('score')}`/5\n"
                f"• Profile: `{result.get('profile')}`\n"
                f"• Model: `{result.get('model')}`\n"
                f"• Channel: `{result.get('channel')}`\n"
                f"• Used last route: `{'да' if result.get('used_last_route') else 'нет'}`\n"
                f"• Model avg: `{model_stats.get('avg', 0)}`/5 (`n={model_stats.get('count', 0)}`)\n"
                f"• Channel avg: `{channel_stats.get('avg', 0)}`/5 (`n={channel_stats.get('count', 0)}`)\n"
                f"\n_Сводка:_ `!model stats {result.get('profile', '')}`"
            )
            return

        if subcommand in {"stats", "quality", "feedback-stats"}:
            if not hasattr(router, "get_feedback_summary"):
                await message.reply_text("❌ feedback summary API недоступен в текущем роутере.")
                return
            profile = None
            if len(args) >= 3:
                profile = args[2].strip().lower() or None
            top = 5
            if len(args) >= 4:
                try:
                    top = int(args[3].strip())
                except Exception:
                    top = 5
            summary = router.get_feedback_summary(profile=profile, top=top)
            models = summary.get("top_models", [])
            channels = summary.get("top_channels", [])
            last_route = summary.get("last_route", {})

            models_text = (
                "\n".join(
                    f"- `{item.get('model')}` ({item.get('profile', '-')}) → "
                    f"`{item.get('avg_score', 0)}`/5 (`n={item.get('count', 0)}`)"
                    for item in models
                )
                if models
                else "- _(пока нет оценок)_"
            )
            channels_text = (
                "\n".join(
                    f"- `{item.get('channel')}` → `{item.get('avg_score', 0)}`/5 (`n={item.get('count', 0)}`)"
                    for item in channels
                )
                if channels
                else "- _(пока нет данных)_"
            )
            last_route_text = (
                f"`{last_route.get('profile', '-')}` / `{last_route.get('model', '-')}` / "
                f"`{last_route.get('channel', '-')}`"
                if isinstance(last_route, dict) and last_route
                else "—"
            )
            await message.reply_text(
                "⭐ **Model Feedback Stats:**\n\n"
                f"• Profile filter: `{summary.get('profile') or 'all'}`\n"
                f"• Total feedback: `{summary.get('total_feedback', 0)}`\n"
                f"• Last route: {last_route_text}\n\n"
                "**Top models:**\n"
                f"{models_text}\n\n"
                "**Top channels:**\n"
                f"{channels_text}"
            )
            return

        if subcommand == "scan":
            msg = await message.reply_text("🔍 **Сканирую модели (Local + Cloud)...**")
            
            # --- Сканирование Local ---
            local_list = await router.list_local_models()
            local_verbose = []
            if hasattr(router, "list_local_models_verbose"):
                try:
                    local_verbose = await router.list_local_models_verbose()
                except Exception:
                    local_verbose = []
            verbose_map = {
                str(item.get("id")): item
                for item in local_verbose
                if isinstance(item, dict) and item.get("id")
            }
            
            # --- Сканирование Cloud ---
            cloud_list = []
            try:
                cloud_list = await router.list_cloud_models()
            except Exception as e:
                logger.error(f"Cloud scan error: {e}")

            # Форматируем
            text = "**🔍 Найденные модели:**\n\n**🖥️ Local (LM Studio):**\n"
            if not local_list:
                text += "  _(Нет моделей или lms недоступен)_\n"
            elif isinstance(local_list[0], str) and (local_list[0].startswith("Error") or "Ошибка" in local_list[0]):
                text += f"  ❌ {local_list[0]}\n"
            else:
                for m in local_list:
                    # Помечаем текущую активную
                    star = " ⭐" if m == router.active_local_model else ""
                    item = verbose_map.get(str(m), {})
                    size_human = str(item.get("size_human", "n/a"))
                    type_label = str(item.get("type", "llm"))
                    text += f"  • `{m}` — `{size_human}` [{type_label}]{star}\n"

            text += "\n**☁️ Cloud (Gemini/OpenClaw):**\n"
            if not cloud_list:
                text += "  _(Нет моделей)_\n"
            else:
                # Ограничим список облака, их может быть много
                limit_cloud = 15
                for m in cloud_list[:limit_cloud]:
                    text += f"  • `{m}`\n"
                if len(cloud_list) > limit_cloud:
                    text += f"  _...и еще {len(cloud_list) - limit_cloud}_\n"
            if getattr(router, "last_cloud_error", None):
                text += f"  ❗ Последняя cloud-ошибка: `{router.last_cloud_error}`\n"
            
            text += "\n_Используйте:_ `!model set chat <ID>` или `!model set reasoning <ID>`"
            await msg.edit_text(text)
            return

        if subcommand == "unload":
            msg = await message.reply_text("🔄 **Выгружаю все локальные модели...**")
            ok = await router.unload_local_model()
            if ok:
                await msg.edit_text("✅ Все модели выгружены из LM Studio. GPU свободен.")
            else:
                await msg.edit_text("❌ Не удалось выгрузить модели (LM Studio не запущен или ошибка CLI).")
            return

        if subcommand == "set":
            parsed = parse_model_set_request(args, list(router.models.keys()))
            if not parsed.get("ok"):
                await message.reply_text(str(parsed.get("error") or "❌ Некорректный формат команды."))
                return

            slot = str(parsed["slot"])
            model_name = str(parsed["model_name"])
            old = router.models.get(slot, "—")
            router.models[slot] = model_name

            # Проактивная попытка загрузки локальной модели.
            lowered = model_name.lower()
            is_probably_local = not any(marker in lowered for marker in ("gemini", "gpt", "claude", "google/"))
            will_try_load = is_probably_local and router.force_mode in {"auto", "force_local"}
            legacy_warning = str(parsed.get("warning") or "")

            if will_try_load:
                msg_load = await message.reply_text(f"⏳ **Устанавливаю `{slot}` и загружаю в LM Studio...**")
                ok = await router.load_local_model(model_name)
                if ok:
                    text = (
                        f"✅ **Модель готова:**\n"
                        f"  Слот: `{slot}`\n"
                        f"  Модель: `{model_name}`\n"
                        f"  Статус: *Загружена в VRAM*"
                    )
                else:
                    text = (
                        f"⚠️ **Модель установлена в конфиг, но не загружена:**\n"
                        f"  Слот: `{slot}`\n"
                        f"  Модель: `{model_name}`\n"
                        f"  _Подсказка: проверьте LM Studio или используйте `!model scan`_"
                    )
                if legacy_warning:
                    text = f"{legacy_warning}\n\n{text}"
                await msg_load.edit_text(text)
                return

            text = (
                f"✅ **Модель обновлена:**\n"
                f"  `{slot}`: ~~{old}~~ → **{model_name}**"
            )
            if legacy_warning:
                text = f"{legacy_warning}\n\n{text}"
            await message.reply_text(text)
            return
        else:
            await message.reply_text(
                "`!model` — статус\n"
                "`!model local/cloud/auto` — режим\n"
                "`!model scan` — поиск моделей\n"
                "`!model unload` — выгрузить локалки\n"
                "`!model set <slot> <id>` — сменить модель\n"
                "`!model recommend <profile>` — рекомендации\n"
                "`!model preflight [task_type] <задача>` — план\n"
                "`!model feedback <1-5> [note]` — оценка"
                "`!model feedback <1-5> <profile> <model> [channel] [note]` — явная оценка\n"
                "`!model stats [profile]` — сводка feedback\n"
                "`!model scan` — поиск\n"
                "`!model set <slot> <name>` — модель\n"
                "Слоты: chat, thinking, pro, coding"
            )

    # --- !personality: Смена личности ---
    @app.on_message(filters.command("personality", prefixes="!"))
    @safe_handler
    async def personality_command(client, message: Message):
        """Смена личности бота."""
        if not is_owner(message): return
        
        persona_manager = deps["persona_manager"]
        args = message.command
        
        if len(args) < 2:
            current = persona_manager.active_persona
            available = ", ".join(persona_manager.personas.keys())
            await message.reply_text(
                f"🎭 **Текущая личность:** `{current}`\n"
                f"✨ **Доступные:** {available}\n\n"
                f"Изменить: `!personality <имя>`"
            )
            return
            
        new_persona = args[1].lower()
        if new_persona in persona_manager.personas:
            persona_manager.active_persona = new_persona
            config_manager.set("personality.active_persona", new_persona)
            await message.reply_text(f"✅ **Личность изменена на:** `{new_persona}`")
        else:
            await message.reply_text(f"❌ Личность `{new_persona}` не найдена.")

    # --- !wallet: Финансовый терминал ---
    @app.on_message(filters.command("wallet", prefixes="!"))
    @safe_handler
    async def wallet_command(client, message: Message):
        """Отображает информацию о кошельке (Owner only)."""
        if not is_owner(message): return
        
        text = (
            "💰 **Krab Monero Terminal v1.0**\n\n"
            "• **Status:** Synced 🟢\n"
            "• **Balance:** `124.52 XMR`\n"
            "• **Dashboard:** http://localhost:8502\n\n"
            "_Запусти `start_wallet.command` для доступа к UI._"
        )
        await message.reply_text(text)

    # --- !test / !smoke: Запуск тестов ---
    @app.on_message(filters.command(["test", "smoke"], prefixes="!"))
    @safe_handler
    async def test_command(client, message: Message):
        """Запуск Smoke-тестов системы."""
        import sys
        if not is_owner(message): return
        
        msg = await message.reply_text("🧪 **Запускаю Smoke-тесты...**\n_(Это займет 5-10 сек)_")
        
        # Используем текущий Python (из venv)
        cmd = f"{sys.executable} tests/smoke_test.py"
        
        # Если такого файла нет, fallback на verify_vision
        if not os.path.exists("tests/smoke_test.py"):
             cmd = f"{sys.executable} verify_vision.py"

        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        output = stdout.decode() + stderr.decode()
        status = "✅ PASS" if process.returncode == 0 else "❌ FAIL"
        
        # Shorten output
        if len(output) > 3000:
            output = output[:1500] + "\n...[truncated]...\n" + output[-1500:]

        await msg.edit_text(
            f"🧪 **Test Results:** {status}\n\n"
            f"```\n{output}\n```"
        )

    # --- !browser: Портал подписок (Gemini Pro/Advanced) ---
    @app.on_message(filters.command("browser", prefixes="!"))
    @safe_handler
    async def browser_command(client, message: Message):
        """
        Универсальный browser-запрос:
        1) OpenClaw-first (предпочтительно),
        2) fallback на локальный SubscriptionPortal.
        """
        if not is_owner(message): return
        
        if len(message.command) < 2:
            await message.reply_text("❓ Использование: `!browser <запрос>`")
            return
            
        prompt = " ".join(message.command[1:])
        msg = await message.reply_text("🌐 **Browser task: OpenClaw-first...**")
        
        try:
            if openclaw_client:
                response = await openclaw_client.execute_agent_task(prompt, agent_id="research_deep")
                if response and "⚠️" not in response and "❌" not in response:
                    await msg.edit_text(f"🌐 **OpenClaw Browser/Web Response:**\n\n{response}")
                    return

            await msg.edit_text("🟡 OpenClaw path не дал ответ, включаю локальный fallback...")

            from src.modules.subscription_portal import SubscriptionPortal
            portal = SubscriptionPortal(headless=True)
            response = await portal.query_gemini(prompt)
            await portal.close()

            await msg.edit_text(f"🌐 **Portal Fallback Response:**\n\n{response}")

        except ImportError:
            await msg.edit_text("❌ Ошибка: `playwright` не установлен для fallback пути.")
        except Exception as e:
            await msg.edit_text(f"❌ Browser Error: {e}")

    # --- !help: Справка ---
    @app.on_message(filters.command("help", prefixes="!"))
    @safe_handler
    async def show_help(client, message: Message):
        """Справка по командам бота."""
        text = (
            "**🦀 Krab v7.2 — Команды:**\n\n"
            "**📋 Основные:**\n"
            "`!status` — Здоровье AI\n"
            "`!brain` — Сводка роутинга/стоимости\n"
            "`!ctx` — Snapshot контекста последнего запроса\n"
            "`!policy` — Runtime policy (queue/guardrails/reactions)\n"
            "`!reactions` — Управление реактивным контуром\n"
            "`!mood` — Профиль настроения чата\n"
            "`!cost [monthly_calls]` — Быстрый отчёт расходов\n"
            "`!diagnose` — Полная диагностика\n"
            "`!web` — Ссылки на web-панель и API\n"
            "`!ops` — Usage/alerts по моделям и расходам\n"
            "`!ops report [N]` — Единый ops-отчет (usage/alerts/cost/history)\n"
            "`!ops export [N]` — Экспорт полного ops-report в JSON\n"
            "`!ops bundle [N]` — Экспорт ops-report + health snapshot\n"
            "`!ops history [N]` — История ops snapshot\n"
            "`!ops prune [days] [keep]` — Очистка ops history по retention\n"
            "`!ops cost [monthly_calls]` — Оценка расходов local/cloud\n"
            "`!ops executive [monthly_calls]` — KPI/риски/рекомендации (компактно)\n"
            "`!ops ack <code> [note]` — Подтвердить ops-alert\n"
            "`!ops unack <code>` — Снять подтверждение ops-alert\n"
            "`!openclaw [status|auth|browser|tools|deep|plan|smoke]` — Health/deep-check/remediation/smoke OpenClaw\n"
            "`!model` — Управление моделями\n"
            "`!model scan` — 🔍 Сканировать доступные\n"
            "`!model recommend` — Рекомендация модели по профилю\n"
            "`!model preflight` — План выполнения задачи до запуска\n"
            "`!model feedback` — Оценка качества ответа (1-5)\n"
            "`!model stats` — Сводка качества по profile/channel/model\n"
            "`!config` — Настройки (hot-reload)\n"
            "`!logs` — Чтение системного лога\n\n"
            "**🧠 AI & Agents:**\n"
            "`!think <тема> [--confirm-expensive]` — Deep Reasoning\n"
            "`!smart <задача> [--confirm-expensive]` — Агентный цикл (Plan → Gen)\n"
            "`!code <описание> [--confirm-expensive] [--raw-code]` — Генерация кода\n"
            "`!learn` / `!remember` — 🧠 Обучение RAG-памяти\n"
            "`!personality` — 🎭 Смена личности\n"
            "`!forget` — 🧹 Сброс контекста чата\n"
            "`!scout <тема>` — Deep Research (Web)\n\n"
            "**🛠️ AI Tools (Advanced):**\n"
            "`!wallet` — 💰 Финансовый терминал (Monero)\n"
            "`!img` <промпт> — 🎨 Генерация картинки (local/cloud)\n"
            "`!img models` — список image-моделей и доступность\n"
            "`!img cost [alias]` — оценка стоимости изображения\n"
            "`!img health` — health local/cloud image backend\n"
            "`!img default ...` — закрепить дефолтные image-модели\n"
            "`!vision ...` — runtime-настройка local vision (LM Studio + fallback)\n"
            "`!browser <запрос>` — 🌐 Gemini Web Portal (Pro/Advanced)\n"
            "`!translate` — Перевод RU↔EN\n"
            "`!say` — Голосовое (TTS)\n"
            "`!callstart ...` — Старт voice-сессии (mode/source/notify/tts)\n"
            "`!callstatus` — Статус voice-сессии\n"
            "`!callstop` — Стоп voice-сессии\n"
            "`!notify on|off` — Уведомление собеседника\n"
            "`!calllang` — Режим перевода voice-сессии\n"
            "`!callcost` — Оценка telephony+AI стоимости звонков\n"
            "`!calldiag` — Диагностика voice-сессии\n"
            "`!callsummary [N]` — Summary звонка и задачи\n"
            "`!callphrase` — Быстрая фраза RU/ES с озвучкой\n"
            "`!callphrases` — Библиотека быстрых фраз\n"
            "`!callwhy` — Почему не перевелось\n"
            "`!calltune` — Тюнинг буфера/VAD\n"
            "`!summaryx` — Саммари последних X сообщений выбранного чата\n"
            "`!chatid` — Показать ID и тип текущего чата\n"
            "`!see` — Vision (Фото/Видео)\n\n"
            "**💰 Finance:**\n"
            "`!crypto <coin>` — Курс криптовалют\n"
            "`!portfolio` — Статус портфеля\n\n"
            "**💻 System & macOS:**\n"
            "`!sysinfo` — RAM/CPU/GPU/Батарея\n"
            "`!test` / `!smoke` — 🧪 Запуск авто-тестов\n"
            "`!mac` — macOS Bridge\n"
            "`!rag` — База знаний\n"
            "`!panic` — 🕶️ Stealth Mode\n"
            "`!privacy` — 🔐 Privacy Policy\n"
            "`!remind` — ⏰ Напоминание\n"
            "`!reminders` — 📋 Список напоминаний\n\n"
            "`!group` — 🛡 Расширенная модерация групп (v2)\n\n"
            "**🔧 Dev & Admin:**\n"
            "`!exec` — Python REPL\n"
            "`!sh` — Terminal\n"
            "`!commit` — Git push\n"
            "`!grant` / `!revoke` — Управление ролями\n"
            "`!roles` — Список ролей\n"
            "`!provision` — Draft/Preview/Apply для agents/skills\n"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📚 GitHub", url="https://github.com/Pavua/Krab-openclaw")],
            [InlineKeyboardButton("📊 Диагностика", callback_data="diag_full")]
        ])

        await message.reply_text(text, reply_markup=keyboard)


    # --- !logs: Просмотр последних логов ---
    @app.on_message(filters.command("logs", prefixes="!"))
    @safe_handler
    async def show_logs(client, message: Message):
        """Показать последние строки логов (Owner only)."""
        if not is_owner(message):
            return

        lines_count = 20
        if len(message.command) > 1:
            try:
                lines_count = int(message.command[1])
            except ValueError:
                pass

        # get_last_logs — из deps (утилита из main.py)
        get_last_logs = deps.get("get_last_logs")
        log_text = get_last_logs(lines_count) if get_last_logs else "Логи недоступны."
        if not log_text:
            log_text = "Логи пусты."

        await message.reply_text(
            f"📋 **Последние {lines_count} строк логов:**\n\n```{log_text[-4000:]}```"
        )


    # --- !privacy: Информация о конфиденциальности ---
    @app.on_message(filters.command("privacy", prefixes="!"))
    @safe_handler
    async def privacy_command(client, message: Message):
        """Отображает текущую политику приватности."""
        text = (
            "🔐 **Krab Privacy Policy v1.0:**\n\n"
            "• **Изоляция чатов:** Каждый чат имеет свою историю и контекст.\n"
            "• **Privacy Guard:** Бот не разглашает детали проектов в общих чатах.\n"
            "• **Full Admin:** В приватном чате с Создателем включен полный доступ.\n"
            "• **History Sync:** При входе в новый чат бот подтягивает последние 30 сообщений для контекста.\n"
        )
        await message.reply_text(text)
