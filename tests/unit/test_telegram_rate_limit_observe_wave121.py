# -*- coding: utf-8 -*-
"""
Wave 121: observability для Telegram rate limiter (FloodWait pattern).

Тестируем:
1. observe_telegram_flood_wait записывает Histogram + Gauge → active=1, deadline в будущем.
2. refresh_telegram_rate_limited_active обнуляет gauge после истечения deadline.
3. Multiple callers получают независимый state (per-label).
4. Legacy counter krab_telegram_flood_wait_total всё ещё инкрементируется.
5. collect_metrics() рендерит gauge в text-format с правильным caller label.
6. observe_telegram_flood_wait с wait_seconds=0 не зависает и работает корректно.
"""

from __future__ import annotations

import time

import pytest

from src.core import prometheus_metrics as pm


@pytest.fixture(autouse=True)
def _clear_state() -> None:
    """Сбрасываем deadlines + legacy counter перед каждым тестом."""
    pm._TELEGRAM_RATE_LIMIT_DEADLINES.clear()
    pm._TELEGRAM_FLOOD_WAIT_COUNTER.clear()
    yield
    pm._TELEGRAM_RATE_LIMIT_DEADLINES.clear()
    pm._TELEGRAM_FLOOD_WAIT_COUNTER.clear()


def test_observe_records_histogram_and_gauge_active() -> None:
    pm.observe_telegram_flood_wait("handle_ask", 30.0)

    # Gauge active=1, deadline в будущем
    assert "handle_ask" in pm._TELEGRAM_RATE_LIMIT_DEADLINES
    assert pm._TELEGRAM_RATE_LIMIT_DEADLINES["handle_ask"] > time.time()

    # Legacy counter тоже инкрементирован
    assert pm._TELEGRAM_FLOOD_WAIT_COUNTER.get("handle_ask") == 1


def test_refresh_clears_expired_deadlines() -> None:
    pm.observe_telegram_flood_wait("auth.bot", 5.0)
    assert "auth.bot" in pm._TELEGRAM_RATE_LIMIT_DEADLINES

    # Симулируем «прошло 10 секунд» — передаём now в будущем.
    snapshot = pm.refresh_telegram_rate_limited_active(now=time.time() + 10.0)

    assert snapshot.get("auth.bot") == 0
    # Истекший deadline удалён из state.
    assert "auth.bot" not in pm._TELEGRAM_RATE_LIMIT_DEADLINES


def test_refresh_keeps_active_callers() -> None:
    pm.observe_telegram_flood_wait("send_message", 300.0)
    snapshot = pm.refresh_telegram_rate_limited_active()

    assert snapshot.get("send_message") == 1
    assert "send_message" in pm._TELEGRAM_RATE_LIMIT_DEADLINES


def test_multiple_callers_isolated() -> None:
    pm.observe_telegram_flood_wait("caller_a", 60.0)
    pm.observe_telegram_flood_wait("caller_b", 600.0)

    snapshot = pm.refresh_telegram_rate_limited_active()
    assert snapshot.get("caller_a") == 1
    assert snapshot.get("caller_b") == 1

    # Имитируем истечение только caller_a.
    snapshot2 = pm.refresh_telegram_rate_limited_active(now=time.time() + 120.0)
    assert snapshot2.get("caller_a") == 0
    assert snapshot2.get("caller_b") == 1


def test_collect_metrics_renders_gauge_line() -> None:
    pm.observe_telegram_flood_wait("voice_refresh", 45.0)
    output = pm.collect_metrics()

    assert "krab_telegram_rate_limited_active" in output
    # Gauge type декларирован
    assert "# TYPE krab_telegram_rate_limited_active gauge" in output
    # Caller label виден в render
    assert 'caller="voice_refresh"' in output


def test_observe_zero_wait_is_safe() -> None:
    # Edge case: некоторые код-пути могут позвать с wait=0 — не должно ломаться.
    pm.observe_telegram_flood_wait("zero_wait", 0.0)

    # Counter всё равно инкрементирован (FloodWait произошёл, даже если wait=0).
    assert pm._TELEGRAM_FLOOD_WAIT_COUNTER.get("zero_wait") == 1
    # Deadline ~= сейчас, refresh немедленно отметит как inactive.
    snapshot = pm.refresh_telegram_rate_limited_active(now=time.time() + 1.0)
    assert snapshot.get("zero_wait") == 0
