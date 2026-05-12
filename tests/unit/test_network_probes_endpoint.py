# -*- coding: utf-8 -*-
"""Tests for /api/network/probes endpoint (Wave 163).

Endpoint восстановлен после Session 47 refactor — отдаёт split-brain state +
pyrogram метрики для внешнего monitoring tooling.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.health_router import build_health_router

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_app(userbot=None) -> FastAPI:
    """Сборка минимального FastAPI app с health router и optional userbot."""
    deps: dict = {}
    if userbot is not None:
        deps["kraab_userbot"] = userbot
    ctx = RouterContext(
        deps=deps,
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
    )
    router = build_health_router(ctx)
    app = FastAPI()
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_network_probes_endpoint_returns_200_with_required_keys():
    """Базовый smoke: endpoint регистрируется и возвращает обязательные ключи."""
    now = time.time()
    userbot = SimpleNamespace(
        _last_telegram_event_ts=now - 5.0,
        _last_dispatcher_tick_ts=now - 3.0,
        _dispatcher_tick_count=42,
        _last_get_state_probe=None,
        _split_brain_suspected=False,
    )
    app = _make_app(userbot=userbot)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/api/network/probes")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Контракт ключей — внешние скрипты Session 47 ожидают exactly эти поля.
    assert data["ok"] is True
    assert "main_app" in data
    assert "split_brain" in data["main_app"]
    assert "last_event_age_sec" in data["main_app"]
    assert "dispatcher_tick" in data
    assert "starved" in data["dispatcher_tick"]
    assert "pyrogram" in data
    assert "disconnects_total" in data["pyrogram"]
    assert "session_label" in data["pyrogram"]

    # Типы — bool/int/str как ожидают monitoring сценарии.
    assert isinstance(data["main_app"]["split_brain"], bool)
    assert isinstance(data["main_app"]["last_event_age_sec"], int)
    assert isinstance(data["dispatcher_tick"]["starved"], bool)
    assert isinstance(data["pyrogram"]["disconnects_total"], int)
    assert isinstance(data["pyrogram"]["session_label"], str)


def test_network_probes_split_brain_from_probe_attr():
    """Если userbot._last_get_state_probe.split_brain_suspected=True — отражается."""
    now = time.time()
    probe = SimpleNamespace(split_brain_suspected=True)
    userbot = SimpleNamespace(
        _last_telegram_event_ts=now - 100.0,
        _last_dispatcher_tick_ts=now - 50.0,
        _dispatcher_tick_count=10,
        _last_get_state_probe=probe,
    )
    app = _make_app(userbot=userbot)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/api/network/probes")
    assert resp.status_code == 200
    data = resp.json()
    assert data["main_app"]["split_brain"] is True
    assert data["main_app"]["last_event_age_sec"] >= 99


def test_network_probes_dispatcher_starved_when_stale(monkeypatch):
    """Wave 63-C: stale dispatcher tick должен пометить starved=True."""
    # Используем низкий threshold чтобы tick 2 сек был "starved".
    monkeypatch.setenv("KRAB_DISPATCHER_TICK_STALENESS_SEC", "1")
    # Reload модуля чтобы новый threshold подхватился.
    import importlib

    from src.userbot import network_watchdog as _nw

    importlib.reload(_nw)

    now = time.time()
    userbot = SimpleNamespace(
        _last_telegram_event_ts=now,
        _last_dispatcher_tick_ts=now - 10.0,  # 10 сек назад > threshold 1с
        _dispatcher_tick_count=1,
        _last_get_state_probe=None,
        _split_brain_suspected=False,
    )
    app = _make_app(userbot=userbot)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/api/network/probes")
    assert resp.status_code == 200
    data = resp.json()
    assert data["dispatcher_tick"]["starved"] is True

    # Restore default threshold (избегаем cross-test contamination).
    monkeypatch.delenv("KRAB_DISPATCHER_TICK_STALENESS_SEC", raising=False)
    importlib.reload(_nw)


def test_network_probes_no_userbot_fails_open():
    """Без userbot в deps endpoint всё равно отвечает 200 с safe defaults."""
    app = _make_app(userbot=None)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/api/network/probes")
    assert resp.status_code == 200
    data = resp.json()
    # main_app поля присутствуют, last_event_age_sec=-1 (нет данных).
    assert data["main_app"]["split_brain"] is False
    assert data["main_app"]["last_event_age_sec"] == -1
    assert data["dispatcher_tick"]["starved"] is False
    # pyrogram секция всегда из глобального registry — присутствует.
    assert isinstance(data["pyrogram"]["session_label"], str)


def test_network_probes_pyrogram_session_label_from_registry():
    """pyrogram.session_label берётся из глобального registry prometheus_metrics."""
    from src.core import prometheus_metrics as _pm

    original_label = _pm._PYROGRAM_SESSION_LABEL[0]
    _pm._PYROGRAM_SESSION_LABEL[0] = "test_session_w163"
    try:
        app = _make_app(userbot=SimpleNamespace())
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/network/probes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pyrogram"]["session_label"] == "test_session_w163"
    finally:
        _pm._PYROGRAM_SESSION_LABEL[0] = original_label


def test_network_probes_disconnects_total_aggregates_all_sessions():
    """pyrogram.disconnects_total — сумма всех session labels из counter."""
    from src.core import prometheus_metrics as _pm

    snapshot = dict(_pm._PYROGRAM_DISCONNECTS_COUNTER)
    _pm._PYROGRAM_DISCONNECTS_COUNTER.clear()
    _pm._PYROGRAM_DISCONNECTS_COUNTER.update({"sess_a": 3, "sess_b": 7})
    try:
        app = _make_app(userbot=SimpleNamespace())
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/network/probes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pyrogram"]["disconnects_total"] == 10
    finally:
        _pm._PYROGRAM_DISCONNECTS_COUNTER.clear()
        _pm._PYROGRAM_DISCONNECTS_COUNTER.update(snapshot)


@pytest.mark.parametrize(
    "field",
    [
        "main_app",
        "dispatcher_tick",
        "pyrogram",
        "ok",
        "timestamp",
    ],
)
def test_network_probes_top_level_contract(field):
    """Top-level ключи endpoint должны быть стабильны для внешнего tooling."""
    app = _make_app(userbot=SimpleNamespace())
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/network/probes")
    assert resp.status_code == 200
    assert field in resp.json()
