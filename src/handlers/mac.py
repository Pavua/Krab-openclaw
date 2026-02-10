# -*- coding: utf-8 -*-
"""
Mac Handler — macOS Automation Bridge.

Извлечён из main.py (строки ~1077-1175). Отвечает за:
- !mac battery, wifi, volume, mute, apps, open, quit, clipboard
- !mac notify, music, say, lock, url
"""

from pyrogram import filters
from pyrogram.types import Message

from .auth import is_owner

import structlog
logger = structlog.get_logger(__name__)


def register_handlers(app, deps: dict):
    """Регистрирует обработчик macOS Bridge."""
    safe_handler = deps["safe_handler"]

    @app.on_message(filters.command("mac", prefixes="!"))
    @safe_handler
    async def mac_command(client, message: Message):
        """
        macOS Automation: !mac <действие> [параметры]
        Примеры:
            !mac volume 50
            !mac notify Заголовок | Текст
            !mac apps
            !mac battery
        """
        if not is_owner(message):
            logger.warning(
                f"⛔ Unauthorized mac command attempt from @{message.from_user.username}"
            )
            return

        if len(message.command) < 2:
            help_text = (
                "**🍎 macOS Bridge — Команды:**\n\n"
                "`!mac battery` — Батарея\n"
                "`!mac wifi` — Текущая сеть\n"
                "`!mac volume <0-100>` — Громкость\n"
                "`!mac mute` — Без звука\n"
                "`!mac apps` — Запущенные приложения\n"
                "`!mac open <App>` — Открыть приложение\n"
                "`!mac quit <App>` — Закрыть приложение\n"
                "`!mac clipboard` — Буфер обмена\n"
                "`!mac notify <text>` — Уведомление\n"
                "`!mac music play/next/current` — Музыка\n"
                "`!mac say <text>` — Произнести вслух\n"
                "`!mac lock` — Заблокировать экран\n"
                "`!mac url <link>` — Открыть URL"
            )
            await message.reply_text(help_text)
            return

        action = message.command[1].lower()
        args = message.command[2:] if len(message.command) > 2 else []

        try:
            from src.utils.mac_bridge import MacAutomation
            mac = MacAutomation

            # Маппинг действий к методам MacAutomation
            if action == "battery":
                result = await mac.get_battery_status()
            elif action == "wifi":
                result = await mac.get_wifi_name()
            elif action == "volume":
                if args:
                    result = await mac.set_volume(int(args[0]))
                else:
                    result = await mac.get_volume()
            elif action == "mute":
                result = await mac.toggle_mute()
            elif action == "apps":
                result = await mac.list_running_apps()
            elif action == "open":
                result = await mac.open_app(" ".join(args))
            elif action == "quit":
                result = await mac.quit_app(" ".join(args))
            elif action == "clipboard":
                result = await mac.get_clipboard()
            elif action == "notify":
                text = " ".join(args)
                if "|" in text:
                    title, msg = text.split("|", 1)
                    result = await mac.send_notification(title.strip(), msg.strip())
                else:
                    result = await mac.send_notification("Krab", text)
            elif action == "music":
                sub = args[0] if args else "current"
                if sub in ("play", "pause", "toggle"):
                    result = await mac.music_play_pause()
                elif sub == "next":
                    result = await mac.music_next()
                else:
                    result = await mac.music_current()
            elif action == "say":
                result = await mac.say_text(" ".join(args))
            elif action == "lock":
                result = await mac.lock_screen()
            elif action == "url":
                result = await mac.open_url(" ".join(args))
            else:
                result = f"❌ Неизвестное действие: {action}"

            await message.reply_text(f"🍎 {result}")

        except Exception as e:
            await message.reply_text(f"❌ Ошибка macOS Bridge: {e}")
