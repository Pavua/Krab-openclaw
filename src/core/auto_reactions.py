# -*- coding: utf-8 -*-
"""
Auto-reactions on user messages — контекстные, человекоподобные.

Режимы (KRAB_AUTO_REACTIONS_MODE):
  off         — реакции полностью выключены
  contextual  — умные реакции по контексту, с rate-limit (default)
  aggressive  — 50%+ вероятность на любое сообщение

Набор реакций:
- 👍 🙏 — благодарность / позитив
- 🤔 😕 — проблема / ошибка / вопрос с ожиданием помощи
- 😂     — юмор / хохот
- ❌ ⚙️ 🧠 — системные (ошибка, агент, память) — остаются для explicit вызовов

Разрешённые emoji (REACTION_INVALID whitelist):
  👍 🙏 🤔 😕 😂 ❤️ 🔥 🤝 👀 ❌ ⚙️ 🧠

Rate-limit: max 1 реакция per KRAB_AUTO_REACTION_RATE_LIMIT (float, default 0.2 = 20%)
на поток контекстных реакций. Явные реакции (mark_failed, mark_agent_mode) — без лимита.

AUTO_REACTIONS_ENABLED=false полностью глушит всё (backward compat).
"""

from __future__ import annotations

import os
import random
import re
import time
from collections import defaultdict
from enum import Enum
from typing import Optional

from structlog import get_logger

logger = get_logger(__name__)

# Пробуем импортировать ChatType; при тестах без pyrogram — fallback на None
try:
    from pyrogram.enums import ChatType as _ChatType
except ImportError:  # noqa: BLE001
    _ChatType = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------

AUTO_REACTIONS_ENABLED = os.environ.get("AUTO_REACTIONS_ENABLED", "true").lower() in (
    "true",
    "1",
    "yes",
)

# Whitelist emoji, гарантированно принимаемых Telegram (избегаем REACTION_INVALID)
SAFE_EMOJI_WHITELIST: frozenset[str] = frozenset(
    {"👍", "🙏", "🤔", "😕", "😂", "❤️", "🔥", "🤝", "👀", "❌", "✅", "⚙️", "🧠"}
)


class ReactionState(str, Enum):
    ACCEPTED = "👍"
    COMPLETED = "✅"
    FAILED = "❌"
    AGENT_MODE = "⚙️"
    MEMORY_RECALL = "🧠"


# ---------------------------------------------------------------------------
# Rate-limit состояние (per-chat, in-memory)
# ---------------------------------------------------------------------------

# chat_id -> список timestamp последних авто-реакций
_reaction_timestamps: dict[int, list[float]] = defaultdict(list)
# Окно rate-limit, сек
_RATE_WINDOW_SEC = 300  # 5 минут
# Максимум авто-реакций per window per chat
_RATE_MAX = 1


def _rate_ok(chat_id: int) -> bool:
    """Проверяет, можно ли ставить ещё одну контекстную реакцию в этом чате."""
    now = time.monotonic()
    ts = _reaction_timestamps[chat_id]
    # Очищаем старые timestamp
    _reaction_timestamps[chat_id] = [t for t in ts if now - t < _RATE_WINDOW_SEC]
    return len(_reaction_timestamps[chat_id]) < _RATE_MAX


def _record_reaction(chat_id: int) -> None:
    """Записывает timestamp новой авто-реакции."""
    _reaction_timestamps[chat_id].append(time.monotonic())


def _get_mode() -> str:
    """Читает режим реакций из env."""
    return os.environ.get("KRAB_AUTO_REACTIONS_MODE", "contextual").lower()


def _get_rate_limit() -> float:
    """Читает вероятность авто-реакции (для режима aggressive)."""
    try:
        return float(os.environ.get("KRAB_AUTO_REACTION_RATE_LIMIT", "0.2"))
    except (ValueError, TypeError):
        return 0.2


# ---------------------------------------------------------------------------
# Контекстный анализ сообщения
# ---------------------------------------------------------------------------

# Паттерны для определения тональности / темы сообщения пользователя
_GRATITUDE_PATTERNS = re.compile(
    r"\b(спасибо|спс|благодарю|thanks|thank\s+you|thx|ty|пасиб|sps|danke|gracias|merci)\b",
    re.IGNORECASE,
)
_SADNESS_PATTERNS = re.compile(
    r"\b(помогите|помоги|не\s+работает|ошибка|проблема|сломалось|не\s+могу|зависло|баг|bug|error|broken|help|help me|stuck|не\s+получается)\b",
    re.IGNORECASE,
)
_JOKE_PATTERNS = re.compile(
    r"\b(хаха|хахах|ха-ха|lol|lmao|rofl|😂|🤣|смешно|прикол|кек|kek)\b",
    re.IGNORECASE,
)


