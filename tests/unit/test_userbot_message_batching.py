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
        date=datetime(2026, 3, 19, 1, 0, 0, tzinfo=timezone.utc)
        + timedelta(seconds=seconds_offset),
        from_user=SimpleNamespace(id=sender_id, username="tester", is_bot=False),
        chat=SimpleNamespace(id=123, type=enums.ChatType.PRIVATE),
        reply_to_message=None,
        reply=AsyncMock(
            return_value=SimpleNamespace(chat=SimpleNamespace(id=123), text="", caption="", id=9000)
        ),
    )


@pytest.mark.asyncio
async def test_private_text_burst_coalesces_followup_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    monkeypatch.setattr(
        userbot_bridge_module.config, "TELEGRAM_MESSAGE_BATCH_WINDOW_SEC", 0.01, raising=False
    )
    monkeypatch.setattr(
        userbot_bridge_module.config, "TELEGRAM_MESSAGE_BATCH_MAX_MESSAGES", 6, raising=False
    )
    monkeypatch.setattr(
        userbot_bridge_module.config, "TELEGRAM_MESSAGE_BATCH_MAX_CHARS", 12000, raising=False
    )
    # Отключаем background handoff для синхронного теста
    monkeypatch.setattr(
        userbot_bridge_module.config, "USERBOT_BACKGROUND_LLM_HANDOFF", False, raising=False
    )

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
    monkeypatch.setattr(
        userbot_bridge_module.config, "TELEGRAM_MESSAGE_BATCH_WINDOW_SEC", 0.01, raising=False
    )
    monkeypatch.setattr(
        userbot_bridge_module.config, "TELEGRAM_MESSAGE_BATCH_MAX_MESSAGES", 6, raising=False
    )
    monkeypatch.setattr(
        userbot_bridge_module.config, "TELEGRAM_MESSAGE_BATCH_MAX_CHARS", 12000, raising=False
    )
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
    monkeypatch.setattr(
        userbot_bridge_module.config, "USERBOT_BACKGROUND_LLM_HANDOFF", False, raising=False
    )

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


# ---------------------------------------------------------------------------
# B.5 (2026-04-09): group-chat burst coalescing
# ---------------------------------------------------------------------------
#
# До этого batcher работал только в private. Теперь он работает и в group/
# supergroup с важной дополнительной гарантией: absorbed сообщение должно
# САМО триггерить Краба (keyword trigger или reply-to-me). Это не даёт
# batcher'у проглотить unrelated сообщение соседа в group.
#
# Чтобы удобно тестировать — маленькая обёртка вокруг _coalesce_text_burst,
# которая принимает готовый список сообщений и прогоняет его напрямую.


def _make_group_message(
    *,
    message_id: int,
    text: str,
    sender_id: int = 42,
    seconds_offset: float = 0.0,
    chat_type: enums.ChatType = enums.ChatType.SUPERGROUP,
    reply_to_sender_id: int | None = None,
) -> SimpleNamespace:
    """Fake group/supergroup Pyrogram Message с опциональным reply_to_message."""
    reply_to = None
    if reply_to_sender_id is not None:
        reply_to = SimpleNamespace(
            from_user=SimpleNamespace(id=reply_to_sender_id, username="someone", is_bot=False),
            id=1,
        )
    return SimpleNamespace(
        id=message_id,
        text=text,
        caption=None,
        photo=None,
        voice=None,
        audio=None,
        date=datetime(2026, 4, 9, 1, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds_offset),
        from_user=SimpleNamespace(id=sender_id, username="tester", is_bot=False),
        chat=SimpleNamespace(id=-1002000000000, type=chat_type),
        reply_to_message=reply_to,
        reply=AsyncMock(
            return_value=SimpleNamespace(
                chat=SimpleNamespace(id=-1002000000000), text="", caption="", id=9000
            )
        ),
    )


def test_is_text_batch_candidate_private_accepts_same_sender() -> None:
    """В приватке любой same-sender plain-text — valid candidate."""
    bot = _make_batching_bot_stub()
    bot._is_trigger = Mock(return_value=False)
    msg = _make_message(message_id=1, text="просто мысль", sender_id=42)
    assert (
        bot._is_text_batch_candidate(
            message=msg,
            sender_id=42,
            is_private_chat=True,
            self_user_id=777,
        )
        is True
    )


