# -*- coding: utf-8 -*-
"""
Unit tests for ``src.modules.web_routers.ecosystem_admin_router`` — Wave 156.

Покрывает:
- GET /api/admin/ecosystem/dashboard — shape, services list, summary counters;
- Per-service collectors: krab_core / openclaw / lm_studio / swarm clients /
  paid_gemini_guard / resources;
- Status mapping (ok / warn / crit / unknown) под разными inputs;
- HTML render с nav tabs (включая Ecosystem active).

Тесты следуют pattern Wave 144/152/155: чистый FastAPI + TestClient без полного
WebApp. Singleton-ы patched через unittest.mock.patch.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.ecosystem_admin_router import (
    _STATUS_CRIT,
    _STATUS_OK,
    _STATUS_UNKNOWN,
    _STATUS_WARN,
    _collect_krab_core,
    _collect_paid_gemini_guard,
    _collect_resources,
    _collect_swarm_clients,
    _format_age,
    _format_uptime,
    build_ecosystem_admin_router,
)

# ── Fakes ──────────────────────────────────────────────────────────────────


class _FakeUserbot:
    """Stub userbot с настраиваемыми dispatcher tick + swarm pts полями."""

    def __init__(
        self,
        *,
        tick_ts: float | None = None,
        tick_count: int = 0,
        swarm_pts: dict[str, dict[str, Any]] | None = None,
        swarm_clients: dict[str, Any] | None = None,
    ) -> None:
        self._last_dispatcher_tick_ts = tick_ts
        self._dispatcher_tick_count = tick_count
        self._last_swarm_pts = swarm_pts or {}
        self._swarm_team_clients = swarm_clients or {}


class _FakeOC:
    """Stub openclaw_client.get_last_runtime_route."""

    def __init__(self, route: dict[str, Any] | None = None) -> None:
        self._route = route or {}

    def get_last_runtime_route(self) -> dict[str, Any]:
        return dict(self._route)


class _FakeSwarmClient:
    def __init__(self, connected: bool = True) -> None:
        self.is_connected = connected


# ── Fixture builders ───────────────────────────────────────────────────────


def _build_ctx(
    *,
    userbot: Any | None = None,
    openclaw: Any | None = None,
    voice_gateway: Any | None = None,
    krab_ear: Any | None = None,
    local_truth: dict[str, Any] | None = None,
    local_raises: Exception | None = None,
) -> RouterContext:
    deps: dict[str, Any] = {}
    if userbot is not None:
        deps["userbot"] = userbot
        deps["kraab_userbot"] = userbot
    if openclaw is not None:
        deps["openclaw_client"] = openclaw
    if voice_gateway is not None:
        deps["voice_gateway_client"] = voice_gateway
    if krab_ear is not None:
        deps["krab_ear_client"] = krab_ear

    if local_truth is not None or local_raises is not None:
        def _local_helper(_router_obj: Any) -> dict[str, Any]:
            if local_raises is not None:
                raise local_raises
            return local_truth or {}

        deps["router"] = object()
        deps["resolve_local_runtime_truth_helper"] = _local_helper

    return RouterContext(
        deps=deps,
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda *_a, **_kw: None,
    )


def _client(ctx: RouterContext | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(build_ecosystem_admin_router(ctx or _build_ctx()))
    return TestClient(app)


# ── Format helpers tests ────────────────────────────────────────────────────


def test_format_uptime_handles_units() -> None:
    """_format_uptime даёт компактные строки по дням/часам/минутам/секундам."""
    assert _format_uptime(45) == "45s"
    assert _format_uptime(60) == "1m"
    assert _format_uptime(125) == "2m"
    assert _format_uptime(3600) == "1h 0m"
    assert _format_uptime(3700) == "1h 1m"
    assert _format_uptime(86400) == "1d 0h"
    assert _format_uptime(2 * 86400 + 3600) == "2d 1h"
    # Отрицательное → 0s
    assert _format_uptime(-5) == "0s"


def test_format_age_handles_none_and_units() -> None:
    """_format_age показывает 's/m/h/d ago' либо «—» для None."""
    assert _format_age(None) == "—"
    assert _format_age(15) == "15s ago"
    assert _format_age(120) == "2m ago"
    assert _format_age(7200) == "2h ago"
    assert _format_age(2 * 86400) == "2d ago"


# ── Endpoint shape tests ────────────────────────────────────────────────────


def test_dashboard_returns_ok_shape() -> None:
    """GET /api/admin/ecosystem/dashboard → ok=true, services list, summary."""
    resp = _client().get("/api/admin/ecosystem/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["services"], list)
    assert isinstance(data["summary"], dict)
    assert "ok_count" in data["summary"]
    assert "warn_count" in data["summary"]
    assert "crit_count" in data["summary"]
    assert "unknown_count" in data["summary"]
    # Минимум один сервис всегда есть (krab_core хотя бы).
    assert len(data["services"]) >= 1


def test_dashboard_each_service_has_required_fields() -> None:
    """Каждая service card содержит id/label/status/metric/detail/link."""
    resp = _client().get("/api/admin/ecosystem/dashboard")
    services = resp.json()["services"]
    for svc in services:
        assert "id" in svc
        assert "label" in svc
        assert "status" in svc
        assert "metric" in svc
        assert "detail" in svc
        assert "link" in svc  # link может быть None
        assert svc["status"] in {_STATUS_OK, _STATUS_WARN, _STATUS_CRIT, _STATUS_UNKNOWN}


def test_dashboard_summary_counts_match_services() -> None:
    """summary.ok_count + warn + crit + unknown = total services."""
    resp = _client().get("/api/admin/ecosystem/dashboard")
    data = resp.json()
    sum_total = (
        data["summary"]["ok_count"]
        + data["summary"]["warn_count"]
        + data["summary"]["crit_count"]
        + data["summary"]["unknown_count"]
    )
    assert sum_total == len(data["services"])


def test_dashboard_includes_core_services() -> None:
    """Ecosystem dashboard включает базовый набор service IDs."""
    resp = _client().get("/api/admin/ecosystem/dashboard")
    data = resp.json()
    ids = {s["id"] for s in data["services"]}
    # Core service IDs:
    for required in ("krab_core", "openclaw", "voice_gateway", "krab_ear",
                     "lm_studio", "paid_gemini_guard", "sentry", "disk", "ram"):
        assert required in ids, f"missing service: {required}"


# ── Per-collector tests ────────────────────────────────────────────────────


def test_krab_core_no_userbot_returns_unknown() -> None:
    """Без userbot dep — krab_core status=unknown с uptime."""
    ctx = _build_ctx()
    card = _collect_krab_core(ctx)
    assert card["id"] == "krab_core"
    assert card["status"] == _STATUS_UNKNOWN
    assert "userbot" in card["detail"].lower()


def test_krab_core_fresh_tick_is_ok() -> None:
    """Свежий dispatcher_tick (< 60s ago) → status=ok."""
    ub = _FakeUserbot(tick_ts=time.time() - 5, tick_count=42)
    ctx = _build_ctx(userbot=ub)
    card = _collect_krab_core(ctx)
    assert card["status"] == _STATUS_OK
    assert "#42" in card["detail"]


def test_krab_core_stale_tick_is_warn() -> None:
    """Tick > 60s (но < 300s) → status=warn."""
    ub = _FakeUserbot(tick_ts=time.time() - 120, tick_count=10)
    ctx = _build_ctx(userbot=ub)
    card = _collect_krab_core(ctx)
    assert card["status"] == _STATUS_WARN


def test_krab_core_very_stale_tick_is_crit() -> None:
    """Tick > 300s → status=crit."""
    ub = _FakeUserbot(tick_ts=time.time() - 600, tick_count=5)
    ctx = _build_ctx(userbot=ub)
    card = _collect_krab_core(ctx)
    assert card["status"] == _STATUS_CRIT


# ── OpenClaw tests ─────────────────────────────────────────────────────────


def test_openclaw_dashboard_routes_status_ok() -> None:
    """OpenClaw с route.status='ok' → ecosystem card status=ok."""
    oc = _FakeOC(
        route={
            "status": "ok",
            "model": "google-vertex/gemini-3-pro-preview",
            "timestamp": time.time(),
        }
    )
    ctx = _build_ctx(openclaw=oc)
    resp = _client(ctx).get("/api/admin/ecosystem/dashboard")
    svc = next(s for s in resp.json()["services"] if s["id"] == "openclaw")
    assert svc["status"] == _STATUS_OK
    assert "gemini-3-pro-preview" in svc["metric"]


def test_openclaw_dashboard_error_status_is_crit() -> None:
    """OpenClaw status='error' → crit."""
    oc = _FakeOC(route={"status": "error", "model": "x", "timestamp": time.time()})
    ctx = _build_ctx(openclaw=oc)
    resp = _client(ctx).get("/api/admin/ecosystem/dashboard")
    svc = next(s for s in resp.json()["services"] if s["id"] == "openclaw")
    assert svc["status"] == _STATUS_CRIT


# ── LM Studio tests ────────────────────────────────────────────────────────


def test_lm_studio_loaded_model_is_ok() -> None:
    """LM Studio reachable + model loaded → status=ok."""
    truth = {
        "runtime_reachable": True,
        "active_model": "gemma-4-26b-a4b-it-optiq",
        "loaded_models": ["gemma-4-26b-a4b-it-optiq"],
    }
    ctx = _build_ctx(local_truth=truth)
    resp = _client(ctx).get("/api/admin/ecosystem/dashboard")
    svc = next(s for s in resp.json()["services"] if s["id"] == "lm_studio")
    assert svc["status"] == _STATUS_OK
    assert "gemma" in svc["metric"]


def test_lm_studio_no_model_loaded_is_warn() -> None:
    """LM Studio reachable но без loaded model → warn."""
    truth = {"runtime_reachable": True, "active_model": "", "loaded_models": []}
    ctx = _build_ctx(local_truth=truth)
    resp = _client(ctx).get("/api/admin/ecosystem/dashboard")
    svc = next(s for s in resp.json()["services"] if s["id"] == "lm_studio")
    assert svc["status"] == _STATUS_WARN
    assert "no model loaded" in svc["detail"].lower()


def test_lm_studio_unreachable_is_warn() -> None:
    """LM Studio unreachable → warn (graceful — может быть offload)."""
    truth = {"runtime_reachable": False, "active_model": "", "loaded_models": []}
    ctx = _build_ctx(local_truth=truth)
    resp = _client(ctx).get("/api/admin/ecosystem/dashboard")
    svc = next(s for s in resp.json()["services"] if s["id"] == "lm_studio")
    assert svc["status"] == _STATUS_WARN


def test_lm_studio_probe_raises_is_crit() -> None:
    """LM Studio probe бросает → crit."""
    ctx = _build_ctx(local_raises=RuntimeError("connection refused"))
    resp = _client(ctx).get("/api/admin/ecosystem/dashboard")
    svc = next(s for s in resp.json()["services"] if s["id"] == "lm_studio")
    assert svc["status"] == _STATUS_CRIT
    assert "probe failed" in svc["detail"].lower()


# ── Swarm clients tests ────────────────────────────────────────────────────


def test_swarm_clients_no_userbot_returns_single_unknown() -> None:
    """Без userbot — единичная card-агрегат status=unknown."""
    ctx = _build_ctx()
    cards = _collect_swarm_clients(ctx)
    assert len(cards) == 1
    assert cards[0]["status"] == _STATUS_UNKNOWN
    assert cards[0]["id"] == "swarm_clients"


def test_swarm_clients_fresh_probe_is_ok() -> None:
    """Свежий probe (< 600s) → status=ok."""
    now = time.time()
    ub = _FakeUserbot(
        swarm_pts={"traders": {"pts": 100, "ts": now - 30}},
        swarm_clients={"traders": _FakeSwarmClient(connected=True)},
    )
    ctx = _build_ctx(userbot=ub)
    cards = _collect_swarm_clients(ctx)
    assert len(cards) == 1
    assert cards[0]["id"] == "swarm_traders"
    assert cards[0]["status"] == _STATUS_OK


def test_swarm_clients_stale_probe_is_warn() -> None:
    """Probe 600-1800s старый → status=warn."""
    now = time.time()
    ub = _FakeUserbot(
        swarm_pts={"coders": {"pts": 50, "ts": now - 900}},
        swarm_clients={"coders": _FakeSwarmClient(connected=True)},
    )
    ctx = _build_ctx(userbot=ub)
    cards = _collect_swarm_clients(ctx)
    target = next(c for c in cards if c["id"] == "swarm_coders")
    assert target["status"] == _STATUS_WARN


def test_swarm_clients_very_stale_probe_is_crit() -> None:
    """Probe > 1800s → status=crit."""
    now = time.time()
    ub = _FakeUserbot(
        swarm_pts={"analysts": {"pts": 1, "ts": now - 3600}},
        swarm_clients={"analysts": _FakeSwarmClient(connected=True)},
    )
    ctx = _build_ctx(userbot=ub)
    cards = _collect_swarm_clients(ctx)
    target = next(c for c in cards if c["id"] == "swarm_analysts")
    assert target["status"] == _STATUS_CRIT


def test_swarm_clients_multiple_teams() -> None:
    """4 swarm teams → 4 cards."""
    now = time.time()
    ub = _FakeUserbot(
        swarm_pts={
            "traders": {"pts": 100, "ts": now - 30},
            "coders": {"pts": 50, "ts": now - 30},
            "analysts": {"pts": 25, "ts": now - 30},
            "creative": {"pts": 75, "ts": now - 30},
        },
        swarm_clients={
            "traders": _FakeSwarmClient(),
            "coders": _FakeSwarmClient(),
            "analysts": _FakeSwarmClient(),
            "creative": _FakeSwarmClient(),
        },
    )
    ctx = _build_ctx(userbot=ub)
    cards = _collect_swarm_clients(ctx)
    assert len(cards) == 4
    ids = {c["id"] for c in cards}
    assert ids == {"swarm_traders", "swarm_coders", "swarm_analysts", "swarm_creative"}


# ── paid_gemini_guard tests ────────────────────────────────────────────────


def test_paid_gemini_guard_block_mode_is_ok(monkeypatch) -> None:
    """KRAB_BLOCK_PAID_GEMINI_AI_STUDIO=1 + zero allowed → ok."""
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "1")
    fake_stats = {
        "blocked_count": 5,
        "allowed_count": 0,
        "warned_count": 0,
        "last_blocked_at": None,
        "last_blocked_host": None,
        "last_blocked_model": None,
    }
    with patch(
        "src.integrations.paid_gemini_guard.get_paid_gemini_guard_stats",
        return_value=fake_stats,
    ):
        card = _collect_paid_gemini_guard()
    assert card["status"] == _STATUS_OK
    assert card["metric"] == "block"
    assert "blocked=5" in card["detail"]


def test_paid_gemini_guard_off_mode_is_crit(monkeypatch) -> None:
    """KRAB_BLOCK_PAID_GEMINI_AI_STUDIO=0 → crit (paid spend possible)."""
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "0")
    card = _collect_paid_gemini_guard()
    assert card["status"] == _STATUS_CRIT
    assert card["metric"] == "off"


def test_paid_gemini_guard_warn_mode_is_warn(monkeypatch) -> None:
    """KRAB_BLOCK_PAID_GEMINI_AI_STUDIO=warn → warn."""
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "warn")
    card = _collect_paid_gemini_guard()
    assert card["status"] == _STATUS_WARN
    assert card["metric"] == "warn"


# ── Resources tests ────────────────────────────────────────────────────────


def test_resources_returns_disk_and_ram() -> None:
    """_collect_resources возвращает 2 card'а: disk + ram."""
    cards = _collect_resources()
    assert len(cards) == 2
    ids = {c["id"] for c in cards}
    assert ids == {"disk", "ram"}


