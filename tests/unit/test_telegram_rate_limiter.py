# -*- coding: utf-8 -*-
"""
Регрессии `src/core/telegram_rate_limiter.py` — global sliding-window
rate limiter для исходящих Telegram API вызовов.

Что тестируем:

1. **Under cap → no wait.** acquire() N раз, где N <= max_per_sec, должен
   завершиться мгновенно (< 50ms суммарно, плюс фиксированные накладные).
2. **Over cap → wait.** acquire() max_per_sec+1 раз в одно окно должен
   разбудить блокирующий sleep.
3. **Window slides forward.** После прохождения window_sec секунд старые
   записи покидают окно и лимит снова разрешает.
4. **Stats counters** корректно отслеживают total_acquired / total_waited /
   total_wait_sec.
5. **reset_counters** работает.
6. **configure** меняет лимит на лету.
7. **Singleton exists** и имеет правильный тип.
8. **purpose label не влияет на логику** — ограничение глобальное, не
   per-purpose.

Тесты используют маленький `max_per_sec=5` и короткое `window_sec=0.2` чтобы
не тормозить pytest. Это не меняет семантику — sliding window масштабируется.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.core.telegram_rate_limiter import (
    GlobalTelegramRateLimiter,
    telegram_rate_limiter,
)


@pytest.mark.asyncio
async def test_under_cap_no_wait() -> None:
    """5 acquires при max=5 за окно 0.2s — никакого ожидания."""
    limiter = GlobalTelegramRateLimiter(max_per_sec=5, window_sec=0.2)
    start = time.monotonic()
    for _ in range(5):
        await limiter.acquire(purpose="test")
    elapsed = time.monotonic() - start
    assert elapsed < 0.05, f"expected <50ms for 5 acquires under cap, got {elapsed:.3f}s"
    stats = limiter.stats()
    assert stats["total_acquired"] == 5
    assert stats["total_waited"] == 0


@pytest.mark.asyncio
async def test_over_cap_triggers_wait() -> None:
    """6-й acquire при max=5 должен разбудить asyncio.sleep."""
    limiter = GlobalTelegramRateLimiter(max_per_sec=5, window_sec=0.2)
    for _ in range(5):
        await limiter.acquire(purpose="burst")
    start = time.monotonic()
    await limiter.acquire(purpose="overflow")
    elapsed = time.monotonic() - start
    # Ждали примерно 0.2s (окно), с небольшим запасом.
    assert 0.15 <= elapsed <= 0.35, f"expected ~0.2s wait, got {elapsed:.3f}s"
    stats = limiter.stats()
    assert stats["total_acquired"] == 6
    assert stats["total_waited"] == 1
    assert stats["total_wait_sec"] > 0


@pytest.mark.asyncio
async def test_window_slides_forward() -> None:
    """
    После прохождения окна старые записи удаляются и мы снова можем
    набрать полный max без ожидания.
    """
    limiter = GlobalTelegramRateLimiter(max_per_sec=3, window_sec=0.15)
    # Первая пачка — заполняем окно.
    for _ in range(3):
        await limiter.acquire()
    # Ждём пока окно проскользнёт.
    await asyncio.sleep(0.2)
    # Теперь следующие 3 acquire должны быть мгновенными.
    start = time.monotonic()
    for _ in range(3):
        await limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.05, f"expected instant after window slide, got {elapsed:.3f}s"
    stats = limiter.stats()
    assert stats["total_acquired"] == 6
    # Один wait НЕ должен был произойти, потому что окно прошло.
    assert stats["total_waited"] == 0


@pytest.mark.asyncio
async def test_reset_counters() -> None:
    limiter = GlobalTelegramRateLimiter(max_per_sec=10, window_sec=0.1)
    for _ in range(3):
        await limiter.acquire()
    assert limiter.stats()["total_acquired"] == 3
    limiter.reset_counters()
    stats = limiter.stats()
    assert stats["total_acquired"] == 0
    assert stats["total_waited"] == 0
    assert stats["total_wait_sec"] == 0.0


@pytest.mark.asyncio
async def test_configure_changes_limit() -> None:
    """configure() должен принять новый max и новое окно без рестарта."""
    limiter = GlobalTelegramRateLimiter(max_per_sec=2, window_sec=0.1)
    await limiter.acquire()
    await limiter.acquire()
    # Сейчас окно полно. Увеличиваем max → следующий acquire должен пройти мгновенно.
    limiter.configure(max_per_sec=10, window_sec=0.1)
    start = time.monotonic()
    await limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.05, f"expected instant after configure raise, got {elapsed:.3f}s"
    stats = limiter.stats()
    assert stats["max_per_sec"] == 10


@pytest.mark.asyncio
async def test_purpose_label_does_not_split_limit() -> None:
    """
    purpose — чистый label для логов, не должен создавать per-purpose buckets.
    5 acquires с разным purpose всё равно исчерпывают глобальный cap=5.
    """
    limiter = GlobalTelegramRateLimiter(max_per_sec=5, window_sec=0.2)
    for label in ("send_message", "get_chat", "edit_message", "reaction", "get_history"):
        await limiter.acquire(purpose=label)
    start = time.monotonic()
    await limiter.acquire(purpose="sixth")
    elapsed = time.monotonic() - start
    # 6-й должен ждать, невзирая на разный purpose.
    assert elapsed >= 0.15


def test_singleton_identity() -> None:
    """Module-level singleton существует и имеет правильный тип."""
    assert isinstance(telegram_rate_limiter, GlobalTelegramRateLimiter)


def test_stats_snapshot_is_copy() -> None:
    """stats() возвращает snapshot, мутация не должна затрагивать limiter state."""
    limiter = GlobalTelegramRateLimiter(max_per_sec=5, window_sec=0.1)
    snapshot = limiter.stats()
    snapshot["total_acquired"] = 9999
    # Реальный state нетронут.
    assert limiter.stats()["total_acquired"] == 0
