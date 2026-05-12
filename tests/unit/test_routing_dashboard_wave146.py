# -*- coding: utf-8 -*-
"""Wave 146: Smart Routing 5-stage visualisation на Owner Panel /admin/routing.

Покрывает:
  - GET /api/routing/stats — shape: ok / stages / total_decisions / top_decisions_path;
  - empty counters → нули, share_pct = 0.0;
  - mock counter values корректно агрегируются по stage+outcome;
  - top_decisions_path находит ячейку с максимальным count;
  - GET /admin/routing — HTML render с nav-link на /admin/models.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import prometheus_metrics as pm
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.system_router import (
    _SMART_ROUTING_STAGES_ORDER,
    _build_routing_stats_payload,
    build_system_router,
)

# ── helpers ─────────────────────────────────────────────────────────────────


def _build_ctx() -> RouterContext:
    """Минимальный RouterContext для router-only тестов."""
    return RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda *_a, **_kw: None,
    )


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(build_system_router(_build_ctx()))
    return TestClient(app)


def _reset_counter() -> None:
    """Wave 146: сбросить значения counter до 0 для всех 5 stages × 2 outcomes."""
    counter = pm.krab_smart_routing_decisions_total
    if counter is None:
        return
    for stage in _SMART_ROUTING_STAGES_ORDER:
        for outcome in ("allow", "deny"):
            cell = counter.labels(stage=stage, outcome=outcome)
            cell._value.set(0)  # type: ignore[attr-defined]


def _bump(stage: str, outcome: str, n: int) -> None:
    """Wave 146: инкрементировать counter в N раз для теста."""
    counter = pm.krab_smart_routing_decisions_total
    assert counter is not None, "prometheus_client must be installed for Wave 146 tests"
    for _ in range(n):
        counter.labels(stage=stage, outcome=outcome).inc()


# ── payload helper tests ────────────────────────────────────────────────────


def test_empty_counter_returns_zero_stages() -> None:
    """Пустые counters → все stages с total=0, top_decisions_path пустой."""
    _reset_counter()
    payload = _build_routing_stats_payload()
    assert payload["ok"] is True
    assert payload["total_decisions"] == 0
    # 5 stages в фиксированном порядке.
    assert len(payload["stages"]) == 5
    assert [s["stage"] for s in payload["stages"]] == list(_SMART_ROUTING_STAGES_ORDER)
    for s in payload["stages"]:
        assert s["allow_count"] == 0
        assert s["deny_count"] == 0
        assert s["total"] == 0
        assert s["allow_rate_pct"] == 0.0
    top = payload["top_decisions_path"]
    assert top["winning_stage"] is None
    assert top["winning_count"] == 0
    assert top["share_pct"] == 0.0


def test_aggregates_counter_values_per_stage_outcome() -> None:
    """Инкременты counter → правильные allow/deny counts + allow_rate."""
    _reset_counter()
    _bump("hard_gate", "allow", 7)
    _bump("hard_gate", "deny", 3)
    _bump("regex", "allow", 20)
    _bump("regex", "deny", 5)
    _bump("llm_classifier", "deny", 4)

    payload = _build_routing_stats_payload()
    assert payload["total_decisions"] == 7 + 3 + 20 + 5 + 4

    by_stage: dict[str, dict[str, Any]] = {s["stage"]: s for s in payload["stages"]}
    assert by_stage["hard_gate"]["allow_count"] == 7
    assert by_stage["hard_gate"]["deny_count"] == 3
    assert by_stage["hard_gate"]["total"] == 10
    assert by_stage["hard_gate"]["allow_rate_pct"] == 70.0

    assert by_stage["regex"]["allow_count"] == 20
    assert by_stage["regex"]["deny_count"] == 5
    assert by_stage["regex"]["allow_rate_pct"] == 80.0

    assert by_stage["llm_classifier"]["allow_count"] == 0
    assert by_stage["llm_classifier"]["deny_count"] == 4
    # Все deny → allow_rate_pct = 0.0
    assert by_stage["llm_classifier"]["allow_rate_pct"] == 0.0

    # chat_policy / feedback не трогали — должны быть нули.
    assert by_stage["chat_policy"]["total"] == 0
    assert by_stage["feedback"]["total"] == 0


def test_top_decisions_path_picks_max_cell() -> None:
    """top_decisions_path = ячейка (stage, outcome) с максимальным count."""
    _reset_counter()
    _bump("hard_gate", "allow", 5)
    _bump("regex", "allow", 42)  # winner
    _bump("regex", "deny", 11)
    _bump("feedback", "deny", 8)

    payload = _build_routing_stats_payload()
    top = payload["top_decisions_path"]
    assert top["winning_stage"] == "regex"
    assert top["winning_outcome"] == "allow"
    assert top["winning_count"] == 42
    total = 5 + 42 + 11 + 8
    expected_share = round(42 / total * 100.0, 2)
    assert top["share_pct"] == expected_share


# ── endpoint shape tests ────────────────────────────────────────────────────


def test_routing_stats_endpoint_returns_200_and_shape() -> None:
    """GET /api/routing/stats → 200 с базовой shape."""
    _reset_counter()
    resp = _client().get("/api/routing/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "stages" in data
    assert "total_decisions" in data
    assert "top_decisions_path" in data
    assert isinstance(data["stages"], list)
    assert len(data["stages"]) == 5
    # Stages в правильном порядке.
    assert [s["stage"] for s in data["stages"]] == list(_SMART_ROUTING_STAGES_ORDER)


def test_routing_stats_endpoint_reflects_live_counter() -> None:
    """Endpoint выдаёт текущие значения counter, не cached."""
    _reset_counter()
    _bump("chat_policy", "deny", 9)
    resp = _client().get("/api/routing/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_decisions"] == 9
    chat_policy = next(s for s in data["stages"] if s["stage"] == "chat_policy")
    assert chat_policy["deny_count"] == 9
    assert chat_policy["allow_count"] == 0
    assert data["top_decisions_path"]["winning_stage"] == "chat_policy"
    assert data["top_decisions_path"]["winning_outcome"] == "deny"


def test_admin_routing_page_renders_html_with_navigation() -> None:
    """GET /admin/routing → HTML с ссылкой на /admin/models."""
    resp = _client().get("/admin/routing")
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("text/html")
    body = resp.text
    # nav-link назад на models page
    assert 'href="/admin/models"' in body
    # Текущая страница помечена active.
    assert 'href="/admin/routing"' in body
    # Polling /api/routing/stats — основной источник данных.
    assert "/api/routing/stats" in body
    # 5 stages упомянуты в footer-note для пользователя.
    for stage in _SMART_ROUTING_STAGES_ORDER:
        assert stage in body
    # Cache-Control: no-store для свежих данных при F5.
    assert resp.headers.get("cache-control") == "no-store"
