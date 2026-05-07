# -*- coding: utf-8 -*-
"""
Детектор Telegram-related запросов для routing override.

Когда пользователь спрашивает об истории переписки, первых сообщениях
или поиске по чатам — запрос должен идти через cloud path (не codex-cli),
потому что codex-cli блокирует Telegram MCP (Wave 9-B/10-A guard).

Использование::

    from src.core.telegram_query_detector import is_telegram_query
    if is_telegram_query(query):
        force_cloud = True
"""

from __future__ import annotations

import re

# Паттерны для русского и английского — история переписки / поиск в чатах.
# Специально упрощённые (substring match), без overengineering.
_PATTERNS_RU: tuple[str, ...] = (
    "первое сообщение",
    "первое мое сообщение",
    "первое моё сообщение",
    "первое сообщение от",
    "история переписки",
    "историю переписки",
    "историю чата",
    "когда написал",
    "когда я написал",
    "когда я впервые",
    "когда первый раз",
    "когда первый раз написал",
    "найди в чате",
    "поищи в чате",
    "поиск в чате",
    "найди в диалоге",
    "поищи в переписке",
    "найди в переписке",
    "был ли разговор",
    "была ли переписка",
    "что писал",
    "что я писал",
    "что он писал",
    "что она писала",
    "история сообщений",
    "последние сообщения",
    "прочитай переписку",
    "переписка с",
)

_PATTERNS_EN: tuple[str, ...] = (
    "first message",
    "chat history",
    "message history",
    "when did i",
    "when i first",
    "find in messages",
    "find in chat",
    "search messages",
    "search chat",
    "search in chat",
    "history with",
    "conversation history",
    "conversation with",
    "read the chat",
    "what did i write",
    "what did they write",
)

# @username упоминание + Telegram-контекстные слова — комбинация указывает на поиск в чатах.
# Regex один раз компилируется.
_AT_MENTION_RE = re.compile(r"@\w+")

# Контекстные слова, которые вместе с @mention указывают на Telegram-query.
_AT_CONTEXT_WORDS: tuple[str, ...] = (
    "написал",
    "написала",
    "сказал",
    "сказала",
    "писал",
    "переписк",  # переписка / переписки / переписку
    "история",
    "чат",
    "диалог",
    "message",
    "wrote",
    "said",
    "chat",
    "history",
    "told",
)


def is_telegram_query(query: str) -> bool:
    """Возвращает True если запрос о Telegram-истории/переписке.

    Достаточно одного совпадения с любым паттерном.
    Специально: false-positive лучше чем silent "Operation not permitted".

    Args:
        query: текст запроса пользователя (unicode, произвольная длина).

    Returns:
        True → нужен cloud path (MCP Telegram работает).
        False → обычный routing (codex-cli может обрабатывать).
    """
    if not query:
        return False

    q_lower = query.lower()

    # 1. Русские паттерны (substring)
    for pattern in _PATTERNS_RU:
        if pattern in q_lower:
            return True

    # 2. Английские паттерны (substring)
    for pattern in _PATTERNS_EN:
        if pattern in q_lower:
            return True

    # 3. @mention + Telegram-контекстное слово
    if _AT_MENTION_RE.search(query):
        for ctx_word in _AT_CONTEXT_WORDS:
            if ctx_word in q_lower:
                return True

    return False
