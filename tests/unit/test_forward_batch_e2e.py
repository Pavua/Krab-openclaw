"""
E2E тесты forward batch: проверяем что _process_message_serialized
корректно принимает _forward_batch_prompt и что ForwardBatchBuffer
строит правильный prompt для разных типов forwards.

Покрываемые кейсы:
1. single forward → буфер, FlushBuffer.format_prompt() работает
2. 3 forwards разных отправителей → batched prompt с attribution
3. forward из канала (forward_from_chat)
4. forward с hidden sender (forward_sender_name только)
5. mixed: forward + own text → batched_prompt включает запрос через owner_query
"""

from __future__ import annotations

import asyncio
import time

import pytest


def _make_pending(
    text: str,
    is_forwarded: bool = True,
    fwd_name: str = "",
    fwd_uname: str = "",
    fwd_date: int | None = None,
    sender_id: str = "100",
):
    from src.core.message_batcher import PendingMessage

    return PendingMessage(
        text=text,
        sender_id=sender_id,
        ts=time.time(),
        is_forwarded=is_forwarded,
        forward_sender_name=fwd_name,
        forward_sender_username=fwd_uname,
        forward_date=fwd_date,
    )


# ---------------------------------------------------------------------------
# 1. single forward → в буфер, format_prompt не пустой
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_forward_buffered_and_prompt():
    from src.core.message_batcher import ForwardBatchBuffer, MessageBatcher

    batcher = MessageBatcher()
    flushed = []

    async def on_flush(chat_id, msgs):
        flushed.append((chat_id, msgs))

    msg = _make_pending("Hello world", fwd_name="Alice", fwd_uname="alice")
    result = batcher.add_forward("chat1", msg, on_flush)
    assert result is True, "forwarded msg должна попасть в буфер"
    # format_prompt работает
    buf = ForwardBatchBuffer(chat_id="chat1")
    buf.messages = [msg]
    prompt = buf.format_prompt()
    assert "alice" in prompt.lower() or "Alice" in prompt
    assert "Hello world" in prompt


# ---------------------------------------------------------------------------
# 2. 3 forwards разных отправителей → batched prompt с per-sender attribution
# ---------------------------------------------------------------------------


def test_forward_batch_prompt_multi_sender():
    from src.core.message_batcher import ForwardBatchBuffer

    buf = ForwardBatchBuffer(chat_id="chat2")
    buf.messages = [
        _make_pending("msg1", fwd_name="Alice", fwd_uname="alice_u", fwd_date=1714000000),
        _make_pending("msg2", fwd_name="Bob", fwd_uname="bob_u", fwd_date=1714000060),
        _make_pending("msg3", fwd_name="Alice", fwd_uname="alice_u", fwd_date=1714000120),
    ]
    prompt = buf.format_prompt()

    # per-sender attribution: оба имени
    assert "alice_u" in prompt
    assert "bob_u" in prompt
    # нумерация 1. 2. 3.
    assert "1." in prompt
    assert "2." in prompt
    assert "3." in prompt
    # текст сообщений
    assert "msg1" in prompt
    assert "msg2" in prompt
    assert "msg3" in prompt
    # временная метка (HH:MM) присутствует
    assert ":" in prompt  # простейшая проверка что timestamp есть


# ---------------------------------------------------------------------------
# 3. Forward из канала (forward_from_chat)
# ---------------------------------------------------------------------------


def test_forward_from_channel_attribution():
    from src.core.message_batcher import ForwardBatchBuffer

    buf = ForwardBatchBuffer(chat_id="chat3")
    buf.messages = [
        _make_pending("breaking news", fwd_name="CNN Channel", fwd_uname="cnn"),
    ]
    prompt = buf.format_prompt()
    assert "cnn" in prompt or "CNN" in prompt
    assert "breaking news" in prompt


# ---------------------------------------------------------------------------
# 4. Forward с hidden sender (только forward_sender_name, нет username)
# ---------------------------------------------------------------------------


def test_forward_hidden_sender():
    from src.core.message_batcher import ForwardBatchBuffer

    buf = ForwardBatchBuffer(chat_id="chat4")
    # hidden: username пустой, есть только display name
    buf.messages = [
        _make_pending("hidden text", fwd_name="Hidden User", fwd_uname=""),
    ]
    prompt = buf.format_prompt()
    assert "Hidden User" in prompt
    assert "hidden text" in prompt


# ---------------------------------------------------------------------------
# 5. Mixed: forwards + owner_query → query приклеивается после пачки
# ---------------------------------------------------------------------------


def test_forward_batch_with_owner_query():
    from src.core.message_batcher import ForwardBatchBuffer

    buf = ForwardBatchBuffer(chat_id="chat5")
    buf.messages = [
        _make_pending("msg from alice", fwd_name="Alice", fwd_uname="alice"),
        _make_pending("msg from bob", fwd_name="Bob", fwd_uname="bob"),
    ]
    prompt = buf.format_prompt(owner_query="Что они обсуждают?")

    assert "Что они обсуждают?" in prompt
    assert "[Запрос]" in prompt
    assert "msg from alice" in prompt
    assert "msg from bob" in prompt


# ---------------------------------------------------------------------------
# 6. _process_message_serialized принимает _forward_batch_prompt (regression)
# ---------------------------------------------------------------------------


def test_process_message_serialized_signature_has_forward_batch_param():
    """Регрессия: TypeError если параметр не объявлен в сигнатуре."""
    import inspect

    from src.userbot_bridge import KraabUserbot

    sig = inspect.signature(KraabUserbot._process_message_serialized)
    assert "_forward_batch_prompt" in sig.parameters, (
        "_process_message_serialized должен принимать _forward_batch_prompt"
    )
    # default должен быть None (не обязательный параметр)
    param = sig.parameters["_forward_batch_prompt"]
    assert param.default is None
