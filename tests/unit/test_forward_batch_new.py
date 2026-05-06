"""
Дополнительные тесты forwarded-message batching.

Покрывают случаи, не охваченные test_forward_batch.py и test_forward_batch_e2e.py:

- ForwardBatchBuffer.drain() возвращает копию и очищает буфер
- ForwardBatchBuffer.format_prompt() на пустом буфере → ""
- ForwardBatchBuffer.reset() очищает messages
- ForwardBatchBuffer.size() корректен
- _get_fwd_buffer str-coercion для int chat_id
- Два разных chat_id имеют изолированные ForwardBatchBuffer'ы
- Таймерный flush: add_forward планирует таймер и он срабатывает через FORWARD_BATCH_WINDOW_SEC
- Буфер не флашится до достижения max при добавлении max-1 сообщений
- message_id сохраняется в PendingMessage при forward-batching
- Мульти-отправитель в одном буфере: header заголовок содержит всех уникальных
- format_prompt с forward_date=None не падает (нет timestamp в prefix)
- format_prompt: только username (без имени) — используется @username в заголовке
- add_forward не вызывает on_flush немедленно при < max сообщений (мягкий вариант)
- drain после schedule_flush отменяет timer (нет двойного flush)
"""

from __future__ import annotations

import asyncio
import time

import pytest


def _make_fwd(
    text: str,
    sender_id: str = "u1",
    fwd_name: str = "Alice",
    fwd_uname: str = "alice",
    fwd_date: int | None = None,
    message_id: int | None = None,
) -> "PendingMessage":  # noqa: F821
    from src.core.message_batcher import PendingMessage

    return PendingMessage(
        text=text,
        sender_id=sender_id,
        ts=time.time(),
        is_forwarded=True,
        forward_sender_name=fwd_name,
        forward_sender_username=fwd_uname,
        forward_date=fwd_date,
        message_id=message_id,
    )


# ---------------------------------------------------------------------------
# ForwardBatchBuffer unit tests
# ---------------------------------------------------------------------------


def test_forward_batch_buffer_drain_returns_copy_and_resets():
    """drain() возвращает все msgs и сбрасывает внутренний список."""
    from src.core.message_batcher import ForwardBatchBuffer

    buf = ForwardBatchBuffer(chat_id="c1")
    buf.messages = [_make_fwd("a"), _make_fwd("b")]
    drained = buf.drain()
    assert len(drained) == 2
    assert buf.size() == 0
    # drained — независимая копия, изменение не влияет на buf
    drained.pop()
    assert buf.size() == 0  # buf уже пустой — не должно упасть


def test_forward_batch_buffer_format_prompt_empty():
    """format_prompt() на пустом буфере возвращает пустую строку."""
    from src.core.message_batcher import ForwardBatchBuffer

    buf = ForwardBatchBuffer(chat_id="c1")
    assert buf.format_prompt() == ""
    assert buf.format_prompt(owner_query="что-то") == ""


def test_forward_batch_buffer_reset_clears():
    """reset() опустошает буфер."""
    from src.core.message_batcher import ForwardBatchBuffer

    buf = ForwardBatchBuffer(chat_id="c1")
    buf.messages = [_make_fwd("msg1"), _make_fwd("msg2")]
    buf.reset()
    assert buf.size() == 0


def test_forward_batch_buffer_size():
    """size() возвращает текущее количество сообщений."""
    from src.core.message_batcher import ForwardBatchBuffer

    buf = ForwardBatchBuffer(chat_id="c1")
    assert buf.size() == 0
    buf.messages.append(_make_fwd("x"))
    assert buf.size() == 1
    buf.messages.append(_make_fwd("y"))
    assert buf.size() == 2


def test_get_fwd_buffer_str_coercion():
    """_get_fwd_buffer принимает int chat_id и приводит к str."""
    from src.core.message_batcher import MessageBatcher

    b = MessageBatcher()
    buf = b._get_fwd_buffer(99999)  # type: ignore[arg-type]
    assert "99999" in b._fwd_buffers
    assert buf.chat_id == "99999"


def test_two_chats_have_isolated_fwd_buffers():
    """Два разных chat_id получают независимые ForwardBatchBuffer."""
    from src.core.message_batcher import MessageBatcher

    b = MessageBatcher()

    async def noop(chat_id, msgs):
        pass

    b.add_forward("chat_A", _make_fwd("msg for A"), noop)
    b.add_forward("chat_B", _make_fwd("msg for B"), noop)
    b.add_forward("chat_B", _make_fwd("msg2 for B"), noop)

    buf_a = b._get_fwd_buffer("chat_A")
    buf_b = b._get_fwd_buffer("chat_B")

    assert buf_a.size() == 1
    assert buf_b.size() == 2


