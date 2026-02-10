# -*- coding: utf-8 -*-
"""
System Handler — Системные команды: терминал, git, рефакторинг, panic.

Извлечён из main.py. Включает:
- !sh / !terminal: выполнение shell-команд (Owner only)
- !commit: git push
- !sysinfo / !system / !ram: системный монитор
- !refactor: саморефакторинг
- !panic / !stealth: режим секретности
"""

import os

from pyrogram import filters
from pyrogram.types import Message

from .auth import is_owner

import structlog
logger = structlog.get_logger(__name__)


def register_handlers(app, deps: dict):
    """Регистрирует системные обработчики."""
    router = deps["router"]
    security = deps["security"]
    safe_handler = deps["safe_handler"]
    tools = deps["tools"]

    # --- !sh: Терминал (Owner only) ---
    @app.on_message(filters.command(["sh", "terminal"], prefixes="!"))
    @safe_handler
    async def shell_command(client, message: Message):
        """Execution Shell: !sh <command> (Owner Only)"""
        if not is_owner(message):
            logger.warning(
                f"⛔ Unauthorized shell attempt from @{message.from_user.username}"
            )
            return

        if len(message.command) < 2:
            await message.reply_text("💻 Введи команду: `!sh ls -la`")
            return

        cmd = message.text.split(" ", 1)[1]
        notification = await message.reply_text("💻 **Выполняю...**")

        result = await tools.run_shell(cmd)

        # Обрезаем вывод для Telegram (лимит 4096 символов)
        if len(result) > 4000:
            result = result[:3900] + "\n...[Output Truncated]..."

        await notification.edit_text(f"💻 **Результат:**\n\n```\n{result}\n```")

    # --- !commit: Git push ---
    @app.on_message(filters.command("commit", prefixes="!"))
    @safe_handler
    async def commit_command(client, message: Message):
        """Git commit & push: !commit [сообщение]"""
        if not is_owner(message):
            return

        commit_msg = (
            " ".join(message.command[1:]) if len(message.command) > 1
            else "🦀 Auto-commit via Krab"
        )

        notification = await message.reply_text("📦 **Коммичу...**")

        # Последовательно: add → commit → push
        await tools.run_shell("git add -A")
        result = await tools.run_shell(f'git commit -m "{commit_msg}"')
        push_result = await tools.run_shell("git push")

        final = f"📦 **Git Push Complete:**\n\n```\n{result}\n{push_result}\n```"
        if len(final) > 4000:
            final = final[:3900] + "\n...[Truncated]..."

        await notification.edit_text(final)

    # --- !sysinfo: Системный монитор ---
    @app.on_message(filters.command(["sysinfo", "system", "ram"], prefixes="!"))
    @safe_handler
    async def sysinfo_command(client, message: Message):
        """Системный монитор: RAM, CPU, диск, GPU, батарея."""
        if not is_owner(message):
            return

        notification = await message.reply_text("🖥️ **Сканирую систему...**")

        try:
            from src.utils.system_monitor import SystemMonitor

            snapshot = SystemMonitor.get_snapshot()
            report = snapshot.format_report()

            # Инфо о процессе бота
            proc_info = SystemMonitor.get_process_info()
            report += (
                f"\n\n**🦀 Процесс Krab:**\n"
                f"  PID: {proc_info['pid']}\n"
                f"  RAM: {proc_info['ram_mb']:.0f} MB\n"
                f"  Потоки: {proc_info['threads']}\n"
                f"  Открытых файлов: {proc_info['open_files']}"
            )

            # Предупреждения
            warnings = []
            if snapshot.is_ram_critical():
                warnings.append("⚠️ **КРИТИЧНО:** RAM почти исчерпана!")
            if snapshot.is_disk_critical():
                warnings.append("⚠️ **КРИТИЧНО:** Диск почти заполнен!")

            if warnings:
                report += "\n\n" + "\n".join(warnings)

            await notification.edit_text(report)

        except Exception as e:
            await notification.edit_text(f"❌ Ошибка мониторинга: {e}")

    # --- !refactor: Саморефакторинг ---
    @app.on_message(filters.command("refactor", prefixes="!"))
    @safe_handler
    async def refactor_command(client, message: Message):
        """
        Саморефакторинг кода Krab.
        !refactor <file_path> [инструкции]
        !refactor audit — аудит безопасности
        """
        if not is_owner(message):
            return

        if len(message.command) < 2:
            await message.reply_text(
                "📋 Использование: `!refactor <путь_к_файлу> [инструкции]` "
                "или `!refactor audit`"
            )
            return

        from src.utils.self_refactor import SelfRefactor
        refactorer = SelfRefactor(os.getcwd())

        sub = message.command[1].lower()

        if sub == "audit":
            notification = await message.reply_text(
                "🕵️‍♂️ **Провожу аудит безопасности проекта...**"
            )
            report = await refactorer.find_vulnerabilities(router)
            await notification.edit_text(
                f"🕵️‍♂️ **Security Audit Report:**\n\n{report}"
            )
        else:
            target_file = sub
            instructions = (
                " ".join(message.command[2:]) if len(message.command) > 2
                else ""
            )

            notification = await message.reply_text(
                f"👨‍🔬 **Анализирую `{target_file}`...**"
            )

            proposal = await refactorer.analyze_and_propose(
                router, target_file, instructions
            )

            await notification.edit_text(
                f"👨‍🔬 **Предложение по рефакторингу `{target_file}`:**\n\n"
                f"{proposal}"
            )
            await message.reply_text(
                "💡 _Чтобы применить изменения, скопируйте код "
                "и используйте !sh или отредактируйте вручную._"
            )

    # --- !panic / !stealth: Режим секретности ---
    @app.on_message(filters.command(["panic", "stealth"], prefixes="!"))
    @safe_handler
    async def panic_command(client, message: Message):
        """Panic Button — мгновенная блокировка системы."""
        if not is_owner(message):
            return

        is_stealth = security.toggle_stealth()

        if is_stealth:
            from src.utils.mac_bridge import MacAutomation
            await MacAutomation.execute_intent(
                "notification",
                {"title": "🛡️ Krab Security", "message": "Stealth Mode Activated."},
            )

            await message.reply_text(
                "🕶️ **STEALTH MODE: ACTIVATED**\n\n"
                "• Все входящие запросы от посторонних будут игнорироваться.\n"
                "• Доступ ограничен только Владельцем.\n"
                "• Режим пониженной видимости."
            )
        else:
            await message.reply_text(
                "🔓 **STEALTH MODE: DEACTIVATED**\n\n"
                "• Стандартный режим работы восстановлен.\n"
                "• Уровни доступа (Admin/User) снова активны."
            )
