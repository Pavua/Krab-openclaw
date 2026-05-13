# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.typing_admin_router`` — Wave 207 (Session 48).

Покрытие сосредоточено на factory-pattern, parsing helpers, env-чтении
и /api/admin/typing/stats endpoint. Использует REAL prometheus-клиентские
объекты (Counter/Histogram) — patch'им только конкретные label-values
через monkeypatch of metric attributes в модуле typing_indicator.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry, Counter, Histogram

from src.modules.web_routers import typing_admin_router as tar
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.typing_admin_router import build_typing_admin_router


def _make_client() -> TestClient:
    ctx = RouterContext(
        deps={},
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
    )
    app = FastAPI()
    app.include_router(build_typing_admin_router(ctx))
    return TestClient(app)


def _make_fresh_metrics() -> dict[str, Any]:
    """Создаёт изолированные Counter/Histogram в отдельной registry,
    чтобы тесты не загрязняли глобальную."""
    reg = CollectorRegistry()
    started = Counter(
        "test_typing_started_total",
        "test started",
        ["action"],
        registry=reg,
    )
    cancelled = Counter(
        "test_typing_cancelled_total",
        "test cancelled",
        ["reason"],
        registry=reg,
    )
    duration = Histogram(
        "test_typing_duration_seconds",
        "test duration",
        buckets=(0.5, 1, 2, 5, 10, 15, 30, 60),
        registry=reg,
    )
    floodwait = Counter(
        "test_typing_floodwait_total",
        "test floodwait",
        ["chat_id_bucket"],
        registry=reg,
    )
    return {
        "started": started,
        "cancelled": cancelled,
        "duration": duration,
        "floodwait": floodwait,
    }


@pytest.fixture
def fresh_metrics(monkeypatch: pytest.MonkeyPatch):
    """Подменяет module-level metric объекты на свежие — изоляция тестов."""
    metrics = _make_fresh_metrics()
    from src.core.metrics import typing_indicator as _ti

    monkeypatch.setattr(_ti, "krab_typing_indicator_started_total", metrics["started"])
    monkeypatch.setattr(_ti, "krab_typing_indicator_cancelled_total", metrics["cancelled"])
    monkeypatch.setattr(_ti, "krab_typing_indicator_duration_seconds", metrics["duration"])
    monkeypatch.setattr(_ti, "krab_typing_indicator_floodwait_total", metrics["floodwait"])
    return metrics


# ---------------------------------------------------------------------------
# helpers — pure functions
# ---------------------------------------------------------------------------


def test_safe_collect_samples_handles_none() -> None:
    assert tar._safe_collect_samples(None) == []


def test_aggregate_counter_by_label_sums_correctly(fresh_metrics: dict) -> None:
    c = fresh_metrics["started"]
    c.labels(action="typing").inc(3)
    c.labels(action="typing").inc(2)
    c.labels(action="upload_photo").inc(7)
    out = tar._aggregate_counter_by_label(c, "action")
    assert out["typing"] == 5.0
    assert out["upload_photo"] == 7.0
    # _total суффикс — _created/sample игнорируется (только counter values).
    assert sum(out.values()) == 12.0


def test_aggregate_counter_returns_empty_for_none() -> None:
    assert tar._aggregate_counter_by_label(None, "action") == {}


def test_histogram_snapshot_basic(fresh_metrics: dict) -> None:
    h = fresh_metrics["duration"]
    h.observe(0.3)
    h.observe(2.5)
    h.observe(8.0)
    snap = tar._histogram_snapshot(h)
    assert snap["count"] == 3.0
    assert snap["sum_seconds"] == pytest.approx(0.3 + 2.5 + 8.0)
    assert snap["avg_seconds"] == pytest.approx((0.3 + 2.5 + 8.0) / 3)
    # buckets ordered by le — non-empty.
    assert len(snap["buckets"]) >= 4
    # +Inf bucket в наличии.
    assert any(b["le"] == "+Inf" for b in snap["buckets"])


def test_histogram_snapshot_empty_no_avg() -> None:
    snap = tar._histogram_snapshot(None)
    assert snap["count"] == 0.0
    assert snap["avg_seconds"] is None


def test_read_env_config_default_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KRAB_TYPING_INDICATOR_ENABLED", raising=False)
    monkeypatch.delenv("KRAB_TYPING_INDICATOR_BLOCKED_CHATS", raising=False)
    cfg = tar._read_env_config()
    assert cfg["enabled"] is True
    assert cfg["blocked_count"] == 0
    assert cfg["blocked_chats"] == []


def test_read_env_config_disabled_and_blocklist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_TYPING_INDICATOR_ENABLED", "0")
    monkeypatch.setenv("KRAB_TYPING_INDICATOR_BLOCKED_CHATS", "-100123,456, 789 ")
    cfg = tar._read_env_config()
    assert cfg["enabled"] is False
    assert cfg["blocked_count"] == 3
    assert "-100123" in cfg["blocked_chats"]
    assert "789" in cfg["blocked_chats"]


