# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.health_dashboard_router`` — Wave 186 (Session 48).

Покрытие:
- /api/admin/health/dashboard — aggregation, cache TTL, fail-soft endpoints
- /admin/health HTML — рендер страницы + Cache-Control headers
- Traffic light derivation: green / yellow / red пути
- Per-card extraction: system / ai / voice / memory / cron / sentry / db
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers import health_dashboard_router as hdr
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.health_dashboard_router import build_health_dashboard_router

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _make_client() -> TestClient:
    ctx = RouterContext(
        deps={},
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *_a, **_kw: None,
    )
    app = FastAPI()
    app.include_router(build_health_dashboard_router(ctx))
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_cache() -> Any:
    """Перед каждым тестом сбрасываем in-memory cache."""
    hdr._cache_clear()
    yield
    hdr._cache_clear()


def _stub_gather(monkeypatch: pytest.MonkeyPatch, payload: dict[str, dict[str, Any]]) -> None:
    """Подменяет _gather_all чтобы вернуть фиксированный payload."""

    async def fake_gather(
        base_url: str = hdr._DEFAULT_PANEL_BASE,
        timeout: float = hdr.DEFAULT_TIMEOUT_SEC,
    ) -> dict[str, dict[str, Any]]:
        return payload

    monkeypatch.setattr(hdr, "_gather_all", fake_gather)


# ---------------------------------------------------------------------------
# Traffic light derivation
# ---------------------------------------------------------------------------


def test_traffic_light_green_when_all_ok() -> None:
    raw = {
        "health": {"status": "ok", "risk_level": "low"},
        "network": {"split_brain": False, "pyrogram_disconnects_24h": 0},
        "cron": {"overdue_count": 0},
        "sentry": {"weekly_quota_used": 10, "weekly_quota_limit": 5000},
        "ecosystem": {},
    }
    tl = hdr._derive_traffic_light(raw)
    assert tl["color"] == "green"
    assert tl["reasons"] == []


def test_traffic_light_yellow_on_overdue_cron() -> None:
    raw = {
        "health": {"status": "ok"},
        "network": {"split_brain": False},
        "cron": {"overdue_count": 3},
        "sentry": {},
    }
    tl = hdr._derive_traffic_light(raw)
    assert tl["color"] == "yellow"
    assert any("cron.overdue" in r for r in tl["reasons"])


def test_traffic_light_yellow_on_sentry_quota_80pct() -> None:
    raw = {
        "health": {"status": "ok"},
        "network": {},
        "cron": {},
        "sentry": {"weekly_quota_used": 4500, "weekly_quota_limit": 5000},
    }
    tl = hdr._derive_traffic_light(raw)
    assert tl["color"] == "yellow"


def test_traffic_light_red_on_split_brain() -> None:
    raw = {
        "health": {"status": "ok"},
        "network": {"split_brain": True},
        "cron": {},
        "sentry": {},
    }
    tl = hdr._derive_traffic_light(raw)
    assert tl["color"] == "red"
    assert any("split_brain" in r for r in tl["reasons"])


def test_traffic_light_red_on_health_critical() -> None:
    raw = {
        "health": {"status": "critical"},
        "network": {},
        "cron": {},
        "sentry": {},
    }
    tl = hdr._derive_traffic_light(raw)
    assert tl["color"] == "red"


def test_traffic_light_red_takes_precedence_over_yellow() -> None:
    """Если есть и red и yellow триггеры — color остаётся red."""
    raw = {
        "health": {"status": "critical"},  # red
        "network": {},
        "cron": {"overdue_count": 5},  # yellow
        "sentry": {"weekly_quota_used": 5000, "weekly_quota_limit": 5000},  # yellow
    }
    tl = hdr._derive_traffic_light(raw)
    assert tl["color"] == "red"


# ---------------------------------------------------------------------------
# Per-card extraction
# ---------------------------------------------------------------------------


