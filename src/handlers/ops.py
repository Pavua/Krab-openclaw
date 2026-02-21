
# -*- coding: utf-8 -*-
"""
Ops Handler (Sprint Block F).
Управление операционной деятельностью: аудит, сводки, здоровье стека.
"""

from __future__ import annotations

import asyncio
from pyrogram import enums, filters
from pyrogram.types import Message
from .auth import is_owner
import structlog

logger = structlog.get_logger(__name__)

def register_handlers(app, deps: dict):
    black_box = deps["black_box"]
    safe_handler = deps["safe_handler"]
    voice_client = deps.get("voice_gateway_client")
    provisioning = deps.get("provisioning_service")
    mod_engine = deps.get("group_moderation_engine")

    @app.on_message(filters.command("ops", prefixes="!"))
    @safe_handler
    async def ops_command(client, message: Message):
        if not is_owner(message):
            return

        args = message.command
        if len(args) < 2:
            await message.reply_text(
                "⚙️ **Operations Hub**\n"
                "- `!ops audit` — последние действия (mod, provis, logic)\n"
                "- `!ops summary` — сводка по всем подсистемам (v2)\n"
                "- `!ops health` — проверка доступности внешних сервисов"
            )
            return

        sub = args[1].lower()

        if sub == "audit":
            # Берем последние 10 событий из журнала событий (не сообщений)
            # В BlackBox должен быть метод get_recent_events
            events = []
            if hasattr(black_box, "get_recent_events"):
                events = black_box.get_recent_events(limit=10)
            
            if not events:
                # Fallback: берем из лог-файла или просто возвращаем пустоту
                await message.reply_text("📋 **Ops Audit Log**\n_Событий пока нет или журнал недоступен._")
                return

            text = "📋 **Ops Audit Log (Latest 10)**\n"
            text += "━━━━━━━━━━━━━━━━━━━━\n"
            for ev in events:
                ts = ev.get("timestamp", "n/a")
                etype = ev.get("event_type", "event")
                detail = ev.get("details", "")[:80]
                text += f"🕒 `{ts}` | **{etype}**\n└ `{detail}`\n"
            
            await message.reply_text(text)
            return

        if sub == "summary":
            # Сборка сводки
            v_status = "🟢 OK" if await voice_client.health_check() else "🔴 OFFLINE"
            
            # Модерация (кол-во активных групп с модерацией)
            mod_chats = len(mod_engine._store.get("chats", {})) if mod_engine else 0
            
            # Провижининг
            provis_count = len(provisioning.list_resources()) if provisioning else 0

            await message.reply_text(
                "📊 **Krab Ops Summary (v7.2)**\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"🎙 Voice Gateway: {v_status}\n"
                f"🛡 Group Moderation: `{mod_chats}` active policies\n"
                f"🏗 Provisioning: `{provis_count}` active resources\n"
                f"🖤 Black Box: `{black_box.get_stats().get('total', 0)}` messages logged\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"🚀 System state: **NOMINAL**"
            )
            return

        if sub == "health":
            # Проверка зависимостей
            results = []
            
            # 1. Voice Gateway
            vg_ok = await voice_client.health_check()
            results.append(f"{'✅' if vg_ok else '❌'} Voice Gateway")

            # 2. LM Studio (router)
            router = deps.get("router")
            if router:
                await router.check_local_health()
                results.append(f"{'✅' if router.is_local_available else '❌'} Local Brain (LM Studio)")

            # 3. OpenClaw
            oc = deps.get("openclaw_client")
            if oc:
                oc_ok = await oc.health_check()
                results.append(f"{'✅' if oc_ok else '❌'} OpenClaw API")

            res_text = "\n".join(results)
            await message.reply_text(f"🩺 **System Health Check**\n━━━━━━━━━━━━━━━━━━━━\n{res_text}")
            return

        await message.reply_text("❓ Неизвестная команда. Используй `!ops` для списка.")
