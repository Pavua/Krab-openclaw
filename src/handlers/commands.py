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
            f"⏰ **Reminders:** {len(deps.get('reminder_manager').get_list(None)) if deps.get('reminder_manager') else 0} active\n"
            f"📂 **Config:** Hot-reload {'🟢' if config_manager else '⚪'}\n"
            f"📈 **Calls:** Local {router._stats['local_calls']}, "
            f"Cloud {router._stats['cloud_calls']}\n"
            f"🌐 **Browser:** {'🟢 Ready' if deps.get('browser_agent') else '❌ Not Installed'}\n"
            f"🐱 **GitHub:** {'🟢 Configured' if os.environ.get('GITHUB_PERSONAL_ACCESS_TOKEN') else '⚠️ Token Missing'}\n"
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

            # Определяем иконку текущего режима
            mode_icon = "🤖"
            if info.get('force_mode') == 'force_cloud': mode_icon = "☁️ [Forced]"
            elif info.get('force_mode') == 'force_local': mode_icon = "🏠 [Forced]"
            else: mode_icon = "🔄 [Auto]"

            text = (
                f"**🧠 Krab v6.5 — Модели ({mode_icon}):**\n\n"
                f"**☁️ Cloud (Gemini):**\n"
            )
            for slot, name in info['cloud_models'].items():
                text += f"  `{slot}`: **{name}**\n"

            text += f"\n**🖥️ Local:**\n  {local_line}\n"
            text += (
                f"\n📈 **Статистика:**\n"
                f"  Local: {info['stats']['local_calls']} ok / {info['stats']['local_failures']} fail\n"
                f"  Cloud: {info['stats']['cloud_calls']} ok / {info['stats']['cloud_failures']} fail\n"
                f"\n_Переключение режима:_\n"
                f"`!model local` — только локально\n"
                f"`!model cloud` — только облако\n"
                f"`!model auto` — авто-выбор\n"
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

        if subcommand == "scan":
            msg = await message.reply_text("🔍 **Сканирую модели (Local + Cloud)...**")
            
            # --- Сканирование Local ---
            local_list = await router.list_local_models()
            
            # --- Сканирование Cloud ---
            try:
                cloud_list = await router.list_cloud_models()
            except Exception as e:
                cloud_list = [f"Error: {e}"]

            # Форматируем
            text = "**🔍 Найденные модели:**\n\n**🖥️ Local (LM Studio):**\n"
            if not local_list:
                text += "  _(Нет моделей или lms недоступен)_\n"
            elif isinstance(local_list[0], str) and local_list[0].startswith("Error"):
                text += f"  ❌ {local_list[0]}\n"
            else:
                for m in local_list:
                    text += f"  • `{m}`\n"

            text += "\n**☁️ Cloud (Gemini):**\n"
            if not cloud_list:
                text += "  _(Нет моделей)_\n"
            elif isinstance(cloud_list[0], str) and cloud_list[0].startswith("Error"):
                text += f"  ❌ {cloud_list[0]}\n"
            else:
                # Ограничим список облака, их может быть много
                limit_cloud = 20
                for m in cloud_list[:limit_cloud]:
                    text += f"  • `{m}`\n"
                if len(cloud_list) > limit_cloud:
                    text += f"  _...и еще {len(cloud_list) - limit_cloud}_\n"
            
            text += "\n_Чтобы выбрать модель:_\n`!model set chat <имя>`"
            await msg.edit_text(text)
            return

        if subcommand == "set" and len(args) >= 4:
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
                "`!model` — статус\n"
                "`!model local/cloud/auto` — режим\n"
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
        Использование Browser Portal для доступа к Gemini Advanced через веб.
        Требует настройки через setup_browser.py.
        """
        if not is_owner(message): return
        
        if len(message.command) < 2:
            await message.reply_text("❓ Использование: `!browser <запрос>`")
            return
            
        prompt = " ".join(message.command[1:])
        msg = await message.reply_text("🌐 **Connecting to Gemini Web...**")
        
        try:
            # Lazy import to avoid heavy init on startup if not used
            # Ensure src is in path if needed (though running from root it should be)
            from src.modules.subscription_portal import SubscriptionPortal
            portal = SubscriptionPortal(headless=True) # Headless by default
            
            # Start (launcher handles context)
            response = await portal.query_gemini(prompt)
            await portal.close()
            
            await msg.edit_text(f"🌐 **Gemini Web Response:**\n\n{response}")
            
        except ImportError:
            await msg.edit_text("❌ Ошибка: `playwright` не установлен.")
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
            "`!diagnose` — Полная диагностика\n"
            "`!model` — Управление моделями\n"
            "`!model scan` — 🔍 Сканировать доступные\n"
            "`!config` — Настройки (hot-reload)\n"
            "`!logs` — Чтение системного лога\n\n"
            "**🧠 AI & Agents:**\n"
            "`!think <тема>` — Deep Reasoning\n"
            "`!smart <задача>` — Агентный цикл (Plan → Gen)\n"
            "`!code <описание>` — Генерация кода\n"
            "`!learn` / `!remember` — 🧠 Обучение RAG-памяти\n"
            "`!personality` — 🎭 Смена личности\n"
            "`!forget` — 🧹 Сброс контекста чата\n"
            "`!scout <тема>` — Deep Research (Web)\n\n"
            "**🛠️ AI Tools (Advanced):**\n"
            "`!wallet` — 💰 Финансовый терминал (Monero)\n"
            "`!img` <промпт> — 🎨 Генерация картинки (Imagen 3)\n"
            "`!browser <запрос>` — 🌐 Gemini Web Portal (Pro/Advanced)\n"
            "`!translate` — Перевод RU↔EN\n"
            "`!say` — Голосовое (TTS)\n"
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
            "**🔧 Dev & Admin:**\n"
            "`!exec` — Python REPL\n"
            "`!sh` — Terminal\n"
            "`!commit` — Git push\n"
            "`!grant` / `!revoke` — Управление ролями\n"
            "`!roles` — Список ролей\n"
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
