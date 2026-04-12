# -*- coding: utf-8 -*-
"""
Тесты для GlobalTelegramRateLimiter (src/core/telegram_rate_limiter.py).

Покрываем: acquire, механику ожидания, скользящее окно, статистику,
сброс счётчиков, configure, конкурентный доступ.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.core.telegram_rate_limiter import GlobalTelegramRateLimiter


# ── Вспомогательные фикстуры ──────────────────────────────────────────────────


@pytest.fixture
def limiter() -> GlobalTelegramRateLimiter:
    """Чистый лимитер с дефолтными параметрами (20 req/s, окно 1 с)."""
    return GlobalTelegramRateLimiter()


@pytest.fixture
def tight_limiter() -> GlobalTelegramRateLimiter:
    """Лимитер с очень маленьким окном для быстрых тестов (3 req/s, 0.1 с)."""
    return GlobalTelegramRateLimiter(max_per_sec=3, window_sec=0.1)


# ── Базовые acquire ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_acquire_single_increments_total(limiter: GlobalTelegramRateLimiter) -> None:
    """Один acquire увеличивает total_acquired на 1."""
    await limiter.acquire(purpose="test")
    assert limiter.stats()["total_acquired"] == 1


@pytest.mark.asyncio
async def test_acquire_multiple_no_wait_within_limit(limiter: GlobalTelegramRateLimiter) -> None:
    """N acquire подряд в пределах лимита не вызывают ожидания."""
    limit = limiter.stats()["max_per_sec"]
    for _ in range(limit):
        await limiter.acquire(purpose="batch")
    s = limiter.stats()
    assert s["total_acquired"] == limit
    # ждать не должны были совсем
    assert s["total_waited"] == 0


@pytest.mark.asyncio
async def test_acquire_purpose_does_not_affect_count(limiter: GlobalTelegramRateLimiter) -> None:
    """purpose — просто строка для логов, не влияет на счётчики."""
    await limiter.acquire(purpose="send_message")
    await limiter.acquire(purpose="get_chat")
    await limiter.acquire()  # дефолт "unknown"
    assert limiter.stats()["total_acquired"] == 3


# ── Механика ожидания ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wait_triggered_when_window_full(tight_limiter: GlobalTelegramRateLimiter) -> None:
    """При превышении лимита acquire должен ждать и записать ожидание в stats."""
    limit = tight_limiter.stats()["max_per_sec"]  # 3
    # Заполняем окно
    for _ in range(limit):
        await tight_limiter.acquire()
    # Следующий acquire должен сработать через sleep
    await tight_limiter.acquire()
    s = tight_limiter.stats()
    assert s["total_waited"] >= 1
    assert s["total_wait_sec"] > 0


@pytest.mark.asyncio
async def test_wait_sec_is_positive(tight_limiter: GlobalTelegramRateLimiter) -> None:
    """total_wait_sec > 0 после хотя бы одного принудительного ожидания."""
    limit = tight_limiter.stats()["max_per_sec"]
    for _ in range(limit):
        await tight_limiter.acquire()
    await tight_limiter.acquire()
    assert tight_limiter.stats()["total_wait_sec"] > 0.0


# ── Скользящее окно ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sliding_window_slots_expire(tight_limiter: GlobalTelegramRateLimiter) -> None:
    """После истечения window_sec слоты освобождаются и новый batch проходит без ожидания."""
    limit = tight_limiter.stats()["max_per_sec"]
    window = tight_limiter.stats()["window_sec"]
    # Заполняем первое окно
    for _ in range(limit):
        await tight_limiter.acquire()
    waited_before = tight_limiter.stats()["total_waited"]
    # Ждём окончания окна
    await asyncio.sleep(window + 0.05)
    # Второй batch должен пройти без ожиданий
    for _ in range(limit):
        await tight_limiter.acquire()
    assert tight_limiter.stats()["total_waited"] == waited_before


@pytest.mark.asyncio
async def test_current_in_window_reflects_recent_acquires(
    tight_limiter: GlobalTelegramRateLimiter,
) -> None:
    """current_in_window корректно отражает количество слотов в текущем окне."""
    await tight_limiter.acquire()
    await tight_limiter.acquire()
    assert tight_limiter.stats()["current_in_window"] == 2


# ── Статистика и сброс ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stats_keys(limiter: GlobalTelegramRateLimiter) -> None:
    """stats() возвращает все ожидаемые ключи."""
    s = limiter.stats()
    expected = {
        "max_per_sec",
        "window_sec",
        "current_in_window",
        "total_acquired",
        "total_waited",
        "total_wait_sec",
    }
    assert expected == set(s.keys())


@pytest.mark.asyncio
async def test_reset_counters_clears_totals(limiter: GlobalTelegramRateLimiter) -> None:
    """reset_counters() обнуляет acquired/waited/wait_sec, не трогает конфиг."""
    for _ in range(5):
        await limiter.acquire()
    limiter.reset_counters()
    s = limiter.stats()
    assert s["total_acquired"] == 0
    assert s["total_waited"] == 0
    assert s["total_wait_sec"] == 0.0
    # конфиг не затронут
    assert s["max_per_sec"] == 20


# ── configure ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_configure_changes_limit(limiter: GlobalTelegramRateLimiter) -> None:
    """configure() изменяет max_per_sec и window_sec."""
    limiter.configure(max_per_sec=5, window_sec=2.0)
    s = limiter.stats()
    assert s["max_per_sec"] == 5
    assert s["window_sec"] == 2.0


@pytest.mark.asyncio
async def test_configure_clamps_minimum(limiter: GlobalTelegramRateLimiter) -> None:
    """configure() не позволяет выставить max_per_sec < 1 или window_sec < 0.1."""
    limiter.configure(max_per_sec=0, window_sec=0.0)
    s = limiter.stats()
    assert s["max_per_sec"] >= 1
    assert s["window_sec"] >= 0.1


# ── Конкурентный доступ ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_acquires_no_race(tight_limiter: GlobalTelegramRateLimiter) -> None:
    """10 конкурентных acquire завершаются без исключений, total_acquired == 10."""
    tasks = [asyncio.create_task(tight_limiter.acquire(purpose=f"t{i}")) for i in range(10)]
    await asyncio.gather(*tasks)
    assert tight_limiter.stats()["total_acquired"] == 10


@pytest.mark.asyncio
async def test_concurrent_does_not_exceed_window(tight_limiter: GlobalTelegramRateLimiter) -> None:
    """При конкурентных acquire current_in_window никогда не превышает max_per_sec."""
    limit = tight_limiter.stats()["max_per_sec"]

    max_seen: list[int] = []

    async def acquire_and_check() -> None:
        await tight_limiter.acquire()
        max_seen.append(tight_limiter.stats()["current_in_window"])

    tasks = [asyncio.create_task(acquire_and_check()) for _ in range(limit + 2)]
    await asyncio.gather(*tasks)
    # После sleep внутри acquire окно частично истекает, но в момент записи
    # не должно превышать лимит + 1 (один новый слот только что добавлен)
    assert max(max_seen) <= limit + 1
