# -*- coding: utf-8 -*-
"""
!chatpolicy — управление per-chat response policy (Smart Routing Phase 4).

Owner-only команда для управления режимами ответов и порогами в каждом чате.
См. docs/SMART_ROUTING_DESIGN.md (Component 6).

Subcommands:
    !chatpolicy                      — показать политику текущего чата
    !chatpolicy show <chat_id>       — политика конкретного чата
    !chatpolicy set <mode>           — silent/cautious/normal/chatty
    !chatpolicy threshold <0.0-1.0>  — manual override порога
    !chatpolicy threshold clear      — снять override
    !chatpolicy add-blocked-topic <topic>
    !chatpolicy clear-blocked-topic <topic>
    !chatpolicy stats [chat_id]      — счётчики signals
    !chatpolicy list                 — все чаты с custom policy
    !chatpolicy reset [chat_id]      — сброс к defaults
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyrogram.types import Message

from ...core.access_control import AccessLevel
from ...core.exceptions import UserInputError
from ...core.logger import get_logger

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)

_VALID_MODES = {"silent", "cautious", "normal", "chatty"}

_USAGE = (
    "📋 **!chatpolicy** — управление per-chat response policy (owner)\n"
    "```\n"
    "!chatpolicy                       — текущий чат\n"
    "!chatpolicy show <chat_id>        — конкретный чат\n"
    "!chatpolicy set <mode>            — silent|cautious|normal|chatty\n"
    "!chatpolicy threshold <0.0-1.0>   — порог override\n"
    "!chatpolicy threshold clear       — снять override\n"
    "!chatpolicy add-blocked-topic <t> — заблокировать тему\n"
    "!chatpolicy clear-blocked-topic <t>\n"
    "!chatpolicy stats [chat_id]       — счётчики\n"
    "!chatpolicy list                  — все custom policies\n"
    "!chatpolicy reset [chat_id]       — сброс\n"
    "```"
)


def _format_policy(policy) -> str:  # noqa: ANN001 — duck-type ChatResponsePolicy
    """Render policy summary."""
    blocked = ", ".join(policy.blocked_topics) if policy.blocked_topics else "—"
    override = (
        f"{policy.threshold_override:.2f}" if policy.threshold_override is not None else "(default)"
    )
    return (
        f"📋 **Chat Policy** `{policy.chat_id}`\n"
        f"• Mode: `{policy.mode.value}`\n"
        f"• Threshold: `{policy.effective_threshold():.2f}` (override: {override})\n"
        f"• Negative signals: `{policy.negative_signals}`\n"
        f"• Positive signals: `{policy.positive_signals}`\n"
        f"• Auto-adjust: {'on' if policy.auto_adjust_enabled else 'off'}\n"
        f"• Blocked topics: {blocked}"
    )


def _format_stats(policy) -> str:  # noqa: ANN001
    return (
        f"📊 **Stats** `{policy.chat_id}` ({policy.mode.value})\n"
        f"• Negatives: `{policy.negative_signals}`"
        f" (last: {policy.last_negative_ts or '—'})\n"
        f"• Positives: `{policy.positive_signals}`"
        f" (last: {policy.last_positive_ts or '—'})\n"
        f"• Last auto-adjust: {policy.last_auto_adjust_ts or '—'}"
    )


async def handle_chatpolicy(bot: "KraabUserbot", message: Message) -> None:
    """Owner-only !chatpolicy router."""
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="❌ Только owner.")

    from ...core.chat_response_policy import ChatMode, get_store

    store = get_store()
    raw = (message.text or "").strip()
    parts = raw.split()
    sub = parts[1].lower() if len(parts) >= 2 else ""
    chat_id = str(message.chat.id)

    # ── show / default ──────────────────────────────────────
    if sub in ("", "show", "current"):
        target = parts[2] if sub == "show" and len(parts) >= 3 else chat_id
        policy = store.get_policy(target)
        await message.reply(_format_policy(policy))
        return

    # ── set <mode> ──────────────────────────────────────────
    if sub == "set":
        if len(parts) < 3:
            raise UserInputError(user_message=f"❌ Укажи режим. {_USAGE}")
        mode = parts[2].lower()
        if mode not in _VALID_MODES:
            raise UserInputError(
                user_message=f"❌ Режим должен быть один из: {', '.join(sorted(_VALID_MODES))}"
            )
        policy = store.update_policy(chat_id, mode=ChatMode(mode))
        await message.reply(f"✅ Режим установлен: `{policy.mode.value}`")
        return

    # ── threshold ───────────────────────────────────────────
    if sub == "threshold":
        if len(parts) < 3:
            raise UserInputError(user_message="❌ Укажи значение `0.0-1.0` или `clear`.")
        arg = parts[2].lower()
        if arg == "clear":
            policy = store.update_policy(chat_id, threshold_override=None)
            await message.reply(
                f"✅ Override снят. Эффективный порог: `{policy.effective_threshold():.2f}`"
            )
            return
        try:
            value = float(arg)
        except ValueError:
            raise UserInputError(
                user_message="❌ Порог должен быть числом 0.0-1.0 или `clear`."
            ) from None
        if not 0.0 <= value <= 1.0:
            raise UserInputError(user_message="❌ Порог вне диапазона 0.0-1.0.")
        policy = store.update_policy(chat_id, threshold_override=value)
        await message.reply(f"✅ Threshold override: `{policy.threshold_override:.2f}`")
        return

    # ── add-blocked-topic ───────────────────────────────────
    if sub == "add-blocked-topic":
        if len(parts) < 3:
            raise UserInputError(user_message="❌ Укажи topic.")
        topic = " ".join(parts[2:]).strip().lower()
        policy = store.get_policy(chat_id)
        topics = list(policy.blocked_topics)
        if topic in topics:
            await message.reply(f"ℹ️ Topic `{topic}` уже в blocked.")
            return
        topics.append(topic)
        store.update_policy(chat_id, blocked_topics=topics)
        await message.reply(f"✅ Topic `{topic}` добавлен в blocked.")
        return

    # ── clear-blocked-topic ─────────────────────────────────
    if sub == "clear-blocked-topic":
        if len(parts) < 3:
            raise UserInputError(user_message="❌ Укажи topic.")
        topic = " ".join(parts[2:]).strip().lower()
        policy = store.get_policy(chat_id)
        topics = [t for t in policy.blocked_topics if t != topic]
        if len(topics) == len(policy.blocked_topics):
            await message.reply(f"ℹ️ Topic `{topic}` не был в blocked.")
            return
        store.update_policy(chat_id, blocked_topics=topics)
        await message.reply(f"✅ Topic `{topic}` убран из blocked.")
        return

    # ── stats ───────────────────────────────────────────────
    if sub == "stats":
        target = parts[2] if len(parts) >= 3 else chat_id
        policy = store.get_policy(target)
        await message.reply(_format_stats(policy))
        return

    # ── list ────────────────────────────────────────────────
    if sub == "list":
        all_policies = store.list_all()
        if not all_policies:
            await message.reply("ℹ️ Нет custom policies.")
            return
        lines = ["📋 **Custom policies:**"]
        for p in all_policies[:50]:
            lines.append(
                f"• `{p.chat_id}` — `{p.mode.value}` "
                f"(thr {p.effective_threshold():.2f}, "
                f"-{p.negative_signals}/+{p.positive_signals})"
            )
        if len(all_policies) > 50:
            lines.append(f"… и ещё {len(all_policies) - 50}")
        await message.reply("\n".join(lines))
        return

    # ── reset ───────────────────────────────────────────────
    if sub == "reset":
        target = parts[2] if len(parts) >= 3 else chat_id
        existed = store.reset_policy(target)
        if existed:
            await message.reply(f"✅ Policy `{target}` сброшена к defaults.")
        else:
            await message.reply(f"ℹ️ Policy `{target}` уже на defaults.")
        return

    # ── help / unknown ──────────────────────────────────────
    raise UserInputError(user_message=f"❌ Неизвестная subcommand `{sub}`. {_USAGE}")


__all__ = ["handle_chatpolicy"]
