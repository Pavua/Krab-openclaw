# -*- coding: utf-8 -*-
"""
Provisioning Handler (Phase E).

Связь с экосистемой:
- использует `src/core/provisioning_service.py` как каноничный слой управления каталогами;
- дает владельцу поток: draft -> preview diff -> apply;
- не требует ручной правки YAML для агентов/skills.
"""

from pyrogram import filters, enums
from pyrogram.types import Message

from .auth import is_superuser


def register_handlers(app, deps: dict):
    """Регистрирует команды управления агентами/skills."""
    safe_handler = deps["safe_handler"]
    provisioning = deps.get("provisioning_service")

    @app.on_message(filters.command("provision", prefixes="!"))
    @safe_handler
    async def provision_command(client, message: Message):
        """Управление provisioning потоком (owner/superuser)."""
        if not is_superuser(message):
            return

        if not provisioning:
            await message.reply_text("❌ Provisioning service не инициализирован.")
            return

        args = message.command
        if len(args) < 2:
            await message.reply_text(
                "🧩 **Provisioning команды:**\n"
                "`!provision templates [agent|skill]`\n"
                "`!provision list [draft|applied]`\n"
                "`!provision draft <agent|skill> <name> <role> <описание>`\n"
                "`!provision preview <draft_id>`\n"
                "`!provision apply <draft_id> confirm`"
            )
            return

        subcommand = args[1].strip().lower()

        if subcommand in {"templates", "template"}:
            entity = "agent"
            if len(args) >= 3:
                entity = args[2].strip().lower()
            if entity not in {"agent", "skill"}:
                await message.reply_text("❌ Укажи `agent` или `skill`.")
                return

            templates = provisioning.list_templates(entity)
            if not templates:
                await message.reply_text("ℹ️ Шаблоны пока не заданы.")
                return

            text = f"🧱 **Шаблоны ({entity}):**\n"
            for item in templates:
                text += f"\n• `{item.get('role', 'unknown')}` — {item.get('description', '')}"
            await message.reply_text(text)
            return

        if subcommand == "list":
            status = None
            if len(args) >= 3:
                requested_status = args[2].strip().lower()
                if requested_status in {"draft", "applied"}:
                    status = requested_status

            drafts = provisioning.list_drafts(limit=15, status=status)
            if not drafts:
                await message.reply_text("📭 Драфты не найдены.")
                return

            text = "📚 **Последние provisioning draft'ы:**\n"
            for draft in drafts:
                text += (
                    f"\n• `{draft.get('draft_id', '-')}` "
                    f"[{draft.get('entity_type', '-')}] "
                    f"`{draft.get('name', '-')}` "
                    f"— {draft.get('status', 'draft')}"
                )
            await message.reply_text(text)
            return

        if subcommand in {"draft", "preview", "apply"}:
            if message.chat.type != enums.ChatType.PRIVATE:
                await message.reply_text("🔒 Команды provisioning доступны только в ЛС.")
                return

        if subcommand == "draft":
            if len(args) < 6:
                await message.reply_text(
                    "❌ Формат: `!provision draft <agent|skill> <name> <role> <описание>`"
                )
                return

            entity_type = args[2].strip().lower()
            name = args[3].strip()
            role = args[4].strip().lower()
            description = " ".join(args[5:]).strip()
            requested_by = (
                f"@{message.from_user.username}" if message.from_user and message.from_user.username
                else str(message.from_user.id if message.from_user else "unknown")
            )

            try:
                draft = provisioning.create_draft(
                    entity_type=entity_type,
                    name=name,
                    role=role,
                    description=description,
                    requested_by=requested_by,
                )
            except Exception as exc:
                await message.reply_text(f"❌ Не удалось создать draft: {exc}")
                return

            await message.reply_text(
                "✅ **Draft создан**\n"
                f"ID: `{draft.get('draft_id')}`\n"
                f"Type: `{draft.get('entity_type')}`\n"
                f"Name: `{draft.get('name')}`\n\n"
                "Дальше:\n"
                f"1) `!provision preview {draft.get('draft_id')}`\n"
                f"2) `!provision apply {draft.get('draft_id')} confirm`"
            )
            return

        if subcommand == "preview":
            if len(args) < 3:
                await message.reply_text("❌ Формат: `!provision preview <draft_id>`")
                return

            draft_id = args[2].strip()
            try:
                preview = provisioning.preview_diff(draft_id)
            except Exception as exc:
                await message.reply_text(f"❌ Не удалось построить preview: {exc}")
                return

            diff_text = preview.get("diff", "(diff пуст)")
            if len(diff_text) > 3200:
                diff_text = diff_text[:3200] + "\n... (обрезано)"

            await message.reply_text(
                f"🧪 **Preview diff**\n"
                f"Draft: `{draft_id}`\n"
                f"Entity: `{preview.get('draft', {}).get('entity_type')}`\n"
                f"Target: `{preview.get('draft', {}).get('name')}`\n"
                f"Update existing: `{'да' if preview.get('exists') else 'нет'}`\n\n"
                f"```diff\n{diff_text}\n```\n\n"
                f"**Что делать дальше:**\n"
                f"1) `!provision validate {draft_id}`\n"
                f"2) Если всё верно: `!provision apply {draft_id} confirm`"
            )
            return

        if subcommand == "validate":
            if len(args) < 3:
                await message.reply_text("❌ Формат: `!provision validate <draft_id>`")
                return

            draft_id = args[2].strip()
            try:
                report = provisioning.validate_draft(draft_id)
            except Exception as exc:
                await message.reply_text(f"❌ Ошибка валидации: {exc}")
                return

            status_emoji = "✅ PASS" if report["ok"] else "❌ FAIL"
            text = f"🛡️ **Provisioning Validation: {status_emoji}**\n"
            text += f"Draft: `{draft_id}`\n"
            
            if report["errors"]:
                text += "\n🛑 **Ошибки:**\n"
                for err in report["errors"]:
                    text += f"- {err}\n"
            
            if report["warnings"]:
                text += "\n⚠️ **Предупреждения:**\n"
                for warn in report["warnings"]:
                    text += f"- {warn}\n"
            
            text += f"\n👉 **Следующий шаг:** {report.get('next_step', '-')}"
            await message.reply_text(text)
            return

        if subcommand == "apply":
            if len(args) < 4:
                await message.reply_text("❌ Формат: `!provision apply <draft_id> confirm`")
                return

            draft_id = args[2].strip()
            confirmed = args[3].strip().lower() == "confirm"
            try:
                result = provisioning.apply_draft(draft_id=draft_id, confirmed=confirmed)
            except Exception as exc:
                await message.reply_text(f"❌ Apply завершился ошибкой: {exc}")
                return

            if result.get("status") == "already_applied":
                await message.reply_text(f"ℹ️ Draft `{draft_id}` уже был применен ранее.")
                return

            await message.reply_text(
                "✅ **Provisioning apply завершен**\n"
                f"Draft: `{result.get('draft_id')}`\n"
                f"Entity: `{result.get('entity_type')}`\n"
                f"Name: `{result.get('name')}`\n"
                f"Result: `{result.get('status')}`\n"
                f"Catalog: `{result.get('catalog_path', '-')}`\n\n"
                "**Что делать дальше:**\n"
                "Конфигурация обновлена. Чтобы изменения вступили в силу в OpenClaw, "
                "может потребоваться `!ops reload` (если предусмотрено runtime)."
            )
            return

        await message.reply_text("❓ Неизвестная sub-команда. См. `!provision`.")
