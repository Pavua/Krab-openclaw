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

from pyrogram import filters, enums
from pyrogram.types import Message

from .auth import is_owner, is_superuser

import structlog
logger = structlog.get_logger(__name__)


def register_handlers(app, deps: dict):
    """Регистрирует системные обработчики."""
    router = deps["router"]
    security = deps["security"]
    safe_handler = deps["safe_handler"]
    tools = deps["tools"]

    async def _danger_audit(message: Message, action: str, status: str, details: str = ""):
        """Логирует опасные команды в Saved Messages и владельцу."""
        sender = message.from_user.username if message.from_user else "unknown"
        chat_title = message.chat.title or "private"
        payload = (
            f"🛡️ **Danger Audit**\n"
            f"- action: `{action}`\n"
            f"- status: `{status}`\n"
            f"- sender: `@{sender}`\n"
            f"- chat: `{chat_title}` (`{message.chat.id}`)\n"
        )
        if details:
            payload += f"- details: `{details[:800]}`\n"
        try:
            await app.send_message("me", payload)
        except Exception:
            pass
        try:
            await app.send_message("@p0lrd", payload)
        except Exception:
            pass

    # --- !sh: Терминал (Owner only) ---
    @app.on_message(filters.command(["sh", "terminal"], prefixes="!"))
    @safe_handler
    async def shell_command(client, message: Message):
        """Execution Shell: !sh <command> (Owner Only)"""
        if not is_superuser(message):
            logger.warning(
                f"⛔ Unauthorized shell attempt from @{message.from_user.username}"
            )
            return

        if message.chat.type != enums.ChatType.PRIVATE:
            await message.reply_text("⛔ `!sh` разрешен только в личных сообщениях.")
            await _danger_audit(message, "sh", "blocked", "non-private-chat")
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
        await _danger_audit(message, "sh", "ok", cmd[:300])

    # --- !commit: Git push ---
    @app.on_message(filters.command("commit", prefixes="!"))
    @safe_handler
    async def commit_command(client, message: Message):
        """Git commit & push: !commit [сообщение]"""
        if not is_superuser(message):
            return

        if message.chat.type != enums.ChatType.PRIVATE:
            await message.reply_text("⛔ `!commit` разрешен только в личных сообщениях.")
            await _danger_audit(message, "commit", "blocked", "non-private-chat")
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
        await _danger_audit(message, "commit", "ok", commit_msg[:300])

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
        if not is_superuser(message):
            return

        if message.chat.type != enums.ChatType.PRIVATE:
            await message.reply_text("⛔ `!refactor` разрешен только в личных сообщениях.")
            await _danger_audit(message, "refactor", "blocked", "non-private-chat")
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
            await _danger_audit(message, "refactor_audit", "ok", "audit")
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
            await _danger_audit(message, "refactor", "ok", target_file[:300])

    # --- !panic / !stealth: Режим секретности ---
    @app.on_message(filters.command(["panic", "stealth"], prefixes="!"))
    @safe_handler
    async def panic_command(client, message: Message):
        """Panic Button — мгновенная блокировка системы."""
        if not is_superuser(message):
            return

        if message.chat.type != enums.ChatType.PRIVATE:
            await message.reply_text("⛔ `!panic` разрешен только в личных сообщениях.")
            await _danger_audit(message, "panic", "blocked", "non-private-chat")
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
        await _danger_audit(message, "panic", "ok", f"stealth={is_stealth}")

    # --- !grant: Назначение ролей ---
    @app.on_message(filters.command("grant", prefixes="!"))
    @safe_handler
    async def grant_command(client, message: Message):
        """!grant @username <role> (admin/user/blocked)"""
        if not is_owner(message): return
        
        args = message.command
        if len(args) < 3:
            await message.reply_text("👮 Usage: `!grant @username <role>`")
            return
            
        target = args[1]
        role = args[2].lower()
        
        if role not in ["admin", "user", "guest", "blocked"]:
             await message.reply_text("❌ Invalid role. Use: admin, user, guest, blocked")
             return

        if security.grant_role(target, role):
            await message.reply_text(f"✅ Role **{role.upper()}** granted to `{target}`")
        else:
            await message.reply_text(f"❌ Failed to grant role to `{target}` (Owner protected?)")

    # --- !revoke: Снятие ролей ---
    @app.on_message(filters.command("revoke", prefixes="!"))
    @safe_handler
    async def revoke_command(client, message: Message):
        """!revoke @username"""
        if not is_owner(message): return
        
        if len(message.command) < 2:
            await message.reply_text("👮 Usage: `!revoke @username`")
            return
            
        target = message.command[1]
        if security.revoke_role(target):
            await message.reply_text(f"✅ Role revoked from `{target}` (now Guest)")
        else:
             await message.reply_text(f"❌ Failed to revoke `{target}`")

    # --- !godmode: Переход в God Mode (Native) ---
    @app.on_message(filters.command("godmode", prefixes="!"))
    @safe_handler
    async def godmode_launch_command(client, message: Message):
        """Native Launch: !godmode (Owner only)"""
        if not is_superuser(message):
            return

        if message.chat.type != enums.ChatType.PRIVATE:
            await message.reply_text("⛔ `!godmode` разрешен только в личных сообщениях.")
            await _danger_audit(message, "godmode", "blocked", "non-private-chat")
            return
        
        notification = await message.reply_text("🚀 **Активирую God Mode (Native macOS)...**")
        
        cmd_path = os.path.join(os.getcwd(), "start_god_mode.command")
        
        if not os.path.exists(cmd_path):
             await notification.edit_text("❌ Ошибка: файл `start_god_mode.command` не найден в корне проекта.")
             return

        # Запуск на macOS через open (открывает новое окно терминала)
        # Если мы в Docker, это сработает ТОЛЬКО если есть доступ к хосту (например, через shared socket или mount)
        # Однако, в God Mode native это просто удобный способ перезапуска/открытия нового окна.
        try:
            # Используем open для запуска .command файла (стандарт для macOS)
            import subprocess
            subprocess.Popen(["open", cmd_path], start_new_session=True)
            
            await notification.edit_text(
                "🚀 **God Mode запущен в новом окне терминала!**\n\n"
                "Если ты в Docker — убедись, что скрипт имеет доступ к хосту. "
                "В нативном режиме это просто откроет параллельную сессию."
            )
            await _danger_audit(message, "godmode", "ok", cmd_path)
        except Exception as e:
             await notification.edit_text(f"❌ Ошибка запуска: {e}")
             await _danger_audit(message, "godmode", "error", str(e))

    # --- !roles: Список ролей ---
    @app.on_message(filters.command("roles", prefixes="!"))
    @safe_handler
    async def roles_list_command(client, message: Message):
        """Show all user roles."""
        if not is_owner(message): return
        
        text = "**👮 User Roles:**\n\n"
        if not security.roles:
            text += "_No roles assigned (defaults only)._"
        else:
            for user, role in security.roles.items():
                emoji = {"admin": "⭐️", "blocked": "🚫", "user": "👤"}.get(role, "❔")
                text += f"{emoji} `{user}`: **{role.upper()}**\n"
        
        await message.reply_text(text)
