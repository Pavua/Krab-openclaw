"""
Wave 14-A — forward_batch coalescing tests.

Bug observed live (2026-05-02 21:43-21:44): user forwarded 14 messages including
3 photos. Krab generated 3 separate AI responses вместо одного.

Fix: forwarded photos coalesce в ту же ForwardBatchBuffer как text-forwards.
Photos несут placeholder "[фото] caption" в combined prompt, и весь batch
обрабатывается ОДНИМ AI-call'ом. Single response вместо N.
"""

from __future__ import annotations

import asyncio
import time

import pytest


def _make_text(text: str, sender: str = "alice", chat: str = "c1") -> "object":
    from src.core.message_batcher import PendingMessage

    return PendingMessage(
        text=text,
        sender_id="u1",
        ts=time.time(),
        is_forwarded=True,
        forward_sender_name=sender,
        forward_sender_username=sender,
    )


def _make_photo(caption: str = "", sender: str = "alice") -> "object":
    from src.core.message_batcher import PendingMessage

    return PendingMessage(
        text=caption,
        sender_id="u1",
        ts=time.time(),
        is_forwarded=True,
        forward_sender_name=sender,
        forward_sender_username=sender,
        is_photo=True,
        photo_caption=caption,
    )


@pytest.mark.asyncio
async def test_text_only_batch_single_call():
    """14 текстовых forwards → 1 on_flush invocation → 1 AI call (existing behavior)."""
    from src.core.message_batcher import MessageBatcher

    b = MessageBatcher()
    flushes: list[list] = []

    async def on_flush(chat_id, msgs):
        flushes.append(list(msgs))

    for i in range(14):
        b.add_forward("c1", _make_text(f"msg{i}"), on_flush)

    # Manual drain, simulating window expiry
    drained = b._get_fwd_buffer("c1").drain()
    if drained:
        await on_flush("c1", drained)

    assert len(flushes) == 1
    assert len(flushes[0]) == 14


@pytest.mark.asyncio
async def test_batch_with_photos_single_call():
    """14 forwards (11 text + 3 photos) → 1 on_flush с photo-маркерами в prompt."""
    from src.core.message_batcher import ForwardBatchBuffer, MessageBatcher

    b = MessageBatcher()
    flushes: list[list] = []

    async def on_flush(chat_id, msgs):
        flushes.append(list(msgs))

    msgs_seq = []
    for i in range(11):
        msgs_seq.append(_make_text(f"msg{i}"))
    msgs_seq.append(_make_photo(caption="закат в Барселоне"))
    msgs_seq.append(_make_photo(caption=""))
    msgs_seq.append(_make_photo(caption="это меню"))

    for m in msgs_seq:
        b.add_forward("c1", m, on_flush)

    drained = b._get_fwd_buffer("c1").drain()
    await on_flush("c1", drained)

    # Один flush, 14 messages.
    assert len(flushes) == 1
    assert len(flushes[0]) == 14

    # Format prompt включает [фото] маркеры.
    buf = ForwardBatchBuffer(chat_id="c1")
    buf.messages = flushes[0]
    prompt = buf.format_prompt()
    assert "[фото] закат в Барселоне" in prompt
    assert "[фото без подписи]" in prompt
    assert "[фото] это меню" in prompt
    # Текстовые msgs тоже на месте.
    assert "msg0" in prompt
    assert "msg10" in prompt


def test_photo_alone_no_batch_returns_false():
    """Не-forwarded photo (is_forwarded=False) → add_forward returns False."""
    from src.core.message_batcher import MessageBatcher, PendingMessage

    b = MessageBatcher()

    async def on_flush(chat_id, msgs):
        pass

    msg = PendingMessage(
        text="",
        sender_id="u1",
        is_forwarded=False,
        is_photo=True,
        photo_caption="hello",
    )
    result = b.add_forward("c1", msg, on_flush)
    assert result is False


@pytest.mark.asyncio
async def test_concurrent_chat_isolation():
    """Batches из chat A и chat B не interfere: каждый flush'ится отдельно."""
    from src.core.message_batcher import MessageBatcher

    b = MessageBatcher()
    flushes: dict[str, list[list]] = {"A": [], "B": []}

    async def on_flush_A(chat_id, msgs):
        flushes["A"].append(list(msgs))

    async def on_flush_B(chat_id, msgs):
        flushes["B"].append(list(msgs))

    # Chat A: 3 text + 1 photo
    b.add_forward("A", _make_text("a1"), on_flush_A)
    b.add_forward("A", _make_text("a2"), on_flush_A)
    b.add_forward("A", _make_photo(caption="A-photo"), on_flush_A)

    # Chat B: 2 photos
    b.add_forward("B", _make_photo(caption="B-1"), on_flush_B)
    b.add_forward("B", _make_photo(caption="B-2"), on_flush_B)

    drained_A = b._get_fwd_buffer("A").drain()
    drained_B = b._get_fwd_buffer("B").drain()

    await on_flush_A("A", drained_A)
    await on_flush_B("B", drained_B)

    assert len(flushes["A"]) == 1
    assert len(flushes["A"][0]) == 3
    assert len(flushes["B"]) == 1
    assert len(flushes["B"][0]) == 2

    # Photos в B имеют is_photo=True
    assert all(m.is_photo for m in flushes["B"][0])


@pytest.mark.asyncio
async def test_window_expires_correctly_partial_batch():
    """Partial batch (e.g. 5 msgs) при истечении timer → drain → on_flush с 5 msgs."""
    from src.core.message_batcher import MessageBatcher

    b = MessageBatcher()
    flushed: list = []

    async def on_flush(chat_id, msgs):
        flushed.extend(msgs)

    for i in range(5):
        b.add_forward("c1", _make_text(f"m{i}"), on_flush)

    # Manual flush via buffer drain (simulates window timer fire).
    buf = b._get_fwd_buffer("c1")
    drained = buf.drain()
    assert len(drained) == 5
    await on_flush("c1", drained)
    assert len(flushed) == 5


def test_format_prompt_photo_only_batch():
    """Edge case: пачка только из фото — все рендерятся как [фото] markers."""
    from src.core.message_batcher import ForwardBatchBuffer

    buf = ForwardBatchBuffer(chat_id="c1")
    buf.messages = [
        _make_photo(caption="первое"),
        _make_photo(caption=""),
        _make_photo(caption="третье"),
    ]
    prompt = buf.format_prompt()
    assert prompt.count("[фото]") == 2  # с подписями
    assert "[фото без подписи]" in prompt
    assert "первое" in prompt
    assert "третье" in prompt
