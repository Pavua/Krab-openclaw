"""Тесты per-chat message batcher (backpressure через batching)."""

from __future__ import annotations

import asyncio
import time

import pytest


@pytest.mark.asyncio
async def test_first_message_processed_immediately():
    from src.core.message_batcher import MessageBatcher, PendingMessage

    b = MessageBatcher()
    calls = []

    async def processor(chat_id, combined):
        calls.append(combined)
        return "response"

    status, resp = await b.try_add_or_flush(
        "c1", PendingMessage(text="hello", sender_id="u1"), processor
    )
    assert status == "immediate"
    assert resp == "response"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_concurrent_messages_buffered():
    """Пока processor работает, новые msgs буферизуются."""
    from src.core.message_batcher import MessageBatcher, PendingMessage

    b = MessageBatcher()
    processing_event = asyncio.Event()

    async def slow_processor(chat_id, combined):
        await processing_event.wait()
        return "done"

    # Первое — зависнет на event
    task1 = asyncio.create_task(
        b.try_add_or_flush("c1", PendingMessage(text="msg1", sender_id="u1"), slow_processor)
    )
    await asyncio.sleep(0.05)  # Даём первому стартовать

    # Следующие — должны буферизоваться
    status2, _ = await b.try_add_or_flush(
        "c1", PendingMessage(text="msg2", sender_id="u1"), slow_processor
    )
    assert status2 == "buffered"

    status3, _ = await b.try_add_or_flush(
        "c1", PendingMessage(text="msg3", sender_id="u1"), slow_processor
    )
    assert status3 == "buffered"

    # Отпускаем первый
    processing_event.set()
    await task1
    # Даём batch flush завершиться
    await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_different_chats_independent():
    """Два разных chat_id не влияют друг на друга."""
    from src.core.message_batcher import MessageBatcher, PendingMessage

    b = MessageBatcher()
    processing_event = asyncio.Event()

    async def slow_processor(chat_id, combined):
        await processing_event.wait()
        return "done"

    async def fast_processor(chat_id, combined):
        return "fast"

    # c1 занят
    asyncio.create_task(
        b.try_add_or_flush("c1", PendingMessage(text="msg1", sender_id="u1"), slow_processor)
    )
    await asyncio.sleep(0.05)

    # c2 должен обработаться немедленно
    status, resp = await b.try_add_or_flush(
        "c2", PendingMessage(text="hello", sender_id="u2"), fast_processor
    )
    assert status == "immediate"
    assert resp == "fast"

    processing_event.set()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_batch_flush_called_after_release():
    """После завершения первого processor — буфер флашится."""
    from src.core.message_batcher import MessageBatcher, PendingMessage

    b = MessageBatcher()
    call_count = 0
    flush_event = asyncio.Event()

    async def processor(chat_id, combined):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            flush_event.set()
        return "ok"

    # Запускаем первый без блокировки — обработается сразу
    await b.try_add_or_flush("c1", PendingMessage(text="first", sender_id="u1"), processor)

    # Теперь искусственно занимаем batch
    batch = b._get_batch("c1")
    async with batch.lock:
        batch.busy = True
        batch.pending = [
            PendingMessage(text="second", sender_id="u1"),
            PendingMessage(text="third", sender_id="u1"),
        ]
        batch.busy = False

    # Триггерим flush вручную
    asyncio.create_task(b._flush_batch("c1", processor))

    # Ждём второго вызова
    try:
        await asyncio.wait_for(flush_event.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pass

    # Должен быть минимум 2 вызова: первый + batch flush
    assert call_count >= 2


def test_batch_format():
    from src.core.message_batcher import ChatBatch, PendingMessage

    batch = ChatBatch(chat_id="c1")
    batch.pending = [
        PendingMessage(text="msg1", sender_id="u1"),
        PendingMessage(text="msg2", sender_id="u1"),
    ]
    prompt = batch.format_batched_prompt()
    assert "2 сообщений" in prompt
    assert "msg1" in prompt
    assert "msg2" in prompt
    assert "[1]" in prompt
    assert "[2]" in prompt


def test_batch_format_single():
    from src.core.message_batcher import ChatBatch, PendingMessage

    batch = ChatBatch(chat_id="c1")
    batch.pending = [PendingMessage(text="solo", sender_id="u1")]
    prompt = batch.format_batched_prompt()
    assert "solo" in prompt


def test_batch_format_empty():
    from src.core.message_batcher import ChatBatch

    batch = ChatBatch(chat_id="c1")
    assert batch.format_batched_prompt() == ""


def test_should_flush_size():
    from src.core.message_batcher import MAX_BATCH_SIZE, ChatBatch, PendingMessage

    batch = ChatBatch(chat_id="c1")
    for i in range(MAX_BATCH_SIZE + 1):
        batch.pending.append(PendingMessage(text=f"m{i}", sender_id="u1"))
    assert batch.should_flush()


def test_should_flush_age():
    from src.core.message_batcher import MAX_BATCH_AGE_SEC, ChatBatch, PendingMessage

    batch = ChatBatch(chat_id="c1")
    old = PendingMessage(text="old", sender_id="u1")
    old.ts = time.time() - MAX_BATCH_AGE_SEC - 1
    batch.pending = [old]
    assert batch.should_flush()


def test_should_not_flush_empty():
    from src.core.message_batcher import ChatBatch

    batch = ChatBatch(chat_id="c1")
    assert not batch.should_flush()


def test_should_not_flush_fresh_small():
    from src.core.message_batcher import ChatBatch, PendingMessage

    batch = ChatBatch(chat_id="c1")
    batch.pending = [PendingMessage(text="fresh", sender_id="u1")]
    assert not batch.should_flush()


def test_drain():
    from src.core.message_batcher import ChatBatch, PendingMessage

    batch = ChatBatch(chat_id="c1")
    batch.pending = [
        PendingMessage(text="a", sender_id="u1"),
        PendingMessage(text="b", sender_id="u1"),
    ]
    drained = batch.drain()
    assert len(drained) == 2
    assert batch.size() == 0


def test_age_sec_empty():
    from src.core.message_batcher import ChatBatch

    batch = ChatBatch(chat_id="c1")
    assert batch.age_sec() == 0.0


def test_age_sec_with_messages():
    from src.core.message_batcher import ChatBatch, PendingMessage

    batch = ChatBatch(chat_id="c1")
    old = PendingMessage(text="x", sender_id="u1")
    old.ts = time.time() - 5.0
    batch.pending = [old]
    assert batch.age_sec() >= 5.0


def test_stats():
    from src.core.message_batcher import MessageBatcher

    b = MessageBatcher()
    b._get_batch("c1")
    b._get_batch("c2")
    s = b.stats()
    assert s["total_batches"] == 2
    assert s["active_batches"] == 0
    assert s["total_pending"] == 0


def test_stats_with_pending():
    from src.core.message_batcher import MessageBatcher, PendingMessage

    b = MessageBatcher()
    batch = b._get_batch("c1")
    batch.pending = [PendingMessage(text="x", sender_id="u1")]
    s = b.stats()
    assert s["total_pending"] == 1


@pytest.mark.asyncio
async def test_no_drop_message_arriving_mid_processing():
    """
    Регрессия: сообщения, прилетевшие во время LLM processing, не должны теряться.

    Старый баг (line 297): после await processor(...) делалось `batch.pending = []`
    безусловно — buffered messages, попавшие в pending пока шёл processor,
    дропались, и has_pending становился False, поэтому flush не планировался.
    """
    from src.core.message_batcher import MessageBatcher, PendingMessage

    b = MessageBatcher()
    processed: list[str] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def processor(chat_id, combined):
        processed.append(combined)
        if not first_started.is_set():
            first_started.set()
            await release_first.wait()
        return "ok"

    # 1) первое сообщение — стартует и зависает на release_first
    task1 = asyncio.create_task(
        b.try_add_or_flush("c1", PendingMessage(text="msg1", sender_id="u1"), processor)
    )
    await first_started.wait()

    # 2) второе сообщение прилетает MID-processing → должно буферизоваться
    status2, _ = await b.try_add_or_flush(
        "c1", PendingMessage(text="msg2", sender_id="u1"), processor
    )
    assert status2 == "buffered"

    # 3) отпускаем первый processor → должен auto-flush msg2 (а не дропнуть)
    release_first.set()
    await task1

    # ждём фоновый flush
    for _ in range(50):
        if len(processed) >= 2:
            break
        await asyncio.sleep(0.02)

    assert len(processed) == 2, f"msg2 потерян, processed={processed}"
    assert "msg1" in processed[0]
    assert "msg2" in processed[1]


def test_get_batch_str_coerce():
    """chat_id всегда приводится к str."""
    from src.core.message_batcher import MessageBatcher

    b = MessageBatcher()
    b._get_batch(12345)  # type: ignore[arg-type]
    assert "12345" in b._batches
