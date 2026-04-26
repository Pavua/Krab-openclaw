# -*- coding: utf-8 -*-
"""
Unit-тесты для monitoring_router (Phase 2 Wave E + Wave T, Session 25).

Wave E (5 stateless GET endpoints, singletons / sub-modules):
- /api/sla
- /api/ops/metrics
- /api/ops/timeline + alias /api/timeline
- /api/archive/growth
- /api/reactions/incoming

Wave T (7 ops endpoints через ctx.deps["router"]):
- /api/ops/usage
- /api/ops/cost-report
- /api/ops/runway
- /api/ops/executive-summary
- /api/ops/report
- /api/ops/alerts
- /api/ops/history
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.monitoring_router import build_monitoring_router


def _make_ctx(model_router: object | None = None) -> RouterContext:
    deps: dict = {}
    if model_router is not None:
        deps["router"] = model_router
    return RouterContext(
        deps=deps,
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
    )


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(build_monitoring_router(_make_ctx()))
    return TestClient(app)


def _client_with_router(model_router: object) -> TestClient:
    app = FastAPI()
    app.include_router(build_monitoring_router(_make_ctx(model_router)))
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
    """Helper: inject fake src.core.reaction_handler в sys.modules."""
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


# ===================================================================
# Wave T: ops endpoints (ctx.deps["router"])
# ===================================================================


# ---------------- /api/ops/usage ----------------


def test_ops_usage_supported() -> None:
    fake_router = SimpleNamespace(get_usage_summary=MagicMock(return_value={"calls": 42}))
    resp = _client_with_router(fake_router).get("/api/ops/usage")
    body = resp.json()
    assert body == {"ok": True, "usage": {"calls": 42}}
    fake_router.get_usage_summary.assert_called_once_with()


def test_ops_usage_unsupported() -> None:
    fake_router = SimpleNamespace()  # без метода
    resp = _client_with_router(fake_router).get("/api/ops/usage")
    assert resp.json() == {"ok": False, "error": "usage_summary_not_supported"}


# ---------------- /api/ops/cost-report ----------------


def test_ops_cost_report_passes_forecast() -> None:
    fake_router = SimpleNamespace(get_cost_report=MagicMock(return_value={"local": 1.0}))
    resp = _client_with_router(fake_router).get("/api/ops/cost-report?monthly_calls_forecast=8000")
    body = resp.json()
    assert body == {"ok": True, "report": {"local": 1.0}}
    fake_router.get_cost_report.assert_called_once_with(monthly_calls_forecast=8000)


def test_ops_cost_report_unsupported() -> None:
    resp = _client_with_router(SimpleNamespace()).get("/api/ops/cost-report")
    assert resp.json() == {"ok": False, "error": "cost_report_not_supported"}


# ---------------- /api/ops/runway ----------------


def test_ops_runway_passes_all_params() -> None:
    fake_router = SimpleNamespace(
        get_credit_runway_report=MagicMock(return_value={"days_left": 60})
    )
    resp = _client_with_router(fake_router).get(
        "/api/ops/runway?credits_usd=500&horizon_days=120&reserve_ratio=0.2"
        "&monthly_calls_forecast=10000"
    )
    body = resp.json()
    assert body == {"ok": True, "runway": {"days_left": 60}}
    fake_router.get_credit_runway_report.assert_called_once_with(
        credits_usd=500.0,
        horizon_days=120,
        reserve_ratio=0.2,
        monthly_calls_forecast=10000,
    )


def test_ops_runway_unsupported() -> None:
    resp = _client_with_router(SimpleNamespace()).get("/api/ops/runway")
    assert resp.json() == {"ok": False, "error": "ops_runway_not_supported"}


# ---------------- /api/ops/executive-summary ----------------


def test_ops_executive_summary_supported() -> None:
    fake_router = SimpleNamespace(get_ops_executive_summary=MagicMock(return_value={"kpi": "ok"}))
    resp = _client_with_router(fake_router).get("/api/ops/executive-summary")
    body = resp.json()
    assert body == {"ok": True, "summary": {"kpi": "ok"}}
    fake_router.get_ops_executive_summary.assert_called_once_with(monthly_calls_forecast=5000)


def test_ops_executive_summary_unsupported() -> None:
    resp = _client_with_router(SimpleNamespace()).get("/api/ops/executive-summary")
    assert resp.json() == {"ok": False, "error": "ops_executive_summary_not_supported"}


# ---------------- /api/ops/report ----------------


def test_ops_report_passes_params() -> None:
    fake_router = SimpleNamespace(get_ops_report=MagicMock(return_value={"x": 1}))
    resp = _client_with_router(fake_router).get(
        "/api/ops/report?history_limit=50&monthly_calls_forecast=7000"
    )
    body = resp.json()
    assert body == {"ok": True, "report": {"x": 1}}
    fake_router.get_ops_report.assert_called_once_with(
        history_limit=50, monthly_calls_forecast=7000
    )


def test_ops_report_unsupported() -> None:
    resp = _client_with_router(SimpleNamespace()).get("/api/ops/report")
    assert resp.json() == {"ok": False, "error": "ops_report_not_supported"}


# ---------------- /api/ops/alerts ----------------


def test_ops_alerts_supported() -> None:
    fake_router = SimpleNamespace(get_ops_alerts=MagicMock(return_value=[{"code": "X"}]))
    resp = _client_with_router(fake_router).get("/api/ops/alerts")
    assert resp.json() == {"ok": True, "alerts": [{"code": "X"}]}


def test_ops_alerts_unsupported() -> None:
    resp = _client_with_router(SimpleNamespace()).get("/api/ops/alerts")
    assert resp.json() == {"ok": False, "error": "ops_alerts_not_supported"}


# ---------------- /api/ops/history ----------------


def test_ops_history_passes_limit() -> None:
    fake_router = SimpleNamespace(get_ops_history=MagicMock(return_value=[{"ts": 1}]))
    resp = _client_with_router(fake_router).get("/api/ops/history?limit=75")
    body = resp.json()
    assert body == {"ok": True, "history": [{"ts": 1}]}
    fake_router.get_ops_history.assert_called_once_with(limit=75)


def test_ops_history_unsupported() -> None:
    resp = _client_with_router(SimpleNamespace()).get("/api/ops/history")
    assert resp.json() == {"ok": False, "error": "ops_history_not_supported"}


# ===================================================================
# Wave AA: write-protected POST/DELETE endpoints
# ===================================================================


# ---------------- POST /api/ops/maintenance/prune ----------------


def test_ops_prune_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake_router = SimpleNamespace(prune_ops_history=MagicMock(return_value={"deleted": 5}))
    resp = _client_with_router(fake_router).post(
        "/api/ops/maintenance/prune", json={"max_age_days": 7, "keep_last": 50}
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "result": {"deleted": 5}}
    fake_router.prune_ops_history.assert_called_once_with(max_age_days=7, keep_last=50)


def test_ops_prune_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    resp = _client_with_router(SimpleNamespace()).post("/api/ops/maintenance/prune", json={})
    assert resp.json() == {"ok": False, "error": "ops_prune_not_supported"}


def test_ops_prune_invalid_auth_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret")
    fake_router = SimpleNamespace(prune_ops_history=MagicMock(return_value={}))
    resp = _client_with_router(fake_router).post("/api/ops/maintenance/prune", json={})
    assert resp.status_code == 403


# ---------------- POST /api/ops/ack/{code} ----------------


def test_ops_ack_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake_router = SimpleNamespace(acknowledge_ops_alert=MagicMock(return_value={"acked": True}))
    resp = _client_with_router(fake_router).post(
        "/api/ops/ack/ALERT_X", json={"actor": "ops_admin", "note": "rolling"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "result": {"acked": True}}
    fake_router.acknowledge_ops_alert.assert_called_once_with(
        code="ALERT_X", actor="ops_admin", note="rolling"
    )


def test_ops_ack_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    resp = _client_with_router(SimpleNamespace()).post("/api/ops/ack/ALERT_X", json={})
    assert resp.json() == {"ok": False, "error": "ops_ack_not_supported"}


def test_ops_ack_invalid_auth_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret")
    fake_router = SimpleNamespace(acknowledge_ops_alert=MagicMock(return_value={}))
    resp = _client_with_router(fake_router).post("/api/ops/ack/ALERT_X", json={})
    assert resp.status_code == 403


# ---------------- DELETE /api/ops/ack/{code} ----------------


def test_ops_unack_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake_router = SimpleNamespace(clear_ops_alert_ack=MagicMock(return_value={"cleared": True}))
    resp = _client_with_router(fake_router).delete("/api/ops/ack/ALERT_X")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "result": {"cleared": True}}
    fake_router.clear_ops_alert_ack.assert_called_once_with(code="ALERT_X")


def test_ops_unack_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    resp = _client_with_router(SimpleNamespace()).delete("/api/ops/ack/ALERT_X")
    assert resp.json() == {"ok": False, "error": "ops_unack_not_supported"}


def test_ops_unack_invalid_auth_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret")
    fake_router = SimpleNamespace(clear_ops_alert_ack=MagicMock(return_value={}))
    resp = _client_with_router(fake_router).delete("/api/ops/ack/ALERT_X")
    assert resp.status_code == 403


# ===========================================================================
# Phase 2 Part 2B (Session 27) — /api/ops/{diagnostics, runtime_snapshot,
# models, report/export, bundle, bundle/export, openclaw-procs}
# ===========================================================================


def _make_ctx_full(
    *,
    model_router: object | None = None,
    extra_deps: dict | None = None,
) -> RouterContext:
    deps: dict = {}
    if model_router is not None:
        deps["router"] = model_router
    if extra_deps:
        deps.update(extra_deps)
    return RouterContext(
        deps=deps,
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
    )


def _client_full(ctx: RouterContext) -> TestClient:
    app = FastAPI()
    app.include_router(build_monitoring_router(ctx))
    return TestClient(app)


def test_ops_diagnostics_router_missing(client: TestClient) -> None:
    """diagnostics возвращает router_not_found без router в deps."""
    resp = client.get("/api/ops/diagnostics")
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "error": "router_not_found"}


def test_ops_diagnostics_with_health_service() -> None:
    """diagnostics использует health_service из deps + resolve helper."""

    class _FakeHealth:
        async def collect(self) -> dict:
            return {"resources": {"cpu": 1}, "budget": {"used": 0}}

    fake_router = SimpleNamespace(active_tier="default", local_engine="lmstudio")

    async def _resolve(_r):
        return {"runtime_reachable": True, "active_model": "m", "loaded_models": []}

    ctx = _make_ctx_full(
        model_router=fake_router,
        extra_deps={
            "health_service": _FakeHealth(),
            "resolve_local_runtime_truth_helper": _resolve,
            "watchdog": SimpleNamespace(last_recovery_attempt={"ts": 1}),
        },
    )
    resp = _client_full(ctx).get("/api/ops/diagnostics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "ok"
    assert data["resources"] == {"cpu": 1}
    assert data["local_ai"]["available"] is True


def test_ops_runtime_snapshot_router_missing(client: TestClient) -> None:
    """runtime_snapshot без router → router_not_found."""
    resp = client.get("/api/ops/runtime_snapshot")
    assert resp.json() == {"ok": False, "error": "router_not_found"}


def test_ops_runtime_snapshot_minimal() -> None:
    """runtime_snapshot собирает agg-snapshot когда router available."""
    fake_router = SimpleNamespace(
        active_tier="default",
        _stats={"local_failures": 1, "cloud_failures": 0},
        _preflight_cache={},
        openclaw_client=SimpleNamespace(get_tier_state_export=lambda: {"tier": "default"}),
    )

    async def _resolve(_r):
        return {"runtime_reachable": True, "active_model": "x", "loaded_models": ["x"]}

    ctx = _make_ctx_full(
        model_router=fake_router,
        extra_deps={"resolve_local_runtime_truth_helper": _resolve},
    )
    resp = _client_full(ctx).get("/api/ops/runtime_snapshot")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["router_state"]["active_local_model"] == "x"
    assert data["tier_state"] == {"tier": "default"}


def test_ops_models_unknown_action() -> None:
    """ops/models возвращает invalid_action для незнакомого action."""
    fake_router = SimpleNamespace()
    resp = _client_full(_make_ctx_full(model_router=fake_router)).post(
        "/api/ops/models", json={"action": "weird"}
    )
    assert resp.status_code == 200
    assert resp.json()["error"] == "invalid_action"


def test_ops_models_load_requires_model_name() -> None:
    fake_router = SimpleNamespace()
    resp = _client_full(_make_ctx_full(model_router=fake_router)).post(
        "/api/ops/models", json={"action": "load"}
    )
    assert resp.json() == {"ok": False, "error": "model_name_required"}


def test_ops_models_unload_all_calls_router() -> None:
    """unload_all action invokes router.unload_models_manual."""

    class _FakeRouter:
        def __init__(self) -> None:
            self.called = False

        async def unload_models_manual(self) -> None:
            self.called = True

    fr = _FakeRouter()
    resp = _client_full(_make_ctx_full(model_router=fr)).post(
        "/api/ops/models", json={"action": "unload_all"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "action": "unload_all"}
    assert fr.called is True


def test_ops_report_export_unsupported() -> None:
    """report/export возвращает ops_report_not_supported когда method отсутствует."""
    fake_router = SimpleNamespace()  # no get_ops_report
    resp = _client_full(_make_ctx_full(model_router=fake_router)).get("/api/ops/report/export")
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "error": "ops_report_not_supported"}


def test_ops_bundle_unsupported() -> None:
    fake_router = SimpleNamespace()
    resp = _client_full(_make_ctx_full(model_router=fake_router)).get("/api/ops/bundle")
    assert resp.json() == {"ok": False, "error": "ops_report_not_supported"}


def test_ops_bundle_with_health() -> None:
    """ops/bundle собирает health snapshot из deps."""

    class _Router:
        def get_ops_report(self, **kwargs):
            return {"summary": {}}

        async def check_local_health(self) -> bool:
            return True

    class _OC:
        async def health_check(self) -> bool:
            return True

    ctx = _make_ctx_full(
        model_router=_Router(),
        extra_deps={"openclaw_client": _OC(), "voice_gateway_client": None},
    )
    resp = _client_full(ctx).get("/api/ops/bundle")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["bundle"]["health"]["openclaw"] is True
    assert data["bundle"]["health"]["local_lm"] is True
    assert data["bundle"]["health"]["voice_gateway"] is False


def test_ops_openclaw_procs(monkeypatch: pytest.MonkeyPatch) -> None:
    """openclaw-procs возвращает агрегат через core.openclaw_cli_budget."""
    from src.core import openclaw_cli_budget as ob

    monkeypatch.setattr(
        ob,
        "list_openclaw_procs",
        lambda: [
            {"is_gateway": True, "pid": 1},
            {"is_gateway": False, "pid": 2},
            {"is_gateway": False, "pid": 3},
        ],
    )
    monkeypatch.setattr(ob, "budget_available", lambda: 2)
    monkeypatch.setattr(ob, "OPENCLAW_CLI_BUDGET", 3)

    resp = _client_full(_make_ctx_full()).get("/api/ops/openclaw-procs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["total"] == 3
    assert data["gateway_count"] == 1
    assert data["transient_count"] == 2
    assert data["leak_suspected"] is False