def test_system_card_extracts_fields() -> None:
    raw = {
        "health": {"uptime_sec": 7200, "status": "ok", "risk_level": "low"},
        "network": {
            "dispatcher_tick_age_sec": 5,
            "split_brain": False,
            "pyrogram_disconnects_24h": 1,
        },
    }
    card = hdr._system_card(raw)
    assert card["krab_uptime_sec"] == 7200
    assert card["krab_status"] == "ok"
    assert card["dispatcher_tick_age_sec"] == 5
    assert card["split_brain"] is False
    assert card["pyrogram_disconnects_24h"] == 1
    assert card["available"] is True


def test_system_card_offline_when_error() -> None:
    raw = {"health": {"error": "timeout"}, "network": {}}
    card = hdr._system_card(raw)
    assert card["available"] is False


def test_memory_card_extracts_archive() -> None:
    raw = {
        "memory": {
            "archive": {"size_mb": 51.3, "messages": 43000, "chunks": 9100},
            "last_retrieval_ts": "2026-05-13T08:00:00Z",
        }
    }
    card = hdr._memory_card(raw)
    assert card["archive_size_mb"] == 51.3
    assert card["messages"] == 43000
    assert card["chunks"] == 9100


def test_sentry_card_quota_pct() -> None:
    raw = {
        "sentry": {
            "weekly_quota_used": 1000,
            "weekly_quota_limit": 5000,
            "recent_issues": [{"id": "1"}, {"id": "2"}],
            "resolved_count_24h": 3,
        }
    }
    card = hdr._sentry_card(raw)
    assert card["quota_used"] == 1000
    assert card["quota_limit"] == 5000
    assert card["quota_pct"] == 20
    assert card["unresolved_count"] == 2
    assert card["resolved_24h"] == 3


def test_db_card_sums_sizes_and_warnings() -> None:
    raw = {
        "db": {
            "databases": [
                {"name": "a", "size_mb": 10.0, "integrity": "ok"},
                {"name": "b", "size_mb": 20.5, "integrity": "ok"},
                {"name": "c", "size_mb": 5.0, "integrity": "MALFORMED"},
            ]
        }
    }
    card = hdr._db_card(raw)
    assert card["total_dbs"] == 3
    assert card["total_size_mb"] == 35.5
    assert card["integrity_warnings"] == 1


def test_cron_card_counts() -> None:
    raw = {
        "cron": {
            "total": 23,
            "overdue_count": 1,
            "failed_recent_count": 0,
            "agents": [],
        }
    }
    card = hdr._cron_card(raw)
    assert card["total_agents"] == 23
    assert card["overdue_count"] == 1


def test_voice_card_extracts_status() -> None:
    raw = {
        "voice": {
            "gateway": {"alive": True, "port": 8090},
            "ear": {"installed": True, "probing": False},
            "tts_state": "ready",
        }
    }
    card = hdr._voice_card(raw)
    assert card["gateway_alive"] is True
    assert card["gateway_port"] == 8090
    assert card["ear_installed"] is True


# ---------------------------------------------------------------------------
# /api/admin/health/dashboard endpoint
# ---------------------------------------------------------------------------


