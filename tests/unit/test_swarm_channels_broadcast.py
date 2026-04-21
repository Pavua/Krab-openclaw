# -*- coding: utf-8 -*-
"""
tests/unit/test_swarm_channels_broadcast.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Тесты для SwarmChannels.broadcast_to_topic (публичный хелпер).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.swarm_channels import SwarmChannels


def _make_channels(forum_chat_id: int = -1001234567890) -> SwarmChannels:
    """Создаёт SwarmChannels с замоканным состоянием (без IO)."""
    with patch.object(SwarmChannels, "_load", return_value=None), \
         patch.object(SwarmChannels, "_save", return_value=None):
        ch = SwarmChannels()
    ch._forum_chat_id = forum_chat_id
    ch._team_topics = {
        "traders": 101,
        "coders": 102,
        "analysts": 103,
        "creative": 104,
        "crossteam": 105,
    }
    return ch


@pytest.mark.asyncio
async def test_broadcast_resolves_team_key():
    """topic_id берётся из _team_topics по строковому ключу."""
    ch = _make_channels()
    mock_send = AsyncMock()
    ch._send_message = mock_send

    result = await ch.broadcast_to_topic("traders", "hello traders")

    assert result is True
    mock_send.assert_awaited_once_with(
        ch._forum_chat_id,
        "hello traders",
        topic_id=101,
        client=None,
    )


@pytest.mark.asyncio
async def test_broadcast_accepts_raw_int_topic_id():
    """Числовой topic_id передаётся напрямую без lookup."""
    ch = _make_channels()
    mock_send = AsyncMock()
    ch._send_message = mock_send

    result = await ch.broadcast_to_topic(999, "direct topic post")

    assert result is True
    mock_send.assert_awaited_once_with(
        ch._forum_chat_id,
        "direct topic post",
        topic_id=999,
        client=None,
    )


@pytest.mark.asyncio
async def test_broadcast_missing_key_returns_false():
    """Несуществующий строковый ключ возвращает False без вызова _send_message."""
    ch = _make_channels()
    mock_send = AsyncMock()
    ch._send_message = mock_send

    result = await ch.broadcast_to_topic("unknown_team", "will not send")

    assert result is False
    mock_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_broadcast_no_forum_returns_false():
    """Без настроенного forum_chat_id сразу возвращает False."""
    ch = _make_channels()
    ch._forum_chat_id = None
    mock_send = AsyncMock()
    ch._send_message = mock_send

    result = await ch.broadcast_to_topic("traders", "no forum")

    assert result is False
    mock_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_broadcast_passes_custom_client():
    """Кастомный client прокидывается в _send_message."""
    ch = _make_channels()
    mock_send = AsyncMock()
    ch._send_message = mock_send
    custom_client = MagicMock()

    result = await ch.broadcast_to_topic("crossteam", "cross msg", client=custom_client)

    assert result is True
    mock_send.assert_awaited_once_with(
        ch._forum_chat_id,
        "cross msg",
        topic_id=105,
        client=custom_client,
    )


@pytest.mark.asyncio
async def test_broadcast_send_exception_returns_false():
    """Если _send_message бросает, возвращаем False (не пробрасываем)."""
    ch = _make_channels()
    ch._send_message = AsyncMock(side_effect=RuntimeError("flood"))

    result = await ch.broadcast_to_topic("coders", "risky post")

    assert result is False


@pytest.mark.asyncio
async def test_broadcast_case_insensitive_key():
    """Ключ команды нечувствителен к регистру."""
    ch = _make_channels()
    mock_send = AsyncMock()
    ch._send_message = mock_send

    result = await ch.broadcast_to_topic("TRADERS", "upper case key")

    assert result is True
    mock_send.assert_awaited_once_with(
        ch._forum_chat_id,
        "upper case key",
        topic_id=101,
        client=None,
    )
