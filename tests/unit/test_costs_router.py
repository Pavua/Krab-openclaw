# -*- coding: utf-8 -*-
"""
Unit tests для costs_router (Phase 2 Wave YY, Session 26).

RouterContext-based extraction. Создаёт RouterContext напрямую без полного
WebApp instance — proves router self-contained.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.costs_router import build_costs_router


def _ctx() -> RouterContext:
    return RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(build_costs_router(_ctx()))
    return TestClient(app)


def _make_call(
    *,
    model_id: str = "gemini-3-pro-preview",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cost_usd: float = 0.0123,
    timestamp: float | None = None,
    channel: str = "telegram",
    is_fallback: bool = False,
    tool_calls_count: int = 0,
):
    import time as _t

    return SimpleNamespace(
        model_id=model_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        timestamp=timestamp if timestamp is not None else _t.time(),
        channel=channel,
        is_fallback=is_fallback,
        tool_calls_count=tool_calls_count,
    )


def _patch_cost_analytics(*, calls=None, **kwargs):
    """Helper: patch cost_analytics module-level singleton."""
    fake = SimpleNamespace(
        _calls=calls or [],
        build_usage_report_dict=lambda: kwargs.get(
            "report",
            {
                "cost_session_usd": 1.5,
                "monthly_budget_usd": 50.0,
                "by_model": {"gemini-3-pro-preview": {"calls": 10}},
                "input_tokens": 1000,
                "output_tokens": 500,
                "total_tool_calls": 3,
                "total_fallbacks": 0,
                "total_context_tokens": 1500,
                "avg_context_tokens": 150,
                "by_channel": {"telegram": {"calls": 10}},
            },
        ),
        get_monthly_budget_usd=lambda: kwargs.get("budget", 50.0),
        get_monthly_cost_usd=lambda: kwargs.get("spent", 1.5),
        get_remaining_budget_usd=lambda: kwargs.get("remaining", 48.5),
        check_budget_ok=lambda: kwargs.get("budget_ok", True),
        monthly_calls_forecast=lambda: kwargs.get("forecast", 1234),
    )
    return patch("src.core.cost_analytics.cost_analytics", fake)


def test_costs_report_ok():
    with _patch_cost_analytics():
        resp = _client().get("/api/costs/report")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    rep = data["report"]
    assert rep["total_cost_usd"] == 1.5
    assert rep["total_calls"] == 10
    assert rep["budget_monthly_usd"] == 50.0
    assert rep["budget_remaining_usd"] == pytest.approx(48.5)
    assert rep["input_tokens"] == 1000
    assert "period_end" in rep


def test_costs_budget_ok():
    with _patch_cost_analytics(budget=100.0, spent=25.0, remaining=75.0):
        resp = _client().get("/api/costs/budget")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    b = data["budget"]
    assert b["monthly_limit_usd"] == 100.0
    assert b["spent_usd"] == 25.0
    assert b["remaining_usd"] == 75.0
    assert b["budget_ok"] is True
    assert b["used_pct"] == 25.0


def test_costs_budget_zero_limit_returns_none():
    with _patch_cost_analytics(budget=0.0, spent=0.0, remaining=None):
        resp = _client().get("/api/costs/budget")
    assert resp.status_code == 200
    b = resp.json()["budget"]
    assert b["monthly_limit_usd"] is None
    assert b["used_pct"] is None


def test_costs_history_filter_and_limit():
    calls = [
        _make_call(channel="telegram", model_id="m1"),
        _make_call(channel="discord", model_id="m2"),
        _make_call(channel="telegram", model_id="m3"),
        _make_call(channel="telegram", model_id="m4"),
    ]
    with _patch_cost_analytics(calls=calls):
        resp = _client().get("/api/costs/history?limit=2&channel=telegram")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["total_records"] == 4
    assert data["returned"] == 2
    # latest first (last 2 of telegram filter, reversed)
    assert data["history"][0]["model_id"] == "m4"
    assert data["history"][1]["model_id"] == "m3"


def test_costs_hourly_buckets_24():
    import time as _t

    now = _t.time()
    calls = [
        _make_call(timestamp=now - 60, cost_usd=0.5),  # current hour bucket
        _make_call(timestamp=now - 3700, cost_usd=0.25),  # previous hour bucket
        _make_call(timestamp=now - 100000, cost_usd=99.0),  # >24h ago, excluded
    ]
    with _patch_cost_analytics(calls=calls):
        resp = _client().get("/api/costs/hourly")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert len(data["buckets"]) == 24
    assert len(data["bucket_calls"]) == 24
    assert len(data["labels"]) == 24
    # Total within 24h window
    assert sum(data["buckets"]) == pytest.approx(0.75, rel=1e-3)


def test_costs_by_chat_top():
    calls = [
        _make_call(channel="chat_a", cost_usd=0.5),
        _make_call(channel="chat_a", cost_usd=0.5),
        _make_call(channel="chat_b", cost_usd=2.0),
        _make_call(channel="", cost_usd=0.1),  # → "unknown"
    ]
    with _patch_cost_analytics(calls=calls):
        resp = _client().get("/api/costs/by_chat?limit=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    top = data["top_chats"]
    assert len(top) == 2
    assert top[0]["chat_title"] == "chat_b"
    assert top[0]["cost_usd"] == 2.0
    assert top[1]["chat_title"] == "chat_a"
    assert top[1]["calls"] == 2


def test_costs_codex_quota_handles_missing_module():
    """codex_quota module отсутствует на main → endpoint должен вернуть ok=False
    через try/except (тот же контракт что и в inline-версии)."""
    resp = _client().get("/api/costs/codex-quota")
    assert resp.status_code == 200
    data = resp.json()
    # Либо успешно загрузил cached/live quota, либо словил ImportError
    assert "ok" in data
    if data["ok"] is False:
        assert "error" in data


def test_costs_by_tier_summary():
    import src.core.model_tier_tracker  # noqa: F401

    fake_summary = {"opus": {"cost": 1.0, "calls": 5}, "sonnet": {"cost": 0.2, "calls": 12}}
    calls = [_make_call()]
    with _patch_cost_analytics(calls=calls):
        with patch("src.core.model_tier_tracker.get_tier_summary", return_value=fake_summary):
            resp = _client().get("/api/costs/by-tier?hours=12")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["summary"] == fake_summary


def test_costs_report_handles_exception():
    """Если cost_analytics падает — endpoint вернёт ok=false с error."""
    fake = SimpleNamespace(
        build_usage_report_dict=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with patch("src.core.cost_analytics.cost_analytics", fake):
        resp = _client().get("/api/costs/report")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "boom" in data["error"]
