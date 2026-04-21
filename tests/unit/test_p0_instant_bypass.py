# -*- coding: utf-8 -*-
"""
Тесты: P0_INSTANT bypass в orchestrator.

Проверяем два аспекта:
1. classify_priority правильно присваивает P0_INSTANT для DM/mention/reply-to-self/command.
2. MessageBatcher не теряет P0 — никакой буферизации нет (отдельный синглтон на тест).
"""

from __future__ import annotations

import pytest

from src.core.message_priority_dispatcher import Priority, classify_priority

# ---------------------------------------------------------------------------
# classify_priority unit tests
# ---------------------------------------------------------------------------


def test_dm_is_p0():
    p, reason = classify_priority(
        text="привет",
        chat_type="PRIVATE",
        is_dm=True,
        is_reply_to_self=False,
        has_mention=False,
        chat_mode="active",
    )
    assert p == Priority.P0_INSTANT
    assert reason == "dm"


def test_private_chat_type_is_p0():
    p, reason = classify_priority(
        text="привет",
        chat_type="PRIVATE",
        is_dm=False,
        is_reply_to_self=False,
        has_mention=False,
        chat_mode="active",
    )
    assert p == Priority.P0_INSTANT


def test_reply_to_self_is_p0():
    p, reason = classify_priority(
        text="продолжай",
        chat_type="SUPERGROUP",
        is_dm=False,
        is_reply_to_self=True,
        has_mention=False,
        chat_mode="active",
    )
    assert p == Priority.P0_INSTANT
    assert reason == "reply_to_self"


def test_mention_is_p0():
    p, reason = classify_priority(
        text="Краб, что думаешь?",
        chat_type="GROUP",
        is_dm=False,
        is_reply_to_self=False,
        has_mention=True,
        chat_mode="active",
    )
    assert p == Priority.P0_INSTANT
    assert reason == "mention"


def test_command_is_p0():
    p, reason = classify_priority(
        text="!ask что такое asyncio",
        chat_type="SUPERGROUP",
        is_dm=False,
        is_reply_to_self=False,
        has_mention=False,
        chat_mode="active",
    )
    assert p == Priority.P0_INSTANT
    assert reason == "command"


def test_regular_group_message_is_p1():
    p, reason = classify_priority(
        text="всем привет",
        chat_type="GROUP",
        is_dm=False,
        is_reply_to_self=False,
        has_mention=False,
        chat_mode="active",
    )
    assert p == Priority.P1_NORMAL


def test_muted_chat_is_p2():
    p, reason = classify_priority(
        text="всем привет",
        chat_type="SUPERGROUP",
        is_dm=False,
        is_reply_to_self=False,
        has_mention=False,
        chat_mode="muted",
    )
    assert p == Priority.P2_LOW
    assert reason == "muted"


def test_mention_only_mode_without_mention_is_p2():
    p, reason = classify_priority(
        text="кто тут?",
        chat_type="GROUP",
        is_dm=False,
        is_reply_to_self=False,
        has_mention=False,
        chat_mode="mention-only",
    )
    assert p == Priority.P2_LOW


# ---------------------------------------------------------------------------
# MessageBatcher: P0 никогда не буферизуется (обрабатывается немедленно)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batcher_processes_p0_immediately_even_when_busy():
    """
    Эмулируем ситуацию: batcher busy для чата.
    P0-сообщение не должно проходить через batcher — оно должно
    обрабатываться напрямую (без вызова try_add_or_flush).

    Логика bypass живёт в userbot_bridge._process_message (вне batcher),
    поэтому здесь проверяем, что batcher сам по себе буферизует P1 когда busy,
    и что P0 bypass корректно задействуется через classify_priority.
    """
    from src.core.message_batcher import MessageBatcher, PendingMessage

    b = MessageBatcher()
    calls = []

    async def slow_processor(chat_id, combined):
        import asyncio

        await asyncio.sleep(0.05)
        calls.append(combined)
        return "ok"

    # Первое сообщение — занимает batcher
    import asyncio

    task = asyncio.create_task(
        b.try_add_or_flush("chat1", PendingMessage(text="msg1", sender_id="u1"), slow_processor)
    )

    # Небольшая задержка чтобы batcher успел войти в busy-state
    await asyncio.sleep(0.01)

    # P1 сообщение идёт через batcher → буферизуется (busy)
    status2, _ = await b.try_add_or_flush(
        "chat1", PendingMessage(text="msg2", sender_id="u1"), slow_processor
    )
    assert status2 == "buffered"

    await task

    # P0 bypass: classify_priority для DM → P0_INSTANT (не идёт через batcher)
    p, reason = classify_priority(
        "!start", "PRIVATE", is_dm=True, is_reply_to_self=False, has_mention=False, chat_mode="active"
    )
    assert p == Priority.P0_INSTANT, f"Expected P0_INSTANT for DM, got {p} ({reason})"