def test_is_text_batch_candidate_group_rejects_non_trigger() -> None:
    """В группе обычный plain-text того же sender'а БЕЗ триггера не должен попадать в batch."""
    bot = _make_batching_bot_stub()
    bot._is_trigger = Mock(return_value=False)
    msg = _make_group_message(message_id=1, text="обычное сообщение в группу", sender_id=42)
    assert (
        bot._is_text_batch_candidate(
            message=msg,
            sender_id=42,
            is_private_chat=False,
            self_user_id=777,
        )
        is False
    )


def test_is_text_batch_candidate_group_accepts_triggered() -> None:
    """В группе сообщение с keyword-триггером становится valid batch candidate."""
    bot = _make_batching_bot_stub()
    bot._is_trigger = Mock(side_effect=lambda text: text.lower().startswith("краб"))
    msg = _make_group_message(message_id=1, text="Краб, расскажи про BTC", sender_id=42)
    assert (
        bot._is_text_batch_candidate(
            message=msg,
            sender_id=42,
            is_private_chat=False,
            self_user_id=777,
        )
        is True
    )


def test_is_text_batch_candidate_group_accepts_reply_to_self() -> None:
    """
    В группе сообщение, которое является reply на сообщение самого Краба,
    должно считаться targeted и попадать в batch даже без keyword-триггера.
    """
    bot = _make_batching_bot_stub()
    bot._is_trigger = Mock(return_value=False)
    msg = _make_group_message(
        message_id=1,
        text="да, кстати",
        sender_id=42,
        reply_to_sender_id=777,  # self.me.id
    )
    assert (
        bot._is_text_batch_candidate(
            message=msg,
            sender_id=42,
            is_private_chat=False,
            self_user_id=777,
        )
        is True
    )


def test_is_text_batch_candidate_group_rejects_reply_to_someone_else() -> None:
    """Reply на чужое сообщение — не targeted, не должен попадать в batch в группе."""
    bot = _make_batching_bot_stub()
    bot._is_trigger = Mock(return_value=False)
    msg = _make_group_message(
        message_id=1,
        text="да, кстати",
        sender_id=42,
        reply_to_sender_id=888,  # not self
    )
    assert (
        bot._is_text_batch_candidate(
            message=msg,
            sender_id=42,
            is_private_chat=False,
            self_user_id=777,
        )
        is False
    )


def test_is_text_batch_candidate_rejects_command_in_group_even_if_triggered() -> None:
    """Команды (!...) не батчатся никогда — в группе тоже."""
    bot = _make_batching_bot_stub()
    bot._is_trigger = Mock(return_value=True)
    msg = _make_group_message(message_id=1, text="!status", sender_id=42)
    assert (
        bot._is_text_batch_candidate(
            message=msg,
            sender_id=42,
            is_private_chat=False,
            self_user_id=777,
        )
        is False
    )


def test_is_text_batch_candidate_rejects_different_sender_in_group() -> None:
    """
    Даже если сообщение от другого участника триггерит Краба, оно НЕ должно
    попадать в batch anchor'а от 42 — batch всегда per-sender.
    """
    bot = _make_batching_bot_stub()
    bot._is_trigger = Mock(return_value=True)
    msg = _make_group_message(message_id=1, text="Краб, привет", sender_id=999)
    assert (
        bot._is_text_batch_candidate(
            message=msg,
            sender_id=42,
            is_private_chat=False,
            self_user_id=777,
        )
        is False
    )


