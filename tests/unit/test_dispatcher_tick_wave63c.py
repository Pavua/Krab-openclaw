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

Session 54 Task C: убраны тесты на `on_raw_update` disambiguation —
handler удалён, был не reliable (UpdateShort(UpdateNewMessage) bypass).
Primary signal теперь `Client.last_update_time` (S53 hotfix3).
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
    """Минимальный duck-type для _check_dispatcher_starved / probe.

    Session 54 Task C: убраны `_raw_update_tick_count` / `_last_raw_update_ts`
    дефолты (handler удалён). client=None по умолчанию → legacy fallback
    срабатывает на conservative 3x threshold (если не override'нуть client).
    """
    tick_ts = attrs.get("_last_dispatcher_tick_ts", time.time())
    base = {
        "_dispatcher_tick_count": 0,
        "_last_dispatcher_tick_ts": tick_ts,
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
    # client=None → legacy fallback на conservative 3x threshold.
    owner = _make_owner(
        _last_dispatcher_tick_ts=time.time() - (3.0 * _DISPATCHER_TICK_STALENESS_SEC + 60.0)
    )
    assert _check_dispatcher_starved(owner) is True


def test_dispatcher_starved_fail_open_when_attr_missing() -> None:
    # owner без _last_dispatcher_tick_ts (например swarm client) → False, не crash.
    owner = types.SimpleNamespace(client=None)
    assert _check_dispatcher_starved(owner) is False


def test_dispatcher_starved_respects_custom_threshold() -> None:
    """S54 C: client=None → legacy conservative 3x threshold path.
    При threshold=2s conservative=6s → tick должен быть >6s старше."""
    now = time.time()
    owner = _make_owner(_last_dispatcher_tick_ts=now - 10.0)
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
    должен сработать.

    S54 C: client.invoke возвращает state → mock client. Но
    `_check_dispatcher_starved` читает `client.last_update_time` отдельно;
    если атрибут отсутствует → legacy conservative fallback. Чтобы тест был
    стабилен, ставим tick > 3x threshold."""
    import logging

    caplog.set_level(logging.WARNING)

    # Probe вернёт alive (pts advanced, update_id моложе baseline)
    owner = _make_owner(
        _last_server_pts=100,
        _last_seen_update_id=999,
        _last_dispatcher_tick_ts=time.time() - (3.0 * _DISPATCHER_TICK_STALENESS_SEC + 30.0),
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


# ---------------------------------------------------------------------------
# Legacy fallback: client.last_update_time отсутствует → conservative 3x
# ---------------------------------------------------------------------------


def test_dispatcher_starved_fallback_legacy_quiet_chat() -> None:
    """S54 C legacy fallback: client.last_update_time нет → conservative 3x
    threshold. Quiet chat 15 мин (1.5x) < 3x → НЕ starved."""
    now = time.time()
    owner = types.SimpleNamespace(
        _last_dispatcher_tick_ts=now - 900.0,  # 15 мин
        client=None,
    )
    assert _check_dispatcher_starved(owner) is False


def test_dispatcher_starved_fallback_legacy_long_silence() -> None:
    """S54 C legacy fallback: 30 мин (>3x threshold) → starved."""
    now = time.time()
    owner = types.SimpleNamespace(
        _last_dispatcher_tick_ts=now - (3.0 * _DISPATCHER_TICK_STALENESS_SEC + 30.0),
        client=None,
    )
    assert _check_dispatcher_starved(owner) is True


# ---------------------------------------------------------------------------
# Session 53 P3.6 hotfix3: Client.last_update_time primary signal
# ---------------------------------------------------------------------------


from datetime import datetime, timedelta

from src.userbot.network_watchdog import _client_last_update_age_sec


def _make_client_with_last_update(age_sec: float) -> types.SimpleNamespace:
    """Mock pyrofork client with `last_update_time` attribute."""
    return types.SimpleNamespace(
        last_update_time=datetime.now() - timedelta(seconds=age_sec)
    )


def test_client_last_update_age_returns_none_when_no_client() -> None:
    """No `client` attr или client=None → None (caller falls back)."""
    owner = types.SimpleNamespace(client=None)
    assert _client_last_update_age_sec(owner, time.time()) is None


def test_client_last_update_age_returns_none_when_attr_missing() -> None:
    """Старый pyrofork без `last_update_time` → None."""
    owner = types.SimpleNamespace(client=types.SimpleNamespace())
    assert _client_last_update_age_sec(owner, time.time()) is None


def test_client_last_update_age_returns_seconds() -> None:
    """Здоровый pyrofork client — возвращает age в секундах."""
    owner = types.SimpleNamespace(client=_make_client_with_last_update(120.0))
    age = _client_last_update_age_sec(owner, time.time())
    assert age is not None
    assert 119.0 < age < 121.0


def test_dispatcher_starved_true_when_network_alive_but_dispatcher_dead() -> None:
    """P3.6 hotfix3 PRIMARY case: client.last_update_time свежий
    (network receives updates), но dispatcher_tick замёрз → handler chain
    мёртв = SILENT-DEATH. Это и есть production pattern 17:29→19:24."""
    now = time.time()
    owner = _make_owner(
        _last_dispatcher_tick_ts=now - (_DISPATCHER_TICK_STALENESS_SEC + 60.0),
    )
    # Pyrofork получает updates 30 sec назад (network alive)
    owner.client = _make_client_with_last_update(30.0)
    assert _check_dispatcher_starved(owner) is True


def test_dispatcher_starved_false_when_both_network_and_dispatcher_stale() -> None:
    """P3.6 hotfix3: оба stale → НЕ silent-death (это network silence).
    Regular reconnect path обрабатывает это, не dispatcher recovery."""
    now = time.time()
    stale = _DISPATCHER_TICK_STALENESS_SEC + 60.0
    owner = _make_owner(_last_dispatcher_tick_ts=now - stale)
    owner.client = _make_client_with_last_update(stale)
    assert _check_dispatcher_starved(owner) is False


def test_dispatcher_starved_false_when_dispatcher_fresh() -> None:
    """P3.6 hotfix3: dispatcher_tick свежий → не starved (early return)."""
    now = time.time()
    owner = _make_owner(_last_dispatcher_tick_ts=now - 60.0)
    owner.client = _make_client_with_last_update(60.0)
    assert _check_dispatcher_starved(owner) is False
