# -*- coding: utf-8 -*-
"""
Общие утилиты для command handlers — Phase 1 scaffold (Session 24).

Содержит helpers, которые в будущем будут использоваться domain modules
``src/handlers/commands/{ai,memory,swarm,...}_commands.py``.

⚠️ Phase 1 — additive scaffold: эти функции **дублируют** оригиналы в
``src/handlers/command_handlers.py``. Удаление дубликатов произойдёт в
Phase 2+ когда первый домен начнёт импортировать отсюда вместо command_handlers.

См. ``docs/CODE_SPLITS_PLAN.md`` § "Build sequence" Phase 1-5.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

# Локальный logger чтобы _shared.py не зависел от command_handlers
logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from pyrogram.types import Message

    from ...userbot_bridge import KraabUserbot


# Импорт исключения — оно определено в src/core/exceptions.py
class _LazyUserInputError:
    """Lazy import wrapper, чтобы избежать circular import при boot.

    UserInputError в src/core/exceptions.py может зависеть от других модулей,
    которые в свою очередь импортируют из command_handlers. Lazy import
    разрывает цикл.
    """

    @staticmethod
    def raise_with_message(message: str) -> None:
        from ...core.exceptions import UserInputError

        raise UserInputError(user_message=message)


async def _reply_tech(message: "Message", bot: "KraabUserbot", text: str, **kwargs: Any) -> None:
    """Тех-ответ: в группе → редирект в ЛС, в ЛС → reply.

    Предназначена для команд с техническим выводом (логи, cron и т.п.),
    которые не должны «засорять» групповые чаты.
    """
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", 0) if chat is not None else 0
    if chat_id < 0:
        # Уведомление в группе
        try:
            await message.reply("📬 Ответ в ЛС (тех-команда).")
        except Exception:  # noqa: BLE001
            pass
        # Сам ответ — в Saved Messages
        try:
            await bot.client.send_message("me", text, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("tech_dm_redirect_failed", error=str(exc))
    else:
        await message.reply(text, **kwargs)


def _parse_toggle_arg(raw: Any, *, field_name: str) -> bool:
    """Нормализует `on/off` аргумент для командных флагов."""
    value = str(raw or "").strip().lower()
    if value == "on":
        return True
    if value == "off":
        return False
    _LazyUserInputError.raise_with_message(
        f"❌ Для `{field_name}` поддерживаются только `on` и `off`."
    )
    return False  # unreachable, но удовлетворяет type checker


def _format_size_gb(size_gb: float) -> str:
    """Форматирует размер модели для человекочитаемого вывода."""
    try:
        value = float(size_gb)
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0:
        return "n/a"
    return f"{value:.2f} GB"


def _split_text_for_telegram(text: str, limit: int = 3900) -> list[str]:
    """
    Делит длинный текст на части с сохранением границ строк.

    Telegram ограничивает текст сообщения примерно 4096 символами.
    """
    lines = text.splitlines()
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(line) <= limit:
            current = line
        else:
            # На случай сверхдлинной строки режем принудительно.
            for i in range(0, len(line), limit):
                part = line[i : i + limit]
                if len(part) == limit:
                    chunks.append(part)
                else:
                    current = part
    if current:
        chunks.append(current)
    return chunks or [text[:limit]]


__all__ = [
    "_reply_tech",
    "_parse_toggle_arg",
    "_format_size_gb",
    "_split_text_for_telegram",
]