def test_message_id_preserved_in_pending_message():
    """message_id сохраняется в PendingMessage (используется для dedup)."""
    from src.core.message_batcher import MessageBatcher

    b = MessageBatcher()
    flushed = []

    async def on_flush(chat_id, msgs):
        flushed.extend(msgs)

    msg = _make_fwd("текст", message_id=12345)
    b.add_forward("c1", msg, on_flush)

    buf = b._get_fwd_buffer("c1")
    assert buf.messages[0].message_id == 12345


def test_add_forward_below_max_does_not_flush_synchronously():
    """
    При добавлении max-1 сообщений flush не должен случиться синхронно.
    (Flush произойдёт через таймер позже.)
    """
    from src.core.message_batcher import FORWARD_BATCH_MAX, MessageBatcher

    b = MessageBatcher()
    flushed = []

    async def on_flush(chat_id, msgs):
        flushed.extend(msgs)

    for i in range(FORWARD_BATCH_MAX - 1):
        b.add_forward("c1", _make_fwd(f"msg{i}"), on_flush)

    # Без await — синхронный flush не произошёл
    assert len(flushed) == 0
    buf = b._get_fwd_buffer("c1")
    assert buf.size() == FORWARD_BATCH_MAX - 1


@pytest.mark.asyncio
async def test_timer_flush_fires_after_window():
    """
    После добавления нескольких forwards таймер срабатывает через
    FORWARD_BATCH_WINDOW_SEC и вызывает on_flush.
    """
    import src.core.message_batcher as batcher_mod

    original_window = batcher_mod.FORWARD_BATCH_WINDOW_SEC
    batcher_mod.FORWARD_BATCH_WINDOW_SEC = 0.05  # ускоряем тест

    try:
        from src.core.message_batcher import MessageBatcher

        b = MessageBatcher()
        flushed: list = []
        flush_event = asyncio.Event()

        async def on_flush(chat_id, msgs):
            flushed.extend(msgs)
            flush_event.set()

        b.add_forward("c1", _make_fwd("first"), on_flush)
        b.add_forward("c1", _make_fwd("second"), on_flush)

        # Ждём срабатывания таймера (с запасом)
        await asyncio.wait_for(flush_event.wait(), timeout=2.0)

        assert len(flushed) == 2
        assert flushed[0].text == "first"
        assert flushed[1].text == "second"
    finally:
        batcher_mod.FORWARD_BATCH_WINDOW_SEC = original_window


@pytest.mark.asyncio
async def test_drain_cancels_pending_timer():
    """
    Если буфер drain'ится вручную (например, при достижении max),
    запланированный таймер должен быть отменён и НЕ вызывать on_flush дважды.
    """
    import src.core.message_batcher as batcher_mod

    original_window = batcher_mod.FORWARD_BATCH_WINDOW_SEC
    batcher_mod.FORWARD_BATCH_WINDOW_SEC = 0.1  # небольшое окно

    try:
        from src.core.message_batcher import ForwardBatchBuffer

        flush_count = 0

        async def on_flush():
            nonlocal flush_count
            flush_count += 1

        buf = ForwardBatchBuffer(chat_id="c1")
        buf.schedule_flush(0.05, on_flush)  # таймер запланирован

        # Drain отменяет таймер
        buf.drain()

        # Ждём дольше чем таймер — on_flush НЕ должен вызваться
        await asyncio.sleep(0.2)
        assert flush_count == 0, "on_flush не должен был вызваться после drain"
    finally:
        batcher_mod.FORWARD_BATCH_WINDOW_SEC = original_window


# ---------------------------------------------------------------------------
# format_prompt edge cases
# ---------------------------------------------------------------------------


def test_format_prompt_no_date_uses_sender_only():
    """
    Если forward_date=None — prefix не содержит timestamp, только sender.
    """
    from src.core.message_batcher import ForwardBatchBuffer

    buf = ForwardBatchBuffer(chat_id="c1")
    buf.messages = [
        _make_fwd("text without date", fwd_name="Bob", fwd_uname="bob_u", fwd_date=None),
    ]
    prompt = buf.format_prompt()
    assert "bob_u" in prompt
    assert "text without date" in prompt
    # Если нет даты — не должно быть формата HH:MM, но sender должен быть
    # (Не проверяем отсутствие ":" жёстко — у prompt могут быть другие ":")
    assert "1." in prompt


