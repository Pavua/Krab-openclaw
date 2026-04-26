# -*- coding: utf-8 -*-
"""
Unit-тесты для monitoring_router (Phase 2 Wave E, Session 25).

Покрывают 5 stateless GET endpoints:
- /api/sla
- /api/ops/metrics
- /api/ops/timeline + alias /api/timeline
- /api/archive/growth
- /api/reactions/incoming
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers.monitoring_router import router


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ---------------- /api/sla ----------------


def test_sla_metrics_with_data(client: TestClient) -> None:
    fake_metrics = MagicMock()
    fake_metrics.get_snapshot.return_value = {
        "counters": {
            "local_success": 80,
            "cloud_success": 20,
            "local_failures": 0,
            "cloud_failures": 0,
            "force_cloud_failfast_total": 3,
        },
        "latencies": {"p50_ms": 120.0, "p95_ms": 450.0},
    }
    with patch("src.core.observability.metrics", fake_metrics):
        resp = client.get("/api/sla")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["latency_p50_ms"] == 120.0
    assert body["latency_p95_ms"] == 450.0
    assert body["success_rate_pct"] == 100.0
    assert body["fail_fast_count"] == 3


def test_sla_metrics_empty_defaults_100(client: TestClient) -> None:
    fake_metrics = MagicMock()
    fake_metrics.get_snapshot.return_value = {"counters": {}, "latencies": {}}
    with patch("src.core.observability.metrics", fake_metrics):
        resp = client.get("/api/sla")
    body = resp.json()
    assert body["ok"] is True
    assert body["success_rate_pct"] == 100.0
    assert body["fail_fast_count"] == 0


# ---------------- /api/ops/metrics ----------------


def test_ops_metrics_flat_fields(client: TestClient) -> None:
    fake_metrics = MagicMock()
    snap = {
        "counters": {"llm_success": 90, "llm_error": 10},
        "latencies": {"p50_ms": 100, "p95_ms": 300},
    }
    fake_metrics.get_snapshot.return_value = snap
    with patch("src.core.observability.metrics", fake_metrics):
        resp = client.get("/api/ops/metrics")
    body = resp.json()
    assert body["ok"] is True
    assert body["latency_p50"] == 100
    assert body["latency_p95"] == 300
    assert body["error_rate"] == 10.0
    assert body["throughput"] == 100
    assert body["metrics"] == snap


def test_ops_metrics_no_traffic_zero_error_rate(client: TestClient) -> None:
    fake_metrics = MagicMock()
    fake_metrics.get_snapshot.return_value = {"counters": {}, "latencies": {}}
    with patch("src.core.observability.metrics", fake_metrics):
        resp = client.get("/api/ops/metrics")
    body = resp.json()
    assert body["error_rate"] == 0.0
    assert body["throughput"] == 0


# ---------------- /api/ops/timeline + /api/timeline ----------------


def test_ops_timeline_passes_filters(client: TestClient) -> None:
    fake_timeline = MagicMock()
    fake_timeline.get_events.return_value = [{"ts": 1.0, "channel": "x"}]
    with patch("src.core.observability.timeline", fake_timeline):
        resp = client.get("/api/ops/timeline?limit=42&min_severity=warn&channel=swarm")
    body = resp.json()
    assert body["ok"] is True
    assert body["events"] == [{"ts": 1.0, "channel": "x"}]
    fake_timeline.get_events.assert_called_once_with(limit=42, min_severity="warn", channel="swarm")


def test_timeline_alias_works(client: TestClient) -> None:
    fake_timeline = MagicMock()
    fake_timeline.get_events.return_value = []
    with patch("src.core.observability.timeline", fake_timeline):
        resp = client.get("/api/timeline")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "events": []}


# ---------------- /api/archive/growth ----------------


def test_archive_growth_with_snapshot(client: TestClient) -> None:
    snap = SimpleNamespace(ts=12345.0, db_bytes=1000, message_count=50)
    fake_take = MagicMock(return_value=snap)
    fake_summary = MagicMock(return_value={"snapshots": 10, "growth_24h_bytes": 500})
    with (
        patch("src.core.archive_growth_monitor.take_snapshot", fake_take),
        patch("src.core.archive_growth_monitor.growth_summary", fake_summary),
    ):
        resp = client.get("/api/archive/growth")
    body = resp.json()
    assert body["ok"] is True
    assert body["current"] == {"ts": 12345.0, "db_bytes": 1000, "message_count": 50}
    assert body["snapshots"] == 10
    assert body["growth_24h_bytes"] == 500


def test_archive_growth_no_snapshot(client: TestClient) -> None:
    fake_take = MagicMock(return_value=None)
    fake_summary = MagicMock(return_value={"snapshots": 0})
    with (
        patch("src.core.archive_growth_monitor.take_snapshot", fake_take),
        patch("src.core.archive_growth_monitor.growth_summary", fake_summary),
    ):
        resp = client.get("/api/archive/growth")
    body = resp.json()
    assert body["ok"] is True
    assert body["current"] is None
    assert body["snapshots"] == 0


# ---------------- /api/reactions/incoming ----------------


def _install_fake_reaction_handler(
    *,
    for_message=None,
    recent=None,
    stats=None,
    raise_on_recent: Exception | None = None,
):
    """Helper: inject fake src.core.reaction_handler в sys.modules.

    Модуль отсутствует в production codebase (endpoint всегда падает в
    except path), поэтому для contract-теста мы создаём stub.
    """
    import sys
    import types

    mod = types.ModuleType("src.core.reaction_handler")
    mod.get_reactions_for_message = MagicMock(return_value=for_message or [])
    if raise_on_recent is not None:
        mod.get_recent_reactions = MagicMock(side_effect=raise_on_recent)
    else:
        mod.get_recent_reactions = MagicMock(return_value=recent or [])
    mod.get_stats = MagicMock(return_value=stats or {})
    return patch.dict(sys.modules, {"src.core.reaction_handler": mod}), mod


def test_reactions_incoming_for_message(client: TestClient) -> None:
    ctx, mod = _install_fake_reaction_handler(for_message=[{"user": 1, "emoji": "👍"}])
    with ctx:
        resp = client.get("/api/reactions/incoming?chat_id=100&message_id=42")
    body = resp.json()
    assert body["ok"] is True
    assert body["chat_id"] == 100
    assert body["message_id"] == 42
    assert body["count"] == 1
    assert body["reactions"] == [{"user": 1, "emoji": "👍"}]
    mod.get_reactions_for_message.assert_called_once_with(100, 42)


def test_reactions_incoming_recent_default(client: TestClient) -> None:
    ctx, mod = _install_fake_reaction_handler(recent=[{"ts": 1}], stats={"total": 7})
    with ctx:
        resp = client.get("/api/reactions/incoming")
    body = resp.json()
    assert body["ok"] is True
    assert body["recent"] == [{"ts": 1}]
    assert body["stats"] == {"total": 7}
    mod.get_recent_reactions.assert_called_once_with(limit=50)


def test_reactions_incoming_graceful_error(client: TestClient) -> None:
    ctx, _mod = _install_fake_reaction_handler(raise_on_recent=RuntimeError("boom"))
    with ctx:
        resp = client.get("/api/reactions/incoming")
    body = resp.json()
    assert body["ok"] is False
    assert "boom" in body["error"]


def test_reactions_incoming_module_missing_returns_error(client: TestClient) -> None:
    """Production state: src.core.reaction_handler отсутствует — endpoint
    возвращает {ok: False, error: ...} вместо 500."""
    resp = client.get("/api/reactions/incoming")
    body = resp.json()
    assert body["ok"] is False
    assert "error" in body
