# -*- coding: utf-8 -*-
"""
Unit tests for ``src.modules.web_routers.costs_admin_router`` — Wave 155.

Покрывает:
- GET /api/admin/costs/dashboard — shape, budget cards, breakdown 24h/7d,
                                    top sessions, extras, totals
- GET /admin/costs — HTML render с nav tab
- Graceful degradation если cost_budget_monitor / prometheus метрики
  отсутствуют или бросают

Используется чистый FastAPI + TestClient, без полного WebApp. Singleton'ы
cost_analytics / cost_budget_monitor patched через unittest.mock.patch.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.costs_admin_router import build_costs_admin_router

# ── Fakes ───────────────────────────────────────────────────────────────────


@dataclass
class _FakeCall:
    """Минимальный shim для CallRecord — атрибуты как у dataclass."""

    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    timestamp: float
    channel: str = ""
    is_fallback: bool = False
    tool_calls_count: int = 0
    context_tokens: int = 0


@dataclass
class _FakeAnalytics:
    """Stub ``cost_analytics`` singleton с _calls."""

    _calls: list[_FakeCall] = field(default_factory=list)


@dataclass
class _FakeBudgetStatus:
    """Mirror BudgetStatus.to_dict shape для evaluate_budget_status."""

    daily_used_eur: float
    daily_budget_eur: float
    daily_pct: float
    daily_status: str
    weekly_used_eur: float
    weekly_budget_eur: float
    weekly_pct: float
    weekly_status: str


class _FakeBudgetMonitor:
    def __init__(self, status: _FakeBudgetStatus | None = None) -> None:
        self._status = status or _FakeBudgetStatus(
            daily_used_eur=1.5,
            daily_budget_eur=5.0,
            daily_pct=30.0,
            daily_status="ok",
            weekly_used_eur=8.0,
            weekly_budget_eur=25.0,
            weekly_pct=32.0,
            weekly_status="ok",
        )

    def evaluate_budget_status(self) -> _FakeBudgetStatus:
        return self._status


# ── Fixture builders ────────────────────────────────────────────────────────


def _build_ctx() -> RouterContext:
    """Минимальный RouterContext для tests — costs_admin_router stateless."""
    return RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda *_a, **_kw: None,
    )


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(build_costs_admin_router(_build_ctx()))
    return TestClient(app)


def _make_calls(now: float) -> list[_FakeCall]:
    """Набор calls по 3 провайдерам с разной возрастной дистрибуцией."""
    return [
        # Свежий вызов google-vertex (внутри 24h)
        _FakeCall(
            model_id="google-vertex/gemini-3-pro-preview",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.010,
            timestamp=now - 3600,
            channel="telegram:123",
        ),
        _FakeCall(
            model_id="google-vertex/gemini-3-pro-preview",
            input_tokens=2000,
            output_tokens=300,
            cost_usd=0.020,
            timestamp=now - 7200,
            channel="telegram:123",
        ),
        # Anthropic — внутри 24h, другой channel
        _FakeCall(
            model_id="anthropic-vertex/claude-opus-4",
            input_tokens=500,
            output_tokens=1000,
            cost_usd=0.075,
            timestamp=now - 1800,
            channel="telegram:456",
        ),
        # Кризис: 5 дней назад — попадает только в 7d window
        _FakeCall(
            model_id="codex-cli/gpt-5",
            input_tokens=1500,
            output_tokens=750,
            cost_usd=0.030,
            timestamp=now - 5 * 24 * 3600,
            channel="reserve_bot",
        ),
        # Очень старый: > 7d — не попадает никуда
        _FakeCall(
            model_id="google-vertex/gemini-3-pro-preview",
            input_tokens=100000,
            output_tokens=100000,
            cost_usd=999.0,
            timestamp=now - 10 * 24 * 3600,
            channel="ancient",
        ),
    ]


# ── GET /api/admin/costs/dashboard tests ────────────────────────────────────


def test_dashboard_returns_ok_shape() -> None:
    """Базовая shape: ok=true, budget, breakdown, tokens, top_sessions, extras, totals."""
    now = time.time()
    analytics = _FakeAnalytics(_calls=_make_calls(now))
    monitor = _FakeBudgetMonitor()
    with (
        patch("src.core.cost_analytics.cost_analytics", analytics),
        patch("src.core.cost_budget.cost_budget_monitor", monitor),
    ):
        resp = _client().get("/api/admin/costs/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    for key in ("budget", "breakdown", "tokens", "top_sessions", "extras", "totals"):
        assert key in data
    assert "24h" in data["breakdown"]
    assert "7d" in data["breakdown"]


def test_dashboard_budget_reflects_monitor_status() -> None:
    """Budget cards отражают данные cost_budget_monitor."""
    now = time.time()
    analytics = _FakeAnalytics(_calls=_make_calls(now))
    status = _FakeBudgetStatus(
        daily_used_eur=4.2,
        daily_budget_eur=5.0,
        daily_pct=84.0,
        daily_status="critical",
        weekly_used_eur=10.0,
        weekly_budget_eur=25.0,
        weekly_pct=40.0,
        weekly_status="ok",
    )
    monitor = _FakeBudgetMonitor(status=status)
    with (
        patch("src.core.cost_analytics.cost_analytics", analytics),
        patch("src.core.cost_budget.cost_budget_monitor", monitor),
    ):
        resp = _client().get("/api/admin/costs/dashboard")
    data = resp.json()
    daily = data["budget"]["daily"]
    weekly = data["budget"]["weekly"]
    assert daily["used_eur"] == 4.2
    assert daily["budget_eur"] == 5.0
    assert daily["pct"] == 84.0
    assert daily["status"] == "critical"
    assert weekly["status"] == "ok"
    assert weekly["pct"] == 40.0


def test_dashboard_breakdown_aggregates_by_provider() -> None:
    """24h breakdown суммирует calls/cost/tokens по provider prefix."""
    now = time.time()
    analytics = _FakeAnalytics(_calls=_make_calls(now))
    monitor = _FakeBudgetMonitor()
    with (
        patch("src.core.cost_analytics.cost_analytics", analytics),
        patch("src.core.cost_budget.cost_budget_monitor", monitor),
    ):
        resp = _client().get("/api/admin/costs/dashboard")
    data = resp.json()
    breakdown_24h = data["breakdown"]["24h"]
    providers_24h = {r["provider"]: r for r in breakdown_24h}
    # google-vertex: 2 calls в 24h, sum cost = 0.030
    assert "google-vertex" in providers_24h
    assert providers_24h["google-vertex"]["calls"] == 2
    assert providers_24h["google-vertex"]["cost_usd"] == round(0.010 + 0.020, 6)
    assert providers_24h["google-vertex"]["input_tokens"] == 1000 + 2000
    assert providers_24h["google-vertex"]["output_tokens"] == 500 + 300
    # anthropic-vertex: 1 call
    assert providers_24h["anthropic-vertex"]["calls"] == 1
    assert providers_24h["anthropic-vertex"]["cost_usd"] == 0.075
    # codex-cli — НЕ в 24h (5 дней назад)
    assert "codex-cli" not in providers_24h

    # 7d breakdown — содержит codex-cli + всё что 24h
    breakdown_7d = data["breakdown"]["7d"]
    providers_7d = {r["provider"]: r for r in breakdown_7d}
    assert "codex-cli" in providers_7d
    assert providers_7d["codex-cli"]["calls"] == 1
    # google-vertex: те же 2 calls (10-дневный исключён)
    assert providers_7d["google-vertex"]["calls"] == 2


def test_dashboard_top_sessions_sorted_by_cost() -> None:
    """top_sessions сортирован по убыванию cost, max 5 элементов."""
    now = time.time()
    analytics = _FakeAnalytics(_calls=_make_calls(now))
    monitor = _FakeBudgetMonitor()
    with (
        patch("src.core.cost_analytics.cost_analytics", analytics),
        patch("src.core.cost_budget.cost_budget_monitor", monitor),
    ):
        resp = _client().get("/api/admin/costs/dashboard")
    data = resp.json()
    top = data["top_sessions"]
    assert len(top) <= 5
    assert len(top) >= 2
    # Самый дорогой channel в 24h — telegram:456 (anthropic-vertex $0.075)
    assert top[0]["channel"] == "telegram:456"
    assert top[0]["cost_usd"] == 0.075
    # Второй — telegram:123 (2x google-vertex)
    assert top[1]["channel"] == "telegram:123"
    assert top[1]["calls"] == 2


def test_dashboard_totals_include_24h_and_7d() -> None:
    """totals содержит cost_24h_usd / cost_7d_usd / calls_24h / calls_7d."""
    now = time.time()
    analytics = _FakeAnalytics(_calls=_make_calls(now))
    monitor = _FakeBudgetMonitor()
    with (
        patch("src.core.cost_analytics.cost_analytics", analytics),
        patch("src.core.cost_budget.cost_budget_monitor", monitor),
    ):
        resp = _client().get("/api/admin/costs/dashboard")
    data = resp.json()
    t = data["totals"]
    # 24h: 3 calls (2 vertex + 1 anthropic); 7d: +1 codex
    assert t["calls_24h"] == 3
    assert t["calls_7d"] == 4
    assert t["cost_24h_usd"] == round(0.010 + 0.020 + 0.075, 6)
    # 7d > 24h
    assert t["cost_7d_usd"] > t["cost_24h_usd"]


def test_dashboard_extras_present_even_if_prometheus_missing() -> None:
    """extras всегда содержит 6 ключей, 0.0 если prometheus недоступен."""
    now = time.time()
    analytics = _FakeAnalytics(_calls=_make_calls(now))
    monitor = _FakeBudgetMonitor()
    with (
        patch("src.core.cost_analytics.cost_analytics", analytics),
        patch("src.core.cost_budget.cost_budget_monitor", monitor),
    ):
        resp = _client().get("/api/admin/costs/dashboard")
    extras = resp.json()["extras"]
    expected_keys = {
        "search_calls",
        "search_cost_eur",
        "voice_tts_chars",
        "voice_tts_cost_eur",
        "voice_stt_seconds",
        "voice_stt_cost_eur",
    }
    assert set(extras.keys()) == expected_keys
    # Все значения numeric (>=0).
    for v in extras.values():
        assert isinstance(v, (int, float))
        assert v >= 0


def test_dashboard_graceful_when_budget_monitor_raises() -> None:
    """Если cost_budget_monitor.evaluate_budget_status бросает — defaults вернутся."""
    now = time.time()
    analytics = _FakeAnalytics(_calls=_make_calls(now))

    class _Broken:
        def evaluate_budget_status(self) -> Any:
            raise RuntimeError("budget eval failed")

    with (
        patch("src.core.cost_analytics.cost_analytics", analytics),
        patch("src.core.cost_budget.cost_budget_monitor", _Broken()),
    ):
        resp = _client().get("/api/admin/costs/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    # Defaults: статусы ok / 0.0
    assert data["budget"]["daily"]["status"] == "ok"
    assert data["budget"]["weekly"]["status"] == "ok"
    assert data["budget"]["daily"]["used_eur"] == 0.0


def test_dashboard_handles_empty_calls() -> None:
    """Пустой список calls — пустые breakdown/tokens/top_sessions, totals = 0."""
    analytics = _FakeAnalytics(_calls=[])
    monitor = _FakeBudgetMonitor()
    with (
        patch("src.core.cost_analytics.cost_analytics", analytics),
        patch("src.core.cost_budget.cost_budget_monitor", monitor),
    ):
        resp = _client().get("/api/admin/costs/dashboard")
    data = resp.json()
    assert data["ok"] is True
    assert data["breakdown"]["24h"] == []
    assert data["breakdown"]["7d"] == []
    assert data["tokens"] == []
    assert data["top_sessions"] == []
    assert data["totals"]["calls_24h"] == 0
    assert data["totals"]["calls_7d"] == 0
    assert data["totals"]["cost_24h_usd"] == 0.0
    assert data["totals"]["cost_7d_usd"] == 0.0


# ── GET /admin/costs tests ──────────────────────────────────────────────────


def test_admin_costs_page_returns_html() -> None:
    """HTML страница рендерится, содержит ключевые UI элементы и nav."""
    resp = _client().get("/admin/costs")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    body = resp.text
    assert "Costs" in body
    assert "/api/admin/costs/dashboard" in body  # endpoint используется в JS
    # Nav links на остальные admin pages.
    assert "/admin/models" in body
    assert "/admin/routing" in body
    # No-store cache header (для real-time polling).
    assert "no-store" in resp.headers.get("cache-control", "")
