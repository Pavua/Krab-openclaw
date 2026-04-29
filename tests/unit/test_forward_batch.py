"""
Тесты forward batch processing.

Сценарии:
- Одиночный forward → НЕ батчится сразу (попадает в буфер, ждёт окно)
- 3 форварда в 3s → батчатся в один запрос
- 3 форварда с gap 10s → второй пакет отдельным запросом
- Не-forward сообщение → add_forward возвращает False
- Forward + non-forward mix → только forwards батчатся
- Максимум 20 сообщений → немедленный flush
- Format prompt: per-sender attribution
- Stats включают forward_pending
"""

from __future__ import annotations

import asyncio
import time

import pytest


def _make_msg(
    text: str,
    sender_id: str = "u1",
    is_forwarded: bool = True,
    fwd_name: str = "",
    fwd_uname: str = "",
    fwd_date: int | None = None,
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
# 1. Non-forwarded message → add_forward returns False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_forward_not_buffered():
    """add_forward должен вернуть False для не-пересланного сообщения."""
    from src.core.message_batcher import MessageBatcher

    b = MessageBatcher()
    flushed = []

    async def on_flush(chat_id, msgs):
        flushed.extend(msgs)

    msg = _make_msg("привет", is_forwarded=False)
    result = b.add_forward("c1", msg, on_flush)
    assert result is False
    assert len(flushed) == 0


# ---------------------------------------------------------------------------
# 2. Single forward → buffered (True returned), flushes after window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_forward_buffered():
    """Единственный forward попадает в буфер → add_forward возвращает True."""
    from src.core.message_batcher import MessageBatcher

    b = MessageBatcher()
    flushed = []

    async def on_flush(chat_id, msgs):
        flushed.extend(msgs)

    msg = _make_msg("текст 1", fwd_name="alice")
    result = b.add_forward("c1", msg, on_flush)
    assert result is True
    # Буфер ещё ждёт — flush не вызван
    assert len(flushed) == 0


# ---------------------------------------------------------------------------
# 3. 3 forwards arrive quickly → all collected in buffer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_three_forwards_collected_in_buffer():
    """3 пересылки подряд → все 3 в буфере, flush ещё не вызван."""
    from src.core.message_batcher import MessageBatcher

    b = MessageBatcher()
    flushed = []

    async def on_flush(chat_id, msgs):
        flushed.extend(msgs)

    for text in ["msg1", "msg2", "msg3"]:
        b.add_forward("c1", _make_msg(text, fwd_name="alice"), on_flush)

    # Flush ещё не должен был случиться
    assert len(flushed) == 0
    buf = b._get_fwd_buffer("c1")
    assert buf.size() == 3


# ---------------------------------------------------------------------------
# 4. max 20 → immediate flush
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_size_triggers_immediate_flush():
    """При достижении FORWARD_BATCH_MAX (20) → немедленный async flush."""
    from src.core.message_batcher import FORWARD_BATCH_MAX, MessageBatcher

    b = MessageBatcher()
    flushed: list = []

    async def on_flush(chat_id, msgs):
        flushed.extend(msgs)

    # Добавляем FORWARD_BATCH_MAX сообщений
    for i in range(FORWARD_BATCH_MAX):
        b.add_forward("c1", _make_msg(f"msg{i}", fwd_name="alice"), on_flush)

    # После добавления ровно MAX — asyncio.ensure_future запустила flush
    await asyncio.sleep(0)  # даём event loop крутануться
    assert len(flushed) == FORWARD_BATCH_MAX


# ---------------------------------------------------------------------------
# 5. Two separate windows (simulated via manual drain)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_separate_windows_produce_separate_batches():
    """
    Симулируем два окна: добавляем 2 msgs → drain (flush) →
    добавляем ещё 2 msgs → drain (flush).
    Итого 2 отдельных вызова on_flush.
    """
    from src.core.message_batcher import MessageBatcher

    b = MessageBatcher()
    flushes: list[list] = []

    async def on_flush(chat_id, msgs):
        flushes.append(list(msgs))

    # Первое окно
    b.add_forward("c1", _make_msg("a1", fwd_name="alice"), on_flush)
    b.add_forward("c1", _make_msg("a2", fwd_name="bob"), on_flush)
    buf = b._get_fwd_buffer("c1")
    first_batch = buf.drain()
    await on_flush("c1", first_batch)

    # Второе окно
    b.add_forward("c1", _make_msg("b1", fwd_name="carol"), on_flush)
    b.add_forward("c1", _make_msg("b2", fwd_name="dave"), on_flush)
    second_batch = buf.drain()
    await on_flush("c1", second_batch)

    assert len(flushes) == 2
    assert len(flushes[0]) == 2
    assert len(flushes[1]) == 2


# ---------------------------------------------------------------------------
# 6. format_prompt — per-sender attribution
# ---------------------------------------------------------------------------


def test_format_prompt_includes_senders():
    """format_prompt должен включать имена отправителей."""
    from src.core.message_batcher import ForwardBatchBuffer, PendingMessage

    buf = ForwardBatchBuffer(chat_id="c1")
    buf.messages = [
        PendingMessage(
            text="привет",
            sender_id="u1",
            is_forwarded=True,
            forward_sender_name="alice",
            forward_sender_username="alice",
        ),
        PendingMessage(
            text="как дела",
            sender_id="u1",
            is_forwarded=True,
            forward_sender_name="bob",
            forward_sender_username="bob",
        ),
    ]
    prompt = buf.format_prompt()
    assert "alice" in prompt
    assert "bob" in prompt
    assert "1." in prompt
    assert "2." in prompt
    assert "привет" in prompt
    assert "как дела" in prompt


def test_format_prompt_with_owner_query():
    """format_prompt включает owner_query в конце."""
    from src.core.message_batcher import ForwardBatchBuffer, PendingMessage

    buf = ForwardBatchBuffer(chat_id="c1")
    buf.messages = [
        PendingMessage(
            text="text",
            sender_id="u1",
            is_forwarded=True,
            forward_sender_name="alice",
        )
    ]
    prompt = buf.format_prompt(owner_query="что они обсуждают?")
    assert "что они обсуждают?" in prompt
    assert "[Запрос]" in prompt


def test_format_prompt_senders_header():
    """Заголовок включает имена уникальных отправителей."""
    from src.core.message_batcher import ForwardBatchBuffer, PendingMessage

    buf = ForwardBatchBuffer(chat_id="c1")
    buf.messages = [
        PendingMessage(text="x", sender_id="u1", is_forwarded=True, forward_sender_name="alice"),
        PendingMessage(text="y", sender_id="u1", is_forwarded=True, forward_sender_name="alice"),
        PendingMessage(text="z", sender_id="u1", is_forwarded=True, forward_sender_name="bob"),
    ]
    prompt = buf.format_prompt()
    # alice появляется один раз в заголовке, bob тоже
    header_line = prompt.split("\n")[0]
    assert "alice" in header_line
    assert "bob" in header_line


# ---------------------------------------------------------------------------
# 7. stats includes forward_pending
# ---------------------------------------------------------------------------


def test_stats_include_forward_pending():
    """stats() должен включать forward_pending."""
    from src.core.message_batcher import MessageBatcher

    b = MessageBatcher()
    flushed = []

    async def on_flush(chat_id, msgs):
        flushed.extend(msgs)

    # Добавляем forwarded вручную без await (add_forward синхронный)
    b.add_forward("c1", _make_msg("msg", fwd_name="alice"), on_flush)

    s = b.stats()
    assert "forward_pending" in s
    assert s["forward_pending"] >= 1


# ---------------------------------------------------------------------------
# 8. Mix: forward + non-forward in same chat
# ---------------------------------------------------------------------------


def test_non_forward_leaves_forward_buffer_intact():
    """
    Не-forwarded сообщение не должно влиять на forward buffer.
    add_forward вернёт False, буфер с forwarded не тронут.
    """
    from src.core.message_batcher import MessageBatcher

    b = MessageBatcher()
    flushed = []

    async def on_flush(chat_id, msgs):
        flushed.extend(msgs)

    # Добавляем forwarded
    b.add_forward("c1", _make_msg("fwd1", fwd_name="alice"), on_flush)
    # Добавляем non-forwarded — должен вернуть False и не очистить буфер
    result = b.add_forward("c1", _make_msg("plain", is_forwarded=False), on_flush)

    assert result is False
    buf = b._get_fwd_buffer("c1")
    assert buf.size() == 1  # fwd1 всё ещё там
