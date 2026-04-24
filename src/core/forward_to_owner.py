# -*- coding: utf-8 -*-
"""
Real owner-DM forward tool — когда гость просит «передать владельцу».

Исправляет phantom action: вместо LLM просто пишущего "передал" —
реально отправляет DM в Saved Messages (self-chat) с деталями запроса.

Public API:
-----------
    async forward_request_to_owner(
        client,                  # Pyrogram client
        *,
        from_user,               # pyrogram.types.User | None
        chat_id: int,
        chat_title: str = "",
        text: str,
        category: str = "request",
    ) -> bool

    Отправляет сообщение в self-DM (Saved Messages):
        📬 @username в «Chat Title» просит передать:
        <text>

    Returns True если успешно отправлено.

    is_phantom_forward_promise(text: str) -> bool

    Возвращает True если текст содержит phantom-фразу типа «передал владельцу»
    без реального выполнения tool.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .logger import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger("forward_to_owner")

# ---------------------------------------------------------------------------
# Паттерн для детекции phantom-фраз
# ---------------------------------------------------------------------------

# Фразы, которые LLM использует как "pseudo-action" без реального tool call.
# Используются и post-processor'ом, и тестами.
_PHANTOM_FORWARD_PHRASES: list[str] = [
    r"передал\s+владельцу",
    r"передал\s+хозяину",
    r"уведомил\s+владельца",
    r"уведомил\s+хозяина",
    r"сообщил\s+владельцу",
    r"переслал\s+владельцу",
    r"forwarded\s+to\s+(?:the\s+)?owner",
    r"notified\s+(?:the\s+)?owner",
    r"отправил\s+(?:тебе|вам|ему)\s+уведомление",
    r"владельцу\s+(?:уже\s+)?(?:передал|сообщил|отправил)",
    # W31: после live-теста — LLM фабриковал messageId/chat ID и формулировки
    # типа «Доставка подтверждена». Эти паттерны ловят phantom-confirmations.
    r"доставка\s+подтверждена",
    r"delivery\s+confirmed",
    r"messageId\s*[:=]?\s*\d+",
    r"message[\s_]*id\s*[:=]?\s*\d+",
    # Начало ответа с «Отправил» + детальная «отчётность» — классический галлюцин.
    r"^\s*отправил\b[^.]{0,80}(?:сообщени|телеграм|telegram|chat)",
    r"^\s*sent\b[^.]{0,80}(?:message|telegram|chat)",
]

_PHANTOM_RE = re.compile(
    "|".join(_PHANTOM_FORWARD_PHRASES),
    re.IGNORECASE | re.UNICODE,
)


def is_phantom_forward_promise(text: str) -> bool:
    """
    Возвращает True если text содержит phantom-фразу «передал владельцу» (и варианты).

    Используется post-processor'ом в LLMTextProcessingMixin._apply_phantom_action_guard
    для перехвата ответов, где LLM обещает действие которое не совершило.
    """
    if not text:
        return False
    return bool(_PHANTOM_RE.search(text))


async def forward_request_to_owner(
    client: Any,
    *,
    from_user: Any = None,
    chat_id: int = 0,
    chat_title: str = "",
    text: str,
    category: str = "request",
) -> bool:
    """
    Реально отправляет DM в Saved Messages (self-chat owner'а) с деталями запроса.

    Аргументы:
        client       — pyrogram.Client
        from_user    — pyrogram.types.User или None
        chat_id      — id чата откуда пришёл запрос
        chat_title   — название чата (для контекста)
        text         — текст запроса гостя
        category     — тип запроса ('request', 'question', 'complaint')

    Возвращает True если сообщение успешно отправлено.
    """
    try:
        # Собираем display имя отправителя
        fname = str(getattr(from_user, "first_name", "") or "").strip()
        lname = str(getattr(from_user, "last_name", "") or "").strip()
        username = str(getattr(from_user, "username", "") or "").strip()
        sender_name = f"{fname} {lname}".strip()
        if username:
            sender_display = f"@{username}"
            if sender_name:
                sender_display = f"{sender_name} (@{username})"
        elif sender_name:
            sender_display = sender_name
        else:
            uid = getattr(from_user, "id", None)
            sender_display = f"id:{uid}" if uid else "Неизвестный"

        # Формируем место
        if chat_title:
            location = f"«{chat_title}»"
        elif chat_id:
            location = f"чат {chat_id}"
        else:
            location = "личке"

        category_label = {
            "request": "📬 Запрос",
            "question": "❓ Вопрос",
            "complaint": "⚠️ Жалоба",
        }.get(category, "📬 Запрос")

        excerpt = str(text or "").strip()[:1500]

        notification = f"{category_label} от {sender_display} в {location}:\n\n{excerpt}"

        # Отправляем в Saved Messages (self-DM)
        me = await client.get_me()
        await client.send_message(me.id, notification)
        logger.info(
            "forward_to_owner_sent",
            sender=sender_display,
            chat_id=str(chat_id),
            category=category,
            text_len=len(excerpt),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("forward_to_owner_failed", error=str(exc))
        return False
