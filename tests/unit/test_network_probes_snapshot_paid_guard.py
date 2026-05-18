# -*- coding: utf-8 -*-
"""S54 D: regression на ``paid_gemini_guard`` секцию в network probes snapshot.

После Wave 163 endpoint ``/api/network/probes`` (health_router) больше не
включает ``paid_gemini_guard`` — он остался только в helper'е
``collect_network_probes_snapshot`` (src/core/network_probes_snapshot.py),
который используется внутренними consumers (cost_analytics, swarm probes,
снапшоты в state файлах).

Этот файл закрывает gap который раньше покрывался
``test_network_probes_endpoint_wave72.py``: Wave 69-fix bug — agent обновил
None-userbot codepath, но забыл alive-userbot codepath. Unit tests на helper
изолированно прошли, bug дошёл до прода. Здесь проверяем ОБА codepath
(kraab=None и alive duck-type) для ВСЕХ 7 ключей.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.network_probes_snapshot import collect_network_probes_snapshot

_PAID_GUARD_KEYS = {
    "mode",
    "blocked_count",
    "allowed_count",
    "warned_count",
    "last_blocked_at",
    "last_blocked_host",
    "last_blocked_model",
}


# ---------------------------------------------------------------------------
# Codepath 1: userbot=None — все 7 ключей в paid_gemini_guard
# ---------------------------------------------------------------------------


def test_paid_gemini_guard_has_all_keys_when_userbot_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wave 69 regression: при ``userbot=None`` все 7 keys должны
    присутствовать в ``paid_gemini_guard`` (None-codepath)."""
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "1")
    snapshot = collect_network_probes_snapshot(None)
    assert snapshot["available"] is False
    guard = snapshot["paid_gemini_guard"]
    assert set(guard.keys()) >= _PAID_GUARD_KEYS, (
        f"paid_gemini_guard missing keys for userbot=None: {_PAID_GUARD_KEYS - set(guard.keys())}"
    )
    assert guard["mode"] == "block"
    assert isinstance(guard["blocked_count"], int)
    assert isinstance(guard["allowed_count"], int)
    assert isinstance(guard["warned_count"], int)


# ---------------------------------------------------------------------------
# Codepath 2: alive userbot — те же 7 keys + tick_count > 0
# ---------------------------------------------------------------------------


def test_paid_gemini_guard_has_all_keys_when_userbot_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wave 69-fix regression: при alive userbot все 7 keys должны
    присутствовать (alive-codepath, который раньше был забыт)."""
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "1")
    bot = SimpleNamespace(
        _dispatcher_tick_count=7,
        _last_dispatcher_tick_ts=1000.0,
        _last_telegram_event_ts=1000.0,
        _last_seen_update_id=42,
        _last_swarm_pts={},
    )
    snapshot = collect_network_probes_snapshot(bot)
    assert snapshot["available"] is True
    assert snapshot["main_dispatcher_tick_count"] == 7
    guard = snapshot["paid_gemini_guard"]
    assert set(guard.keys()) >= _PAID_GUARD_KEYS, (
        f"paid_gemini_guard missing keys for alive userbot: {_PAID_GUARD_KEYS - set(guard.keys())}"
    )
    assert guard["mode"] == "block"
    assert isinstance(guard["blocked_count"], int)
    assert isinstance(guard["allowed_count"], int)
    assert isinstance(guard["warned_count"], int)


# ---------------------------------------------------------------------------
# Mode reflects env (both codepaths)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env_value", "expected_mode"),
    [
        ("1", "block"),
        ("warn", "warn"),
        ("0", "off"),
    ],
)
def test_paid_gemini_guard_mode_reflects_env(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str,
    expected_mode: str,
) -> None:
    """``paid_gemini_guard.mode`` должен следовать env переменной для
    обоих codepath'ов (None и alive)."""
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", env_value)

    # codepath: userbot=None
    snap_none = collect_network_probes_snapshot(None)
    assert snap_none["paid_gemini_guard"]["mode"] == expected_mode

    # codepath: alive userbot
    bot = SimpleNamespace(_dispatcher_tick_count=1, _last_swarm_pts={})
    snap_alive = collect_network_probes_snapshot(bot)
    assert snap_alive["paid_gemini_guard"]["mode"] == expected_mode
