"""
Тесты Wave 33-C: bulk forward detection.

Сценарии:
1. Обычный режим — 20 max / 10s (стандартный FORWARD_BATCH_MAX)
2. Bulk mode активируется когда 5+ forwards за 5s
3. Bulk mode: лимит 200 сообщений
4. Bulk mode: окно 60s
5. Flush по max_reached в bulk mode
6. Bulk state сбрасывается после flush
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest


def _make_fwd(text: str = "msg", fwd_name: str = "alice"):
    from src.core.message_batcher import PendingMessage

    return PendingMessage(
        text=text,
        sender_id="u1",
        ts=time.time(),
        is_forwarded=True,
        forward_sender_name=fwd_name,
    )


# ---------------------------------------------------------------------------
# 1. Обычный режим (не bulk): FORWARD_BATCH_MAX / FORWARD_BATCH_WINDOW_SEC
# ---------------------------------------------------------------------------


def test_normal_mode_default_limits():
    """В обычном режиме effective_max == FORWARD_BATCH_MAX (20)."""
    from src.core.message_batcher import FORWARD_BATCH_MAX, ForwardBatchBuffer

    buf = ForwardBatchBuffer(chat_id="c1")
    # Меньше порога — normal mode
    buf.add(_make_fwd("a"))
    buf.add(_make_fwd("b"))
    assert not buf.is_bulk_mode()
    assert buf.effective_max() == FORWARD_BATCH_MAX


def test_normal_mode_window():
    """В обычном режиме effective_window == FORWARD_BATCH_WINDOW_SEC (5)."""
    from src.core.message_batcher import FORWARD_BATCH_WINDOW_SEC, ForwardBatchBuffer

    buf = ForwardBatchBuffer(chat_id="c1")
    assert buf.effective_window() == FORWARD_BATCH_WINDOW_SEC


# ---------------------------------------------------------------------------
# 2. Bulk mode активируется при burst 5+ forwards за 5s
# ---------------------------------------------------------------------------


def test_bulk_mode_activated_on_threshold():
    """
    При добавлении BULK_DETECTION_THRESHOLD сообщений за BULK_DETECTION_WINDOW_SEC
    bulk mode должен активироваться.
    """
    from src.core.message_batcher import BULK_DETECTION_THRESHOLD, ForwardBatchBuffer

    buf = ForwardBatchBuffer(chat_id="c1")
    # Добавляем threshold сообщений в одно время (elapsed ≈ 0)
    for i in range(BULK_DETECTION_THRESHOLD):
        buf.add(_make_fwd(f"msg{i}"))
        buf._check_and_activate_bulk_mode()

    assert buf.is_bulk_mode(), "Bulk mode должен активироваться при burst"


def test_bulk_mode_not_activated_below_threshold():
    """Если сообщений меньше порога — bulk mode НЕ активируется."""
    from src.core.message_batcher import BULK_DETECTION_THRESHOLD, ForwardBatchBuffer

    buf = ForwardBatchBuffer(chat_id="c1")
    for i in range(BULK_DETECTION_THRESHOLD - 1):
        buf.add(_make_fwd(f"msg{i}"))
        buf._check_and_activate_bulk_mode()

    assert not buf.is_bulk_mode()


def test_bulk_mode_not_activated_if_slow():
    """
    Если BULK_DETECTION_THRESHOLD сообщений пришли с задержкой > BULK_DETECTION_WINDOW_SEC
    — bulk mode НЕ активируется.
    """
    from src.core.message_batcher import (
        BULK_DETECTION_THRESHOLD,
        BULK_DETECTION_WINDOW_SEC,
        ForwardBatchBuffer,
    )

    buf = ForwardBatchBuffer(chat_id="c1")
    # Симулируем старый первый timestamp
    buf._first_msg_ts = time.time() - BULK_DETECTION_WINDOW_SEC - 10
    for i in range(BULK_DETECTION_THRESHOLD):
        buf.messages.append(_make_fwd(f"msg{i}"))
    buf._check_and_activate_bulk_mode()

    assert not buf.is_bulk_mode(), "Slow burst не должен активировать bulk mode"


# ---------------------------------------------------------------------------
# 3. Bulk mode: лимит 200 сообщений
# ---------------------------------------------------------------------------


def test_bulk_mode_allows_200_messages():
    """В bulk mode effective_max должен быть BULK_MAX_BATCH_SIZE (200)."""
    from src.core.message_batcher import BULK_MAX_BATCH_SIZE, ForwardBatchBuffer

    buf = ForwardBatchBuffer(chat_id="c1")
    buf._bulk_mode = True  # форсируем bulk mode
    assert buf.effective_max() == BULK_MAX_BATCH_SIZE


@pytest.mark.asyncio
async def test_bulk_mode_no_flush_at_20():
    """
    В bulk mode при 20 сообщениях flush НЕ происходит немедленно
    (порог 200, а не 20).
    """
    from src.core.message_batcher import (
        BULK_DETECTION_THRESHOLD,
        FORWARD_BATCH_MAX,
        MessageBatcher,
    )

    b = MessageBatcher()
    flushed: list = []

    async def on_flush(chat_id, msgs):
        flushed.extend(msgs)

    # Добавляем BULK_DETECTION_THRESHOLD сообщений быстро → bulk mode
    for i in range(BULK_DETECTION_THRESHOLD):
        b.add_forward("c1", _make_fwd(f"msg{i}"), on_flush)

    # Убеждаемся что bulk mode активирован
    buf = b._get_fwd_buffer("c1")
    assert buf.is_bulk_mode(), "Bulk mode должен быть активирован"

    # Добавляем до 20 (бывший лимит) — flush НЕ должен произойти
    for i in range(BULK_DETECTION_THRESHOLD, FORWARD_BATCH_MAX):
        b.add_forward("c1", _make_fwd(f"msg{i}"), on_flush)

    await asyncio.sleep(0)  # даём event loop крутануться
    # В bulk mode 20 сообщений ещё не достигают нового лимита 200
    assert len(flushed) == 0, (
        f"В bulk mode flush не должен срабатывать при {FORWARD_BATCH_MAX} сообщениях, "
        f"flushed={len(flushed)}"
    )


# ---------------------------------------------------------------------------
# 4. Bulk mode: окно 60s
# ---------------------------------------------------------------------------


def test_bulk_mode_window_60s():
    """В bulk mode effective_window должен быть BULK_WINDOW_SEC (60)."""
    from src.core.message_batcher import BULK_WINDOW_SEC, ForwardBatchBuffer

    buf = ForwardBatchBuffer(chat_id="c1")
    buf._bulk_mode = True
    assert buf.effective_window() == BULK_WINDOW_SEC


# ---------------------------------------------------------------------------
# 5. Flush по max_reached в bulk mode (200 сообщений)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_flush_on_200_messages():
    """
    В bulk mode при достижении BULK_MAX_BATCH_SIZE (200) происходит немедленный flush.
    """
    from src.core.message_batcher import BULK_MAX_BATCH_SIZE, MessageBatcher

    b = MessageBatcher()
    flushed: list = []

    async def on_flush(chat_id, msgs):
        flushed.extend(msgs)

    # Форсируем bulk mode на буфере сразу
    buf = b._get_fwd_buffer("c1")
    buf._bulk_mode = True

    # Добавляем BULK_MAX_BATCH_SIZE сообщений
    for i in range(BULK_MAX_BATCH_SIZE):
        b.add_forward("c1", _make_fwd(f"msg{i}"), on_flush)

    await asyncio.sleep(0)  # даём event loop
    assert len(flushed) == BULK_MAX_BATCH_SIZE, (
        f"Ожидали flush на {BULK_MAX_BATCH_SIZE} сообщениях, получили {len(flushed)}"
    )


# ---------------------------------------------------------------------------
# 6. Bulk state сбрасывается после flush
# ---------------------------------------------------------------------------


def test_bulk_state_reset_after_drain():
    """
    После drain() bulk mode сбрасывается — следующая пачка начнётся
    в обычном режиме.
    """
    from src.core.message_batcher import ForwardBatchBuffer

    buf = ForwardBatchBuffer(chat_id="c1")
    buf._bulk_mode = True
    buf._first_msg_ts = time.time() - 1
    for i in range(10):
        buf.messages.append(_make_fwd(f"msg{i}"))

    drained = buf.drain()
    assert len(drained) == 10
    assert not buf.is_bulk_mode(), "Bulk mode должен сброситься после drain"
    assert buf._first_msg_ts == 0.0, "_first_msg_ts должен сброситься после drain"
    assert buf.size() == 0
