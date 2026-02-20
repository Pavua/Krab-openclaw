# -*- coding: utf-8 -*-
"""
Тесты дедупликации burst-буфера пересланных сообщений.

Нужны, чтобы одно и то же forwarded-сообщение (chat_id + message_id)
не попадало в буфер дважды при срабатывании нескольких обработчиков.
"""

from types import SimpleNamespace

from src.handlers.ai import _append_forward_to_burst_state


def _make_message(chat_id: int, message_id: int):
    """Минимальный объект сообщения для unit-теста."""
    return SimpleNamespace(chat=SimpleNamespace(id=chat_id), id=message_id)


def test_append_forward_to_burst_state_skips_duplicate_message() -> None:
    """Дубликат (тот же chat_id/message_id) должен игнорироваться."""
    state = {"messages": []}
    message = _make_message(chat_id=100, message_id=777)

    assert _append_forward_to_burst_state(state, message, max_items=8) is True
    assert _append_forward_to_burst_state(state, message, max_items=8) is False
    assert len(state["messages"]) == 1


def test_append_forward_to_burst_state_keeps_non_duplicates() -> None:
    """Разные message_id должны накапливаться в буфере."""
    state = {"messages": []}

    assert _append_forward_to_burst_state(state, _make_message(100, 1), max_items=8) is True
    assert _append_forward_to_burst_state(state, _make_message(100, 2), max_items=8) is True
    assert _append_forward_to_burst_state(state, _make_message(100, 3), max_items=8) is True
    assert [msg.id for msg in state["messages"]] == [1, 2, 3]


def test_append_forward_to_burst_state_applies_ring_limit() -> None:
    """Буфер должен работать как кольцо с ограничением max_items*2."""
    state = {"messages": []}
    max_items = 3

    for idx in range(1, 10):
        _append_forward_to_burst_state(state, _make_message(100, idx), max_items=max_items)

    assert [msg.id for msg in state["messages"]] == [4, 5, 6, 7, 8, 9]
