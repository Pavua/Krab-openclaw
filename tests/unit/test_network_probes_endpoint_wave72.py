# -*- coding: utf-8 -*-
"""Wave 72: FastAPI TestClient regression на ``GET /api/network/probes``.

Контекст: Wave 69 экспонировал counters paid_gemini_guard через
``collect_network_probes_snapshot``; Wave 69-fix поймал bug — agent
обновил None-userbot codepath, но забыл alive-userbot codepath. Unit
tests на helper изолированно прошли, bug дошёл до прода и был пойман
только manual ``curl``.

Этот файл закрывает gap — тесты дёргают actual endpoint через FastAPI
TestClient и проверяют ALL ключи в ``paid_gemini_guard`` для ОБОИХ
codepath'ов (kraab=None и alive duck-type).

Паттерн TestClient заимствован у ``test_system_router.py``: factory
``build_system_router(ctx)`` с моками всех ``ctx.deps``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.system_router import build_system_router

_PAID_GUARD_KEYS = {
    "mode",
    "blocked_count",
    "allowed_count",
    "warned_count",
    "last_blocked_at",
    "last_blocked_host",
    "last_blocked_model",
}


def _make_client(*, kraab: Any) -> TestClient:
    """Сборка TestClient с минимально необходимым ctx.deps для endpoint'а."""

    async def _runtime_lite(*, force_refresh: bool = False) -> dict[str, Any]:
        return {}

    async def _build_stats_payload(_router: Any) -> dict:
        return {}

    async def _resolve_local(_router: Any) -> dict:
        return {}

    deps: dict[str, Any] = {
        "router": None,
        "health_service": None,
        "kraab_userbot": kraab,
        "black_box": None,
        "watchdog": None,
        "openclaw_client": None,
        "voice_gateway_client": None,
        "krab_ear_client": None,
        "runtime_operator_profile_helper": lambda: {},
        "build_stats_router_payload_helper": _build_stats_payload,
        "resolve_local_runtime_truth_helper": _resolve_local,
    }
    ctx = RouterContext(
        deps=deps,
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
        runtime_lite_provider=_runtime_lite,
    )

    app = FastAPI()
    app.include_router(build_system_router(ctx))
    return TestClient(app)


# ---------------------------------------------------------------------------
# Endpoint shape + status
# ---------------------------------------------------------------------------


def test_network_probes_endpoint_returns_200_and_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """200 + JSON envelope {ok, probes:{...}} независимо от userbot state."""
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "1")
    resp = _make_client(kraab=None).get("/api/network/probes")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    probes = body["probes"]
    assert isinstance(probes, dict)
    # Wave 65-K contract: ключи snapshot обязательны.
    for key in (
        "available",
        "main_dispatcher_tick_count",
        "main_dispatcher_tick_ago_sec",
        "main_last_event_ago_sec",
        "swarm_probes",
        "paid_gemini_guard",
    ):
        assert key in probes, f"missing key {key} in probes"


# ---------------------------------------------------------------------------
# Codepath 1: kraab dep returns None — paid_gemini_guard все 7 ключей
# ---------------------------------------------------------------------------


def test_paid_gemini_guard_has_all_keys_when_kraab_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wave 69 regression: при ``kraab_userbot=None`` все 7 keys должны
    присутствовать в ``paid_gemini_guard`` (None-codepath)."""
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "1")
    resp = _make_client(kraab=None).get("/api/network/probes")
    assert resp.status_code == 200
    probes = resp.json()["probes"]
    assert probes["available"] is False
    guard = probes["paid_gemini_guard"]
    assert set(guard.keys()) >= _PAID_GUARD_KEYS, (
        f"paid_gemini_guard missing keys for kraab=None: "
        f"{_PAID_GUARD_KEYS - set(guard.keys())}"
    )
    assert guard["mode"] == "block"
    assert isinstance(guard["blocked_count"], int)
    assert isinstance(guard["allowed_count"], int)
    assert isinstance(guard["warned_count"], int)


# ---------------------------------------------------------------------------
# Codepath 2: alive userbot — те же 7 keys + tick_count > 0
# ---------------------------------------------------------------------------


def test_paid_gemini_guard_has_all_keys_when_kraab_alive(
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
    resp = _make_client(kraab=bot).get("/api/network/probes")
    assert resp.status_code == 200
    probes = resp.json()["probes"]
    assert probes["available"] is True
    assert probes["main_dispatcher_tick_count"] == 7
    guard = probes["paid_gemini_guard"]
    assert set(guard.keys()) >= _PAID_GUARD_KEYS, (
        f"paid_gemini_guard missing keys for alive kraab: "
        f"{_PAID_GUARD_KEYS - set(guard.keys())}"
    )
    assert guard["mode"] == "block"
    assert isinstance(guard["blocked_count"], int)
    assert isinstance(guard["allowed_count"], int)
    assert isinstance(guard["warned_count"], int)


# ---------------------------------------------------------------------------
# Mode reflects env
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

    # codepath: kraab=None
    resp_none = _make_client(kraab=None).get("/api/network/probes")
    assert resp_none.json()["probes"]["paid_gemini_guard"]["mode"] == expected_mode

    # codepath: alive userbot
    bot = SimpleNamespace(_dispatcher_tick_count=1, _last_swarm_pts={})
    resp_alive = _make_client(kraab=bot).get("/api/network/probes")
    assert resp_alive.json()["probes"]["paid_gemini_guard"]["mode"] == expected_mode
