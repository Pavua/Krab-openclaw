# -*- coding: utf-8 -*-
"""
Plugin System Handler (Phase 13).
Управление динамическими плагинами.
"""

import os
from pyrogram import filters
from pyrogram.types import Message
from .auth import is_owner
import structlog

logger = structlog.get_logger(__name__)

def register_handlers(app, deps: dict):
    safe_handler = deps["safe_handler"]
    plugin_manager = deps["plugin_manager"]

    @app.on_message(filters.command("plugin", prefixes="!"))
    @safe_handler
    async def plugin_command(client, message: Message):
        """Управление плагинами: !plugin <load/unload/list> <name>"""
        if not is_owner(message): return
        
        args = message.command
        
        if len(args) < 2:
            await message.reply_text(
                "🧩 **Управление плагинами:**\n"
                "- `!plugin list`: список загруженных\n"
                "- `!plugin load <name>`: загрузить/обновить\n"
                "- `!plugin unload <name>`: выгрузить"
            )
            return

        cmd = args[1].lower()
        if cmd == "list":
            loaded = list(plugin_manager.plugins.keys())
            files = [f[:-3] for f in os.listdir("plugins") if f.endswith(".py")]
            resp = "🧩 **Плагины:**\n"
            if not files:
                resp += "_Папка plugins/ пуста_"
            for f in files:
                status = "✅" if f in loaded else "💤"
                resp += f"- {status} `{f}`\n"
            await message.reply_text(resp)
            
        elif cmd == "load":
            if len(args) < 3: return
            name = args[2]
            success = await plugin_manager.load_plugin(name, app, deps)
            await message.reply_text(f"🧩 Плагин `{name}`: {'Успешно' if success else 'Ошибка'}")

        elif cmd == "unload":
            if len(args) < 3: return
            name = args[2]
            success = await plugin_manager.unload_plugin(name)
            await message.reply_text(f"🔌 Плагин `{name}`: {'Выгружен' if success else 'Не найден'}")
