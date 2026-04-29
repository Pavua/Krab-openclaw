# -*- coding: utf-8 -*-
"""
Unit tests для pages_router (Phase 2 Wave XX, Session 25 — final extraction).

Покрывает HTML page routes: landing, V4 dashboards, legacy stubs, prototypes,
/v4/* → primary 301 redirects, static CSS+JS assets, nano_theme.css.

Все тесты проверяют status code + content-type без зависимости от реального
наличия html-файлов — используются fallback HTMLResponse stubs / реальные
файлы из ``src/web/v4/``, если присутствуют (CI). Redirect routes проверяются
через ``follow_redirects=False``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.pages_router import build_pages_router


def _build_ctx() -> RouterContext:
    return RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(build_pages_router(_build_ctx()))
    return TestClient(app)


def test_landing_page_returns_html() -> None:
    """GET / — landing page (FileResponse или Gemini stub HTML)."""
    resp = _client().get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    # cache-control header задан _no_store_headers
    cc = resp.headers.get("cache-control", "")
    assert "no-store" in cc


def test_legacy_stub_pages_return_html() -> None:
    """GET /legacy/ops|settings|commands — stubs всегда возвращают HTML."""
    client = _client()
    for path in ("/legacy/ops", "/legacy/settings", "/legacy/commands"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers["content-type"].startswith("text/html"), path
        assert "Legacy" in resp.text, path


def test_v4_redirects_to_primary() -> None:
    """GET /v4/{costs|inbox|swarm|...} → 301 → /{costs|inbox|swarm|...}."""
    client = _client()
    pairs = [
        ("/v4/costs", "/costs"),
        ("/v4/inbox", "/inbox"),
        ("/v4/swarm", "/swarm"),
        ("/v4/translator", "/translator"),
        ("/v4/ops", "/ops"),
        ("/v4/settings", "/settings"),
        ("/v4/commands", "/commands"),
    ]
    for src, dst in pairs:
        resp = client.get(src, follow_redirects=False)
        assert resp.status_code == 301, src
        assert resp.headers["location"] == dst, src


def test_primary_dashboard_routes_serve_html() -> None:
    """GET /costs|/inbox|/swarm|/translator|/ops|/settings|/commands — HTML."""
    client = _client()
    for path in (
        "/costs",
        "/inbox",
        "/swarm",
        "/translator",
        "/ops",
        "/settings",
        "/commands",
    ):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers["content-type"].startswith("text/html"), path


def test_prototype_unknown_returns_404() -> None:
    """GET /prototypes/<bogus> → 404 + HTML message."""
    resp = _client().get("/prototypes/__definitely_not_a_real_proto__")
    assert resp.status_code == 404
    assert "not found" in resp.text.lower()


def test_stats_dashboard_returns_html() -> None:
    """GET /stats — Gemini stats dashboard."""
    resp = _client().get("/stats")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
