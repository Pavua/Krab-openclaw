# -*- coding: utf-8 -*-
"""
Commands Handler — Базовые команды бота: !status, !diagnose, !config, !help, !logs.

Извлечён из main.py (строки ~290-898). Отвечает за общую информацию
о состоянии бота, диагностику и конфигурацию.
"""

import os
from datetime import datetime

from pyrogram import filters, enums
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)

from .auth import is_owner

import structlog
logger = structlog.get_logger(__name__)


def register_handlers(app, deps: dict):
    """Регистрирует обработчики базовых команд."""
    router = deps["router"]
    config_manager = deps["config_manager"]
    black_box = deps["black_box"]
    safe_handler = deps["safe_handler"]

    # --- !status: Состояние AI ---
    @app.on_message(filters.command("status", prefixes="!"))
    @safe_handler
    async def status_command(client, message: Message):
        """Показывает текущее состояние всех подсистем."""
        if not is_owner(message):
            return

        notification = await message.reply_text("🔍 **Проверяю состояние...**")

        # Проверка роутера (локальные модели + Gemini)
        local_ok = await router.check_local_health()
        gemini_ok = router.gemini_client is not None

        # Формируем отчёт
        local_status = "🟢 Online" if local_ok else "🔴 Offline"
        gemini_status = "🟢 Ready" if gemini_ok else "🟡 Degraded"
        local_model = router.active_local_model or "—"
        cloud_model = router.models.get("chat", "—")

        report = (
            "**🦀 Krab v6.5 Status:**\n\n"
            f"🤖 **Local AI:** {local_status}\n"
            f"   └ Engine: `{router.local_engine or '—'}`\n"
            f"   └ Model: `{local_model}`\n"
            f"☁️  **Gemini:** {gemini_status}\n"
            f"   └ Model: `{cloud_model}`\n"
            f"🧠 **RAG:** 🟢 Active ({router.rag.get_total_documents()} docs)\n"
            f"📊 **Uptime:** {black_box.get_uptime()}\n"
            f"📂 **Config:** Hot-reload {'🟢' if config_manager else '⚪'}\n"
            f"📈 **Calls:** Local {router._stats['local_calls']}, "
            f"Cloud {router._stats['cloud_calls']}\n"
        )

        await notification.edit_text(report)

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
            emoji = "✅" if val.get("ok") else "❌"
            lines.append(f"{emoji} **{key}**: {val.get('status', val)}")

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить", callback_data="diag_full")]
        ])

        await notification.edit_text("\n".join(lines), reply_markup=keyboard)

    # --- Callback: обновление диагностики ---
    @app.on_callback_query(filters.regex("^diag_full$"))
    async def diag_callback(client, callback_query: CallbackQuery):
        """Обновление диагностики по нажатию inline-кнопки."""
        await callback_query.answer("🔄 Обновляю...")
        diag = await router.diagnose()

        lines = ["**🔍 Diagnostic Report (Updated):**\n"]
        for key, val in diag.items():
            emoji = "✅" if val.get("ok") else "❌"
            lines.append(f"{emoji} **{key}**: {val.get('status', val)}")

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

            text = (
                "**🧠 Krab v6.5 — Модели:**\n\n"
                f"**☁️ Cloud (Gemini):**\n"
            )
            for slot, name in info['cloud_models'].items():
                text += f"  `{slot}`: **{name}**\n"

            text += f"\n**🖥️ Local:**\n  {local_line}\n"
            text += (
                f"\n📈 **Статистика:**\n"
                f"  Local: {info['stats']['local_calls']} ok / {info['stats']['local_failures']} fail\n"
                f"  Cloud: {info['stats']['cloud_calls']} ok / {info['stats']['cloud_failures']} fail\n"
                f"\n_Изменить:_ `!model set chat gemini-2.5-flash`"
            )
            await message.reply_text(text)
            return

        if args[1] == "set" and len(args) >= 4:
            slot = args[2].lower()
            model_name = " ".join(args[3:])

            if slot not in router.models:
                await message.reply_text(
                    f"❌ Слот `{slot}` не найден.\n"
                    f"Доступные: {', '.join(router.models.keys())}"
                )
                return

            old = router.models[slot]
            router.models[slot] = model_name
            await message.reply_text(
                f"✅ **Модель обновлена:**\n"
                f"  `{slot}`: ~~{old}~~ → **{model_name}**"
            )
        else:
            await message.reply_text(
                "🧠 Использование:\n"
                "`!model` — показать все\n"
                "`!model set <slot> <name>` — изменить\n"
                "Слоты: chat, thinking, pro, coding"
            )

    # --- !help: Справка ---
    @app.on_message(filters.command("help", prefixes="!"))
    @safe_handler
    async def show_help(client, message: Message):
        """Справка по командам бота."""
        text = (
            "**🦀 Krab v6.5 — Команды:**\n\n"
            "**📋 Основные:**\n"
            "`!status` — Здоровье AI\n"
            "`!diagnose` — Полная диагностика\n"
            "`!model` — Модели (просмотр/переключение)\n"
            "`!config` — Настройки (hot-reload)\n"
            "`!logs` — Чтение системного лога\n"
            "`!help` — Справка\n\n"
            "**🧠 AI & Agents:**\n"
            "`!think <тема>` — Deep Reasoning\n"
            "`!smart <задача>` — Агентный цикл (Plan → Gen)\n"
            "`!code <описание>` — Генерация кода\n"
            "`!learn <факт>` — Обучение RAG-памяти\n"
            "`!scout <тема>` — Deep Research (Web)\n"
            "`!personality` — Смена личности\n"
            "`!summary` — Саммари чата\n\n"
            "**🛠️ AI Tools:**\n"
            "`!translate` — Перевод RU↔EN\n"
            "`!say` — Голосовое (TTS)\n"
            "`!see` — Screen Awareness\n"
            "📎 Документ → авто-анализ\n"
            "📹 Видео/кружок → AI-анализ\n\n"
            "**💻 System & macOS:**\n"
            "`!sysinfo` — RAM/CPU/GPU/Батарея\n"
            "`!mac` — macOS Bridge\n"
            "`!rag` — База знаний\n"
            "`!panic` — Stealth Mode\n\n"
            "**🔧 Dev (Owner):**\n"
            "`!exec` — Python REPL\n"
            "`!sh` — Terminal\n"
            "`!commit` — Git push\n"
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
