# -*- coding: utf-8 -*-
"""Тесты формирования reply/forward контекста для автоответчика."""

from __future__ import annotations

from types import SimpleNamespace

from src.handlers.ai import _build_author_context, _build_forward_context, _build_reply_context


class _Msg(SimpleNamespace):
    """Минимальная заглушка Telegram Message для unit-тестов."""


def test_build_forward_context_marks_as_foreign_content() -> None:
    """Форвард должен явно помечаться как чужой контент для анализа."""
    msg = _Msg(
        forward_from=None,
        forward_sender_name="Другой пользователь",
        forward_from_chat=None,
        forward_date="2026-02-16 14:00:00",
        is_automatic_forward=False,
    )

    context = _build_forward_context(msg, enabled=True)

    assert "пересланный материал" in context
    assert "Другой пользователь" in context
    assert "Автофорвард: False" in context


def test_build_reply_context_includes_original_message() -> None:
    """Reply-контекст должен включать текст исходного сообщения."""
    reply_to = _Msg(
        text="Исходный текст для ответа",
        caption=None,
        voice=None,
        audio=None,
        video=None,
        animation=None,
        photo=None,
        sticker=None,
        document=None,
        from_user=SimpleNamespace(username="origin_user", first_name="Origin"),
    )
    msg = _Msg(reply_to_message=reply_to)

    context = _build_reply_context(msg)

    assert "REPLY CONTEXT" in context
    assert "@origin_user" in context
    assert "Исходный текст" in context


def test_build_author_context_marks_participant_in_group() -> None:
    """В группах контекст автора должен явно отмечать participant, а не owner."""
    msg = _Msg(
        chat=SimpleNamespace(type=SimpleNamespace(name="GROUP")),
        from_user=SimpleNamespace(id=777, username="guest_user", first_name="Guest"),
    )
    context = _build_author_context(msg, is_owner_sender=False)

    assert "author=@guest_user" in context
    assert "author_role=participant" in context
    assert "chat_type=group" in context