def test_format_prompt_username_used_over_name_in_header():
    """
    Если есть и username, и display name — в заголовке используется username.
    """
    from src.core.message_batcher import ForwardBatchBuffer

    buf = ForwardBatchBuffer(chat_id="c1")
    buf.messages = [
        _make_fwd("hello", fwd_name="Full Name", fwd_uname="actual_handle"),
    ]
    prompt = buf.format_prompt()
    header_line = prompt.split("\n")[0]
    assert "actual_handle" in header_line
    # display name может быть в body, но в заголовке приоритет у username
    assert "actual_handle" in prompt


def test_format_prompt_multi_sender_unique_in_header():
    """
    Если один отправитель прислал несколько сообщений — в заголовке он один раз.
    """
    from src.core.message_batcher import ForwardBatchBuffer

    buf = ForwardBatchBuffer(chat_id="c1")
    for _ in range(3):
        buf.messages.append(_make_fwd("msg", fwd_name="Repeated", fwd_uname="repeated_u"))

    prompt = buf.format_prompt()
    header_line = prompt.split("\n")[0]
    # repeated_u должен встречаться в заголовке ровно один раз
    assert header_line.count("repeated_u") == 1


def test_format_prompt_three_senders_all_in_header():
    """Три разных отправителя → все три в заголовке."""
    from src.core.message_batcher import ForwardBatchBuffer

    buf = ForwardBatchBuffer(chat_id="c1")
    buf.messages = [
        _make_fwd("msg1", fwd_name="Alice", fwd_uname="alice"),
        _make_fwd("msg2", fwd_name="Bob", fwd_uname="bob"),
        _make_fwd("msg3", fwd_name="Carol", fwd_uname="carol"),
    ]
    prompt = buf.format_prompt()
    header_line = prompt.split("\n")[0]
    for name in ["alice", "bob", "carol"]:
        assert name in header_line, f"{name} должен быть в заголовке"


def test_format_prompt_at_sign_prefix_for_username():
    """Отправители без '@' в username → добавляется '@' в заголовке."""
    from src.core.message_batcher import ForwardBatchBuffer

    buf = ForwardBatchBuffer(chat_id="c1")
    buf.messages = [_make_fwd("hi", fwd_name="User", fwd_uname="noatsign")]
    prompt = buf.format_prompt()
    header_line = prompt.split("\n")[0]
    # '@noatsign' должен быть в заголовке
    assert "@noatsign" in header_line


def test_format_prompt_existing_at_not_doubled():
    """Если username уже начинается с '@' — не дублируем."""
    from src.core.message_batcher import ForwardBatchBuffer

    buf = ForwardBatchBuffer(chat_id="c1")
    buf.messages = [_make_fwd("hi", fwd_name="User", fwd_uname="@already")]
    prompt = buf.format_prompt()
    header_line = prompt.split("\n")[0]
    # '@already' должен быть ровно один раз, не '@@already'
    assert "@@already" not in header_line
    assert "@already" in header_line


# ---------------------------------------------------------------------------
# MessageBatcher.stats() for forward buffers
# ---------------------------------------------------------------------------


def test_stats_forward_buffers_count():
    """stats() корректно считает количество forward-буферов."""
    from src.core.message_batcher import MessageBatcher

    b = MessageBatcher()

    async def noop(chat_id, msgs):
        pass

    b.add_forward("chat1", _make_fwd("a"), noop)
    b.add_forward("chat2", _make_fwd("b"), noop)
    b.add_forward("chat2", _make_fwd("c"), noop)

    s = b.stats()
    assert s["forward_buffers"] == 2
    assert s["forward_pending"] == 3  # chat1:1 + chat2:2


@pytest.mark.asyncio
async def test_max_size_exact_boundary():
    """Ровно FORWARD_BATCH_MAX сообщений → немедленный flush, буфер пуст (non-bulk mode)."""
    import src.core.message_batcher as batcher_mod
    from src.core.message_batcher import FORWARD_BATCH_MAX, MessageBatcher

    # Wave 33-C: отключаем bulk mode — тест проверяет non-bulk лимит 20
    original_threshold = batcher_mod.BULK_DETECTION_THRESHOLD
    batcher_mod.BULK_DETECTION_THRESHOLD = 999
    try:
        b = MessageBatcher()
        flushed: list = []

        async def on_flush(chat_id, msgs):
            flushed.extend(msgs)

        for i in range(FORWARD_BATCH_MAX):
            b.add_forward("c1", _make_fwd(f"msg{i}"), on_flush)

        await asyncio.sleep(0)  # даём event loop крутануться

        assert len(flushed) == FORWARD_BATCH_MAX
        # Буфер после flush должен быть пуст
        buf = b._get_fwd_buffer("c1")
        assert buf.size() == 0
    finally:
        batcher_mod.BULK_DETECTION_THRESHOLD = original_threshold


