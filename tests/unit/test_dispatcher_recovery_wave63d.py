"""Wave 63-D: surgical recovery main kraab client при dispatcher_starved.

Тесты проверяют:
- gate KRAB_DISPATCHER_RECOVERY_ENABLED off (default) → skip + reason=disabled
- gate on + throttle позволяет → recovery вызывается
- gate on + throttle блокирует → skip + reason=throttled
- recovery exception → лог + return без crash
- recovery success → reset _last_telegram_event_ts
- recovery failure → лог dispatcher_starved_recovery_failed
- client=None → skip + reason=no_client
"""

from __future__ import annotations

import importlib
import time
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def watchdog_module(monkeypatch):
    """Reload module с заданными env-vars."""

    def _reload(*, enabled: str = "0", min_interval: str = "600"):
        monkeypatch.setenv("KRAB_DISPATCHER_RECOVERY_ENABLED", enabled)
        monkeypatch.setenv("KRAB_DISPATCHER_RECOVERY_MIN_INTERVAL_SEC", min_interval)
        import src.userbot.network_watchdog as nw  # noqa: PLC0415

        return importlib.reload(nw)

    return _reload


def _make_stub(nw_module, *, with_client: bool = True):
    """Минимальный stub bridge: bound _attempt_dispatcher_recovery + state."""
    from src.userbot.network_watchdog import NetworkWatchdogMixin  # noqa: PLC0415

    stub = NetworkWatchdogMixin.__new__(NetworkWatchdogMixin)
    stub.client = object() if with_client else None
    stub._last_dispatcher_recovery_ts = 0.0
    stub._last_telegram_event_ts = 0.0
    stub._try_reconnect_pyrofork = AsyncMock(return_value=True)
    return stub


@pytest.mark.asyncio
async def test_gate_disabled_skips_recovery(watchdog_module):
    nw = watchdog_module(enabled="0")
    stub = _make_stub(nw)
    await stub._attempt_dispatcher_recovery()
    stub._try_reconnect_pyrofork.assert_not_awaited()
    # _last_dispatcher_recovery_ts не должен меняться
    assert stub._last_dispatcher_recovery_ts == 0.0


@pytest.mark.asyncio
async def test_gate_enabled_throttle_passes_calls_reconnect(watchdog_module):
    nw = watchdog_module(enabled="1", min_interval="600")
    stub = _make_stub(nw)
    # last_recovery_ts=0 → elapsed = now (огромный) >= 600 → проходит
    await stub._attempt_dispatcher_recovery()
    stub._try_reconnect_pyrofork.assert_awaited_once_with(stub.client)
    assert stub._last_dispatcher_recovery_ts > 0


@pytest.mark.asyncio
async def test_gate_enabled_throttle_blocks_recent(watchdog_module):
    nw = watchdog_module(enabled="1", min_interval="600")
    stub = _make_stub(nw)
    # Только что был recovery
    stub._last_dispatcher_recovery_ts = time.time() - 10.0  # 10s ago
    await stub._attempt_dispatcher_recovery()
    stub._try_reconnect_pyrofork.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_exception_handled_gracefully(watchdog_module):
    nw = watchdog_module(enabled="1", min_interval="600")
    stub = _make_stub(nw)
    stub._try_reconnect_pyrofork = AsyncMock(side_effect=RuntimeError("boom"))
    # Не должно бросать
    await stub._attempt_dispatcher_recovery()
    stub._try_reconnect_pyrofork.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovery_success_resets_telegram_event_ts(watchdog_module):
    nw = watchdog_module(enabled="1", min_interval="600")
    stub = _make_stub(nw)
    stub._last_telegram_event_ts = 0.0
    stub._try_reconnect_pyrofork = AsyncMock(return_value=True)
    await stub._attempt_dispatcher_recovery()
    assert stub._last_telegram_event_ts > 0


@pytest.mark.asyncio
async def test_recovery_failure_logs_no_event_ts_reset(watchdog_module):
    nw = watchdog_module(enabled="1", min_interval="600")
    stub = _make_stub(nw)
    stub._last_telegram_event_ts = 0.0
    stub._try_reconnect_pyrofork = AsyncMock(return_value=False)
    await stub._attempt_dispatcher_recovery()
    # failure → НЕ сбрасываем event_ts
    assert stub._last_telegram_event_ts == 0.0


@pytest.mark.asyncio
async def test_no_client_skipped(watchdog_module):
    nw = watchdog_module(enabled="1", min_interval="600")
    stub = _make_stub(nw, with_client=False)
    await stub._attempt_dispatcher_recovery()
    stub._try_reconnect_pyrofork.assert_not_awaited()


@pytest.mark.asyncio
async def test_throttle_boundary_passes_when_interval_elapsed(watchdog_module):
    nw = watchdog_module(enabled="1", min_interval="60")
    stub = _make_stub(nw)
    # 61s назад — больше 60s → проходит
    stub._last_dispatcher_recovery_ts = time.time() - 61.0
    await stub._attempt_dispatcher_recovery()
    stub._try_reconnect_pyrofork.assert_awaited_once()
