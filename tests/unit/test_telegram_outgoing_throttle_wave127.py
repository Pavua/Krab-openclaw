# -*- coding: utf-8 -*-
"""Wave 127: pre-emptive Telegram outgoing throttle — регрессии.

Что покрываем:

1. Under-rate calls НЕ инициируют throttle (acquire returns False).
2. Over-rate calls триггерят pre-emptive delay (returns True + sleep).
3. Sliding window decay — старые timestamps уходят, лимит снова разрешает.
4. Env-gate KRAB_TG_OUTGOING_THROTTLE_ENABLED=0 → no-op.
5. Per-caller изоляция: спам в одном caller'е не throttle'ит другой.
6. Singleton присутствует и имеет правильный тип.
7. stats() возвращает корректные счётчики.

Тесты используют маленький window=1s + max_rps=10 для быстрой проверки.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.core.telegram_outgoing_throttle import (
    TelegramOutgoingThrottle,
    telegram_outgoing_throttle,
)


@pytest.mark.asyncio
async def test_under_rate_no_throttle() -> None:
    """Rate ниже cap → acquire возвращает False для всех вызовов."""
    th = TelegramOutgoingThrottle(
        max_rps=10.0, window_sec=1.0, delay_sec=0.05, enabled=True
    )
    # 5 calls за раз: rate = 5/1.0 = 5 msg/sec < 10 → no throttle.
    results = []
    for _ in range(5):
        results.append(await th.acquire(caller="handle_ask"))
    assert results == [False] * 5
    stats = th.stats()
    assert stats["total_acquired"] == 5
    assert stats["total_throttled"] == 0


@pytest.mark.asyncio
async def test_over_rate_triggers_throttle() -> None:
    """Burst сверх cap → последующие acquires возвращают True + спят."""
    th = TelegramOutgoingThrottle(
        max_rps=5.0, window_sec=1.0, delay_sec=0.05, enabled=True
    )
    # Забиваем окно: 6 calls → rate = 6/1.0 = 6 msg/sec > 5.
    # Первые 5 проходят без throttle (rate ещё < threshold к моменту acquire).
    # 6-й уже видит rate=5 → НЕ throttle (нужно > max_rps, не >=).
    # 7-й видит rate=6 → throttle.
    for _ in range(6):
        await th.acquire(caller="voice_reply")
    start = time.monotonic()
    throttled = await th.acquire(caller="voice_reply")
    elapsed = time.monotonic() - start
    assert throttled is True
    # Должен был поспать delay_sec=0.05.
    assert elapsed >= 0.04, f"expected >=0.04s sleep, got {elapsed:.3f}s"
    stats = th.stats()
    assert stats["total_throttled"] >= 1


@pytest.mark.asyncio
async def test_window_decay_releases_throttle() -> None:
    """После прохождения window_sec старые timestamps выходят → no throttle."""
    th = TelegramOutgoingThrottle(
        max_rps=3.0, window_sec=0.3, delay_sec=0.01, enabled=True
    )
    # Забиваем окно burst'ом.
    for _ in range(5):
        await th.acquire(caller="bg_loop")
    # Ждём пока окно очистится.
    await asyncio.sleep(0.4)
    # Теперь rate=0, новый acquire не throttle'ит.
    throttled = await th.acquire(caller="bg_loop")
    assert throttled is False


@pytest.mark.asyncio
async def test_env_gate_off_disables_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    """KRAB_TG_OUTGOING_THROTTLE_ENABLED=0 → acquire всегда возвращает False."""
    monkeypatch.setenv("KRAB_TG_OUTGOING_THROTTLE_ENABLED", "0")
    # enabled=None → читаем env.
    th = TelegramOutgoingThrottle(
        max_rps=2.0, window_sec=1.0, delay_sec=0.05, enabled=None
    )
    # Даже при явной перегрузке — без throttle.
    results = []
    for _ in range(20):
        results.append(await th.acquire(caller="anything"))
    assert all(r is False for r in results)
    assert th.stats()["total_throttled"] == 0


@pytest.mark.asyncio
async def test_per_caller_isolation() -> None:
    """Burst в caller A не должен throttle'ить caller B."""
    th = TelegramOutgoingThrottle(
        max_rps=3.0, window_sec=1.0, delay_sec=0.01, enabled=True
    )
    # 6 calls в caller_a — rate высокий.
    for _ in range(6):
        await th.acquire(caller="caller_a")
    # caller_b видит свой собственный пустой deque.
    throttled_b = await th.acquire(caller="caller_b")
    assert throttled_b is False


@pytest.mark.asyncio
async def test_stats_exposes_counters() -> None:
    th = TelegramOutgoingThrottle(
        max_rps=2.0, window_sec=1.0, delay_sec=0.01, enabled=True
    )
    for _ in range(4):
        await th.acquire(caller="x")
    stats = th.stats()
    assert stats["total_acquired"] == 4
    assert stats["enabled"] is True
    assert stats["max_rps"] >= 1.0
    assert "x" in stats["per_caller_in_window"]


def test_singleton_exists() -> None:
    assert isinstance(telegram_outgoing_throttle, TelegramOutgoingThrottle)
    snapshot = telegram_outgoing_throttle.stats()
    # is_enabled() возвращает bool из env (default ON).
    assert isinstance(snapshot["enabled"], bool)