def test_dashboard_endpoint_returns_aggregated(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: endpoint возвращает agregated payload."""
    _stub_gather(
        monkeypatch,
        {
            "health": {"status": "ok", "uptime_sec": 100, "risk_level": "low"},
            "ecosystem": {"services": {}},
            "network": {"split_brain": False},
            "voice": {"gateway": {"alive": True}, "ear": {"installed": True}},
            "memory": {"archive": {"size_mb": 1.0, "messages": 10, "chunks": 5}},
            "cron": {"total": 5, "overdue_count": 0},
            "sentry": {
                "weekly_quota_used": 100,
                "weekly_quota_limit": 5000,
                "recent_issues": [],
                "resolved_count_24h": 0,
            },
            "db": {"databases": []},
        },
    )
    client = _make_client()
    resp = client.get("/api/admin/health/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["traffic_light"]["color"] == "green"
    assert "system" in body["cards"]
    assert "ai" in body["cards"]
    assert "voice" in body["cards"]
    assert "memory" in body["cards"]
    assert "cron" in body["cards"]
    assert "sentry" in body["cards"]
    assert "db" in body["cards"]


def test_dashboard_cache_serves_repeat_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Второй вызов в пределах TTL не зовёт _gather_all повторно."""
    calls = {"n": 0}

    async def fake_gather(*_a: Any, **_kw: Any) -> dict[str, dict[str, Any]]:
        calls["n"] += 1
        return {"health": {"status": "ok"}}

    monkeypatch.setattr(hdr, "_gather_all", fake_gather)

    client = _make_client()
    r1 = client.get("/api/admin/health/dashboard")
    r2 = client.get("/api/admin/health/dashboard")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert calls["n"] == 1  # cached на втором


def test_dashboard_handles_offline_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """Все endpoint'ы возвращают ошибки → dashboard всё равно отвечает 200."""
    _stub_gather(
        monkeypatch,
        {
            "health": {"ok": False, "error": "connection refused"},
            "ecosystem": {"ok": False, "error": "timeout"},
            "network": {"ok": False, "error": "timeout"},
            "voice": {"ok": False, "error": "timeout"},
            "memory": {"ok": False, "error": "timeout"},
            "cron": {"ok": False, "error": "timeout"},
            "sentry": {"ok": False, "error": "timeout"},
            "db": {"ok": False, "error": "timeout"},
        },
    )
    client = _make_client()
    resp = client.get("/api/admin/health/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    # При полном offline health+ecosystem → red.
    assert body["traffic_light"]["color"] == "red"
    # Карточки помечают available=False.
    assert body["cards"]["system"]["available"] is False
    assert body["cards"]["voice"]["available"] is False


# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------


def test_admin_health_page_renders_html() -> None:
    client = _make_client()
    resp = client.get("/admin/health")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Health Dashboard" in resp.text
    # Все 7 cards должны присутствовать в template.
    for card_id in (
        "card-system",
        "card-ai",
        "card-voice",
        "card-memory",
        "card-cron",
        "card-sentry",
        "card-db",
    ):
        assert card_id in resp.text
    # Cache-Control: no-store
    assert "no-store" in resp.headers.get("cache-control", "")


def test_admin_health_page_traffic_light_placeholder() -> None:
    """Страница содержит placeholder для traffic-light + polling JS."""
    client = _make_client()
    resp = client.get("/admin/health")
    assert "traffic-light" in resp.text
    assert "setInterval(refresh, 10000)" in resp.text


# ---------------------------------------------------------------------------
# Fail-soft _fetch_one
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_one_handles_http_error() -> None:
    """_fetch_one на HTTPError возвращает skeleton fail-soft."""
    import httpx as _httpx

    class _FakeClient:
        async def get(self, url: str) -> Any:
            raise _httpx.ConnectError("refused")

    result = await hdr._fetch_one(_FakeClient(), "http://127.0.0.1:8080", "/api/health")  # type: ignore[arg-type]
    assert result["ok"] is False
    assert "ConnectError" in result["error"]


@pytest.mark.asyncio
async def test_fetch_one_handles_4xx() -> None:
    """HTTP 4xx/5xx → ok=False с error message."""

    class _FakeResp:
        status_code = 503
        text = "service unavailable"

        def json(self) -> Any:
            return {}

    class _FakeClient:
        async def get(self, url: str) -> Any:
            return _FakeResp()

    result = await hdr._fetch_one(_FakeClient(), "http://127.0.0.1:8080", "/api/health")  # type: ignore[arg-type]
    assert result["ok"] is False
    assert "HTTP 503" in result["error"]
