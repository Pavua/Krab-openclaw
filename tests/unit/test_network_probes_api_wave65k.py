# -*- coding: utf-8 -*-
"""Wave 65-K tests: ``collect_network_probes_snapshot`` snapshot helper.

Покрывают:
    * empty/missing userbot — fail-open behavior;
    * populated state — все Wave 63 поля экспонированы;
    * graceful обработка broken типов (несовместимые значения);
    * swarm_probes структура совпадает с runtime layout;
    * paid_gemini_guard.mode определяется через env;
    * ``ago_sec`` корректно вычисляется относительно ``now``.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from src.core.network_probes_snapshot import collect_network_probes_snapshot


def test_returns_unavailable_for_none_userbot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "1")
    snap = collect_network_probes_snapshot(None)
    assert snap["available"] is False
    assert snap["main_dispatcher_tick_count"] == 0
    assert snap["main_dispatcher_tick_ago_sec"] is None
    assert snap["main_last_event_ago_sec"] is None
    assert snap["swarm_probes"] == {}
    guard = snap["paid_gemini_guard"]
    assert guard["mode"] == "block"
    # Wave 69: counters экспонированы из get_paid_gemini_guard_stats().
    assert "blocked_count" in guard
    assert "allowed_count" in guard
    assert "warned_count" in guard
    assert "last_blocked_at" in guard
    assert "last_blocked_host" in guard
    assert "last_blocked_model" in guard
    assert isinstance(guard["blocked_count"], int)
    assert isinstance(guard["allowed_count"], int)


def test_populated_state_exposes_all_fields() -> None:
    now = 1000.0
    bot = SimpleNamespace(
        _dispatcher_tick_count=42,
        _last_dispatcher_tick_ts=now - 5.0,
        _last_telegram_event_ts=now - 12.5,
        _last_seen_update_id=99887,
        _last_swarm_pts={
            "traders": {"pts": 100, "qts": 1, "seq": 2, "date": 1700000000, "ts": now - 3.0},
            "coders": {"pts": 55, "qts": 0, "seq": 1, "date": 1700000001, "ts": now - 1.5},
        },
    )

    snap = collect_network_probes_snapshot(bot, now=now)

    assert snap["available"] is True
    assert snap["main_dispatcher_tick_count"] == 42
    assert snap["main_dispatcher_tick_ago_sec"] == pytest.approx(5.0)
    assert snap["main_last_event_ago_sec"] == pytest.approx(12.5)
    assert snap["main_last_seen_update_id"] == 99887

    traders = snap["swarm_probes"]["traders"]
    assert traders["pts"] == 100
    assert traders["qts"] == 1
    assert traders["seq"] == 2
    assert traders["date"] == 1700000000
    assert traders["ago_sec"] == pytest.approx(3.0)

    coders = snap["swarm_probes"]["coders"]
    assert coders["ago_sec"] == pytest.approx(1.5)


def test_missing_attributes_fail_open() -> None:
    bot = SimpleNamespace()  # совсем пустой
    snap = collect_network_probes_snapshot(bot, now=2000.0)
    assert snap["available"] is True
    assert snap["main_dispatcher_tick_count"] == 0
    assert snap["main_dispatcher_tick_ago_sec"] is None
    assert snap["main_last_event_ago_sec"] is None
    assert snap["main_last_seen_update_id"] == 0
    assert snap["swarm_probes"] == {}


def test_broken_swarm_payload_does_not_raise() -> None:
    bot = SimpleNamespace(
        _last_swarm_pts={
            "good": {"pts": 1, "qts": 0, "seq": 0, "date": 0, "ts": 100.0},
            "bad_str": "not-a-dict",  # должен быть отфильтрован
            "bad_none": None,
        },
    )
    snap = collect_network_probes_snapshot(bot, now=200.0)
    assert "good" in snap["swarm_probes"]
    assert "bad_str" not in snap["swarm_probes"]
    assert "bad_none" not in snap["swarm_probes"]


def test_broken_swarm_root_object_returns_empty_dict() -> None:
    # _last_swarm_pts случайно не dict — не падаем.
    bot = SimpleNamespace(_last_swarm_pts=["unexpected", "list"])
    snap = collect_network_probes_snapshot(bot, now=100.0)
    assert snap["swarm_probes"] == {}


def test_paid_gemini_guard_mode_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "warn")
    snap = collect_network_probes_snapshot(None)
    assert snap["paid_gemini_guard"]["mode"] == "warn"


def test_paid_gemini_guard_mode_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "0")
    snap = collect_network_probes_snapshot(None)
    assert snap["paid_gemini_guard"]["mode"] == "off"


def test_now_defaults_to_wall_clock() -> None:
    # Без override — используем time.time(); ago_sec должно быть малым.
    bot = SimpleNamespace(
        _last_dispatcher_tick_ts=time.time() - 0.1,
        _last_telegram_event_ts=time.time() - 0.2,
    )
    snap = collect_network_probes_snapshot(bot)
    assert snap["main_dispatcher_tick_ago_sec"] is not None
    assert snap["main_dispatcher_tick_ago_sec"] < 5.0
    assert snap["main_last_event_ago_sec"] is not None
    assert snap["main_last_event_ago_sec"] < 5.0
