# -*- coding: utf-8 -*-
"""
Unit-тесты `TypingKeepalive` (Session 11, fix #6 — stuck «typing» indicator).

Проверяем:
- initial TYPING + финальный CANCEL;
- CANCEL гарантирован даже при exception внутри `async with`;
- транспортные ошибки `send_chat_action` не ломают context manager;
- cancellation внутри блока (Wave-2 stagnation cancel) корректно
  проходит cleanup путь.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from pyrogram import enums

from src.userbot.typing_keepalive import TypingKeepalive


def _action_of_call(call) -> str:
    """Достаёт action (positional или kwarg) из mock-вызова и приводит к upper-case str."""
    if len(call.args) >= 2:
        action = call.args[1]
    else:
        action = call.kwargs.get("action")
    return str(action).upper()


@pytest.mark.asyncio
async def test_typing_keepalive_sends_initial_and_cancel():
    """Минимальный happy-path: как минимум один TYPING + финальный CANCEL."""
    bot = AsyncMock()
    bot.send_chat_action = AsyncMock()
    async with TypingKeepalive(bot, 123):
        # Даём чуть времени keep-alive циклу проснуться, но не ждём 4 сек.
        await asyncio.sleep(0.05)
    # Минимум 2 вызова: initial TYPING + финальный CANCEL.
    assert bot.send_chat_action.call_count >= 2
    calls = bot.send_chat_action.call_args_list
    # Первый — TYPING.
    assert "TYPING" in _action_of_call(calls[0])
    # Последний — CANCEL.
    assert "CANCEL" in _action_of_call(calls[-1])


@pytest.mark.asyncio
async def test_typing_keepalive_cancel_on_exit():
    """Выход из `async with` без ожиданий — CANCEL всё равно шлётся."""
    bot = AsyncMock()
    bot.send_chat_action = AsyncMock()
    async with TypingKeepalive(bot, 123):
        pass
    # Последний вызов — CANCEL.
    last_call = bot.send_chat_action.call_args_list[-1]
    assert "CANCEL" in _action_of_call(last_call)


@pytest.mark.asyncio
async def test_typing_keepalive_cancel_on_exception():
    """При exception внутри блока CANCEL всё равно должен уйти (гарантия finalizer-а)."""
    bot = AsyncMock()
    bot.send_chat_action = AsyncMock()

    class _BoomError(RuntimeError):
        pass

    with pytest.raises(_BoomError):
        async with TypingKeepalive(bot, 456):
            raise _BoomError("llm failed mid-request")

    # Финальный CANCEL обязан пройти даже при exception.
    last_call = bot.send_chat_action.call_args_list[-1]
    assert "CANCEL" in _action_of_call(last_call)
    # Exception не должен был «съесть» manager — мы уже проверили pytest.raises.


@pytest.mark.asyncio
async def test_typing_keepalive_cancel_on_asyncio_cancelled():
    """Cancellation внутри блока (Wave-2 stagnation cancel) — CANCEL action всё равно уходит."""
    bot = AsyncMock()
    bot.send_chat_action = AsyncMock()

    with pytest.raises(asyncio.CancelledError):
        async with TypingKeepalive(bot, 789):
            raise asyncio.CancelledError("stagnation_cancel")

    last_call = bot.send_chat_action.call_args_list[-1]
    assert "CANCEL" in _action_of_call(last_call)


@pytest.mark.asyncio
async def test_typing_keepalive_tolerates_send_error():
    """Сетевые ошибки `send_chat_action` не ломают манагер (best-effort)."""
    bot = AsyncMock()
    bot.send_chat_action = AsyncMock(side_effect=Exception("network unreachable"))
    # Сам выход из `async with` НЕ должен кидать — manager подавляет сетевые сбои.
    async with TypingKeepalive(bot, 111):
        pass
    # Попытки были сделаны (initial + финальный CANCEL).
    assert bot.send_chat_action.call_count >= 2


@pytest.mark.asyncio
async def test_typing_keepalive_custom_action():
    """Можно подставить произвольный ChatAction (например, RECORD_AUDIO)."""
    bot = AsyncMock()
    bot.send_chat_action = AsyncMock()
    async with TypingKeepalive(bot, 222, action=enums.ChatAction.RECORD_AUDIO):
        pass
    # Первый action — RECORD_AUDIO (кастомный), последний — CANCEL (штатный cleanup).
    first_call = bot.send_chat_action.call_args_list[0]
    assert "RECORD_AUDIO" in _action_of_call(first_call)
    last_call = bot.send_chat_action.call_args_list[-1]
    assert "CANCEL" in _action_of_call(last_call)


@pytest.mark.asyncio
async def test_typing_keepalive_periodic_resends():
    """С коротким интервалом убеждаемся что есть ≥2 TYPING-вызова + финальный CANCEL."""
    bot = AsyncMock()
    bot.send_chat_action = AsyncMock()
    async with TypingKeepalive(bot, 333, interval_sec=1.0):
        # Держимся чуть дольше одного цикла — keep-alive обязан повторить action.
        await asyncio.sleep(1.2)
    typing_count = sum(
        1 for call in bot.send_chat_action.call_args_list if "TYPING" in _action_of_call(call)
    )
    assert typing_count >= 2  # initial + как минимум 1 re-send
    # Финальный вызов — всё равно CANCEL.
    last_call = bot.send_chat_action.call_args_list[-1]
    assert "CANCEL" in _action_of_call(last_call)
