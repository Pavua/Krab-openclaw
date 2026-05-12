# -*- coding: utf-8 -*-
"""Wave 113: tests для Telegram MCP session connection pool.

Pool — instrumentation-слой поверх singleton ``TelegramBridge``: проверяет
session reuse, idle-based recycle, Prometheus метрики и lifecycle.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_MCP_DIR = Path(__file__).resolve().parents[2] / "mcp-servers" / "telegram"
if str(_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_DIR))

from session_pool import (  # type: ignore[import-not-found]  # noqa: E402
    DEFAULT_IDLE_TIMEOUT_SEC,
    SessionStats,
    TelegramSessionPool,
)


class _FakeBridge:
    """Минимальная реализация _BridgeLike для unit-тестов."""

    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0
        self.alive = False

    async def start(self) -> None:
        self.start_calls += 1
        self.alive = True

    async def stop(self) -> None:
        self.stop_calls += 1
        self.alive = False


def _make_pool(idle: float = 60.0) -> tuple[TelegramSessionPool, _FakeBridge, list[float]]:
    """Создаёт pool с инжектируемым clock — список с одним элементом-now."""
    clock = [1000.0]
    bridge = _FakeBridge()
    pool = TelegramSessionPool(
        bridge=bridge,
        idle_timeout=idle,
        now_fn=lambda: clock[0],
    )
    return pool, bridge, clock


@pytest.mark.asyncio
async def test_first_acquire_starts_session_without_reuse() -> None:
    """Первый acquire — старт session, reused_total остаётся 0."""
    pool, bridge, _ = _make_pool()
    await pool.acquire()
    stats = pool.stats()
    assert bridge.start_calls == 1
    assert bridge.alive is True
    assert stats.active == 1
    assert stats.reused_total == 0
    assert stats.recycled_total == 0


@pytest.mark.asyncio
async def test_second_acquire_within_idle_window_reuses() -> None:
    """Второй acquire в пределах idle window — reuse, ++reused_total."""
    pool, bridge, clock = _make_pool(idle=60.0)
    await pool.acquire()
    await pool.release()
    clock[0] += 30.0  # внутри окна
    await pool.acquire()
    stats = pool.stats()
    assert bridge.start_calls == 1  # повторного start не было
    assert bridge.stop_calls == 0
    assert stats.reused_total == 1
    assert stats.recycled_total == 0


@pytest.mark.asyncio
async def test_acquire_after_idle_recycles_session() -> None:
    """Acquire после idle-таймаута — stop+start, ++recycled_total."""
    pool, bridge, clock = _make_pool(idle=60.0)
    await pool.acquire()
    await pool.release()
    clock[0] += 120.0  # вышли за idle window
    await pool.acquire()
    stats = pool.stats()
    assert bridge.start_calls == 2
    assert bridge.stop_calls == 1
    assert stats.recycled_total == 1
    assert stats.reused_total == 0
    assert stats.active == 1


@pytest.mark.asyncio
async def test_shutdown_stops_session_and_clears_active() -> None:
    """``shutdown()`` останавливает bridge и обнуляет active gauge."""
    pool, bridge, _ = _make_pool()
    await pool.acquire()
    await pool.shutdown()
    stats = pool.stats()
    assert bridge.stop_calls == 1
    assert bridge.alive is False
    assert stats.active == 0


@pytest.mark.asyncio
async def test_prometheus_lines_contain_all_three_metrics() -> None:
    """Pool отдаёт три метрики в Prometheus text format."""
    pool, _, clock = _make_pool(idle=10.0)
    await pool.acquire()
    await pool.release()
    clock[0] += 5.0
    await pool.acquire()  # reuse
    await pool.release()
    clock[0] += 60.0
    await pool.acquire()  # recycle
    lines = pool.prometheus_lines()
    names = {line.split()[0] for line in lines}
    assert names == {
        "krab_mcp_telegram_session_active",
        "krab_mcp_telegram_session_reused_total",
        "krab_mcp_telegram_session_recycled_total",
    }
    by_name = {line.split()[0]: float(line.split()[1]) for line in lines}
    assert by_name["krab_mcp_telegram_session_active"] == 1.0
    assert by_name["krab_mcp_telegram_session_reused_total"] == 1.0
    assert by_name["krab_mcp_telegram_session_recycled_total"] == 1.0


@pytest.mark.asyncio
async def test_stats_returns_independent_snapshot() -> None:
    """``stats()`` отдаёт snapshot — мутации внутри pool не аффектят копию."""
    pool, _, _ = _make_pool()
    await pool.acquire()
    snapshot = pool.stats()
    assert isinstance(snapshot, SessionStats)
    await pool.release()
    await pool.acquire()
    # Snapshot из прошлого вызова не должен измениться
    assert snapshot.reused_total == 0


def test_default_idle_timeout_matches_brief() -> None:
    """Sanity: дефолт = 5 минут, согласно Wave 113 brief."""
    assert DEFAULT_IDLE_TIMEOUT_SEC == pytest.approx(300.0)