def pick_contextual_emoji(text: str, *, mode: str = "contextual") -> Optional[str]:
    """
    Выбирает emoji для реакции на основе текста сообщения.

    Возвращает None если реакция не нужна.
    mode: 'contextual' | 'aggressive' | 'off'
    """
    if mode == "off":
        return None

    if not text or text.startswith(("!", "/")):
        # Команды — не реагируем (чтобы не спамить при каждой команде)
        if mode != "aggressive":
            return None

    # Контекстный анализ
    if _GRATITUDE_PATTERNS.search(text):
        return random.choice(["👍", "🙏", "❤️"])  # noqa: S311

    if _SADNESS_PATTERNS.search(text):
        return random.choice(["🤔", "😕"])  # noqa: S311

    if _JOKE_PATTERNS.search(text):
        return "😂"

    # В aggressive режиме — реагируем с вероятностью rate_limit на любое сообщение
    if mode == "aggressive":
        rate = _get_rate_limit()
        if random.random() < rate:  # noqa: S311
            return random.choice(["👍", "🤔", "👀", "🔥"])  # noqa: S311

    # В contextual режиме — небольшой шанс на нейтральное "прочитал"
    if mode == "contextual" and len(text) > 20:
        rate = _get_rate_limit()
        if random.random() < rate:  # noqa: S311
            return "👀"

    return None


def _can_react(message) -> bool:
    """DM-чаты не поддерживают произвольные emoji-реакции (REACTION_INVALID).

    Telegram разрешает custom-emoji реакции только в группах/каналах,
    где администратор включил paid reactions. В приватных чатах (PRIVATE)
    вызов send_reaction с нестандартным emoji возвращает 400 REACTION_INVALID.
    """
    chat = getattr(message, "chat", None)
    if chat is None:
        return False
    chat_type = getattr(chat, "type", None)
    if chat_type is None:
        return False
    # Сравниваем через _ChatType если доступен, иначе по строке
    if _ChatType is not None:
        return chat_type != _ChatType.PRIVATE
    # Fallback: строковое сравнение для тестовых окружений без pyrogram
    return "PRIVATE" not in str(chat_type).upper()


# ---------------------------------------------------------------------------
# Основная функция отправки реакции
# ---------------------------------------------------------------------------


async def set_reaction(
    bot,
    chat_id: int,
    message_id: int,
    emoji: str,
    log_ctx: Optional[dict] = None,
) -> bool:
    """Отправить реакцию на сообщение. Graceful при отсутствии API.

    Backward-compatible: сигнатура не изменилась.
    Добавлена проверка whitelist для предотвращения REACTION_INVALID.
    """
    # Читаем env в runtime (команда !react меняет его)
    enabled = os.environ.get("AUTO_REACTIONS_ENABLED", "true").lower() in ("true", "1", "yes")
    if not enabled:
        return False

    # Проверка whitelist — если emoji не в whitelist, молча пропускаем
    if emoji not in SAFE_EMOJI_WHITELIST:
        logger.debug("auto_reaction_skipped_not_in_whitelist", emoji=emoji)
        return False

    try:
        if hasattr(bot, "send_reaction"):
            await bot.send_reaction(chat_id=chat_id, message_id=message_id, emoji=emoji)
        elif hasattr(bot, "client") and hasattr(bot.client, "send_reaction"):
            await bot.client.send_reaction(chat_id=chat_id, message_id=message_id, emoji=emoji)
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


# ---------------------------------------------------------------------------
# Контекстная авто-реакция перед ответом (replaces mark_accepted + mark_memory_recall)
# ---------------------------------------------------------------------------