@pytest.mark.asyncio
async def test_coalesce_text_burst_channels_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Каналы (broadcast) НЕ должны проходить через batcher — их семантика
    слишком отличается, а риск схлопнуть unrelated посты слишком велик.
    _coalesce_text_burst должен вернуть исходное сообщение без изменений.
    """
    bot = _make_batching_bot_stub()
    bot._is_trigger = Mock(return_value=True)
    bot.client = SimpleNamespace(get_chat_history=AsyncMock())

    channel_msg = _make_group_message(
        message_id=1,
        text="пост в канале",
        sender_id=42,
        chat_type=enums.ChatType.CHANNEL,
    )

    monkeypatch.setattr(
        userbot_bridge_module.config, "TELEGRAM_MESSAGE_BATCH_WINDOW_SEC", 0.01, raising=False
    )

    result_msg, result_query = await bot._coalesce_text_burst(
        message=channel_msg,
        user=channel_msg.from_user,
        query="пост в канале",
    )
    assert result_msg is channel_msg
    assert result_query == "пост в канале"
    # get_chat_history даже не должен быть вызван — мы сразу вышли на chat_type gate.
    assert not bot.client.get_chat_history.called


@pytest.mark.asyncio
async def test_coalesce_text_burst_merges_triggered_group_burst(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    В группе два triggered сообщения одного sender'а за короткое время
    должны склеиваться в один combined query через _coalesce_text_burst.
    Это и есть B.5 happy-path: owner пишет «Краб, привет» + «как дела»,
    Краб отвечает один раз по combined context вместо двух отдельных flow.
    """
    bot = _make_batching_bot_stub()
    bot._is_trigger = Mock(side_effect=lambda text: text.lower().startswith("краб"))

    first = _make_group_message(
        message_id=500,
        text="Краб, расскажи про BTC",
        sender_id=42,
        seconds_offset=0.0,
    )
    second = _make_group_message(
        message_id=501,
        text="Краб, и про ETH тоже",
        sender_id=42,
        seconds_offset=0.3,
    )

    async def _fake_history(chat_id: int, limit: int = 0):
        _ = (chat_id, limit)
        for row in (second, first):
            yield row

    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        send_message=AsyncMock(),
        send_voice=AsyncMock(),
        send_reaction=AsyncMock(),
        get_chat_history=_fake_history,
    )

    monkeypatch.setattr(
        userbot_bridge_module.config, "TELEGRAM_MESSAGE_BATCH_WINDOW_SEC", 0.01, raising=False
    )
    monkeypatch.setattr(
        userbot_bridge_module.config, "TELEGRAM_MESSAGE_BATCH_MAX_MESSAGES", 6, raising=False
    )
    monkeypatch.setattr(
        userbot_bridge_module.config, "TELEGRAM_MESSAGE_BATCH_MAX_CHARS", 12000, raising=False
    )
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

    anchor, combined_query = await bot._coalesce_text_burst(
        message=first,
        user=first.from_user,
        query="Краб, расскажи про BTC",
    )
    assert combined_query == "Краб, расскажи про BTC\n\nКраб, и про ETH тоже"
    assert anchor is second  # последнее сообщение — anchor для reply
    # Second must have been recorded as absorbed follower — вторая обработка
    # через `_process_message` должна быть проигнорирована.
    assert (
        bot._consume_batched_followup_message_id(chat_id=str(first.chat.id), message_id="501")
        is True
    )


@pytest.mark.asyncio
async def test_coalesce_text_burst_stops_at_non_triggered_neighbor_in_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Если между двумя triggered сообщениями есть non-triggered от того же sender'а,
    batcher должен остановиться на нём, а не «перепрыгнуть». Иначе мы схлопнем
    unrelated context.
    """
    bot = _make_batching_bot_stub()
    bot._is_trigger = Mock(side_effect=lambda text: text.lower().startswith("краб"))

    first = _make_group_message(
        message_id=600, text="Краб, привет", sender_id=42, seconds_offset=0.0
    )
    middle = _make_group_message(
        message_id=601, text="ой подождите, я тут кофе варю", sender_id=42, seconds_offset=0.2
    )
    last = _make_group_message(
        message_id=602, text="Краб, продолжай", sender_id=42, seconds_offset=0.5
    )

    async def _fake_history(chat_id: int, limit: int = 0):
        _ = (chat_id, limit)
        for row in (last, middle, first):
            yield row

    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        send_message=AsyncMock(),
        send_reaction=AsyncMock(),
        get_chat_history=_fake_history,
    )

    monkeypatch.setattr(
        userbot_bridge_module.config, "TELEGRAM_MESSAGE_BATCH_WINDOW_SEC", 0.01, raising=False
    )
    monkeypatch.setattr(
        userbot_bridge_module.config, "TELEGRAM_MESSAGE_BATCH_MAX_MESSAGES", 6, raising=False
    )
    monkeypatch.setattr(
        userbot_bridge_module.config, "TELEGRAM_MESSAGE_BATCH_MAX_CHARS", 12000, raising=False
    )
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

    anchor, combined_query = await bot._coalesce_text_burst(
        message=first,
        user=first.from_user,
        query="Краб, привет",
    )
    # Middle message non-triggered → batcher должен остановиться на нём,
    # последний triggered вообще не попадает.
    assert combined_query == "Краб, привет"
    assert anchor is first
