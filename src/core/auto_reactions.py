# -*- coding: utf-8 -*-
"""
Auto-reactions on user messages — silent status indicators.

Reactions:
- 👍 (thumb up)  — task accepted / in progress
- ✅ (check mark) — task completed successfully
- ❌ (red X)      — task failed
- ⚙️ (gear)       — agentic / tool-use mode
- 🧠 (brain)      — memory/RAG recall active

Включается через env AUTO_REACTIONS_ENABLED=true (default).
Переключается командой !react on|off|status.
"""
from __future__ import annotations

import os
from enum import Enum
from typing import Optional

from structlog import get_logger

logger = get_logger(__name__)

AUTO_REACTIONS_ENABLED = os.environ.get("AUTO_REACTIONS_ENABLED", "true").lower() in (
    "true",
    "1",
    "yes",
)


class ReactionState(str, Enum):
    ACCEPTED = "👍"
    COMPLETED = "✅"
    FAILED = "❌"
    AGENT_MODE = "⚙️"
    MEMORY_RECALL = "🧠"


async def set_reaction(
    bot,
    chat_id: int,
    message_id: int,
    emoji: str,
    log_ctx: Optional[dict] = None,
) -> bool:
    """Отправить реакцию на сообщение. Graceful при отсутствии API."""
    # Читаем env в runtime (команда !react меняет его)
    enabled = os.environ.get("AUTO_REACTIONS_ENABLED", "true").lower() in ("true", "1", "yes")
    if not enabled:
        return False
    try:
        if hasattr(bot, "send_reaction"):
            await bot.send_reaction(chat_id=chat_id, message_id=message_id, emoji=emoji)
        elif hasattr(bot, "client") and hasattr(bot.client, "send_reaction"):
            await bot.client.send_reaction(
                chat_id=chat_id, message_id=message_id, emoji=emoji
            )
        else:
            logger.debug("auto_reaction_api_not_available", emoji=emoji)
            return False
        logger.debug(
            "auto_reaction_set",
            emoji=emoji,
            chat_id=chat_id,
            message_id=message_id,
            **(log_ctx or {}),
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("auto_reaction_failed", emoji=emoji, error=str(e))
        return False


async def mark_accepted(bot, message) -> bool:
    """👍 при принятии задачи (например, !ask стартовал)."""
    return await set_reaction(
        bot,
        message.chat.id,
        message.id,
        ReactionState.ACCEPTED.value,
        {"phase": "accepted"},
    )


async def mark_completed(bot, message) -> bool:
    """✅ при успешном завершении задачи."""
    return await set_reaction(
        bot,
        message.chat.id,
        message.id,
        ReactionState.COMPLETED.value,
        {"phase": "completed"},
    )


async def mark_failed(bot, message, error: str = "") -> bool:
    """❌ при ошибке."""
    return await set_reaction(
        bot,
        message.chat.id,
        message.id,
        ReactionState.FAILED.value,
        {"phase": "failed", "error": error[:100]},
    )


async def mark_agent_mode(bot, message) -> bool:
    """⚙️ при переходе в агентный/tool-use режим."""
    return await set_reaction(
        bot,
        message.chat.id,
        message.id,
        ReactionState.AGENT_MODE.value,
        {"phase": "agent"},
    )


async def mark_memory_recall(bot, message) -> bool:
    """🧠 при активации RAG/memory recall."""
    return await set_reaction(
        bot,
        message.chat.id,
        message.id,
        ReactionState.MEMORY_RECALL.value,
        {"phase": "memory"},
    )


async def handle_react(bot, message) -> None:
    """!react on|off|status — управление auto-reactions."""
    args = (bot._get_command_args(message) or "").strip().lower()
    if args in ("on", "enable"):
        os.environ["AUTO_REACTIONS_ENABLED"] = "true"
        await bot._safe_reply(message, "✅ Auto-reactions enabled.")
    elif args in ("off", "disable"):
        os.environ["AUTO_REACTIONS_ENABLED"] = "false"
        await bot._safe_reply(message, "🔇 Auto-reactions disabled.")
    else:
        state = os.environ.get("AUTO_REACTIONS_ENABLED", "true")
        await bot._safe_reply(
            message,
            f"🎛️ Auto-reactions: `{state}`\n\nToggle: `!react on` / `!react off`",
        )
