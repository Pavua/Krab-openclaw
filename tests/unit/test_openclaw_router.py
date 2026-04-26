# -*- coding: utf-8 -*-
"""
Unit tests для openclaw_router (Phase 2 Wave M, Session 25).

Endpoints:
- GET /api/openclaw/report
- GET /api/openclaw/deep-check
- GET /api/openclaw/remediation-plan
- GET /api/openclaw/cloud/tier/state
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.openclaw_router import build_openclaw_router


class _FakeOpenClaw:
    def __init__(
        self,
        *,
        report: dict | None = None,
        deep: dict | None = None,
        remediation: dict | None = None,
        tier_state: dict | None = None,
        raise_in: str | None = None,
    ) -> None:
        self._report = report or {"status": "ok", "checks": []}
        self._deep = deep or {"status": "ok", "remediation": []}
        self._remediation = remediation or {"plan": []}
        self._tier = tier_state or {"tier": "free", "switches": 0}
        self._raise = raise_in

    async def get_health_report(self) -> dict:
        if self._raise == "report":
            raise RuntimeError("boom-report")
        return self._report

    async def get_deep_health_report(self) -> dict:
        if self._raise == "deep":
            raise RuntimeError("boom-deep")
        return self._deep

    async def get_remediation_plan(self) -> dict:
        if self._raise == "remediation":
            raise RuntimeError("boom-rem")
        return self._remediation

    def get_tier_state_export(self) -> dict:
        if self._raise == "tier":
            raise RuntimeError("boom-tier")
        return self._tier


class _StubNoMethods:
    """Клиент без поддерживаемых методов — для проверки *_not_supported веток."""

    pass


def _build_ctx(*, openclaw: object | None = ...) -> RouterContext:
    deps: dict = {}
    if openclaw is ...:
        deps["openclaw_client"] = _FakeOpenClaw()
    elif openclaw is not None:
        deps["openclaw_client"] = openclaw
    return RouterContext(
        deps=deps,
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )


def _client(ctx: RouterContext) -> TestClient:
    app = FastAPI()
    app.include_router(build_openclaw_router(ctx))
    return TestClient(app)


# ---------- /api/openclaw/report --------------------------------------------


def test_openclaw_report_ok() -> None:
    body = _client(_build_ctx()).get("/api/openclaw/report").json()
    assert body["available"] is True
    assert body["report"]["status"] == "ok"


def test_openclaw_report_no_client() -> None:
    body = _client(_build_ctx(openclaw=None)).get("/api/openclaw/report").json()
    assert body["available"] is False
    assert body["error"] == "openclaw_client_not_configured"


def test_openclaw_report_not_supported() -> None:
    body = _client(_build_ctx(openclaw=_StubNoMethods())).get("/api/openclaw/report").json()
    assert body["available"] is False
    assert body["error"] == "openclaw_report_not_supported"


def test_openclaw_report_exception_graceful() -> None:
    fake = _FakeOpenClaw(raise_in="report")
    body = _client(_build_ctx(openclaw=fake)).get("/api/openclaw/report").json()
    assert body["available"] is False
    assert body["error"] == "openclaw_report_failed"
    assert "boom-report" in body["detail"]


# ---------- /api/openclaw/deep-check ----------------------------------------


def test_openclaw_deep_check_ok() -> None:
    body = _client(_build_ctx()).get("/api/openclaw/deep-check").json()
    assert body["available"] is True
    assert body["report"]["status"] == "ok"


def test_openclaw_deep_check_exception_graceful() -> None:
    fake = _FakeOpenClaw(raise_in="deep")
    body = _client(_build_ctx(openclaw=fake)).get("/api/openclaw/deep-check").json()
    assert body["available"] is False
    assert body["error"] == "openclaw_deep_check_failed"


# ---------- /api/openclaw/remediation-plan ----------------------------------


def test_openclaw_remediation_plan_ok() -> None:
    body = _client(_build_ctx()).get("/api/openclaw/remediation-plan").json()
    assert body["available"] is True
    assert "plan" in body["report"]


def test_openclaw_remediation_plan_no_client() -> None:
    body = (
        _client(_build_ctx(openclaw=None)).get("/api/openclaw/remediation-plan").json()
    )
    assert body["available"] is False
    assert body["error"] == "openclaw_client_not_configured"


# ---------- /api/openclaw/cloud/tier/state ----------------------------------


def test_openclaw_cloud_tier_state_ok() -> None:
    body = _client(_build_ctx()).get("/api/openclaw/cloud/tier/state").json()
    # build_ops_response возвращает {"status": "ok", "data": {...}, ...}
    assert body.get("status") == "ok"
    assert body["data"]["tier_state"]["tier"] == "free"


def test_openclaw_cloud_tier_state_no_client() -> None:
    body = (
        _client(_build_ctx(openclaw=None)).get("/api/openclaw/cloud/tier/state").json()
    )
    assert body.get("status") == "failed"
    assert body.get("error_code") == "openclaw_client_not_configured"


def test_openclaw_cloud_tier_state_not_supported() -> None:
    body = (
        _client(_build_ctx(openclaw=_StubNoMethods()))
        .get("/api/openclaw/cloud/tier/state")
        .json()
    )
    assert body.get("status") == "failed"
    assert body.get("error_code") == "tier_state_not_supported"


def test_openclaw_cloud_tier_state_system_error() -> None:
    fake = _FakeOpenClaw(raise_in="tier")
    body = _client(_build_ctx(openclaw=fake)).get("/api/openclaw/cloud/tier/state").json()
    assert body.get("status") == "failed"
    assert body.get("error_code") == "system_error"