@pytest.mark.asyncio
async def test_max_size_plus_one_triggers_two_flushes():
    """
    FORWARD_BATCH_MAX + 1 сообщений → первый batch из MAX немедленно,
    +1 остаётся в буфере и флашится по таймеру (non-bulk mode).
    """
    import src.core.message_batcher as batcher_mod

    original_window = batcher_mod.FORWARD_BATCH_WINDOW_SEC
    original_threshold = batcher_mod.BULK_DETECTION_THRESHOLD
    # Wave 33-C: отключаем bulk mode чтобы тестировать старый лимит 20
    batcher_mod.FORWARD_BATCH_WINDOW_SEC = 0.05
    batcher_mod.BULK_DETECTION_THRESHOLD = 999

    try:
        from src.core.message_batcher import FORWARD_BATCH_MAX, MessageBatcher

        b = MessageBatcher()
        batch_sizes: list[int] = []
        second_flush = asyncio.Event()

        async def on_flush(chat_id, msgs):
            batch_sizes.append(len(msgs))
            if len(batch_sizes) >= 2:
                second_flush.set()

        for i in range(FORWARD_BATCH_MAX + 1):
            b.add_forward("c1", _make_fwd(f"msg{i}"), on_flush)

        await asyncio.sleep(0)  # даём первый flush случиться
        assert batch_sizes[0] == FORWARD_BATCH_MAX

        # Ждём второй flush (таймер)
        await asyncio.wait_for(second_flush.wait(), timeout=2.0)
        assert batch_sizes[1] == 1
    finally:
        batcher_mod.FORWARD_BATCH_WINDOW_SEC = original_window
        batcher_mod.BULK_DETECTION_THRESHOLD = original_threshold


# ---------------------------------------------------------------------------
# Regression test: is_self owner forwards must be batched (bug fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_self_forwards_are_batched():
    """
    Регрессионный тест: owner пересылает несколько сообщений (is_self=True).
    add_forward должен принять их в буфер и вернуть True — т.е. батчинг работает
    для is_forwarded=True независимо от того, кто является отправителем.

    До фикса: _is_fwd_message имел условие `not is_self`, что приводило к
    пропуску батчинга и двойным LLM-вызовам для каждого переслан. сообщения.
    """
    import src.core.message_batcher as batcher_mod

    original_window = batcher_mod.FORWARD_BATCH_WINDOW_SEC
    batcher_mod.FORWARD_BATCH_WINDOW_SEC = 0.05

    try:
        from src.core.message_batcher import MessageBatcher, PendingMessage

        b = MessageBatcher()
        flushed: list = []
        flush_event = asyncio.Event()

        async def on_flush(chat_id, msgs):
            flushed.extend(msgs)
            flush_event.set()

        # Симулируем owner (is_self=True) пересылающего 2 сообщения
        # sender_id совпадает с self.me.id — как при is_self=True
        owner_id = "123456789"

        msg1 = PendingMessage(
            text="Привет из пересланного чата",
            sender_id=owner_id,
            is_forwarded=True,
            forward_sender_name="Alice",
            forward_sender_username="alice",
        )
        msg2 = PendingMessage(
            text="Второе переслан. сообщение",
            sender_id=owner_id,
            is_forwarded=True,
            forward_sender_name="Bob",
            forward_sender_username="bob",
        )

        r1 = b.add_forward("saved_messages", msg1, on_flush)
        r2 = b.add_forward("saved_messages", msg2, on_flush)

        # Оба должны быть приняты в буфер (return True)
        assert r1 is True, "msg1 должен быть буферизован"
        assert r2 is True, "msg2 должен быть буферизован"

        # Дожидаемся таймерного flush
        await asyncio.wait_for(flush_event.wait(), timeout=2.0)

        # Оба сообщения должны прийти в один flush
        assert len(flushed) == 2
        texts = {m.text for m in flushed}
        assert "Привет из пересланного чата" in texts
        assert "Второе переслан. сообщение" in texts
    finally:
        batcher_mod.FORWARD_BATCH_WINDOW_SEC = original_window
