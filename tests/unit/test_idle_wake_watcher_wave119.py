# -*- coding: utf-8 -*-
"""Wave 119: tests for src/core/idle_wake_watcher.py."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from src.core import idle_wake_watcher as iww


def _make_monotonic(values: list[float]):
    """Возвращает callable, который последовательно отдаёт значения из списка.

    После исчерпания списка повторяет последнее значение — это нужно для
    `asyncio.sleep`'а, который под mock'ом не блокирует и может опросить
    monotonic неограниченно.
    """
    it = iter(values)
    last = [values[-1]] if values else [0.0]

    def _fn() -> float:
        try:
            v = next(it)
            last[0] = v
            return v
        except StopIteration:
            return last[0]

    return _fn


@pytest.mark.asyncio
async def test_idle_wake_normal_heartbeat_no_event():
    """Нормальный ритм — никаких wake-событий, callback не вызывается."""
    callback_calls: list[float] = []

    # monotonic: 0 (init) → 30 (после первой sleep) → 60 → ...
    # Каждый tick = ожидаемый interval=30, threshold=120 → нет события.
    mono = _make_monotonic([0.0, 30.0, 60.0, 90.0])

    async def _short_sleep(_: float) -> None:
        # 4-я итерация — отменяем loop, чтобы вышел из while
        if len(callback_calls) >= 0 and mono.__self__ if False else False:
            pass

    iterations = {"n": 0}

    async def _fake_sleep(_):
        iterations["n"] += 1
        if iterations["n"] >= 3:
            raise asyncio.CancelledError()

    with patch.object(asyncio, "sleep", _fake_sleep):
        await iww.idle_wake_watcher_loop(
            on_wake=lambda gap: callback_calls.append(gap),
            interval_sec=30.0,
            threshold_sec=120.0,
            enabled=True,
            _monotonic=mono,
            _wall_clock=lambda: 1_700_000_000.0,
        )

    assert callback_calls == []


@pytest.mark.asyncio
async def test_idle_wake_gap_above_threshold_triggers():
    """Gap > threshold → callback вызван с gap_seconds."""
    callback_calls: list[float] = []

    # monotonic: init=0, после 1-го sleep = 3600 (час "сна")
    mono = _make_monotonic([0.0, 3600.0])

    iterations = {"n": 0}

    async def _fake_sleep(_):
        iterations["n"] += 1
        if iterations["n"] >= 2:
            raise asyncio.CancelledError()

    with patch.object(asyncio, "sleep", _fake_sleep):
        await iww.idle_wake_watcher_loop(
            on_wake=lambda gap: callback_calls.append(gap),
            interval_sec=30.0,
            threshold_sec=120.0,
            enabled=True,
            _monotonic=mono,
            _wall_clock=lambda: 1_700_000_000.0,
        )

    assert len(callback_calls) == 1
    assert callback_calls[0] == pytest.approx(3600.0, abs=0.1)


@pytest.mark.asyncio
async def test_idle_wake_async_callback_awaited():
    """Async callback должен быть awaited."""
    result: list[str] = []

    async def _async_cb(gap: float) -> None:
        # Не используем asyncio.sleep — он patch'ится ниже под cancel.
        result.append(f"woke:{gap:.0f}")

    mono = _make_monotonic([0.0, 5000.0])
    iterations = {"n": 0}

    async def _fake_sleep(_):
        iterations["n"] += 1
        if iterations["n"] >= 2:
            raise asyncio.CancelledError()

    with patch.object(asyncio, "sleep", _fake_sleep):
        await iww.idle_wake_watcher_loop(
            on_wake=_async_cb,
            interval_sec=30.0,
            threshold_sec=120.0,
            enabled=True,
            _monotonic=mono,
            _wall_clock=lambda: 1_700_000_000.0,
        )

    assert result == ["woke:5000"]


@pytest.mark.asyncio
async def test_idle_wake_callback_exception_does_not_crash_loop():
    """Если callback бросает — loop логирует и продолжает работать."""

    def _bad_cb(gap: float):
        raise RuntimeError("boom")

    # Два wake-события подряд: проверяем что после первого crash loop живой.
    mono = _make_monotonic([0.0, 1000.0, 2000.0])
    iterations = {"n": 0}

    async def _fake_sleep(_):
        iterations["n"] += 1
        if iterations["n"] >= 3:
            raise asyncio.CancelledError()

    # Не должно поднять исключение наружу.
    with patch.object(asyncio, "sleep", _fake_sleep):
        await iww.idle_wake_watcher_loop(
            on_wake=_bad_cb,
            interval_sec=30.0,
            threshold_sec=120.0,
            enabled=True,
            _monotonic=mono,
            _wall_clock=lambda: 1_700_000_000.0,
        )


@pytest.mark.asyncio
async def test_idle_wake_disabled_returns_immediately():
    """enabled=False → loop сразу выходит, callback никогда не вызовется."""
    callback_calls: list[float] = []

    await iww.idle_wake_watcher_loop(
        on_wake=lambda gap: callback_calls.append(gap),
        interval_sec=30.0,
        threshold_sec=120.0,
        enabled=False,
    )

    assert callback_calls == []


@pytest.mark.asyncio
async def test_idle_wake_env_gate_off(monkeypatch):
    """KRAB_IDLE_WAKE_WATCHER_ENABLED=0 → loop сразу выходит."""
    monkeypatch.setenv("KRAB_IDLE_WAKE_WATCHER_ENABLED", "0")
    callback_calls: list[float] = []

    await iww.idle_wake_watcher_loop(
        on_wake=lambda gap: callback_calls.append(gap),
        interval_sec=30.0,
        threshold_sec=120.0,
    )

    assert callback_calls == []


@pytest.mark.asyncio
async def test_idle_wake_metrics_recorded():
    """При wake-событии вызывается record_idle_wake с правильными аргументами."""
    mono = _make_monotonic([0.0, 7200.0])
    iterations = {"n": 0}

    async def _fake_sleep(_):
        iterations["n"] += 1
        if iterations["n"] >= 2:
            raise asyncio.CancelledError()

    recorded: list[tuple[float, float]] = []

    def _fake_record(gap: float, ts: float) -> None:
        recorded.append((gap, ts))

    with patch.object(asyncio, "sleep", _fake_sleep), patch.object(
        iww, "record_idle_wake", _fake_record
    ):
        await iww.idle_wake_watcher_loop(
            on_wake=None,
            interval_sec=30.0,
            threshold_sec=120.0,
            enabled=True,
            _monotonic=mono,
            _wall_clock=lambda: 1_700_000_500.0,
        )

    assert len(recorded) == 1
    assert recorded[0][0] == pytest.approx(7200.0, abs=0.1)
    assert recorded[0][1] == pytest.approx(1_700_000_500.0, abs=0.1)
