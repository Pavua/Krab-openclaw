# -*- coding: utf-8 -*-
"""
Тесты inbox-capture для входящих owner message flow в userbot_bridge.

Покрываем:
1) trusted private message попадает в inbox как `owner_request`;
2) trusted group reply/mention попадает в inbox как `owner_mention`.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pyrogram import enums

import src.userbot_bridge as userbot_bridge_module
from src.core.inbox_service import InboxService
from src.userbot_bridge import KraabUserbot


def _build_inbox_bot_stub(*, has_trigger: bool) -> KraabUserbot:
    """Создаёт минимальный bot stub для проверки inbox-capture без реального Telegram."""
    bot = KraabUserbot.__new__(KraabUserbot)
    bot.me = SimpleNamespace(id=777, username="owner")
    bot.current_role = "default"
    bot.voice_mode = False
    bot._known_commands = set()
    bot._disclosure_sent_for_chat_ids = set()

    bot._is_trigger = Mock(return_value=has_trigger)
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
    bot._deliver_response_parts = AsyncMock()
    bot._extract_live_stream_text = Mock(side_effect=lambda text, allow_reasoning=False: text)
    bot._strip_transport_markup = Mock(side_effect=lambda text: text)
    bot._apply_deferred_action_guard = Mock(side_effect=lambda text: text)
    bot._should_send_voice_reply = Mock(return_value=False)
    bot._should_force_cloud_for_photo_route = Mock(return_value=False)
    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        send_message=AsyncMock(),
        send_voice=AsyncMock(),
    )
    return bot


def _make_message(
    *,
    chat_id: int,
    chat_type: enums.ChatType,
    text: str,
    message_id: int,
    reply_to_me: bool,
) -> tuple[SimpleNamespace, SimpleNamespace]:
    """Готовит входящее текстовое сообщение и placeholder reply."""
    temp_msg = SimpleNamespace(
        chat=SimpleNamespace(id=chat_id), text="", caption="", delete=AsyncMock()
    )
    reply_from = (
        SimpleNamespace(id=777, username="owner")
        if reply_to_me
        else SimpleNamespace(id=999, username="other")
    )
    incoming = SimpleNamespace(
        id=message_id,
        from_user=SimpleNamespace(id=42, username="trusted", is_bot=False),
        text=text,
        caption=None,
        photo=None,
        voice=None,
        audio=None,
        chat=SimpleNamespace(id=chat_id, type=chat_type),
        reply_to_message=SimpleNamespace(from_user=reply_from) if reply_to_me else None,
        reply=AsyncMock(return_value=temp_msg),
    )
    return incoming, temp_msg


def _run_inbox_sync(
    *,
    bot: KraabUserbot,
    inbox: InboxService,
    message: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    has_trigger: bool,
    is_reply_to_me: bool,
) -> dict[str, object]:
    """Вызывает transport->inbox helper напрямую."""
    monkeypatch.setattr(userbot_bridge_module, "inbox_service", inbox)
    return bot._sync_incoming_message_to_inbox(
        message=message,
        user=message.from_user,
        query=query,
        is_self=False,
        is_allowed_sender=True,
        has_trigger=has_trigger,
        is_reply_to_me=is_reply_to_me,
        has_audio_message=False,
    )


def test_private_trusted_message_is_captured_as_owner_request(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trusted private message должен попасть в inbox как owner_request."""
    bot = _build_inbox_bot_stub(has_trigger=False)
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    incoming, _ = _make_message(
        chat_id=123,
        chat_type=enums.ChatType.PRIVATE,
        text="Проверь health lite после restart",
        message_id=10,
        reply_to_me=False,
    )

    result = _run_inbox_sync(
        bot=bot,
        inbox=inbox,
        message=incoming,
        monkeypatch=monkeypatch,
        query=incoming.text,
        has_trigger=False,
        is_reply_to_me=False,
    )

    items = inbox.list_items(status="open", kind="owner_request", limit=5)
    assert result["ok"] is True
    assert items
    assert items[0]["metadata"]["message_id"] == "10"
    assert inbox.get_summary()["pending_owner_requests"] == 1


def test_group_reply_to_me_is_captured_as_owner_mention(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trusted group reply_to_me должен попасть в inbox как owner_mention."""
    bot = _build_inbox_bot_stub(has_trigger=False)
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    incoming, _ = _make_message(
        chat_id=-100777,
        chat_type=enums.ChatType.GROUP,
        text="Посмотри, пожалуйста, этот кейс",
        message_id=11,
        reply_to_me=True,
    )

    result = _run_inbox_sync(
        bot=bot,
        inbox=inbox,
        message=incoming,
        monkeypatch=monkeypatch,
        query=incoming.text,
        has_trigger=False,
        is_reply_to_me=True,
    )

    items = inbox.list_items(status="open", kind="owner_mention", limit=5)
    assert result["ok"] is True
    assert items
    assert items[0]["metadata"]["is_reply_to_me"] is True
    assert inbox.get_summary()["pending_owner_mentions"] == 1


def test_incoming_owner_message_closes_open_relay_request_for_same_chat(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если owner уже вернулся в тот же чат, старый relay_request должен закрыться."""
    bot = _build_inbox_bot_stub(has_trigger=True)
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    inbox.upsert_item(
        dedupe_key="relay:312322764:11402",
        kind="relay_request",
        source="telegram-userbot",
        title="📨 Relay от @p0lrd",
        body="relay body",
        severity="warning",
        status="open",
        identity=inbox.build_identity(
            channel_id="312322764",
            team_id="owner",
            trace_id="relay:test",
            approval_scope="owner",
        ),
        metadata={"chat_id": "312322764", "message_id": "11402"},
    )
    incoming, _ = _make_message(
        chat_id=312322764,
        chat_type=enums.ChatType.PRIVATE,
        text="Краб, проверь этот диалог",
        message_id=11427,
        reply_to_me=False,
    )

    result = _run_inbox_sync(
        bot=bot,
        inbox=inbox,
        message=incoming,
        monkeypatch=monkeypatch,
        query=incoming.text,
        has_trigger=True,
        is_reply_to_me=False,
    )

    assert result["ok"] is True
    relay_items = inbox.list_items(kind="relay_request", limit=5)
    assert relay_items
    assert relay_items[0]["status"] == "done"
    assert relay_items[0]["metadata"]["resolution_note"] == "owner_followed_up_after_relay"
