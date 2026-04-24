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
from typing import TYPE_CHECKING, Any, Iterable

from .logger import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger("forward_to_owner")

# ---------------------------------------------------------------------------
# Паттерны для детекции phantom-фраз
# ---------------------------------------------------------------------------

# STRONG: явные phantom-фразы «передал владельцу» — одного достаточно.
_PHANTOM_STRONG_PHRASES: list[str] = [
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
]

# WEAK: маркеры «фабрикованной отчётности» — по отдельности могут быть
# легитимными (техническое обсуждение Telegram API, ответ «Отправил сообщение
# в чат X» после реального tool call). Phantom только при distinct-match ≥ 2.
# Каждая запись — (group_name, pattern). Паттерны внутри одной группы считаются
# одним weak-сигналом (чтобы дубли типа messageId/message_id не давали composite
# на одиночном упоминании).
_PHANTOM_WEAK_GROUPS: list[tuple[str, str]] = [
    ("delivery_confirmed", r"доставка\s+подтверждена"),
    ("delivery_confirmed", r"delivery\s+confirmed"),
    ("message_id", r"messageId\s*[:=]?\s*\d+"),
    ("message_id", r"message[\s_]*id\s*[:=]?\s*\d+"),
    ("sent_report", r"^\s*отправил\b[^.]{0,80}(?:сообщени|телеграм|telegram|chat)"),
    ("sent_report", r"^\s*sent\b[^.]{0,80}(?:message|telegram|chat)"),
    ("chat_id_literal", r"\bchat(?:[_\s-]*id)?\s*[:=]?\s*-?\d{3,}"),
]
_PHANTOM_WEAK_PHRASES: list[str] = [p for _g, p in _PHANTOM_WEAK_GROUPS]

# Совместимость: полный список для legacy-импорта
_PHANTOM_FORWARD_PHRASES: list[str] = _PHANTOM_STRONG_PHRASES + _PHANTOM_WEAK_PHRASES

_PHANTOM_STRONG_RES = [re.compile(p, re.IGNORECASE | re.UNICODE) for p in _PHANTOM_STRONG_PHRASES]
_PHANTOM_WEAK_RES: list[tuple[str, "re.Pattern[str]"]] = [
    (group, re.compile(p, re.IGNORECASE | re.UNICODE)) for group, p in _PHANTOM_WEAK_GROUPS
]

# Legacy combined regex — сохраняем для внешних импортов
_PHANTOM_RE = re.compile(
    "|".join(_PHANTOM_FORWARD_PHRASES),
    re.IGNORECASE | re.UNICODE,
)

# Tool calls, which, будучи реально выполненными, означают что это НЕ phantom.
_REAL_FORWARD_TOOLS: frozenset[str] = frozenset(
    {
        "telegram_send_message",
        "forward_request_to_owner",
        "telegram_edit_message",
    }
)


def is_phantom_forward_promise(
    text: str,
    *,
    tool_calls_made: Iterable[str] | None = None,
) -> bool:
    """
    Возвращает True если text содержит phantom-фразу о передаче владельцу —
    без реального выполнения tool.

    Precision rules:
      1. Если в `tool_calls_made` есть любой из `_REAL_FORWARD_TOOLS` —
         это не phantom (real forward уже произошёл).
      2. Strong-phrase (прямое «передал владельцу») — достаточно одной.
      3. Weak-patterns (messageId, «Отправил сообщение в chat N», «доставка
         подтверждена») — phantom только при composite score ≥ 2.
    """
    if not text:
        return False

    # Real tool call → не phantom
    if tool_calls_made:
        tcalls = {str(t).strip().lower() for t in tool_calls_made if t}
        if tcalls & {t.lower() for t in _REAL_FORWARD_TOOLS}:
            return False

    strong_hits: list[str] = [r.pattern for r in _PHANTOM_STRONG_RES if r.search(text)]
    if strong_hits:
        logger.info(
            "phantom_guard_matched",
            kind="strong",
            patterns=strong_hits,
            text_preview=text[:120],
        )
        return True

    weak_groups_hit: set[str] = set()
    weak_hits: list[str] = []
    for group, pattern_re in _PHANTOM_WEAK_RES:
        if pattern_re.search(text):
            weak_groups_hit.add(group)
            weak_hits.append(pattern_re.pattern)
    if len(weak_groups_hit) >= 2:
        logger.info(
            "phantom_guard_matched",
            kind="composite",
            groups=sorted(weak_groups_hit),
            patterns=weak_hits,
            text_preview=text[:120],
        )
        return True

    return False


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
