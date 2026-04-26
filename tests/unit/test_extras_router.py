# -*- coding: utf-8 -*-
"""
Unit tests для extras_router (Phase 2 Wave F, Session 25).

Первый тест RouterContext-based extraction. Создаёт RouterContext напрямую
без полного WebApp instance — proves router self-contained.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.extras_router import build_extras_router


def _build_ctx(default_port: int = 8080) -> RouterContext:
    return RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
        default_port=default_port,
    )


def _client(ctx: RouterContext) -> TestClient:
    app = FastAPI()
    app.include_router(build_extras_router(ctx))
    return TestClient(app)


def test_links_default_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default base URL — http://127.0.0.1:<port>."""
    monkeypatch.delenv("WEB_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("WEB_HOST", raising=False)
    resp = _client(_build_ctx(default_port=8080)).get("/api/links")
    assert resp.status_code == 200
    data = resp.json()
    assert data["dashboard"] == "http://127.0.0.1:8080"
    assert data["health_api"] == "http://127.0.0.1:8080/api/health"


def test_links_custom_base_url_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """WEB_PUBLIC_BASE_URL override переопределяет dashboard URL."""
    monkeypatch.setenv("WEB_PUBLIC_BASE_URL", "https://krab.example.com")
    resp = _client(_build_ctx()).get("/api/links")
    data = resp.json()
    assert data["dashboard"] == "https://krab.example.com"
    assert data["stats_api"] == "https://krab.example.com/api/stats"


def test_links_custom_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """default_port из RouterContext попадает в URL."""
    monkeypatch.delenv("WEB_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("WEB_HOST", raising=False)
    resp = _client(_build_ctx(default_port=9090)).get("/api/links")
    assert resp.json()["dashboard"] == "http://127.0.0.1:9090"


def test_links_includes_external_services(monkeypatch: pytest.MonkeyPatch) -> None:
    """Voice gateway / openclaw URL читаются из env."""
    monkeypatch.setenv("VOICE_GATEWAY_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("OPENCLAW_BASE_URL", "http://127.0.0.1:11111")
    resp = _client(_build_ctx()).get("/api/links")
    data = resp.json()
    assert data["voice_gateway"] == "http://127.0.0.1:9999"
    assert data["openclaw"] == "http://127.0.0.1:11111"


def test_uptime_basic_shape() -> None:
    """/api/uptime возвращает ok=True + uptime_sec >= 0 + boot_ts."""
    ctx = _build_ctx()
    resp = _client(ctx).get("/api/uptime")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["uptime_sec"] >= 0
    assert isinstance(data["boot_ts"], (int, float))
    # holder lazy-инициализирован
    assert ctx.boot_ts_holder
    assert ctx.boot_ts_holder[0] == data["boot_ts"]


def test_uptime_boot_ts_stable_across_calls() -> None:
    """Повторный вызов не меняет boot_ts (holder shared)."""
    ctx = _build_ctx()
    client = _client(ctx)
    first = client.get("/api/uptime").json()["boot_ts"]
    second = client.get("/api/uptime").json()["boot_ts"]
    assert first == second


def test_uptime_seeded_boot_ts() -> None:
    """Если holder уже pre-seeded — uptime использует его."""
    ctx = _build_ctx()
    ctx.boot_ts_holder.append(1000.0)  # как будто WebApp уже знает boot
    data = _client(ctx).get("/api/uptime").json()
    assert data["boot_ts"] == 1000.0
    assert data["uptime_sec"] >= 0