# ---------------------------------------------------------------------------
# /api/admin/typing/stats endpoint
# ---------------------------------------------------------------------------


def test_stats_endpoint_returns_ok_with_metrics(fresh_metrics: dict) -> None:
    # Эмулируем работающего бота: typing + один cancelled + один FloodWait.
    fresh_metrics["started"].labels(action="typing").inc(5)
    fresh_metrics["started"].labels(action="upload_photo").inc(2)
    fresh_metrics["cancelled"].labels(reason="success").inc(6)
    fresh_metrics["cancelled"].labels(reason="floodwait").inc(1)
    fresh_metrics["duration"].observe(1.2)
    fresh_metrics["duration"].observe(3.5)
    fresh_metrics["floodwait"].labels(chat_id_bucket="42").inc(1)
    fresh_metrics["floodwait"].labels(chat_id_bucket="07").inc(3)

    client = _make_client()
    resp = client.get("/api/admin/typing/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    # started_by_action
    assert data["started_by_action"]["typing"] == 5.0
    assert data["started_by_action"]["upload_photo"] == 2.0
    # cancelled_by_reason
    assert data["cancelled_by_reason"]["success"] == 6.0
    assert data["cancelled_by_reason"]["floodwait"] == 1.0
    # totals derivation
    assert data["totals"]["started_total"] == 7.0
    assert data["totals"]["success_total"] == 6.0
    assert data["totals"]["floodwait_events_total"] == 4.0
    # histogram
    assert data["duration_histogram"]["count"] == 2.0
    # floodwait top10 — sorted desc.
    top = data["floodwait_top10"]
    assert top[0]["chat_id_bucket"] == "07"
    assert top[0]["count"] == 3.0
    # env block.
    assert "enabled" in data["env"]


def test_stats_endpoint_handles_none_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    # Slim-env (prometheus_client отсутствует) → все metric'и None.
    from src.core.metrics import typing_indicator as _ti

    monkeypatch.setattr(_ti, "krab_typing_indicator_started_total", None)
    monkeypatch.setattr(_ti, "krab_typing_indicator_cancelled_total", None)
    monkeypatch.setattr(_ti, "krab_typing_indicator_duration_seconds", None)
    monkeypatch.setattr(_ti, "krab_typing_indicator_floodwait_total", None)

    client = _make_client()
    resp = client.get("/api/admin/typing/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["started_by_action"] == {}
    assert data["cancelled_by_reason"] == {}
    assert data["floodwait_top10"] == []
    assert data["totals"]["started_total"] == 0.0


def test_stats_endpoint_includes_env_config(
    monkeypatch: pytest.MonkeyPatch, fresh_metrics: dict
) -> None:
    monkeypatch.setenv("KRAB_TYPING_INDICATOR_ENABLED", "false")
    monkeypatch.setenv("KRAB_TYPING_INDICATOR_BLOCKED_CHATS", "-100,200,300")
    client = _make_client()
    resp = client.get("/api/admin/typing/stats")
    data = resp.json()
    assert data["env"]["enabled"] is False
    assert data["env"]["blocked_count"] == 3
    assert "-100" in data["env"]["blocked_chats"]


def test_stats_endpoint_fail_safe_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Заставляем _collect_stats_snapshot падать.
    def _broken_snapshot() -> dict:
        raise RuntimeError("forced_failure_for_test")

    monkeypatch.setattr(tar, "_collect_stats_snapshot", _broken_snapshot)
    client = _make_client()
    resp = client.get("/api/admin/typing/stats")
    # Fail-safe: 200 + ok=False (UI рендерит banner).
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "forced_failure_for_test" in data["error"]


# ---------------------------------------------------------------------------
# /admin/typing — HTML page
# ---------------------------------------------------------------------------


def test_admin_typing_html_page_renders() -> None:
    client = _make_client()
    resp = client.get("/admin/typing")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    # Sanity-проверки на наличие ключевых UI-узлов.
    assert "Krab" in body
    assert "Typing Indicator" in body
    assert "/api/admin/typing/stats" in body
    # XSS-safe: пользовательских строк в шаблоне нет — JS строит DOM
    # через createElement/textContent.
    assert "createElement" in body
    assert "textContent" in body


def test_admin_typing_html_polling_30s() -> None:
    client = _make_client()
    resp = client.get("/admin/typing")
    # 30 sec = 30000 ms.
    assert "30000" in resp.text


# ---------------------------------------------------------------------------
# factory contract
# ---------------------------------------------------------------------------


def test_build_typing_admin_router_returns_apirouter() -> None:
    ctx = RouterContext(
        deps={},
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
    )
    router = build_typing_admin_router(ctx)
    paths = {r.path for r in router.routes}
    assert "/api/admin/typing/stats" in paths
    assert "/admin/typing" in paths
