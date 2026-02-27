# -*- coding: utf-8 -*-
"""
Обработчики Telegram-команд, вынесенные из userbot_bridge (Фаза 4.4).
Каждая функция принимает (bot, message) для тестируемости и уплощения register_handlers.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

import httpx
from pyrogram.types import Message

from ..config import config
from ..core.exceptions import UserInputError
from ..core.lm_studio_health import is_lm_studio_available
from ..core.logger import get_logger
from ..employee_templates import ROLES, list_roles, save_role
from ..mcp_client import mcp_manager
from ..memory_engine import memory_manager
from ..model_manager import model_manager
from ..openclaw_client import openclaw_client
from ..search_engine import search_brave

logger = get_logger(__name__)

if TYPE_CHECKING:
    from ..userbot_bridge import KraabUserbot


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
    message.stop_propagation()


async def handle_remember(bot: "KraabUserbot", message: Message) -> None:
    """Запомнить факт."""
    text = bot._get_command_args(message)
    if not text:
        raise UserInputError(user_message="🧠 Что запомнить? Напиши: `!remember <текст>`")
    try:
        success = memory_manager.save_fact(text)
        if success:
            await message.reply(f"🧠 **Запомнил:** `{text}`")
        else:
            await message.reply("❌ Ошибка памяти.")
    except (ValueError, RuntimeError, OSError) as e:
        await message.reply(f"❌ Critical Memory Error: {e}")
    message.stop_propagation()


async def handle_recall(bot: "KraabUserbot", message: Message) -> None:
    """Вспомнить факт."""
    text = bot._get_command_args(message)
    if not text:
        raise UserInputError(user_message="🧠 Что вспомнить? Напиши: `!recall <запрос>`")
    try:
        facts = memory_manager.recall(text)
        if facts:
            await message.reply(f"🧠 **Вспомнил:**\n\n{facts}")
        else:
            await message.reply("🧠 Ничего не нашел по этому запросу.")
    except (ValueError, RuntimeError, OSError) as e:
        await message.reply(f"❌ Recalling Error: {e}")
    message.stop_propagation()


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
    message.stop_propagation()


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
    message.stop_propagation()


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
    message.stop_propagation()


async def handle_status(bot: "KraabUserbot", message: Message) -> None:
    """Статус системы и ресурсов."""
    ram = model_manager.get_ram_usage()
    is_ok = await openclaw_client.health_check()
    bar = "▓" * int(ram["percent"] / 10) + "░" * (10 - int(ram["percent"] / 10))
    text = f"""
🦀 **Системный статус Краба**
---------------------------
📡 **Gateway (OpenClaw):** {"✅ Online" if is_ok else "❌ Offline"}
🧠 **Модель:** `{config.MODEL}`
🎭 **Роль:** `{bot.current_role}`
🎙️ **Голос:** `{"ВКЛ" if bot.voice_mode else "ВЫКЛ"}`
💻 **RAM:** [{bar}] {ram["percent"]}%
"""
    if message.from_user and message.from_user.id == bot.me.id:
        await message.edit(text)
    else:
        await message.reply(text)


async def handle_model(bot: "KraabUserbot", message: Message) -> None:
    """Управление загрузкой AI моделей."""
    args = message.text.split()
    if len(args) < 2:
        await handle_status(bot, message)
        return
    cmd = args[1].lower()
    if cmd == "list":
        models = await model_manager.discover_models()
        lines = [f"{('☁️' if m.type.name == 'CLOUD_GEMINI' else '💻')} `{m.id}`" for m in models]
        await message.reply("**Доступные модели:**\n\n" + "\n".join(lines[:15]))
    elif cmd == "load" and len(args) > 2:
        mid = args[2]
        msg = await message.reply(f"⏳ Переключаюсь на `{mid}`...")
        if await model_manager.load_model(mid):
            config.update_setting("MODEL", mid)
            await msg.edit(f"✅ Успешно! Текущая модель: `{mid}`")
        else:
            await msg.edit(f"❌ Не удалось загрузить `{mid}`")


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
👤 **Владелец:** `{config.OWNER_USERNAME}`
🎯 **Триггеры:** `{", ".join(config.TRIGGER_PREFIXES)}`
🧠 **Память (RAM):** `{config.MAX_RAM_GB}GB`
"""
    await message.reply(text)


async def handle_set(bot: "KraabUserbot", message: Message) -> None:
    """Изменение настроек на лету."""
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        raise UserInputError(user_message="⚙️ `!set <KEY> <VAL>`")
    if config.update_setting(args[1], args[2]):
        await message.reply(f"✅ `{args[1]}` обновлено!")
    else:
        await message.reply("❌ Ошибка обновления.")


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


async def handle_voice(bot: "KraabUserbot", message: Message) -> None:
    """Переключение голосовых ответов."""
    bot.voice_mode = not bot.voice_mode
    await message.reply(f"🎙️ Голосовой режим: `{'ВКЛ' if bot.voice_mode else 'ВЫКЛ'}`")


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


async def handle_restart(bot: "KraabUserbot", message: Message) -> None:
    """Мягкая перезагрузка процесса."""
    await message.reply("🔄 Перезапускаюсь...")
    sys.exit(42)


async def handle_agent(bot: "KraabUserbot", message: Message) -> None:
    """Управление агентами: !agent new <name> <prompt>."""
    text = bot._get_command_args(message)
    if not text:
        raise UserInputError(
            user_message="🕵️‍♂️ Использование: `!agent new <имя> <промпт>`\nИли: `!agent list`"
        )
    if text.startswith("list"):
        await message.reply(f"🕵️‍♂️ **Доступные агенты:**\n\n{list_roles()}")
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
    message.stop_propagation()


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
