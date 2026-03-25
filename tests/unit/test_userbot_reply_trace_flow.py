# -*- coding: utf-8 -*-
"""
Тесты reply-trace flow для inbox-связки userbot -> persisted workflow.

Покрываем:
1) capture входящего owner request;
2) фиксацию финального ответа как `reply_sent` в том же trace/item;
3) сохранение delivery summary в metadata.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pyrogram import enums

import src.userbot_bridge as userbot_bridge_module
from src.core.inbox_service import InboxService
from src.userbot_bridge import KraabUserbot


def _build_bot() -> KraabUserbot:
    """Создаёт минимальный bot stub для reply-trace helper-ов."""
    bot = KraabUserbot.__new__(KraabUserbot)
    bot.me = SimpleNamespace(id=777, username="owner")
    bot._is_trigger = Mock(return_value=False)
    bot._get_clean_text = Mock(side_effect=lambda text: text or "")
    return bot


def _make_message(*, chat_id: int, message_id: int, text: str) -> SimpleNamespace:
    """Готовит минимальное private-message payload."""
    return SimpleNamespace(
        id=message_id,
        text=text,
        photo=None,
        audio=None,
        voice=None,
        chat=SimpleNamespace(id=chat_id, type=enums.ChatType.PRIVATE),
    )


def test_record_incoming_reply_keeps_same_trace_and_marks_request_done(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ответ должен закрывать тот же inbox item, а не создавать новый след рядом."""
    bot = _build_bot()
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr(userbot_bridge_module, "inbox_service", inbox)
    message = _make_message(chat_id=123, message_id=41, text="Проверь reply trail")

    incoming = bot._sync_incoming_message_to_inbox(
        message=message,
        user=SimpleNamespace(id=42, username="owner"),
        query=message.text,
        is_self=False,
        is_allowed_sender=True,
        has_trigger=False,
        is_reply_to_me=False,
        has_audio_message=False,
    )
    result = bot._record_incoming_reply_to_inbox(
        incoming_item_result=incoming,
        response_text="Reply trail уже записан.",
        delivery_result={
            "delivery_mode": "edit_and_reply",
            "text_message_ids": ["9001", "9002"],
            "parts_count": 2,
        },
        note="llm_response_delivered",
    )
    rows = inbox.list_items(status="done", kind="owner_request", limit=5)

    assert incoming["ok"] is True
    assert result["ok"] is True
    assert rows
    assert rows[0]["identity"]["trace_id"] == incoming["item"]["identity"]["trace_id"]
    assert rows[0]["metadata"]["reply_message_ids"] == ["9001", "9002"]
    assert rows[0]["metadata"]["reply_delivery_mode"] == "edit_and_reply"
    assert rows[0]["metadata"]["reply_excerpt"] == "Reply trail уже записан."
