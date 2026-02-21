# -*- coding: utf-8 -*-
"""
Group Management Handler (Phase C, moderation v2).

Что добавлено:
1) Rule-engine интеграция (dry-run, rule actions, banned words, caps/link checks).
2) Управление policy через `!group` команды.
3) Авто-действия warn/delete/mute/ban с аккуратным fallback и логированием.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from pyrogram import enums, filters
from pyrogram.types import ChatPermissions, Message

from .auth import is_owner

import structlog

logger = structlog.get_logger(__name__)


def register_handlers(app, deps: dict):
    """Регистрирует обработчики для управления группами."""
    black_box = deps["black_box"]
    safe_handler = deps["safe_handler"]
    moderation_engine = deps.get("group_moderation_engine")

    def _is_group_chat(message: Message) -> bool:
        return message.chat.type in {enums.ChatType.GROUP, enums.ChatType.SUPERGROUP}

    async def _send_temporary_notice(client, chat_id: int, text: str, ttl_sec: int = 8):
        notice = await client.send_message(chat_id, text)
        try:
            await asyncio.sleep(max(3, ttl_sec))
            await notice.delete()
        except Exception:
            pass

    @app.on_message(filters.command("group", prefixes="!"))
    @safe_handler
    async def group_command(client, message: Message):
        """Управление группой: !group <subcommand>."""
        if not is_owner(message):
            return

        if not _is_group_chat(message):
            await message.reply_text("❌ Эта команда работает только в группах.")
            return

        chat_id = message.chat.id
        args = message.command
        settings = black_box.get_group_settings(chat_id)
        policy = moderation_engine.get_policy(chat_id) if moderation_engine else {}

        if len(args) < 2:
            await message.reply_text(
                "🏘 **Управление группой (v2):**\n"
                "- `!group status`\n"
                "- `!group mod on/off`\n"
                "- `!group dryrun on/off`\n"
                "- `!group links on/off`\n"
                "- `!group caps <0.1..1.0>`\n"
                "- `!group action <link|banned_word|caps|repeated_chars> <none|warn|delete|mute|ban>`\n"
                "- `!group badword add <слово>`\n"
                "- `!group badword del <слово>`\n"
                "- `!group badword list`\n"
                "- `!group template <strict|balanced|lenient>`\n"
                "- `!group welcome <текст>`\n"
                "- `!group on/off`"
            )
            return

        sub = args[1].lower()

        if sub == "status":
            is_active = "✅ Активен" if settings.get("is_active", 1) else "❌ Выключен"
            mod = "🛡 ON" if settings.get("auto_moderation", 0) else "🔓 OFF"
            welcome = settings.get("welcome_message", "_Не задано_")
            banned_words = policy.get("banned_words", [])
            actions_json = json.dumps(policy.get("actions", {}), ensure_ascii=False)

            await message.reply_text(
                f"🏘 **Статус группы: {message.chat.title}**\n\n"
                f"🤖 Бот: {is_active}\n"
                f"🛡 Модерация: {mod}\n"
                f"🧪 Dry-run: {'ON' if policy.get('dry_run', True) else 'OFF'}\n"
                f"🔗 Block links: {'ON' if policy.get('block_links', True) else 'OFF'}\n"
                f"🔠 Max caps ratio: `{policy.get('max_caps_ratio', 0.72)}`\n"
                f"🚫 Banned words: `{len(banned_words)}`\n"
                f"🎛 Actions: `{actions_json}`\n"
                f"👋 Приветствие: {welcome}\n"
                f"🆔 CID: `{chat_id}`"
            )
            return

        if sub == "mod":
            if len(args) < 3:
                await message.reply_text("ℹ️ Использование: `!group mod on|off`")
                return
            val = 1 if args[2].lower() == "on" else 0
            black_box.set_group_setting(chat_id, "auto_moderation", val)
            await message.reply_text(f"🛡 Авто-модерация: {'ВКЛ' if val else 'ВЫКЛ'}")
            return

        if sub == "dryrun":
            if not moderation_engine:
                await message.reply_text("❌ Group Moderation Engine не инициализирован.")
                return
            if len(args) < 3:
                await message.reply_text("ℹ️ Использование: `!group dryrun on|off`")
                return
            enabled = args[2].lower() == "on"
            moderation_engine.update_policy(chat_id, {"dry_run": enabled})
            await message.reply_text(f"🧪 Dry-run: {'ON' if enabled else 'OFF'}")
            return

        if sub == "links":
            if not moderation_engine:
                await message.reply_text("❌ Group Moderation Engine не инициализирован.")
                return
            if len(args) < 3:
                await message.reply_text("ℹ️ Использование: `!group links on|off`")
                return
            enabled = args[2].lower() == "on"
            moderation_engine.update_policy(chat_id, {"block_links": enabled})
            await message.reply_text(f"🔗 Block links: {'ON' if enabled else 'OFF'}")
            return

        if sub == "caps":
            if not moderation_engine:
                await message.reply_text("❌ Group Moderation Engine не инициализирован.")
                return
            if len(args) < 3:
                await message.reply_text("ℹ️ Использование: `!group caps <0.1..1.0>`")
                return
            try:
                ratio = float(args[2])
            except ValueError:
                await message.reply_text("❌ Неверный формат числа.")
                return
            ratio = min(max(ratio, 0.1), 1.0)
            moderation_engine.update_policy(chat_id, {"max_caps_ratio": ratio})
            await message.reply_text(f"🔠 Max caps ratio обновлён: `{ratio}`")
            return

        if sub == "threshold":
            if not moderation_engine:
                await message.reply_text("❌ Group Moderation Engine не инициализирован.")
                return
            if len(args) < 3:
                await message.reply_text("ℹ️ Использование: `!group threshold <0.1..1.0>` (AI Guardian sensibility)")
                return
            try:
                val = float(args[2])
            except ValueError:
                await message.reply_text("❌ Неверный формат числа.")
                return
            val = min(max(val, 0.1), 1.0)
            moderation_engine.update_policy(chat_id, {"ai_guardian_threshold": val})
            await message.reply_text(f"🤖 AI Guardian threshold обновлён: `{val}`")
            return

        if sub == "action":
            if not moderation_engine:
                await message.reply_text("❌ Group Moderation Engine не инициализирован.")
                return
            if len(args) < 4:
                await message.reply_text(
                    "ℹ️ Использование: `!group action <link|banned_word|caps|repeated_chars> <none|warn|delete|mute|ban>`"
                )
                return
            rule = args[2].strip().lower()
            action = args[3].strip().lower()
            if rule not in {"link", "banned_word", "caps", "repeated_chars", "ai_guardian"}:
                await message.reply_text("❌ Rule неизвестен.")
                return
            if action not in {"none", "warn", "delete", "mute", "ban"}:
                await message.reply_text("❌ Action должен быть one of: none,warn,delete,mute,ban")
                return
            moderation_engine.update_policy(chat_id, {"actions": {rule: action}})
            await message.reply_text(f"🎛 Rule `{rule}` -> action `{action}`")
            return

        if sub == "badword":
            if not moderation_engine:
                await message.reply_text("❌ Group Moderation Engine не инициализирован.")
                return
            if len(args) < 3:
                await message.reply_text("ℹ️ Использование: `!group badword add|del|list [слово]`")
                return
            op = args[2].strip().lower()
            if op == "list":
                current = moderation_engine.get_policy(chat_id).get("banned_words", [])
                if not current:
                    await message.reply_text("✅ Список banned words пуст.")
                else:
                    rendered = "\n".join(f"- `{word}`" for word in current)
                    await message.reply_text(f"🚫 **Banned words:**\n{rendered}")
                return

            if len(args) < 4:
                await message.reply_text("❌ Укажи слово: `!group badword add spamword`")
                return
            word = " ".join(args[3:]).strip()
            if op == "add":
                policy = moderation_engine.add_banned_word(chat_id, word)
                await message.reply_text(f"✅ Добавлено. Всего banned words: `{len(policy.get('banned_words', []))}`")
                return
            if op in {"del", "remove", "rm"}:
                policy = moderation_engine.remove_banned_word(chat_id, word)
                await message.reply_text(f"🗑 Удалено. Всего banned words: `{len(policy.get('banned_words', []))}`")
                return

            await message.reply_text("❌ Используй `add`, `del` или `list`.")
            return

        if sub == "template":
            if not moderation_engine:
                await message.reply_text("❌ Group Moderation Engine не инициализирован.")
                return
            if len(args) < 3:
                names = ", ".join(moderation_engine.templates.keys())
                await message.reply_text(f"ℹ️ Использование: `!group template <{names}>`")
                return

            tpl_name = args[2].lower()
            try:
                moderation_engine.apply_template(chat_id, tpl_name)
                await message.reply_text(f"✅ Шаблон `{tpl_name}` применен.")
            except ValueError as exc:
                await message.reply_text(f"❌ {exc}")
            return

        if sub == "welcome":
            text = " ".join(args[2:]) if len(args) > 2 else ""
            black_box.set_group_setting(chat_id, "welcome_message", text)
            await message.reply_text("✅ Приветствие обновлено." if text else "🗑 Приветствие удалено.")
            return

        if sub == "debug":
            if not moderation_engine:
                await message.reply_text("❌ Group Moderation Engine не инициализирован.")
                return
            if len(args) < 3 or args[2].lower() != "policy":
                await message.reply_text("ℹ️ Использование: `!group debug policy`")
                return
            
            snapshot = moderation_engine.get_policy_debug_snapshot(chat_id)
            # Формируем компактный вывод
            policy = snapshot.get("effective_policy", {})
            actions = policy.get("actions", {})
            
            text = (
                f"🔍 **Debug Policy Snapshot**\n"
                f"🆔 CID: `{snapshot['chat_id']}`\n"
                f"🏷 Template: `{snapshot['template']}`\n"
                f"🧪 Dry-run: `{'ON' if snapshot['is_dry_run'] else 'OFF'}`\n"
                f"⚙️ Engine: `{snapshot['engine_version']}`\n\n"
                f"📊 **Effective Settings:**\n"
                f"- Max links: `{policy.get('max_links')}`\n"
                f"- Max caps: `{policy.get('max_caps_ratio')}`\n"
                f"- Actions: `{json.dumps(actions)}`"
            )
            await message.reply_text(text)
            return

        if sub == "on":
            black_box.set_group_setting(chat_id, "is_active", 1)
            await message.reply_text("✅ Бот активирован в этой группе.")
            return

        if sub == "off":
            black_box.set_group_setting(chat_id, "is_active", 0)
            await message.reply_text("💤 Бот теперь игнорирует сообщения в этой группе.")
            return

        await message.reply_text("❓ Неизвестная sub-команда. Используй `!group`.")

    # --- Приветствие новых участников ---
    @app.on_chat_member_updated()
    async def welcome_new_member(client, cms):
        """Приветствие новых участников."""
        if not cms.new_chat_member or cms.new_chat_member.status != "member":
            return

        if cms.old_chat_member and cms.old_chat_member.status == "member":
            return

        settings = black_box.get_group_settings(cms.chat.id)
        welcome_text = settings.get("welcome_message")

        if welcome_text and settings.get("is_active", 1):
            user = cms.new_chat_member.user
            mention = f"@{user.username}" if user.username else user.first_name
            formatted = welcome_text.replace("{user}", mention).replace("{title}", cms.chat.title)
            await client.send_message(cms.chat.id, formatted)

    # --- Авто-модерация v2 ---
    @app.on_message(filters.group & ~filters.me, group=1)
    async def auto_mod_handler(client, message: Message):
        """Авто-модерация с rule-engine и dry-run режимом."""
        if not moderation_engine:
            return

        chat_id = message.chat.id
        settings = black_box.get_group_settings(chat_id)

        if not settings.get("auto_moderation", 0) or not settings.get("is_active", 1):
            return

        if not message.from_user:
            return

        # Владелец и служебные сообщения не модерируем.
        if is_owner(message) or message.from_user.is_self:
            return

        text = message.text or message.caption or ""
        evaluation = await moderation_engine.evaluate_message(chat_id, text, message.entities)
        if not evaluation.get("matched"):
            return

        user_id = message.from_user.id
        username = message.from_user.username or str(user_id)
        action = evaluation.get("action", "warn")
        primary_rule = evaluation.get("primary_rule", "unknown")
        policy = evaluation.get("policy", {})
        reason = "; ".join(v.get("reason", "") for v in evaluation.get("violations", [])[:2])

        if evaluation.get("dry_run", True):
            explain = evaluation.get("explain", {})
            matched_rules = ", ".join(explain.get("matched_rules", []))
            await _send_temporary_notice(
                client,
                chat_id,
                f"🧪 **AutoMod DRY-RUN**: @{username}\n"
                f"🎯 **Rule:** `{primary_rule}` | 🧩 **All:** `[{matched_rules}]` | ⚡ **Action:** `{action}`\n"
                f"📝 **Reason:** {reason}",
                ttl_sec=int(policy.get("warn_ttl_sec", 8)),
            )
            black_box.log_event(
                "group_mod_dry_run",
                f"chat={chat_id} user={username} primary={primary_rule} rules=[{matched_rules}] action={action} reason={reason}",
            )
            return

        try:
            if action in {"delete", "mute", "ban"}:
                await message.delete()

            if action == "mute":
                until_date = datetime.now(timezone.utc) + timedelta(minutes=int(policy.get("mute_minutes", 15)))
                await client.restrict_chat_member(
                    chat_id,
                    user_id,
                    permissions=ChatPermissions(),
                    until_date=until_date,
                )

            if action == "ban":
                await client.ban_chat_member(chat_id, user_id)

            if action in {"warn", "delete", "mute", "ban"}:
                await _send_temporary_notice(
                    client,
                    chat_id,
                    f"🛡 **AutoMod**: @{username} rule=`{primary_rule}` action=`{action}`\n{reason}",
                    ttl_sec=int(policy.get("warn_ttl_sec", 8)),
                )

            black_box.log_event(
                "group_mod_action",
                f"chat={chat_id} user={username} rule={primary_rule} action={action} reason={reason}",
            )
            logger.info("AutoMod action applied", chat_id=chat_id, user=username, action=action, rule=primary_rule)

        except Exception as exc:
            logger.warning("AutoMod apply failed", error=str(exc), chat_id=chat_id, action=action)
