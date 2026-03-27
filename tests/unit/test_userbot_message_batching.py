# -*- coding: utf-8 -*-
"""
Тесты batch-склейки последовательных private-сообщений.

Проверяем два ключевых инварианта:
1) несколько быстрых plain-text сообщений одного отправителя должны уходить
   в LLM как один склеенный запрос;
2) follow-up handler для уже поглощённого сообщения обязан тихо завершиться,
   а не запускать вторую обработку.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pyrogram import enums

import src.userbot_bridge as userbot_bridge_module
from src.core.access_control import AccessLevel, AccessProfile
from src.userbot_bridge import KraabUserbot


def _make_batching_bot_stub() -> KraabUserbot:
    """Создаёт минимальный bot stub для проверки batching-сценария."""
    bot = KraabUserbot.__new__(KraabUserbot)
    bot.me = SimpleNamespace(id=777, username="owner")
    bot.current_role = "default"
    bot.voice_mode = False
    bot._known_commands = set()
    bot._disclosure_sent_for_chat_ids = set()
    bot._batched_followup_message_ids = {}
    bot._chat_processing_locks = {}

    bot._message_has_audio = Mock(return_value=False)
    bot._is_trigger = Mock(return_value=False)
    bot._get_clean_text = Mock(side_effect=lambda text: (text or "").strip())
    bot._get_chat_context = AsyncMock(return_value="")
    bot._safe_edit = AsyncMock(side_effect=lambda msg, text: msg)
    bot._apply_optional_disclosure = Mock(side_effect=lambda **kwargs: kwargs["text"])
    bot._split_message = Mock(side_effect=lambda text: [text])
    bot._looks_like_runtime_truth_question = Mock(return_value=False)
    bot._looks_like_model_status_question = Mock(return_value=False)
    bot._looks_like_capability_status_question = Mock(return_value=False)
    bot._looks_like_commands_question = Mock(return_value=False)
    bot._looks_like_integrations_question = Mock(return_value=False)
    bot._build_system_prompt_for_sender = Mock(return_value="SYSTEM")
    bot._build_runtime_chat_scope_id = Mock(return_value="runtime-chat-123")
    bot._build_effective_user_query = Mock(side_effect=lambda *, query, has_images: query)
    bot._extract_live_stream_text = Mock(side_effect=lambda text, allow_reasoning=False: text)
    bot._strip_transport_markup = Mock(side_effect=lambda text: text)
    bot._apply_deferred_action_guard = Mock(side_effect=lambda text: text)
    bot._remember_hidden_reasoning_trace = Mock()
    bot._should_send_voice_reply = Mock(return_value=False)
    bot._should_send_full_text_reply = Mock(return_value=True)
    bot._should_force_cloud_for_photo_route = Mock(return_value=False)
    bot._sync_incoming_message_to_inbox = Mock(return_value=None)
    bot._record_incoming_reply_to_inbox = Mock()
    bot._deliver_response_parts = AsyncMock(
        side_effect=lambda **kwargs: {
            "delivery_mode": "edit",
            "text_message_ids": [],
            "parts_count": 1,
            "full_response": kwargs["full_response"],
        }
    )
    return bot


def _make_message(
    *,
    message_id: int,
    text: str,
    sender_id: int = 42,
    seconds_offset: float = 0.0,
) -> SimpleNamespace:
    """Создаёт минимальный объект сообщения, похожий на Pyrogram Message."""
    return SimpleNamespace(
        id=message_id,
        text=text,
        caption=None,
        photo=None,
        voice=None,
        audio=None,
        date=datetime(2026, 3, 19, 1, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds_offset),
        from_user=SimpleNamespace(id=sender_id, username="tester", is_bot=False),
        chat=SimpleNamespace(id=123, type=enums.ChatType.PRIVATE),
        reply_to_message=None,
        reply=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=123), text="", caption="", id=9000)),
    )


@pytest.mark.asyncio
async def test_private_text_burst_coalesces_followup_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Несколько быстрых private-сообщений должны уйти в модель как один query."""
    bot = _make_batching_bot_stub()
    first = _make_message(message_id=100, text="первая часть", seconds_offset=0.0)
    second = _make_message(message_id=101, text="вторая часть", seconds_offset=0.4)
    third = _make_message(message_id=102, text="третья часть", seconds_offset=0.8)
    sent_queries: list[str] = []

    async def _fake_history(chat_id: int, limit: int = 0):
        _ = (chat_id, limit)
        for row in (third, second, first):
            yield row

    async def _fake_stream(**kwargs):
        sent_queries.append(kwargs["message"])
        yield "Склеенный ответ"

    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        send_message=AsyncMock(),
        send_voice=AsyncMock(),
        get_chat_history=_fake_history,
    )

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)
    monkeypatch.setattr(userbot_bridge_module.config, "TELEGRAM_MESSAGE_BATCH_WINDOW_SEC", 0.01, raising=False)
    monkeypatch.setattr(userbot_bridge_module.config, "TELEGRAM_MESSAGE_BATCH_MAX_MESSAGES", 6, raising=False)
    monkeypatch.setattr(userbot_bridge_module.config, "TELEGRAM_MESSAGE_BATCH_MAX_CHARS", 12000, raising=False)
    # Отключаем background handoff для синхронного теста
    monkeypatch.setattr(userbot_bridge_module.config, "USERBOT_BACKGROUND_LLM_HANDOFF", False, raising=False)

    access_profile = AccessProfile(
        level=AccessLevel.FULL,
        source="unit-test",
        matched_subject="tester",
    )
    await bot._process_message_serialized(
        message=first,
        user=first.from_user,
        access_profile=access_profile,
        is_allowed_sender=True,
        chat_id=str(first.chat.id),
    )

    assert sent_queries == ["первая часть\n\nвторая часть\n\nтретья часть"]
    delivered_text = bot._deliver_response_parts.await_args.kwargs["full_response"]
    assert delivered_text == "Склеенный ответ"
    assert bot._consume_batched_followup_message_id(chat_id="123", message_id="101") is True
    assert bot._consume_batched_followup_message_id(chat_id="123", message_id="102") is True