async def contextual_pre_reply_reaction(
    bot,
    message,
    *,
    user_text: str = "",
) -> bool:
    """
    Контекстная реакция перед LLM-ответом.

    Заменяет безусловные mark_accepted / mark_memory_recall.
    - Не ставит реакцию если mode=off
    - Не ставит реакцию на команды (! / /)
    - Не ставит реакцию если rate-limit исчерпан для чата
    - Выбирает emoji на основе контента сообщения пользователя
    """
    if not _can_react(message):
        return False

    mode = _get_mode()
    if mode == "off":
        return False

    # Читаем env в runtime
    enabled = os.environ.get("AUTO_REACTIONS_ENABLED", "true").lower() in ("true", "1", "yes")
    if not enabled:
        return False

    _raw_text = getattr(message, "text", None)
    _raw_caption = getattr(message, "caption", None)
    text = user_text or (
        (_raw_text if isinstance(_raw_text, str) else "")
        or (_raw_caption if isinstance(_raw_caption, str) else "")
    )

    emoji = pick_contextual_emoji(text, mode=mode)
    if emoji is None:
        return False

    chat_id = message.chat.id
    if not _rate_ok(chat_id):
        logger.debug("auto_reaction_rate_limited", chat_id=chat_id)
        return False

    ok = await set_reaction(bot, chat_id, message.id, emoji, {"phase": "contextual"})
    if ok:
        _record_reaction(chat_id)
    return ok


# ---------------------------------------------------------------------------
# Явные системные реакции (без rate-limit, используются в llm_flow)
# ---------------------------------------------------------------------------


async def mark_accepted(bot, message) -> bool:
    """Больше НЕ ставит безусловную 👍 перед каждым ответом.

    Теперь делегирует в contextual_pre_reply_reaction — реакция только если
    контент сообщения этого требует. Вызывается из llm_flow без изменений API.
    """
    # Берём текст из message для контекстного анализа (только строки)
    _t = getattr(message, "text", None)
    _c = getattr(message, "caption", None)
    text = (_t if isinstance(_t, str) else "") or (_c if isinstance(_c, str) else "")
    return await contextual_pre_reply_reaction(bot, message, user_text=text)


async def mark_completed(bot, message) -> bool:
    """Убрана безусловная ✅ после каждого ответа.

    Telegram-реакции — это сигнал «я прочитал», не «я ответил».
    Пользователь видит ответ — этого достаточно. Реакция не ставится.
    """
    # Намеренно no-op: не спамим ✅ после каждого ответа.
    # Если нужно явное подтверждение — используй mark_explicit_completed.
    return False


async def mark_explicit_completed(bot, message) -> bool:
    """✅ для явного успешного завершения (например, длинная задача)."""
    if not _can_react(message):
        return False
    return await set_reaction(
        bot,
        message.chat.id,
        message.id,
        ReactionState.COMPLETED.value,
        {"phase": "completed"},
    )


async def mark_failed(bot, message, error: str = "") -> bool:
    """❌ при ошибке — явная, всегда без rate-limit."""
    if not _can_react(message):
        return False
    return await set_reaction(
        bot,
        message.chat.id,
        message.id,
        ReactionState.FAILED.value,
        {"phase": "failed", "error": error[:100]},
    )


async def mark_agent_mode(bot, message) -> bool:
    """⚙️ при переходе в агентный/tool-use режим — явная, без rate-limit."""
    if not _can_react(message):
        return False
    return await set_reaction(
        bot,
        message.chat.id,
        message.id,
        ReactionState.AGENT_MODE.value,
        {"phase": "agent"},
    )


async def mark_memory_recall(bot, message) -> bool:
    """🧠 при активации RAG/memory recall.

    Больше НЕ ставится безусловно — только если контекст сообщения уместен.
    Теперь это no-op, contextual_pre_reply_reaction уже учла этот сигнал.
    """
    # Не спамим 🧠 перед каждым ответом, где есть memory layer.
    # Контекстная реакция уже сделала своё дело в mark_accepted.
    return False


# ---------------------------------------------------------------------------
# Команда !react
# ---------------------------------------------------------------------------


async def handle_react(bot, message) -> None:
    """!react on|off|status — управление auto-reactions."""
    args = (bot._get_command_args(message) or "").strip().lower()
    if args in ("on", "enable"):
        os.environ["AUTO_REACTIONS_ENABLED"] = "true"
        await message.reply("✅ Auto-reactions enabled.")
    elif args in ("off", "disable"):
        os.environ["AUTO_REACTIONS_ENABLED"] = "false"
        await message.reply("🔇 Auto-reactions disabled.")
    else:
        state = os.environ.get("AUTO_REACTIONS_ENABLED", "true")
        mode = _get_mode()
        rate = _get_rate_limit()
        await message.reply(
            f"🎛️ Auto-reactions: `{state}`\n"
            f"Mode: `{mode}` (KRAB_AUTO_REACTIONS_MODE)\n"
            f"Rate limit: `{rate}` (KRAB_AUTO_REACTION_RATE_LIMIT)\n\n"
            f"Toggle: `!react on` / `!react off`",
        )
