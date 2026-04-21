# -*- coding: utf-8 -*-
"""Тесты reread_chat asyncio.Event API (Chado §2 P2)."""

import asyncio

import pytest

from src.core.chat_window_manager import ChatWindowManager


@pytest.fixture
def mgr() -> ChatWindowManager:
    return ChatWindowManager()


def test_consume_after_signal_returns_true(mgr: ChatWindowManager) -> None:
    """signal_reread → consume_reread возвращает True."""
    mgr.signal_reread(42)
    assert mgr.consume_reread(42) is True


def test_second_consume_returns_false(mgr: ChatWindowManager) -> None:
    """Второй consume после первого возвращает False (одноразовый)."""
    mgr.signal_reread(42)
    mgr.consume_reread(42)
    assert mgr.consume_reread(42) is False


def test_consume_without_signal_returns_false(mgr: ChatWindowManager) -> None:
    """consume_reread без signal_reread возвращает False."""
    assert mgr.consume_reread(99) is False


def test_clear_reread_resets_signal(mgr: ChatWindowManager) -> None:
    """clear_reread сбрасывает сигнал без потребления."""
    mgr.signal_reread(7)
    mgr.clear_reread(7)
    assert mgr.consume_reread(7) is False


def test_get_reread_event_lazy_create(mgr: ChatWindowManager) -> None:
    """get_reread_event создаёт Event при первом обращении."""
    event = mgr.get_reread_event(123)
    assert isinstance(event, asyncio.Event)
    # Повторный вызов возвращает тот же объект
    assert mgr.get_reread_event(123) is event


def test_independent_events_per_chat(mgr: ChatWindowManager) -> None:
    """События независимы для разных chat_id."""
    mgr.signal_reread(1)
    assert mgr.consume_reread(1) is True
    assert mgr.consume_reread(2) is False


def test_signal_multiple_times_consume_once(mgr: ChatWindowManager) -> None:
    """Несколько signal подряд — consume возвращает True один раз."""
    mgr.signal_reread(5)
    mgr.signal_reread(5)
    assert mgr.consume_reread(5) is True
    assert mgr.consume_reread(5) is False
