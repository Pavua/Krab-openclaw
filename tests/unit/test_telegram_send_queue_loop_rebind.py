# -*- coding: utf-8 -*-
"""
W32: regression tests для _TelegramSendQueue event-loop rebinding.

Сценарий: модульный singleton `_telegram_send_queue` создаётся один раз на
процесс, но при рестарте userbot'а event loop пересоздаётся. Старые
`asyncio.Queue` остаются привязанными к убитому loop → `queue.put()` бросает
`RuntimeError: Queue bound to different event loop`.

Проверяем:
1. `_get_or_create_queue` пересоздаёт очередь при смене loop;
2. `reset()` чистит state без async-cancel (безопасно на module-level);
3. end-to-end: `run()` работает в новом loop после того, как старый убит.
"""

from __future__ import annotations

import asyncio

import pytest

from src.userbot_bridge import _TelegramSendQueue


def _run_in_new_loop(coro_factory):
    """Запускает корутину в свежем loop'е и аккуратно его закрывает."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro_factory()), loop
    finally:
        loop.close()


class TestLoopRebind:
    def test_get_or_create_queue_rebinds_on_loop_mismatch(self) -> None:
        """Очередь, созданная в loop A, должна пересоздаться в loop B."""
        q = _TelegramSendQueue()

        # Loop A: создаём очередь и форсируем binding через `_get_loop()`
        # (в Python 3.10+ Queue._loop lazy-init при первом вызове _get_loop).
        async def _first() -> asyncio.Queue:
            queue = q._get_or_create_queue(42)
            # Форсируем lazy bind — читаем _loop через _get_loop внутри coro.
            queue._get_loop()
            return queue

        loop_a = asyncio.new_event_loop()
        try:
            queue_a = loop_a.run_until_complete(_first())
        finally:
            loop_a.close()

        # Sanity: loop A реально закэшировал себя в queue_a._loop.
        assert queue_a._loop is not None

        # Loop B: тот же chat_id → ожидаем новую очередь
        async def _second() -> asyncio.Queue:
            return q._get_or_create_queue(42)

        loop_b = asyncio.new_event_loop()
        try:
            queue_b = loop_b.run_until_complete(_second())
        finally:
            loop_b.close()

        assert queue_a is not queue_b, "Queue must be rebuilt under new loop"

    def test_get_or_create_queue_reuses_in_same_loop(self) -> None:
        """В одном loop'е — одна и та же очередь (кэш работает)."""
        q = _TelegramSendQueue()

        async def _twice() -> tuple[asyncio.Queue, asyncio.Queue]:
            first = q._get_or_create_queue(7)
            second = q._get_or_create_queue(7)
            return first, second

        loop = asyncio.new_event_loop()
        try:
            first, second = loop.run_until_complete(_twice())
        finally:
            loop.close()

        assert first is second

    def test_reset_clears_state_without_async(self) -> None:
        """`reset()` должен работать на module-level (без running loop)."""
        q = _TelegramSendQueue()

        async def _populate() -> None:
            q._get_or_create_queue(1)
            q._get_or_create_queue(2)
            q._slowmode_last_sent[1] = 123.0

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_populate())
        finally:
            loop.close()

        assert len(q._queues) == 2

        # reset() вызывается БЕЗ running loop — это и тестируем
        q.reset()

        assert q._queues == {}
        assert q._workers == {}
        assert q._slowmode_last_sent == {}

    def test_run_end_to_end_after_loop_restart(self) -> None:
        """End-to-end: `run()` работает в loop B после убитого loop A."""
        q = _TelegramSendQueue()

        async def _use_queue(value: str) -> str:
            async def coro_factory() -> str:
                return f"ok:{value}"

            return await q.run(chat_id=99, coro_factory=coro_factory)

        # Loop A — первый запуск (эмулирует предыдущий userbot процесс).
        # Штатно останавливаем воркера, чтобы не было ResourceWarning при
        # закрытии loop'а — это имитирует корректный shutdown userbot'а.
        loop_a = asyncio.new_event_loop()
        try:
            result_a = loop_a.run_until_complete(_use_queue("A"))
            loop_a.run_until_complete(q.stop_all())
        finally:
            loop_a.close()

        assert result_a == "ok:A"

        # Loop B — рестарт. До фикса здесь был RuntimeError даже если
        # stop_all() выше не был вызван. Симулируем худший сценарий —
        # заново populate'им state и проверяем rebind.
        async def _populate_then_use() -> str:
            q._get_or_create_queue(99)
            return await _use_queue("B")

        loop_b = asyncio.new_event_loop()
        try:
            result_b = loop_b.run_until_complete(_populate_then_use())
            loop_b.run_until_complete(q.stop_all())
        finally:
            loop_b.close()

        assert result_b == "ok:B"


class TestSingletonReset:
    def test_module_singleton_survives_bootstrap_reset(self) -> None:
        """Модульный singleton должен чиститься через explicit reset()."""
        from src.userbot_bridge import _telegram_send_queue

        # Изолируем state на случай, если другие тесты что-то насорили.
        _telegram_send_queue.reset()

        async def _populate() -> None:
            _telegram_send_queue._get_or_create_queue(1001)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_populate())
        finally:
            loop.close()

        assert len(_telegram_send_queue._queues) == 1

        # Эмуляция bootstrap reset — после смерти loop'а
        _telegram_send_queue.reset()

        assert _telegram_send_queue._queues == {}
        assert _telegram_send_queue._workers == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
