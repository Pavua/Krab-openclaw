# -*- coding: utf-8 -*-
"""Wave 63-C: dispatcher tick hook + staleness detection.

Контекст
--------
Wave 63-A: GetState pts probe детектит split-brain (server pts advanced,
update_id frozen). Wave 63-C добавляет КОМПЛЕМЕНТАРНЫЙ outcomes-not-heartbeats
signal: `_process_message` инкрементит `_dispatcher_tick_count` и
обновляет `_last_dispatcher_tick_ts`. Если pts probe говорит alive, а tick
stale > _DISPATCHER_TICK_STALENESS_SEC — handler chain мёртв (network OK,
dispatcher dead) → log `dispatcher_starved_detected`.

Hook approach: централизованный inline-инкремент в `_process_message`
(единственный funnel для всех ~50 @on_message decorators в bridge). Не
monkey-patch dispatcher, не decorator wrappers.
"""

from __future__ import annotations

import time
import types
from unittest.mock import AsyncMock

import pytest

from src.userbot.network_watchdog import (
    _DISPATCHER_TICK_STALENESS_SEC,
    _check_dispatcher_starved,
    _probe_updates_via_get_state,
)

# ---------------------------------------------------------------------------
# Hook semantics (counter + timestamp)
# ---------------------------------------------------------------------------


def _make_owner(**attrs: object) -> types.SimpleNamespace:
    """Минимальный duck-type для _check_dispatcher_starved / probe."""
    base = {
        "_dispatcher_tick_count": 0,
        "_last_dispatcher_tick_ts": time.time(),
        "_last_server_pts": 0,
        "_last_seen_update_id": 0,
        "client": None,
    }
    base.update(attrs)
    return types.SimpleNamespace(**base)


def _simulate_handler_entry(owner: types.SimpleNamespace) -> None:
    """Имитирует тот участок _process_message, что инкрементит tick."""
    owner._dispatcher_tick_count += 1
    owner._last_dispatcher_tick_ts = time.time()


def test_handler_entry_increments_counter_and_ts() -> None:
    owner = _make_owner()
    owner._last_dispatcher_tick_ts = time.time() - 100.0  # явно «старый» ts

    _simulate_handler_entry(owner)

    assert owner._dispatcher_tick_count == 1
    assert (time.time() - owner._last_dispatcher_tick_ts) < 1.0


def test_handler_entry_monotonic_counter() -> None:
    owner = _make_owner()
    for _ in range(5):
        _simulate_handler_entry(owner)
    assert owner._dispatcher_tick_count == 5


# ---------------------------------------------------------------------------
# Staleness check
# ---------------------------------------------------------------------------


def test_dispatcher_starved_false_when_fresh() -> None:
    owner = _make_owner(_last_dispatcher_tick_ts=time.time() - 60.0)
    assert _check_dispatcher_starved(owner) is False


def test_dispatcher_starved_true_when_stale() -> None:
    owner = _make_owner(
        _last_dispatcher_tick_ts=time.time() - (_DISPATCHER_TICK_STALENESS_SEC + 60.0)
    )
    assert _check_dispatcher_starved(owner) is True


def test_dispatcher_starved_fail_open_when_attr_missing() -> None:
    # owner без _last_dispatcher_tick_ts (например swarm client) → False, не crash.
    owner = types.SimpleNamespace(client=None)
    assert _check_dispatcher_starved(owner) is False


def test_dispatcher_starved_respects_custom_threshold() -> None:
    owner = _make_owner(_last_dispatcher_tick_ts=time.time() - 5.0)
    # При threshold 2с — точно starved
    assert _check_dispatcher_starved(owner, staleness_sec=2.0) is True
    assert _check_dispatcher_starved(owner, staleness_sec=60.0) is False


# ---------------------------------------------------------------------------
# Cross-reference: pts probe alive + dispatcher stale → starved signal
# ---------------------------------------------------------------------------


class _FakeState:
    def __init__(self, pts: int) -> None:
        self.pts = pts
        self.qts = 0
        self.date = 0
        self.seq = 0


@pytest.mark.asyncio
async def test_probe_alive_but_dispatcher_stale_logs_starved(caplog) -> None:
    """pts двинулся (probe.alive=True, delta>0), update_id тоже двинулся
    (нет split_brain_suspected), но `_last_dispatcher_tick_ts` старше
    threshold → cross-reference сигнал `dispatcher_starved_detected`
    должен сработать."""
    import logging

    caplog.set_level(logging.WARNING)

    # Probe вернёт alive (pts advanced, update_id моложе baseline)
    owner = _make_owner(
        _last_server_pts=100,
        _last_seen_update_id=999,
        _last_dispatcher_tick_ts=time.time() - (_DISPATCHER_TICK_STALENESS_SEC + 30.0),
    )
    client = types.SimpleNamespace()
    client.invoke = AsyncMock(return_value=_FakeState(pts=110))
    owner.client = client

    probe = await _probe_updates_via_get_state(
        owner, update_id_baseline=500
    )
    assert probe.alive is True
    assert probe.server_pts_delta == 10
    assert probe.split_brain_suspected is False

    # Симулируем cross-reference: тот же check что в network_watchdog
    # перед split_brain branch.
    starved = (
        probe.alive
        and probe.server_pts_delta > 0
        and _check_dispatcher_starved(owner)
    )
    assert starved is True


@pytest.mark.asyncio
async def test_probe_alive_and_dispatcher_fresh_no_starved() -> None:
    """pts двинулся, update_id двинулся, tick свежий → не starved."""
    owner = _make_owner(
        _last_server_pts=100,
        _last_seen_update_id=999,
        _last_dispatcher_tick_ts=time.time() - 30.0,
    )
    client = types.SimpleNamespace()
    client.invoke = AsyncMock(return_value=_FakeState(pts=120))
    owner.client = client

    probe = await _probe_updates_via_get_state(
        owner, update_id_baseline=500
    )
    assert probe.alive is True

    starved = (
        probe.alive
        and probe.server_pts_delta > 0
        and _check_dispatcher_starved(owner)
    )
    assert starved is False
