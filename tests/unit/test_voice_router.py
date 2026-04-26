# -*- coding: utf-8 -*-
"""
Unit tests для voice_router (Phase 2 Wave L, Session 25).

Endpoints:
- GET  /api/voice/profile
- GET  /api/voice/runtime
- POST /api/voice/runtime/update
- POST /api/voice/toggle
- GET  /api/krab_ear/status
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.voice_router import build_voice_router


class _FakeKraab:
    def __init__(self) -> None:
        self.voice_mode = False
        self._profile = {
            "enabled": True,
            "speed": 1.0,
            "voice": "edge-ru",
            "delivery": "text+voice",
        }

    def get_voice_runtime_profile(self) -> dict:
        return dict(self._profile)

    def update_voice_runtime_profile(
        self,
        *,
        enabled=None,
        speed=None,
        voice=None,
        delivery=None,
        persist=True,
    ) -> dict:
        if enabled is not None:
            self._profile["enabled"] = bool(enabled)
        if speed is not None:
            self._profile["speed"] = speed
        if voice is not None:
            self._profile["voice"] = voice
        if delivery is not None:
            self._profile["delivery"] = delivery
        return dict(self._profile)


class _FakeKrabEar:
    def __init__(self, *, raise_on_health: bool = False) -> None:
        self._raise = raise_on_health

    async def health_report(self) -> dict:
        if self._raise:
            raise RuntimeError("ear-down")
        return {
            "ok": True,
            "status": "ready",
            "latency_ms": 12,
            "source": "krab-ear",
            "detail": "all good",
        }


def _build_ctx(
    *,
    kraab: _FakeKraab | None = None,
    krab_ear: _FakeKrabEar | None | object = ...,  # ... = use default fake
) -> RouterContext:
    deps: dict = {}
    deps["kraab_userbot"] = kraab if kraab is not None else _FakeKraab()
    if krab_ear is ...:
        deps["krab_ear_client"] = _FakeKrabEar()
    elif krab_ear is not None:
        deps["krab_ear_client"] = krab_ear
    return RouterContext(
        deps=deps,
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )


def _client(ctx: RouterContext) -> TestClient:
    app = FastAPI()
    app.include_router(build_voice_router(ctx))
    return TestClient(app)


# ---------- GET /api/voice/profile ------------------------------------------


def test_voice_profile_ok() -> None:
    resp = _client(_build_ctx()).get("/api/voice/profile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["profile"]["voice"] == "edge-ru"


# ---------- GET /api/voice/runtime ------------------------------------------


def test_voice_runtime_ok() -> None:
    resp = _client(_build_ctx()).get("/api/voice/runtime")
    body = resp.json()
    assert body["ok"] is True
    assert body["voice"]["enabled"] is True


def test_voice_runtime_unavailable_when_no_kraab() -> None:
    ctx = RouterContext(
        deps={},  # no kraab_userbot
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )
    body = _client(ctx).get("/api/voice/runtime").json()
    assert body["ok"] is False
    assert body["error"] == "voice_runtime_not_available"


# ---------- POST /api/voice/runtime/update ----------------------------------


def test_voice_runtime_update_no_auth_required_when_unset() -> None:
    """Без WEB_API_KEY — open access."""
    with patch.dict(os.environ, {"WEB_API_KEY": ""}, clear=False):
        resp = _client(_build_ctx()).post(
            "/api/voice/runtime/update", json={"speed": 1.5}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["voice"]["speed"] == 1.5


def test_voice_runtime_update_403_with_wrong_key() -> None:
    with patch.dict(os.environ, {"WEB_API_KEY": "secret"}, clear=False):
        resp = _client(_build_ctx()).post(
            "/api/voice/runtime/update",
            json={"speed": 2.0},
            headers={"X-Krab-Web-Key": "wrong"},
        )
    assert resp.status_code == 403


# ---------- POST /api/voice/toggle ------------------------------------------


def test_voice_toggle_explicit_enabled() -> None:
    kraab = _FakeKraab()
    with patch.dict(os.environ, {"WEB_API_KEY": ""}, clear=False):
        resp = _client(_build_ctx(kraab=kraab)).post(
            "/api/voice/toggle", json={"enabled": True}
        )
    body = resp.json()
    assert body["ok"] is True
    assert body["voice_enabled"] is True
    assert kraab.voice_mode is True


def test_voice_toggle_flip_default() -> None:
    """Без 'enabled' — переключаем относительно current."""
    kraab = _FakeKraab()
    kraab.voice_mode = False
    with patch.dict(os.environ, {"WEB_API_KEY": ""}, clear=False):
        resp = _client(_build_ctx(kraab=kraab)).post("/api/voice/toggle", json={})
    assert resp.json()["voice_enabled"] is True
    assert kraab.voice_mode is True


def test_voice_toggle_403_with_wrong_key() -> None:
    with patch.dict(os.environ, {"WEB_API_KEY": "secret"}, clear=False):
        resp = _client(_build_ctx()).post(
            "/api/voice/toggle",
            json={"enabled": True},
            headers={"X-Krab-Web-Key": "nope"},
        )
    assert resp.status_code == 403


# ---------- GET /api/krab_ear/status ----------------------------------------


def test_krab_ear_status_ok() -> None:
    body = _client(_build_ctx()).get("/api/krab_ear/status").json()
    assert body["ok"] is True
    assert body["status"] == "ready"
    assert body["latency_ms"] == 12


def test_krab_ear_status_unavailable_when_no_client() -> None:
    ctx = _build_ctx(krab_ear=None)
    body = _client(ctx).get("/api/krab_ear/status").json()
    assert body["ok"] is False
    assert body["status"] == "unavailable"


def test_krab_ear_status_error_graceful() -> None:
    ctx = _build_ctx(krab_ear=_FakeKrabEar(raise_on_health=True))
    body = _client(ctx).get("/api/krab_ear/status").json()
    assert body["ok"] is False
    assert body["status"] == "error"
    assert "ear-down" in body["error"]
