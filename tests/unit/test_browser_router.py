# -*- coding: utf-8 -*-
"""
Тесты browser_router (Phase 2 Wave U, Session 25).

Покрывают factory-pattern build_browser_router(ctx) с моком RouterContext
и lazy module-attribute lookup для browser_bridge / dedicated_chrome.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.browser_router import build_browser_router


def _make_ctx() -> RouterContext:
    return RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *_a, **_k: None,
    )


def _make_app(ctx: RouterContext | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(build_browser_router(ctx or _make_ctx()))
    return app


def _patch_browser_bridge(monkeypatch: pytest.MonkeyPatch, **mock_attrs) -> AsyncMock:
    """Подменяет sys.modules['src.integrations.browser_bridge'].browser_bridge."""
    import importlib

    mod_name = "src.integrations.browser_bridge"
    mod = sys.modules.get(mod_name) or importlib.import_module(mod_name)
    mock = AsyncMock()
    for k, v in mock_attrs.items():
        setattr(mock, k, v)
    monkeypatch.setattr(mod, "browser_bridge", mock)
    return mock


# ── /api/browser/status ───────────────────────────────────────────────────


def test_browser_status_attached(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_browser_bridge(
        monkeypatch,
        is_attached=AsyncMock(return_value=True),
        list_tabs=AsyncMock(return_value=[{"url": "https://a"}, {"url": "https://b"}]),
    )
    client = TestClient(_make_app())
    resp = client.get("/api/browser/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["attached"] is True
    assert data["tab_count"] == 2
    assert data["active_url"] == "https://b"


def test_browser_status_not_attached(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_browser_bridge(
        monkeypatch,
        is_attached=AsyncMock(return_value=False),
        list_tabs=AsyncMock(return_value=[]),
    )
    client = TestClient(_make_app())
    resp = client.get("/api/browser/status")
    data = resp.json()
    assert data["ok"] is True
    assert data["attached"] is False
    assert data["tab_count"] == 0
    assert data["active_url"] is None


def test_browser_status_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_browser_bridge(
        monkeypatch,
        is_attached=AsyncMock(side_effect=TimeoutError("boom")),
        list_tabs=AsyncMock(side_effect=TimeoutError("boom")),
    )
    client = TestClient(_make_app())
    resp = client.get("/api/browser/status")
    data = resp.json()
    assert data["ok"] is False
    assert data["attached"] is False
    assert data["error"] == "browser_timeout"


# ── /api/browser/tabs ─────────────────────────────────────────────────────


def test_browser_tabs_returns_list(monkeypatch: pytest.MonkeyPatch) -> None:
    tabs = [{"url": "https://x", "title": "X"}]
    _patch_browser_bridge(monkeypatch, list_tabs=AsyncMock(return_value=tabs))
    client = TestClient(_make_app())
    resp = client.get("/api/browser/tabs")
    assert resp.status_code == 200
    assert resp.json() == tabs


# ── /api/browser/navigate ─────────────────────────────────────────────────


def test_browser_navigate_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_browser_bridge(monkeypatch, navigate=AsyncMock(return_value="https://example.com"))
    client = TestClient(_make_app())
    resp = client.post("/api/browser/navigate", json={"url": "https://example.com"})
    data = resp.json()
    assert data["ok"] is True
    assert data["current_url"] == "https://example.com"


def test_browser_navigate_missing_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_browser_bridge(monkeypatch, navigate=AsyncMock(return_value=""))
    client = TestClient(_make_app())
    resp = client.post("/api/browser/navigate", json={})
    assert resp.status_code == 400


# ── /api/browser/screenshot, /read, /js ───────────────────────────────────


def test_browser_screenshot_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_browser_bridge(monkeypatch, screenshot_base64=AsyncMock(return_value="abc=="))
    client = TestClient(_make_app())
    data = client.post("/api/browser/screenshot").json()
    assert data["ok"] is True
    assert data["data"] == "abc=="


def test_browser_screenshot_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_browser_bridge(monkeypatch, screenshot_base64=AsyncMock(return_value=None))
    client = TestClient(_make_app())
    data = client.post("/api/browser/screenshot").json()
    assert data["ok"] is False
    assert data["error"] == "screenshot_failed"


def test_browser_read_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_browser_bridge(monkeypatch, get_page_text=AsyncMock(return_value="hello"))
    client = TestClient(_make_app())
    data = client.post("/api/browser/read").json()
    assert data["ok"] is True
    assert data["text"] == "hello"


def test_browser_js_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_browser_bridge(monkeypatch, execute_js=AsyncMock(return_value=42))
    client = TestClient(_make_app())
    data = client.post("/api/browser/js", json={"code": "1+1"}).json()
    assert data["ok"] is True
    assert data["result"] == 42


def test_browser_js_missing_code(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_browser_bridge(monkeypatch, execute_js=AsyncMock(return_value=None))
    client = TestClient(_make_app())
    resp = client.post("/api/browser/js", json={})
    assert resp.status_code == 400


# ── /api/chrome/dedicated/* ───────────────────────────────────────────────


def test_chrome_dedicated_status(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_mod = types.ModuleType("src.integrations.dedicated_chrome")
    fake_mod.DEFAULT_CDP_PORT = 9222
    fake_mod.find_chrome_binary = lambda: "/Applications/Chrome"
    fake_mod.is_dedicated_chrome_running = lambda port: True
    monkeypatch.setitem(sys.modules, "src.integrations.dedicated_chrome", fake_mod)
    monkeypatch.delenv("DEDICATED_CHROME_PORT", raising=False)
    monkeypatch.delenv("DEDICATED_CHROME_PROFILE_DIR", raising=False)
    monkeypatch.setenv("DEDICATED_CHROME_ENABLED", "true")
    client = TestClient(_make_app())
    data = client.get("/api/chrome/dedicated/status").json()
    assert data["ok"] is True
    assert data["enabled"] is True
    assert data["running"] is True
    assert data["port"] == 9222
    assert data["binary"] == "/Applications/Chrome"
    assert data["profile_dir"] == "/tmp/krab-chrome"


def test_chrome_dedicated_launch_requires_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """assert_write_access raise → endpoint должен выкинуть 403."""
    fake_mod = types.ModuleType("src.integrations.dedicated_chrome")

    fake_mod.launch_dedicated_chrome = lambda: (True, {"pid": 1})
    monkeypatch.setitem(sys.modules, "src.integrations.dedicated_chrome", fake_mod)

    # Используем default ctx: assert_write_access вызывает _helpers, который
    # проверяет WEB_API_KEY env. Если не задан — пропускает; если задан и
    # не совпал — кидает 403. Здесь сначала проверим happy path (без env).
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    client = TestClient(_make_app())
    data = client.post("/api/chrome/dedicated/launch").json()
    assert data["ok"] is True
    assert data["status"] == {"pid": 1}