def test_resources_disk_status_reflects_pct() -> None:
    """Disk card имеет valid status (на любой реальной машине)."""
    cards = _collect_resources()
    disk = next(c for c in cards if c["id"] == "disk")
    assert disk["status"] in {_STATUS_OK, _STATUS_WARN, _STATUS_CRIT, _STATUS_UNKNOWN}
    # Метрика — % использования.
    assert "%" in disk["metric"] or disk["metric"] == "—"


# ── HTML page tests ────────────────────────────────────────────────────────


def test_admin_ecosystem_page_returns_html() -> None:
    """GET /admin/ecosystem → HTML 200 с правильными элементами."""
    resp = _client().get("/admin/ecosystem")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    body = resp.text
    # Заголовок страницы.
    assert "Ecosystem" in body
    # Endpoint используется в JS.
    assert "/api/admin/ecosystem/dashboard" in body
    # Nav tabs включают все admin pages.
    assert "/admin/models" in body
    assert "/admin/routing" in body
    assert "/admin/ecosystem" in body
    assert "/admin/swarm" in body
    assert "/admin/costs" in body
    # Page active link отмечен class="active".
    assert 'href="/admin/ecosystem" class="active"' in body
    # No-store cache header.
    assert "no-store" in resp.headers.get("cache-control", "")


def test_admin_ecosystem_page_includes_summary_grid() -> None:
    """HTML содержит ключевые UI элементы (summary cards + grid)."""
    resp = _client().get("/admin/ecosystem")
    body = resp.text
    # Summary counters.
    assert "sum-ok" in body
    assert "sum-warn" in body
    assert "sum-crit" in body
    # Grid container для service cards.
    assert 'id="grid"' in body
    # Polling 30s (требование задачи).
    assert "30000" in body
