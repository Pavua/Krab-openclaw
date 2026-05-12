# -*- coding: utf-8 -*-
"""
Тесты Wave 26-A: greeting_target_hint — Краб должен адресовать ответ
reply_to пользователю когда owner пишет слова типа «поздоровайся / тегни / упомяни».

Bug: в группе YMB FAMILY FOREVER владелец написал reply на join Polda:
«Краб, поздоровайся с новеньким» — Краб ответил generic «Здравствуй, путник 🦀»,
не упомянув Polda. После Wave 26-A в [context] добавляется greeting_target_hint
с inline-mention reply_to пользователя.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.core.sender_context import (
    build_context_block,
    detect_greeting_request,
)

# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------


def _make_message_with_reply(
    *,
    text: str = "",
    owner_id: int = 312322764,
    reply_user_id: int = 8441281276,
    reply_first_name: str = "Polda",
    reply_last_name: str = "",
    reply_username: str = "",
    is_owner: bool = True,
) -> MagicMock:
    """Message от owner с reply на другого пользователя."""
    # Создаём from_user (owner)
    owner = SimpleNamespace(id=owner_id, username="owner", first_name="Owner")

    # Создаём reply_to_message.from_user
    reply_from = SimpleNamespace(
        id=reply_user_id,
        username=reply_username or None,
        first_name=reply_first_name,
        last_name=reply_last_name or None,
    )
    reply_msg = MagicMock()
    reply_msg.from_user = reply_from
    reply_msg.text = ""
    reply_msg.caption = ""
    # Медиа нет
    for field in ("photo", "audio", "voice", "video", "document", "sticker", "animation"):
        setattr(reply_msg, field, None)

    # Основное сообщение
    chat_type_ns = SimpleNamespace(value="supergroup")
    chat = SimpleNamespace(type=chat_type_ns, title="YMB FAMILY FOREVER")

    msg = MagicMock()
    msg.from_user = owner
    msg.chat = chat
    msg.outgoing = False
    msg.text = text
    msg.caption = ""
    msg.reply_to_message = reply_msg
    msg.entities = []
    msg.forward_from = None
    msg.forward_sender_name = None
    msg.forward_from_chat = None

    return msg


def _make_message_no_reply(*, text: str = "обычное сообщение") -> MagicMock:
    """Message без reply."""
    owner = SimpleNamespace(id=312322764, username="owner", first_name="Owner")
    chat_type_ns = SimpleNamespace(value="supergroup")
    chat = SimpleNamespace(type=chat_type_ns, title="YMB FAMILY FOREVER")

    msg = MagicMock()
    msg.from_user = owner
    msg.chat = chat
    msg.outgoing = False
    msg.text = text
    msg.caption = ""
    msg.reply_to_message = None
    msg.entities = []
    msg.forward_from = None
    msg.forward_sender_name = None
    msg.forward_from_chat = None

    return msg


# ---------------------------------------------------------------------------
# Тесты detect_greeting_request
# ---------------------------------------------------------------------------


def test_detect_greeting_request_поздоровайся() -> None:
    """'поздоровайся с новеньким' → True."""
    assert detect_greeting_request("Краб, поздоровайся с новеньким") is True


def test_detect_greeting_request_обычное_сообщение() -> None:
    """Обычное сообщение без приветственных слов → False."""
    assert detect_greeting_request("Как дела?") is False


def test_detect_greeting_request_тегни() -> None:
    """'тегни друга' → True."""
    assert detect_greeting_request("тегни друга") is True


def test_detect_greeting_request_пустая_строка() -> None:
    """Пустая строка → False."""
    assert detect_greeting_request("") is False


def test_detect_greeting_request_упомяни() -> None:
    """'упомяни его' → True."""
    assert detect_greeting_request("упомяни его пожалуйста") is True


def test_detect_greeting_request_нет_ничего() -> None:
    """Сообщение с нейтральным текстом → False."""
    assert detect_greeting_request("нет ничего интересного здесь") is False


# ---------------------------------------------------------------------------
# Тесты build_context_block — greeting_target_hint
# ---------------------------------------------------------------------------


def test_build_context_block_greeting_hint_added_when_greeting_plus_reply() -> None:
    """При reply + greeting keyword → hint с reply_to_user_id и именем присутствует."""
    msg = _make_message_with_reply(
        text="Краб, поздоровайся с новеньким",
        reply_user_id=8441281276,
        reply_first_name="Polda",
    )
    block = build_context_block(msg, is_owner=True)
    assert "greeting_target_hint" in block
    assert "8441281276" in block
    assert "Polda" in block
    assert "tg://user?id=8441281276" in block


def test_build_context_block_greeting_hint_not_added_without_greeting() -> None:
    """Reply без greeting keywords → hint НЕ добавляется."""
    msg = _make_message_with_reply(
        text="Это просто reply без приветствия",
        reply_user_id=8441281276,
        reply_first_name="Polda",
    )
    block = build_context_block(msg, is_owner=True)
    assert "greeting_target_hint" not in block


def test_build_context_block_greeting_hint_not_added_without_reply() -> None:
    """Greeting keyword без reply → hint НЕ добавляется (некого тэгать)."""
    msg = _make_message_no_reply(text="поздоровайся со всеми")
    block = build_context_block(msg, is_owner=True)
    assert "greeting_target_hint" not in block


def test_build_context_block_тэгни_variant() -> None:
    """Вариант 'тэгни' (с мягким знаком) — также триггерит hint."""
    msg = _make_message_with_reply(
        text="тэгни Polda пожалуйста",
        reply_user_id=8441281276,
        reply_first_name="Polda",
    )
    block = build_context_block(msg, is_owner=True)
    assert "greeting_target_hint" in block
    assert "Polda" in block


def test_build_context_block_hint_no_generic_fallback_text() -> None:
    """Hint явно запрещает «путник» / «друг» — в тексте hint упоминается этот запрет."""
    msg = _make_message_with_reply(
        text="Краб, поздоровайся с новеньким",
        reply_user_id=8441281276,
        reply_first_name="Polda",
    )
    block = build_context_block(msg, is_owner=True)
    # Hint должен содержать предупреждение о generic-обращениях
    assert "путник" in block or "generic" in block.lower() or "НЕ используй" in block


def test_build_context_block_existing_tests_not_broken() -> None:
    """Стандартный build_context_block без reply → backward compat (нет hint, есть [context]/[policy])."""
    msg = _make_message_no_reply(text="Привет!")
    block = build_context_block(msg, is_owner=False)
    assert "[context]" in block
    assert "[/context]" in block
    assert "[policy]" in block
    assert "greeting_target_hint" not in block