@pytest.mark.asyncio
async def test_private_text_burst_retries_history_until_followups_appear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Batching должен переживать краткую задержку появления follower-сообщений в history."""
    bot = _make_batching_bot_stub()
    first = _make_message(message_id=300, text="alpha", seconds_offset=0.0)
    second = _make_message(message_id=301, text="beta", seconds_offset=0.2)
    third = _make_message(message_id=302, text="gamma", seconds_offset=0.4)
    sent_queries: list[str] = []
    history_reads = {"count": 0}

    async def _fake_history(chat_id: int, limit: int = 0):
        _ = (chat_id, limit)
        history_reads["count"] += 1
        if history_reads["count"] == 1:
            for row in (first,):
                yield row
            return
        for row in (third, second, first):
            yield row

    async def _fake_stream(**kwargs):
        sent_queries.append(kwargs["message"])
        yield "Склеенный delayed-ответ"

    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        send_message=AsyncMock(),
        send_voice=AsyncMock(),
        get_chat_history=_fake_history,
    )

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)
    monkeypatch.setattr(userbot_bridge_module.config, "TELEGRAM_MESSAGE_BATCH_WINDOW_SEC", 0.01, raising=False)
    monkeypatch.setattr(userbot_bridge_module.config, "TELEGRAM_MESSAGE_BATCH_MAX_MESSAGES", 6, raising=False)
    monkeypatch.setattr(userbot_bridge_module.config, "TELEGRAM_MESSAGE_BATCH_MAX_CHARS", 12000, raising=False)
    monkeypatch.setattr(
        userbot_bridge_module.config,
        "TELEGRAM_MESSAGE_BATCH_SETTLE_INTERVAL_SEC",
        0.01,
        raising=False,
    )
    monkeypatch.setattr(
        userbot_bridge_module.config,
        "TELEGRAM_MESSAGE_BATCH_SETTLE_MAX_EXTRA_SEC",
        0.05,
        raising=False,
    )
    monkeypatch.setattr(userbot_bridge_module.config, "USERBOT_BACKGROUND_LLM_HANDOFF", False, raising=False)

    access_profile = AccessProfile(
        level=AccessLevel.FULL,
        source="unit-test",
        matched_subject="tester",
    )
    await bot._process_message_serialized(
        message=first,
        user=first.from_user,
        access_profile=access_profile,
        is_allowed_sender=True,
        chat_id=str(first.chat.id),
    )

    assert history_reads["count"] >= 2
    assert sent_queries == ["alpha\n\nbeta\n\ngamma"]
    delivered_text = bot._deliver_response_parts.await_args.kwargs["full_response"]
    assert delivered_text == "Склеенный delayed-ответ"
    assert bot._consume_batched_followup_message_id(chat_id="123", message_id="301") is True
    assert bot._consume_batched_followup_message_id(chat_id="123", message_id="302") is True


@pytest.mark.asyncio
async def test_process_message_skips_absorbed_followup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Handler не должен повторно запускать обработку для уже поглощённого follower-сообщения."""
    bot = _make_batching_bot_stub()
    bot._process_message_serialized = AsyncMock()

    absorbed = _make_message(message_id=202, text="вторая часть")

    bot._remember_batched_followup_message_ids(chat_id="123", message_ids=["202"])

    await bot._process_message(absorbed)

    bot._process_message_serialized.assert_not_awaited()
