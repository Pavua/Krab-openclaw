# -*- coding: utf-8 -*-
"""
!proactive — управление proactive event detection per-chat (Wave 39-B-2).

Owner-only команда для включения/отключения proactive-флагов в политике
текущего чата. Работает автономно без proactive_dispatcher.

Subcommands:
    !proactive on              — включить все флаги (joins + media + ai)
    !proactive off             — выключить все флаги
    !proactive status          — текущее состояние + placeholder квоты
    !proactive joins on/off    — toggle proactive_joins
    !proactive media on/off    — toggle proactive_media
    !proactive ai on/off       — toggle proactive_ai
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

_USAGE = (
    "🔔 **!proactive** — управление proactive event detection (owner)\n"
    "```\n"
    "!proactive on              — включить все флаги\n"
    "!proactive off             — выключить все флаги\n"
    "!proactive status          — текущее состояние\n"
    "!proactive joins on|off    — toggle: join-события\n"
    "!proactive media on|off    — toggle: медиа-события\n"
    "!proactive ai on|off       — toggle: AI-события\n"
    "```"
)


def _flag_icon(enabled: bool) -> str:
    """Иконка статуса флага."""
    return "✅" if enabled else "⭕"


def _format_proactive_status(policy) -> str:  # noqa: ANN001 — duck-type ChatResponsePolicy
    """Форматирует статус proactive-флагов для owner'а."""
    return (
        f"🔔 **Proactive** `{policy.chat_id}` (режим: `{policy.mode.value}`)\n"
        f"• Joins:  {_flag_icon(policy.proactive_joins)} "
        f"{'включён' if policy.proactive_joins else 'выключен'}\n"
        f"• Media:  {_flag_icon(policy.proactive_media)} "
        f"{'включён' if policy.proactive_media else 'выключен'}\n"
        f"• AI:     {_flag_icon(policy.proactive_ai)} "
        f"{'включён' if policy.proactive_ai else 'выключен'}\n"
        f"• Квоты (joins/media/ai): — / — / — _(диспетчер не подключён)_"
    )


async def handle_proactive(bot: "KraabUserbot", message: Message) -> None:
    """Owner-only !proactive router."""
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="❌ Только owner.")

    from ...core.chat_response_policy import get_store

    store = get_store()
    raw = (message.text or "").strip()
    parts = raw.split()
    sub = parts[1].lower() if len(parts) >= 2 else ""
    chat_id = str(message.chat.id)

    # ── status / default ────────────────────────────────────
    if sub in ("", "status"):
        policy = store.get_policy(chat_id)
        await message.reply(_format_proactive_status(policy))
        return

    # ── on — включить все флаги ──────────────────────────────
    if sub == "on":
        policy = store.update_policy(
            chat_id,
            proactive_joins=True,
            proactive_media=True,
            proactive_ai=True,
        )
        await message.reply(
            f"✅ Proactive включён для `{chat_id}` (joins+media+ai).\n"
            + _format_proactive_status(policy)
        )
        return

    # ── off — выключить все флаги ────────────────────────────
    if sub == "off":
        policy = store.update_policy(
            chat_id,
            proactive_joins=False,
            proactive_media=False,
            proactive_ai=False,
        )
        await message.reply(
            f"⭕ Proactive выключен для `{chat_id}` (joins+media+ai).\n"
            + _format_proactive_status(policy)
        )
        return

    # ── joins on|off ─────────────────────────────────────────
    if sub == "joins":
        if len(parts) < 3:
            raise UserInputError(user_message="❌ Укажи `on` или `off`. " + _USAGE)
        flag = _parse_onoff(parts[2], field_name="joins")
        policy = store.update_policy(chat_id, proactive_joins=flag)
        await message.reply(
            f"{'✅' if flag else '⭕'} proactive_joins = `{'on' if flag else 'off'}` "
            f"для `{chat_id}`."
        )
        return

    # ── media on|off ─────────────────────────────────────────
    if sub == "media":
        if len(parts) < 3:
            raise UserInputError(user_message="❌ Укажи `on` или `off`. " + _USAGE)
        flag = _parse_onoff(parts[2], field_name="media")
        policy = store.update_policy(chat_id, proactive_media=flag)
        await message.reply(
            f"{'✅' if flag else '⭕'} proactive_media = `{'on' if flag else 'off'}` "
            f"для `{chat_id}`."
        )
        return

    # ── ai on|off ────────────────────────────────────────────
    if sub == "ai":
        if len(parts) < 3:
            raise UserInputError(user_message="❌ Укажи `on` или `off`. " + _USAGE)
        flag = _parse_onoff(parts[2], field_name="ai")
        policy = store.update_policy(chat_id, proactive_ai=flag)
        await message.reply(
            f"{'✅' if flag else '⭕'} proactive_ai = `{'on' if flag else 'off'}` для `{chat_id}`."
        )
        return

    # ── help / unknown ───────────────────────────────────────
    raise UserInputError(user_message=f"❌ Неизвестная subcommand `{sub}`. {_USAGE}")


def _parse_onoff(raw: str, *, field_name: str) -> bool:
    """Нормализует on/off строку в bool, иначе UserInputError."""
    value = str(raw or "").strip().lower()
    if value == "on":
        return True
    if value == "off":
        return False
    from ...core.exceptions import UserInputError

    raise UserInputError(user_message=f"❌ Для `{field_name}` поддерживаются только `on` и `off`.")


__all__ = ["handle_proactive"]
