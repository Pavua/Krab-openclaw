"""!loglevel command — runtime log verbosity toggle."""

import logging
from typing import TYPE_CHECKING

import structlog

logger = structlog.get_logger(__name__)

VALID_LEVELS = ("TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

if TYPE_CHECKING:
    from pyrogram.types import Message

    from ..userbot_bridge import KraabUserbot


async def handle_loglevel(bot: "KraabUserbot", message: "Message") -> None:
    """!loglevel [TRACE|DEBUG|INFO|WARNING|ERROR|CRITICAL] — runtime log level toggle.

    Форматы:
      !loglevel              — показать текущий уровень
      !loglevel DEBUG        — сменить на DEBUG
      !loglevel INFO         — сменить на INFO
      !loglevel ERROR        — сменить на ERROR

    Изменение не персистентно — сбросится при перезапуске.
    Для постоянного изменения обновить env KRAB_LOG_LEVEL.
    """
    args = (bot._get_command_args(message) or "").strip().upper()

    root = logging.getLogger()
    current_level_int = root.getEffectiveLevel()
    current_level_name = logging.getLevelName(current_level_int)

    if not args or args == "STATUS":
        await message.reply(
            f"📋 **Текущий уровень логирования:** `{current_level_name}`\n\n"
            f"Доступные уровни: {', '.join(VALID_LEVELS)}\n\n"
            f"Пример: `!loglevel DEBUG`\n\n"
            f"⚠️ Изменение runtime — сбросится при перезапуске Краба.",
        )
        return

    if args not in VALID_LEVELS:
        await message.reply(
            f"❌ Неизвестный уровень: `{args}`.\n\nВалидные уровни: {', '.join(VALID_LEVELS)}"
        )
        return

    # Применяем изменение
    try:
        if args == "TRACE":
            # TRACE ниже DEBUG
            new_level = 5
        else:
            new_level = getattr(logging, args)

        logging.getLogger().setLevel(new_level)

        logger.warning(
            "log_level_changed_runtime",
            old_level=current_level_name,
            new_level=args,
            correlation_id=getattr(message, "_correlation_id", "N/A"),
        )

        await message.reply(
            f"🔧 **Log level изменён:**\n"
            f"`{current_level_name}` → `{args}`\n\n"
            f"⚠️ Runtime изменение — сбросится при перезапуске.\n"
            f"Для персистентного изменения: `KRAB_LOG_LEVEL={args}`"
        )

    except Exception as exc:
        await message.reply(f"❌ Ошибка при смене уровня: {exc}")
        logger.exception("loglevel_change_failed", error=str(exc))
