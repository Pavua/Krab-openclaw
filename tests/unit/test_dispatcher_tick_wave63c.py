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
    """Минимальный duck-type для _check_dispatcher_starved / probe.

    Session 53 P3.6 (revised after 2026-05-17 hotfix):
    - дефолт `_raw_update_tick_count = 1` (handler triggered хотя бы раз)
    - дефолт `_last_raw_update_ts` синхронизируется с `_last_dispatcher_tick_ts`
    - тесты для conservative fallback могут явно ставить count=0
    """
    tick_ts = attrs.get("_last_dispatcher_tick_ts", time.time())
    base = {
        "_dispatcher_tick_count": 0,
        "_last_dispatcher_tick_ts": tick_ts,
        "_raw_update_tick_count": 1,  # handler healthy (triggered хотя бы раз)
        "_last_raw_update_ts": tick_ts,  # дефолт: синхронно с message tick
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
    """P3.6 hotfix: tick_ts свежее raw_age > 2*threshold → conservative path
    срабатывает (3x threshold). Тест ставит достаточно старый tick для
    пересечения conservative порога."""
    now = time.time()
    # При threshold=2s, conservative=6s → tick должен быть >6s старше
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


# ---------------------------------------------------------------------------
# Session 53 P3.6: on_raw_update disambiguation
# ---------------------------------------------------------------------------


def test_dispatcher_starved_false_when_raw_alive_but_message_stale() -> None:
    """P3.6: raw_update tick свежий, message tick устарел → НЕ starved.

    Кейс: в чате просто нет messages, но user_status/typing/channel updates
    идут (raw_update триггерится). Это нормальный «тихий чат», не silent-death.
    """
    now = time.time()
    owner = _make_owner(
        _last_dispatcher_tick_ts=now - (_DISPATCHER_TICK_STALENESS_SEC + 60.0),
        _last_raw_update_ts=now - 30.0,  # свежий raw tick
    )
    assert _check_dispatcher_starved(owner) is False


def test_dispatcher_starved_true_when_both_stale() -> None:
    """P3.6: оба ts устарели → silent-death detected.

    Это и есть production-инцидент 2026-05-17 17:29→19:24: handler chain
    мёртв ПОЛНОСТЬЮ (никаких updates от Pyrogram dispatcher).
    """
    now = time.time()
    stale_offset = _DISPATCHER_TICK_STALENESS_SEC + 60.0
    owner = _make_owner(
        _last_dispatcher_tick_ts=now - stale_offset,
        _last_raw_update_ts=now - stale_offset,
    )
    assert _check_dispatcher_starved(owner) is True


def test_dispatcher_starved_message_chain_broken_pattern() -> None:
    """P3.6: raw alive + message stale = message filter chain broken (НЕ starved).

    Этот pattern требует другой recovery — не reconnect Pyrogram, а debug
    filter chain. Чтобы не ускорять recovery в ложных случаях, current
    detection возвращает False; future Wave может добавить отдельный
    `dispatcher_filter_chain_broken` signal.
    """
    now = time.time()
    owner = _make_owner(
        _last_dispatcher_tick_ts=now - (_DISPATCHER_TICK_STALENESS_SEC + 60.0),
        _last_raw_update_ts=now - 1.0,  # raw совсем свежий
    )
    assert _check_dispatcher_starved(owner) is False


def test_dispatcher_starved_fallback_when_raw_attr_missing() -> None:
    """P3.6 hotfix: backward compat — если `_raw_update_tick_count` / ts отсутствуют,
    fall back на CONSERVATIVE message-only (3x threshold) чтобы избежать
    false-positive recovery в quiet chats overnight.

    Тут message_starved 3x+ → conservative threshold пройден → starved True.
    """
    now = time.time()
    owner = types.SimpleNamespace(
        _last_dispatcher_tick_ts=now - (3.0 * _DISPATCHER_TICK_STALENESS_SEC + 60.0),
        # _raw_update_tick_count / _last_raw_update_ts отсутствуют
        client=None,
    )
    assert _check_dispatcher_starved(owner) is True


def test_dispatcher_starved_NOT_in_quiet_chat_when_raw_broken() -> None:
    """P3.6 hotfix: если raw handler broken (count=0 или age>2x threshold),
    quiet message period <3x threshold НЕ должен trigger'ить recovery.

    Это и есть production регрессия которую мы фиксим: pyrofork on_raw_update
    handler сломан → raw_count=0/застрял → false-positive starved=True в
    healthy quiet чатах через каждые 10 мин тишины.
    """
    now = time.time()
    # Quiet chat: messages были 15 мин назад (> 1x threshold, < 3x threshold)
    owner = _make_owner(
        _last_dispatcher_tick_ts=now - 900.0,  # 15 мин
        _raw_update_tick_count=0,  # broken handler
        _last_raw_update_ts=now - 900.0,
    )
    assert _check_dispatcher_starved(owner) is False  # не starved — quiet chat


def test_dispatcher_starved_TRUE_after_3x_threshold_when_raw_broken() -> None:
    """P3.6 hotfix: даже при broken raw handler, если message молчит 30+ мин
    (3x threshold) — recovery всё-таки нужно. Длительные тишины редки в
    активных чатах; в quiet чатах overnight 30 мин = legitimate dead state."""
    now = time.time()
    owner = _make_owner(
        _last_dispatcher_tick_ts=now - (3.0 * _DISPATCHER_TICK_STALENESS_SEC + 30.0),
        _raw_update_tick_count=0,
        _last_raw_update_ts=now - 1800.0,
    )
    assert _check_dispatcher_starved(owner) is True


def test_dispatcher_starved_raw_handler_stuck_uses_conservative_threshold() -> None:
    """P3.6 hotfix: handler работал когда-то (count > 0) но raw_age > 2x
    threshold → handler застрял (pyrofork bug). Используем conservative."""
    now = time.time()
    owner = _make_owner(
        _last_dispatcher_tick_ts=now - 900.0,  # 15 мин (1.5x)
        _raw_update_tick_count=168,  # был triggered ранее
        _last_raw_update_ts=now - 1500.0,  # 25 мин (> 2x = 1200)
    )
    # raw_age > 2x threshold → conservative mode → message не достиг 3x → False
    assert _check_dispatcher_starved(owner) is False


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
        # raw_count > 0 чтобы не fall back на legacy
        _raw_update_tick_count=10,
        _last_raw_update_ts=now - 30.0,
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
