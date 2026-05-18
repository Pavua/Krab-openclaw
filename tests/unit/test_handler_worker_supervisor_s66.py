"""S66 W1: handler_worker supervisor — safety net beyond S65 W1 P0 fix.

Tests verify:
- supervisor detects dead handler_worker tasks (logs warning per dead worker)
- supervisor triggers recovery when ≥ majority dead
- supervisor respects ENV gate (=0 → no-op)
- supervisor is no-op if dispatcher absent or empty tasks list
"""

from __future__ import annotations

import asyncio
import importlib
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def watchdog_module(monkeypatch):
    """Reload module с заданными env-vars."""

    def _reload(*, enabled: str = "1", majority: str = "0.5"):
        monkeypatch.setenv("KRAB_HANDLER_WORKER_SUPERVISOR_ENABLED", enabled)
        monkeypatch.setenv("KRAB_HANDLER_WORKER_MAJORITY_THRESHOLD", majority)
        import src.userbot.network_watchdog as nw  # noqa: PLC0415

        return importlib.reload(nw)

    return _reload


class _FakeDoneTask:
    """Mimics asyncio.Task interface for testing."""

    def __init__(self, *, done: bool = True, exc: BaseException | None = None):
        self._done = done
        self._exc = exc

    def done(self) -> bool:
        return self._done

    def exception(self) -> BaseException | None:
        if not self._done:
            raise asyncio.InvalidStateError("not done")
        return self._exc


class _FakeDispatcher:
    def __init__(self, tasks: list):
        self.handler_worker_tasks = tasks


class _FakeClient:
    def __init__(self, tasks: list | None = None):
        if tasks is None:
            self.dispatcher = None
        else:
            self.dispatcher = _FakeDispatcher(tasks)


def _make_stub(nw_module, *, client: _FakeClient | None):
    from src.userbot.network_watchdog import NetworkWatchdogMixin  # noqa: PLC0415

    stub = NetworkWatchdogMixin.__new__(NetworkWatchdogMixin)
    stub.client = client
    stub._last_telegram_event_ts = 0.0
    stub._try_reconnect_pyrofork = AsyncMock(return_value=True)
    return stub


@pytest.mark.asyncio
async def test_handler_worker_supervisor_detects_dead_worker(watchdog_module, caplog):
    """Один worker мёртв из 4 — лог warning, recovery НЕ дёргается (ratio < 0.5)."""
    nw = watchdog_module(enabled="1", majority="0.5")
    tasks = [
        _FakeDoneTask(done=False),
        _FakeDoneTask(done=False),
        _FakeDoneTask(done=False),
        _FakeDoneTask(done=True, exc=RuntimeError("worker boom")),
    ]
    stub = _make_stub(nw, client=_FakeClient(tasks))
    await stub._supervise_handler_workers()
    # Recovery не должен быть triggered: 1/4 = 0.25 < 0.5
    stub._try_reconnect_pyrofork.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_worker_supervisor_triggers_recovery_when_majority_dead(
    watchdog_module,
):
    """≥50% workers dead → trigger _try_reconnect_pyrofork + reset event_ts."""
    nw = watchdog_module(enabled="1", majority="0.5")
    tasks = [
        _FakeDoneTask(done=True, exc=RuntimeError("dead1")),
        _FakeDoneTask(done=True, exc=ValueError("dead2")),
        _FakeDoneTask(done=False),
        _FakeDoneTask(done=False),
    ]
    stub = _make_stub(nw, client=_FakeClient(tasks))
    stub._last_telegram_event_ts = 0.0
    await stub._supervise_handler_workers()
    stub._try_reconnect_pyrofork.assert_awaited_once_with(stub.client)
    # Successful reconnect должен сбросить event timer
    assert stub._last_telegram_event_ts > 0


@pytest.mark.asyncio
async def test_handler_worker_supervisor_env_gate(watchdog_module):
    """KRAB_HANDLER_WORKER_SUPERVISOR_ENABLED=0 → полный no-op."""
    nw = watchdog_module(enabled="0", majority="0.5")
    tasks = [
        _FakeDoneTask(done=True, exc=RuntimeError("dead1")),
        _FakeDoneTask(done=True, exc=RuntimeError("dead2")),
        _FakeDoneTask(done=True, exc=RuntimeError("dead3")),
        _FakeDoneTask(done=True, exc=RuntimeError("dead4")),
    ]
    stub = _make_stub(nw, client=_FakeClient(tasks))
    await stub._supervise_handler_workers()
    # Gate disabled → recovery никогда не дёргается даже при 100% dead
    stub._try_reconnect_pyrofork.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_worker_supervisor_skips_no_dispatcher(watchdog_module):
    """Если у client отсутствует dispatcher (старт ещё не завершён) → no-op."""
    nw = watchdog_module(enabled="1")
    stub = _make_stub(nw, client=_FakeClient(tasks=None))
    await stub._supervise_handler_workers()
    stub._try_reconnect_pyrofork.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_worker_supervisor_skips_empty_tasks(watchdog_module):
    """Пустой handler_worker_tasks list → no-op (dispatcher не зафейлил)."""
    nw = watchdog_module(enabled="1")
    stub = _make_stub(nw, client=_FakeClient(tasks=[]))
    await stub._supervise_handler_workers()
    stub._try_reconnect_pyrofork.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_worker_supervisor_skips_no_client(watchdog_module):
    """client=None (e.g. до startup) → no-op."""
    nw = watchdog_module(enabled="1")
    stub = _make_stub(nw, client=None)
    await stub._supervise_handler_workers()
    stub._try_reconnect_pyrofork.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_worker_supervisor_recovery_exception_swallowed(watchdog_module):
    """Если _try_reconnect_pyrofork бросает — supervisor НЕ роняет heartbeat."""
    nw = watchdog_module(enabled="1", majority="0.5")
    tasks = [
        _FakeDoneTask(done=True, exc=RuntimeError("dead1")),
        _FakeDoneTask(done=True, exc=RuntimeError("dead2")),
    ]
    stub = _make_stub(nw, client=_FakeClient(tasks))
    stub._try_reconnect_pyrofork = AsyncMock(side_effect=RuntimeError("reconnect boom"))
    # Не должно бросать наружу
    await stub._supervise_handler_workers()
    stub._try_reconnect_pyrofork.assert_awaited_once()


def test_inspect_handler_worker_tasks_counts_correctly(watchdog_module):
    """Module-level helper: возвращает (total, [exceptions])."""
    nw = watchdog_module(enabled="1")
    tasks = [
        _FakeDoneTask(done=False),  # alive
        _FakeDoneTask(done=True, exc=None),  # cleanly done (no exc, e.g. cancel-with-result)
        _FakeDoneTask(done=True, exc=RuntimeError("boom1")),  # dead
        _FakeDoneTask(done=True, exc=ValueError("boom2")),  # dead
    ]
    client = _FakeClient(tasks)
    total, dead = nw._inspect_handler_worker_tasks(client)
    assert total == 4
    assert len(dead) == 2
    assert any(isinstance(e, RuntimeError) for e in dead)
    assert any(isinstance(e, ValueError) for e in dead)
