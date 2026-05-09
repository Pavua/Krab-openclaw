# -*- coding: utf-8 -*-
"""
Reply preprocessor — строгий разбор reply_to и mentions ДО генерации ответа.

Решает Bug 3 (продолжение) + Bug 10:
- Bug 3: при длинном reply_to модель видела только начало цитаты и игнорировала
  хвост текущего сообщения, где находится @mention + сам вопрос.
- Bug 10: @mention внутри тела цитируемого сообщения (reply_to.text) не триггерил
  Краба, потому что mention-detector смотрел только в new message.text.

Подход: формируем сегментированный prompt, где блоки явно отделены и LLM не
склеивает их в один поток текста. reply_to передаётся ПОЛНОСТЬЮ (без обрезки),
mentions из reply_to/text собраны в отдельную секцию.

Public API:
- ``extract_reply_segments(message)`` → ``ReplySegments`` (для тестов / диагностики).
- ``build_segmented_prompt(...)`` → итоговый prompt-string с явными блоками.
- ``has_persona_mention_in_reply_to(message, personas)`` → bool (для smart-trigger
  bump priority).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# Pyrogram Message либо dict — обращаемся через _attr (как в mention_detector).
def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


_MENTION_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_]{2,})")


@dataclass
class ReplySegments:
    """Сегментированное представление сообщения.

    Поля:
    - reply_to_text: полный текст reply target (text+caption), без обрезки;
    - reply_to_author: username/first_name автора reply target;
    - mentions: список уникальных @username, найденных в reply_to+current text;
    - current_text: исходный текст текущего сообщения (без префиксов);
    - has_reply: bool — было ли reply_to.
    """

    reply_to_text: str = ""
    reply_to_author: str = ""
    mentions: list[str] = field(default_factory=list)
    current_text: str = ""
    has_reply: bool = False


def _message_text(msg: Any) -> str:
    text = _attr(msg, "text") or _attr(msg, "caption") or ""
    return str(text or "").strip()


def _author_label(reply_target: Any) -> str:
    """Достаёт username или first_name автора reply target."""
    user = _attr(reply_target, "from_user")
    if user is None:
        return ""
    username = str(_attr(user, "username", "") or "").strip()
    if username:
        return username
    first = str(_attr(user, "first_name", "") or "").strip()
    return first


def _collect_mentions(*texts: str) -> list[str]:
    """Уникальные @username в порядке появления."""
    seen: list[str] = []
    seen_lower: set[str] = set()
    for text in texts:
        if not text:
            continue
        for match in _MENTION_RE.finditer(text):
            handle = match.group(1)
            key = handle.lower()
            if key in seen_lower:
                continue
            seen_lower.add(key)
            seen.append(handle)
    return seen


def extract_reply_segments(message: Any) -> ReplySegments:
    """Разбирает входящее сообщение на сегменты для prompt builder'а.

    Не обрезает reply_to text (Bug 3): даже длинная цитата важна для понимания
    адресата вопроса. Mentions собираются и из reply_to body, и из current text
    (Bug 10).
    """
    current_text = _message_text(message)
    reply_target = _attr(message, "reply_to_message")
    reply_to_text = ""
    reply_to_author = ""
    has_reply = False
    if reply_target is not None:
        reply_to_text = _message_text(reply_target)
        reply_to_author = _author_label(reply_target)
        has_reply = bool(reply_to_text or reply_to_author)

    mentions = _collect_mentions(reply_to_text, current_text)
    return ReplySegments(
        reply_to_text=reply_to_text,
        reply_to_author=reply_to_author,
        mentions=mentions,
        current_text=current_text,
        has_reply=has_reply,
    )


def build_segmented_prompt(
    *,
    segments: ReplySegments,
    sender_name: str = "",
    is_group: bool = False,
    fallback_query: str = "",
) -> str:
    """Собирает явно сегментированный prompt для LLM.

    Формат (только non-empty блоки попадают):

        [В ответ на сообщение @author — полностью]:
        <reply_to_text>

        [Адресовано (@mentions)]: @yung_nagato, @callme_chado

        [Текущее сообщение от @sender]:
        <current_text>

    fallback_query используется если current_text пустой (например, photo+caption
    путь). is_group — control: в private чате sender prefix не нужен.
    """
    parts: list[str] = []

    if segments.has_reply and segments.reply_to_text:
        author_tag = f" @{segments.reply_to_author}" if segments.reply_to_author else ""
        parts.append(f"[В ответ на сообщение{author_tag} — полностью]:\n{segments.reply_to_text}")

    if segments.mentions:
        joined = ", ".join(f"@{m}" for m in segments.mentions)
        parts.append(f"[Адресовано (@mentions)]: {joined}")

    current = segments.current_text or fallback_query
    current = str(current or "").strip()
    if current:
        if is_group and sender_name:
            parts.append(f"[Текущее сообщение от @{sender_name}]:\n{current}")
        else:
            parts.append(f"[Текущее сообщение]:\n{current}")

    # Wave 37-B (P1-5): anaphora hint. Если в current text есть местоимения
    # "его/ему/её/ей" И есть reply target с known author — подсказываем LLM
    # на кого они ссылаются. Без подсказки модель путалась и иногда
    # отвечала "не той стороне".
    if segments.has_reply and segments.reply_to_author and current:
        from .delivery_helpers import (
            _query_has_anaphora,
        )  # local: избегаем circular  # noqa: PLC0415

        if _query_has_anaphora(current):
            parts.append(
                f"[Контекст: местоимения 'его/ему/её/ей' в текущем "
                f"сообщении относятся к @{segments.reply_to_author} "
                f"(автору цитаты выше), не к отправителю]"
            )

    return "\n\n".join(parts).strip()


def has_persona_mention_in_reply_to(
    message: Any,
    personas: list[str] | tuple[str, ...] | set[str],
) -> bool:
    """True если reply_to.text/caption содержит @mention одной из persona-aliases.

    personas — список username без `@` (например, ['yung_nagato', 'kraab']).
    Используется smart-trigger'ом чтобы поднять priority signal — если в цитате
    адресовано Крабу, мы должны ответить, даже если в current text нет mention.
    """
    if not personas:
        return False
    reply_target = _attr(message, "reply_to_message")
    if reply_target is None:
        return False
    text = _message_text(reply_target)
    if not text:
        return False
    found = {h.lower() for h in _collect_mentions(text)}
    return any(str(p).lower().lstrip("@") in found for p in personas)
