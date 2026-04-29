# -*- coding: utf-8 -*-
"""
Тесты для src/core/sender_context.py.

Покрываем:
1) build_context_block — структура и поля [context] блока
2) attach_to_system_prompt — prepend в system prompt
3) is_owner_user_id — определение owner по user_id
4) build_sender_context_from_message — one-shot helper
5) Граничные случаи (None, пустые поля, отсутствие from_user)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.core.sender_context import (
    attach_to_system_prompt,
    build_context_block,
    build_sender_context_from_message,
    is_owner_user_id,
)


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


def _make_message(
    *,
    user_id: int = 369342975,
    username: str = "SwMaster",
    first_name: str = "Konstantin",
    chat_type: str = "supergroup",
    chat_title: str = "ЧАТ How2AI",
    outgoing: bool = False,
) -> MagicMock:
    """Создаёт mock pyrogram Message с нужными атрибутами."""
    user = SimpleNamespace(
        id=user_id,
        username=username,
        first_name=first_name,
    )
    chat_type_ns = SimpleNamespace(value=chat_type)
    chat = SimpleNamespace(
        type=chat_type_ns,
        title=chat_title,
    )
    msg = MagicMock()
    msg.from_user = user
    msg.chat = chat
    msg.outgoing = outgoing
    return msg


def _make_private_message(
    *,
    user_id: int = 111111,
    username: str = "pablito",
    first_name: str = "Pavel",
) -> MagicMock:
    """Приватный чат — title отсутствует."""
    user = SimpleNamespace(
        id=user_id,
        username=username,
        first_name=first_name,
    )
    chat_type_ns = SimpleNamespace(value="private")
    chat = SimpleNamespace(
        type=chat_type_ns,
        title=None,
    )
    msg = MagicMock()
    msg.from_user = user
    msg.chat = chat
    msg.outgoing = False
    return msg


# ---------------------------------------------------------------------------
# Тесты build_context_block
# ---------------------------------------------------------------------------


def test_build_context_block_contains_tags() -> None:
    """Блок начинается с [context] и содержит [/context] (за ним идёт [policy]…[/policy])."""
    msg = _make_message()
    block = build_context_block(msg, is_owner=False)
    assert block.startswith("[context]")
    # Wave 11: после [/context] добавляется persona-policy блок [policy]…[/policy].
    assert "[/context]" in block
    assert block.strip().endswith("[/policy]")


def test_build_context_block_contains_sender_user_id() -> None:
    """sender_user_id корректно извлекается."""
    msg = _make_message(user_id=369342975)
    block = build_context_block(msg, is_owner=False)
    assert "sender_user_id: 369342975" in block


def test_build_context_block_contains_username_with_at() -> None:
    """username форматируется с @ префиксом."""
    msg = _make_message(username="SwMaster")
    block = build_context_block(msg, is_owner=False)
    assert "sender_username: @SwMaster" in block


def test_build_context_block_contains_first_name() -> None:
    """first_name присутствует в блоке."""
    msg = _make_message(first_name="Konstantin")
    block = build_context_block(msg, is_owner=False)
    assert "sender_first_name: Konstantin" in block


def test_build_context_block_guest_is_owner_false() -> None:
    """Гостевое сообщение → is_owner: false."""
    msg = _make_message()
    block = build_context_block(msg, is_owner=False)
    assert "is_owner: false" in block


def test_build_context_block_owner_is_owner_true() -> None:
    """Owner сообщение → is_owner: true."""
    msg = _make_message()
    block = build_context_block(msg, is_owner=True)
    assert "is_owner: true" in block


def test_build_context_block_chat_type() -> None:
    """chat_type корректно передаётся."""
    msg = _make_message(chat_type="supergroup")
    block = build_context_block(msg, is_owner=False)
    assert "chat_type: supergroup" in block


def test_build_context_block_chat_title_present_in_group() -> None:
    """chat_title присутствует для групповых чатов."""
    msg = _make_message(chat_title="ЧАТ How2AI")
    block = build_context_block(msg, is_owner=False)
    assert "chat_title: ЧАТ How2AI" in block


def test_build_context_block_no_chat_title_for_private() -> None:
    """Приватный чат без title — поле chat_title не добавляется."""
    msg = _make_private_message()
    block = build_context_block(msg, is_owner=False)
    assert "chat_title" not in block


def test_build_context_block_no_username_fallback() -> None:
    """Отсутствие username → fallback-значение в скобках."""
    msg = _make_message(username="")
    block = build_context_block(msg, is_owner=False)
    assert "sender_username: (нет username)" in block


def test_build_context_block_no_from_user_safe_fallback() -> None:
    """Отсутствие from_user → безопасный fallback, не бросает исключений."""
    msg = MagicMock()
    msg.from_user = None
    msg.chat = SimpleNamespace(type=SimpleNamespace(value="private"), title=None)
    msg.outgoing = False
    # Не должно бросить исключение
    block = build_context_block(msg, is_owner=False)
    assert "[context]" in block
    assert "[/context]" in block
    assert "is_owner: false" in block


# ---------------------------------------------------------------------------
# Тесты attach_to_system_prompt
# ---------------------------------------------------------------------------


def test_attach_to_system_prompt_prepends_block() -> None:
    """context_block предшествует system_prompt."""
    block = "[context]\nis_owner: false\n[/context]"
    prompt = "Ты — ассистент."
    result = attach_to_system_prompt(prompt, block)
    assert result.startswith("[context]")
    assert "Ты — ассистент." in result
    # Блок должен быть ДО промпта
    assert result.index("[context]") < result.index("Ты — ассистент.")


def test_attach_to_system_prompt_empty_block_returns_prompt() -> None:
    """Пустой block → возвращает system_prompt без изменений."""
    prompt = "Ты — ассистент."
    result = attach_to_system_prompt(prompt, "")
    assert result == prompt


def test_attach_to_system_prompt_empty_prompt_returns_block() -> None:
    """Пустой prompt → возвращает только block."""
    block = "[context]\nis_owner: true\n[/context]"
    result = attach_to_system_prompt("", block)
    assert result == block


def test_attach_to_system_prompt_separator() -> None:
    """Между блоком и промптом есть разделитель (двойной перевод строки)."""
    block = "[context]\nis_owner: false\n[/context]"
    prompt = "Инструкции."
    result = attach_to_system_prompt(prompt, block)
    assert "\n\n" in result


# ---------------------------------------------------------------------------
# Тесты is_owner_user_id
# ---------------------------------------------------------------------------


def test_is_owner_user_id_match() -> None:
    """Одинаковые user_id → True."""
    assert is_owner_user_id(111222, 111222) is True


def test_is_owner_user_id_match_str_int() -> None:
    """Смешанные типы (str/int) → корректное сравнение."""
    assert is_owner_user_id("111222", 111222) is True


def test_is_owner_user_id_no_match() -> None:
    """Разные user_id → False."""
    assert is_owner_user_id(111222, 999888) is False


def test_is_owner_user_id_none_returns_false() -> None:
    """None user_id → False (нет исключений)."""
    assert is_owner_user_id(None, 111222) is False


def test_is_owner_user_id_none_self_returns_false() -> None:
    """None self_user_id → False."""
    assert is_owner_user_id(111222, None) is False


# ---------------------------------------------------------------------------
# Тесты build_sender_context_from_message
# ---------------------------------------------------------------------------


def test_build_sender_context_from_message_guest() -> None:
    """Гостевое сообщение → is_owner: false."""
    msg = _make_message(user_id=369342975, outgoing=False)
    block = build_sender_context_from_message(msg, self_user_id=111111)
    assert "is_owner: false" in block


def test_build_sender_context_from_message_owner_by_self_user_id() -> None:
    """Сообщение от self_user_id → is_owner: true."""
    msg = _make_message(user_id=111111, outgoing=False)
    block = build_sender_context_from_message(msg, self_user_id=111111)
    assert "is_owner: true" in block


def test_build_sender_context_from_message_outgoing_is_owner() -> None:
    """Исходящее сообщение (outgoing=True) → is_owner: true."""
    msg = _make_message(outgoing=True)
    block = build_sender_context_from_message(msg)
    assert "is_owner: true" in block


def test_build_sender_context_from_message_explicit_is_owner_override() -> None:
    """Явный is_owner=True переопределяет автоматическое определение."""
    # user_id != self_user_id, но is_owner=True передан явно
    msg = _make_message(user_id=369342975, outgoing=False)
    block = build_sender_context_from_message(msg, self_user_id=111111, is_owner=True)
    assert "is_owner: true" in block


def test_build_sender_context_from_message_full_block_structure() -> None:
    """One-shot helper возвращает полноценный [context] блок."""
    msg = _make_message(
        user_id=369342975,
        username="SwMaster",
        first_name="Konstantin",
        chat_type="supergroup",
        chat_title="How2AI",
    )
    block = build_sender_context_from_message(msg, self_user_id=111111)
    assert "[context]" in block
    assert "sender_user_id: 369342975" in block
    assert "@SwMaster" in block
    assert "Konstantin" in block
    assert "supergroup" in block
    assert "is_owner: false" in block
    assert "[/context]" in block
