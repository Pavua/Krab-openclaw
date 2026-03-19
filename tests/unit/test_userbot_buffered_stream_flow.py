# -*- coding: utf-8 -*-
"""
Тесты buffered-text потока userbot_bridge.

Проверяем ключевой регресс:
1) первый soft-timeout не должен мгновенно убивать живой OpenClaw-запрос;
2) userbot обязан дождаться buffered-ответа в пределах hard-timeout;
3) пользователю должно прийти явное notice о долгом ожидании.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pyrogram import enums

import src.userbot_bridge as userbot_bridge_module
from src.core.access_control import AccessLevel, AccessProfile
from src.userbot_bridge import KraabUserbot


def _make_buffered_bot_stub() -> KraabUserbot:
    """Создаёт минимальный bot stub для проверки buffered-text сценария."""
    bot = KraabUserbot.__new__(KraabUserbot)
    bot.me = SimpleNamespace(id=777, username="owner")
    bot.current_role = "default"
    bot.voice_mode = False
    bot._known_commands = set()
    bot._disclosure_sent_for_chat_ids = set()

    bot._message_has_audio = Mock(return_value=False)
    bot._is_trigger = Mock(return_value=True)
    bot._get_clean_text = Mock(side_effect=lambda text: text or "")
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
    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        send_message=AsyncMock(),
        send_voice=AsyncMock(),
        get_chat_history=None,
    )
    return bot


@pytest.mark.asyncio
async def test_text_route_waits_past_first_chunk_soft_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Buffered-text запрос не должен падать после первого soft-timeout,
    если ответ приходит в пределах расширенного hard-timeout окна.
    """
    bot = _make_buffered_bot_stub()
    incoming = SimpleNamespace(
        id=10,
        from_user=SimpleNamespace(id=42, username="tester", is_bot=False),
        text="Привет, Краб",
        caption=None,
        photo=None,
        voice=None,
        audio=None,
        chat=SimpleNamespace(id=123, type=enums.ChatType.PRIVATE),
        reply_to_message=None,
        reply=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=123), text="", caption="")),
    )
    access_profile = AccessProfile(
        level=AccessLevel.FULL,
        source="unit-test",
        matched_subject="tester",
    )

    async def _fake_stream(**kwargs):
        _ = kwargs
        await asyncio.sleep(0.03)
        yield "Готовый buffered-ответ"

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)
    monkeypatch.setattr(
        userbot_bridge_module.openclaw_client,
        "get_last_runtime_route",
        lambda: {"model": "openai-codex/gpt-5.4", "channel": "planning", "status": "ok"},
    )
    monkeypatch.setattr(
        userbot_bridge_module,
        "_resolve_openclaw_stream_timeouts",
        lambda **kwargs: (0.01, 0.05),
    )
    monkeypatch.setattr(
        userbot_bridge_module,
        "_resolve_openclaw_buffered_response_timeout",
        lambda **kwargs: 0.08,
    )
    monkeypatch.setattr(
        userbot_bridge_module,
        "_resolve_openclaw_progress_notice_schedule",
        lambda **kwargs: (0.01, 0.05),
    )
    monkeypatch.setattr(
        userbot_bridge_module,
        "_build_openclaw_slow_wait_notice",
        lambda **kwargs: "SLOW_NOTICE",
    )

    await bot._process_message_serialized(
        message=incoming,
        user=incoming.from_user,
        access_profile=access_profile,
        is_allowed_sender=True,
        chat_id=str(incoming.chat.id),
    )

    delivered_text = bot._deliver_response_parts.await_args.kwargs["full_response"]
    assert delivered_text == "Готовый buffered-ответ"
    edited_texts = [call.args[1] for call in bot._safe_edit.await_args_list]
    assert any("SLOW_NOTICE" in text for text in edited_texts)
