# -*- coding: utf-8 -*-
"""
Unit tests для openclaw_router (Phase 2 Wave M+N, Session 25).

Endpoints (Wave M, GET):
- GET /api/openclaw/report
- GET /api/openclaw/deep-check
- GET /api/openclaw/remediation-plan
- GET /api/openclaw/cloud/tier/state

Endpoints (Wave N, POST через ctx.assert_write_access):
- POST /api/openclaw/cloud/tier/reset
- POST /api/openclaw/channels/runtime-repair
- POST /api/openclaw/channels/signal-guard-run
"""

from __future__ import annotations

from pathlib import Path

import pytest
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

    async def reset_cloud_tier(self) -> dict:
        if self._raise == "reset":
            raise RuntimeError("boom-reset")
        return {"previous_tier": "paid", "new_tier": "free", "reset_at": "now"}


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


# ===========================================================================
# Wave N — POST endpoints через ctx.assert_write_access
# ===========================================================================


# ---------- POST /api/openclaw/cloud/tier/reset -----------------------------


def test_openclaw_cloud_tier_reset_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без WEB_API_KEY доступ открыт; reset_cloud_tier вызывается успешно."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    body = _client(_build_ctx()).post("/api/openclaw/cloud/tier/reset").json()
    assert body.get("status") == "ok"
    assert body["data"]["result"]["new_tier"] == "free"


def test_openclaw_cloud_tier_reset_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    """WEB_API_KEY установлен, header не передан → forbidden."""
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    body = _client(_build_ctx()).post("/api/openclaw/cloud/tier/reset").json()
    assert body.get("status") == "failed"
    assert body.get("error_code") == "forbidden"


def test_openclaw_cloud_tier_reset_valid_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Корректный X-Krab-Web-Key → 200/ok."""
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    resp = _client(_build_ctx()).post(
        "/api/openclaw/cloud/tier/reset",
        headers={"X-Krab-Web-Key": "secret-key"},
    )
    assert resp.status_code == 200
    assert resp.json().get("status") == "ok"


def test_openclaw_cloud_tier_reset_no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """openclaw_client отсутствует → openclaw_client_not_configured."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    body = (
        _client(_build_ctx(openclaw=None))
        .post("/api/openclaw/cloud/tier/reset")
        .json()
    )
    assert body.get("status") == "failed"
    assert body.get("error_code") == "openclaw_client_not_configured"


def test_openclaw_cloud_tier_reset_not_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Клиент без reset_cloud_tier → tier_reset_not_supported."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    body = (
        _client(_build_ctx(openclaw=_StubNoMethods()))
        .post("/api/openclaw/cloud/tier/reset")
        .json()
    )
    assert body.get("status") == "failed"
    assert body.get("error_code") == "tier_reset_not_supported"


def test_openclaw_cloud_tier_reset_system_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """reset_cloud_tier бросает → tier_reset_error."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake = _FakeOpenClaw(raise_in="reset")
    body = (
        _client(_build_ctx(openclaw=fake))
        .post("/api/openclaw/cloud/tier/reset")
        .json()
    )
    assert body.get("status") == "failed"
    assert body.get("error_code") == "tier_reset_error"


# ---------- POST /api/openclaw/channels/runtime-repair ----------------------


def test_openclaw_runtime_repair_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    """WEB_API_KEY установлен, header не передан → 403."""
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    resp = _client(_build_ctx()).post("/api/openclaw/channels/runtime-repair")
    assert resp.status_code == 403


def test_openclaw_runtime_repair_script_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Без WEB_API_KEY вызывается subprocess; для несуществующего script_path
    asyncio.create_subprocess_exec падает → handler возвращает {ok: False,
    error: "system_error"}."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    ctx = RouterContext(
        deps={},
        project_root=tmp_path,  # пусто, openclaw_runtime_repair.command нет
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )
    resp = _client(ctx).post("/api/openclaw/channels/runtime-repair")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] == "system_error"


# ---------- POST /api/openclaw/channels/signal-guard-run --------------------


def test_openclaw_signal_guard_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    """WEB_API_KEY установлен, header не передан → 403."""
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    resp = _client(_build_ctx()).post("/api/openclaw/channels/signal-guard-run")
    assert resp.status_code == 403


def test_openclaw_signal_guard_valid_auth_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Корректный header пропускает auth; subprocess может ok/fail в зависимости
    от наличия скрипта — главное что мы прошли guard и получили JSON."""
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    resp = _client(_build_ctx()).post(
        "/api/openclaw/channels/signal-guard-run",
        headers={"X-Krab-Web-Key": "secret-key"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "ok" in body
