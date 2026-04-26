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
    body = _client(_build_ctx(openclaw=None)).get("/api/openclaw/remediation-plan").json()
    assert body["available"] is False
    assert body["error"] == "openclaw_client_not_configured"


# ---------- /api/openclaw/cloud/tier/state ----------------------------------


def test_openclaw_cloud_tier_state_ok() -> None:
    body = _client(_build_ctx()).get("/api/openclaw/cloud/tier/state").json()
    # build_ops_response возвращает {"status": "ok", "data": {...}, ...}
    assert body.get("status") == "ok"
    assert body["data"]["tier_state"]["tier"] == "free"


def test_openclaw_cloud_tier_state_no_client() -> None:
    body = _client(_build_ctx(openclaw=None)).get("/api/openclaw/cloud/tier/state").json()
    assert body.get("status") == "failed"
    assert body.get("error_code") == "openclaw_client_not_configured"


def test_openclaw_cloud_tier_state_not_supported() -> None:
    body = (
        _client(_build_ctx(openclaw=_StubNoMethods())).get("/api/openclaw/cloud/tier/state").json()
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
    body = _client(_build_ctx(openclaw=None)).post("/api/openclaw/cloud/tier/reset").json()
    assert body.get("status") == "failed"
    assert body.get("error_code") == "openclaw_client_not_configured"


def test_openclaw_cloud_tier_reset_not_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Клиент без reset_cloud_tier → tier_reset_not_supported."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    body = (
        _client(_build_ctx(openclaw=_StubNoMethods())).post("/api/openclaw/cloud/tier/reset").json()
    )
    assert body.get("status") == "failed"
    assert body.get("error_code") == "tier_reset_not_supported"


def test_openclaw_cloud_tier_reset_system_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """reset_cloud_tier бросает → tier_reset_error."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake = _FakeOpenClaw(raise_in="reset")
    body = _client(_build_ctx(openclaw=fake)).post("/api/openclaw/cloud/tier/reset").json()
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


# ---------------------------------------------------------------------------
# Wave DD: /api/openclaw/cron/status, /api/openclaw/cron/jobs, /api/openclaw/runtime-config
# ---------------------------------------------------------------------------


def _build_ctx_with_helpers(
    *,
    cron_helper: object | None = None,
    runtime_config_helper: object | None = None,
) -> RouterContext:
    """Контекст с инжектированными Wave DD helpers (без openclaw_client)."""
    deps: dict = {}
    if cron_helper is not None:
        deps["openclaw_cron_snapshot_helper"] = cron_helper
    if runtime_config_helper is not None:
        deps["openclaw_runtime_config_snapshot_helper"] = runtime_config_helper
    return RouterContext(
        deps=deps,
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )


def test_openclaw_cron_status_ok() -> None:
    """GET /api/openclaw/cron/status возвращает snapshot при ok=True."""

    async def _helper(*, include_all: bool = True) -> dict:
        return {"ok": True, "summary": {"jobs_total": 2}, "jobs": [{"id": "j1"}, {"id": "j2"}]}

    body = (
        _client(_build_ctx_with_helpers(cron_helper=_helper))
        .get("/api/openclaw/cron/status")
        .json()
    )
    assert body["ok"] is True
    assert body["summary"]["jobs_total"] == 2


def test_openclaw_cron_status_helper_unavailable() -> None:
    """Если helper не инжектирован — мягкая ошибка."""
    body = _client(_build_ctx_with_helpers()).get("/api/openclaw/cron/status").json()
    assert body["ok"] is False
    assert body["error"] == "helper_unavailable"


def test_openclaw_cron_status_propagates_error() -> None:
    """Если helper вернул ok=False — отдаём snapshot как есть."""

    async def _helper(*, include_all: bool = True) -> dict:
        return {"ok": False, "error": "cli_failed", "detail": "no gateway"}

    body = (
        _client(_build_ctx_with_helpers(cron_helper=_helper))
        .get("/api/openclaw/cron/status")
        .json()
    )
    assert body["ok"] is False
    assert body["error"] == "cli_failed"


def test_openclaw_cron_status_timeout() -> None:
    """TimeoutError из helper → ok=False, error содержит timeout."""
    import asyncio as _aio

    async def _helper(*, include_all: bool = True) -> dict:
        raise _aio.TimeoutError

    body = (
        _client(_build_ctx_with_helpers(cron_helper=_helper))
        .get("/api/openclaw/cron/status")
        .json()
    )
    assert body["ok"] is False
    assert "timeout" in body.get("error", "").lower()


def test_openclaw_cron_jobs_returns_summary_and_jobs() -> None:
    """GET /api/openclaw/cron/jobs формирует {summary, jobs}."""

    async def _helper(*, include_all: bool = True) -> dict:
        return {
            "ok": True,
            "summary": {"jobs_total": 1, "scheduler_status": "running"},
            "jobs": [{"id": "j1", "name": "test"}],
        }

    body = (
        _client(_build_ctx_with_helpers(cron_helper=_helper)).get("/api/openclaw/cron/jobs").json()
    )
    assert body["ok"] is True
    assert body["summary"]["jobs_total"] == 1
    assert body["jobs"] == [{"id": "j1", "name": "test"}]


def test_openclaw_cron_jobs_passes_include_all_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """Параметр include_all передаётся в helper."""
    seen: dict = {}

    async def _helper(*, include_all: bool = True) -> dict:
        seen["include_all"] = include_all
        return {"ok": True, "summary": {}, "jobs": []}

    _client(_build_ctx_with_helpers(cron_helper=_helper)).get(
        "/api/openclaw/cron/jobs?include_all=false"
    ).json()
    assert seen["include_all"] is False


def test_openclaw_cron_jobs_helper_unavailable() -> None:
    body = _client(_build_ctx_with_helpers()).get("/api/openclaw/cron/jobs").json()
    assert body["ok"] is False
    assert body["error"] == "helper_unavailable"


def test_openclaw_runtime_config_ok_sync_helper() -> None:
    """GET /api/openclaw/runtime-config поддерживает sync helper."""

    def _helper() -> dict:
        return {
            "ok": True,
            "openclaw_base_url": "http://127.0.0.1:18789",
            "gateway_token_present": True,
            "gateway_token_kind": "plain",
            "runtime_policy": {"force_cloud": False},
        }

    body = (
        _client(_build_ctx_with_helpers(runtime_config_helper=_helper))
        .get("/api/openclaw/runtime-config")
        .json()
    )
    assert body["ok"] is True
    assert body["openclaw_base_url"] == "http://127.0.0.1:18789"
    assert body["runtime_policy"]["force_cloud"] is False


def test_openclaw_runtime_config_ok_async_helper() -> None:
    """GET /api/openclaw/runtime-config поддерживает async helper."""

    async def _helper() -> dict:
        return {"ok": True, "openclaw_base_url": "http://x", "runtime_policy": {}}

    body = (
        _client(_build_ctx_with_helpers(runtime_config_helper=_helper))
        .get("/api/openclaw/runtime-config")
        .json()
    )
    assert body["ok"] is True
    assert body["openclaw_base_url"] == "http://x"


def test_openclaw_runtime_config_helper_unavailable() -> None:
    body = _client(_build_ctx_with_helpers()).get("/api/openclaw/runtime-config").json()
    assert body["ok"] is False
    assert body["error"] == "helper_unavailable"


# ---------------------------------------------------------------------------
# Wave EE: /api/openclaw/model-routing/status, /api/openclaw/model-compat/probe
# ---------------------------------------------------------------------------


def _build_ctx_wave_ee(
    *,
    routing_helper: object | None = None,
    overlay_helper: object | None = None,
    compat_probe_helper: object | None = None,
    autoswitch_helper: object | None = None,
    openclaw: object | None = None,
) -> RouterContext:
    """Контекст с инжектированными Wave EE helpers."""
    deps: dict = {}
    if routing_helper is not None:
        deps["openclaw_model_routing_helper"] = routing_helper
    if overlay_helper is not None:
        deps["openclaw_model_routing_overlay_helper"] = overlay_helper
    if compat_probe_helper is not None:
        deps["openclaw_model_compat_probe_helper"] = compat_probe_helper
    if autoswitch_helper is not None:
        deps["openclaw_model_autoswitch_helper"] = autoswitch_helper
    if openclaw is not None:
        deps["openclaw_client"] = openclaw
    return RouterContext(
        deps=deps,
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )


def test_openclaw_model_routing_status_ok_with_overlay() -> None:
    """GET /api/openclaw/model-routing/status: routing + overlay helper вызваны."""
    seen: dict = {}

    def _routing() -> dict:
        return {"primary": "gemini-3-pro", "fallbacks": []}

    def _overlay(*, routing: dict, last_runtime_route: dict) -> dict:
        seen["routing"] = routing
        seen["last_route"] = last_runtime_route
        return {**routing, "last_runtime_route": last_runtime_route}

    class _Client:
        def get_last_runtime_route(self) -> dict:
            return {"channel": "openclaw_cloud", "model": "gemini-3-pro"}

    body = (
        _client(
            _build_ctx_wave_ee(
                routing_helper=_routing,
                overlay_helper=_overlay,
                openclaw=_Client(),
            )
        )
        .get("/api/openclaw/model-routing/status")
        .json()
    )
    assert body["ok"] is True
    assert body["routing"]["primary"] == "gemini-3-pro"
    assert body["routing"]["last_runtime_route"]["channel"] == "openclaw_cloud"
    assert seen["last_route"]["model"] == "gemini-3-pro"


def test_openclaw_model_routing_status_no_openclaw_client() -> None:
    """Без openclaw_client overlay получает пустой last_runtime_route."""
    captured: dict = {}

    def _routing() -> dict:
        return {"primary": "p"}

    def _overlay(*, routing: dict, last_runtime_route: dict) -> dict:
        captured["last"] = last_runtime_route
        return routing

    body = (
        _client(_build_ctx_wave_ee(routing_helper=_routing, overlay_helper=_overlay))
        .get("/api/openclaw/model-routing/status")
        .json()
    )
    assert body["ok"] is True
    assert captured["last"] == {}


def test_openclaw_model_routing_status_helper_unavailable() -> None:
    """Если helpers не инжектированы — soft error."""
    body = _client(_build_ctx_wave_ee()).get("/api/openclaw/model-routing/status").json()
    assert body["ok"] is False
    assert body["error"] == "helper_unavailable"


def test_openclaw_model_routing_status_swallows_get_last_route_error() -> None:
    """Если openclaw.get_last_runtime_route бросает — last_runtime_route={}, не падаем."""

    def _routing() -> dict:
        return {"primary": "p"}

    captured: dict = {}

    def _overlay(*, routing: dict, last_runtime_route: dict) -> dict:
        captured["last"] = last_runtime_route
        return routing

    class _BadClient:
        def get_last_runtime_route(self) -> dict:
            raise RuntimeError("boom")

    body = (
        _client(
            _build_ctx_wave_ee(
                routing_helper=_routing, overlay_helper=_overlay, openclaw=_BadClient()
            )
        )
        .get("/api/openclaw/model-routing/status")
        .json()
    )
    assert body["ok"] is True
    assert captured["last"] == {}


def test_openclaw_model_compat_probe_ok_passes_query() -> None:
    """GET /api/openclaw/model-compat/probe передаёт query params в helper."""
    seen: dict = {}

    def _helper(*, model: str, reasoning: str, skip_reasoning: bool) -> dict:
        seen.update(model=model, reasoning=reasoning, skip_reasoning=skip_reasoning)
        return {"compatible": True, "model": model}

    body = (
        _client(_build_ctx_wave_ee(compat_probe_helper=_helper))
        .get(
            "/api/openclaw/model-compat/probe",
            params={"model": "gemini-3-pro", "reasoning": "low", "skip_reasoning": "true"},
        )
        .json()
    )
    assert body["ok"] is True
    assert body["probe"]["compatible"] is True
    assert seen == {"model": "gemini-3-pro", "reasoning": "low", "skip_reasoning": True}


def test_openclaw_model_compat_probe_default_query() -> None:
    """Default query params: empty model, reasoning='high', skip_reasoning=False."""
    seen: dict = {}

    def _helper(*, model: str, reasoning: str, skip_reasoning: bool) -> dict:
        seen.update(model=model, reasoning=reasoning, skip_reasoning=skip_reasoning)
        return {"ok_probe": True}

    _client(_build_ctx_wave_ee(compat_probe_helper=_helper)).get(
        "/api/openclaw/model-compat/probe"
    ).json()
    assert seen == {"model": "", "reasoning": "high", "skip_reasoning": False}


def test_openclaw_model_compat_probe_helper_unavailable() -> None:
    """Без helper — 500 helper_unavailable."""
    resp = _client(_build_ctx_wave_ee()).get("/api/openclaw/model-compat/probe")
    assert resp.status_code == 500
    assert resp.json()["detail"] == "helper_unavailable"


def test_openclaw_model_autoswitch_status_ok_passes_query() -> None:
    """GET /api/openclaw/model-autoswitch/status: profile передаётся в helper."""
    seen: dict = {}

    def _helper(*, dry_run: bool, profile: str, toggle: bool) -> dict:
        seen.update(dry_run=dry_run, profile=profile, toggle=toggle)
        return {"status": "OK", "reason": "test"}

    body = (
        _client(_build_ctx_wave_ee(autoswitch_helper=_helper))
        .get("/api/openclaw/model-autoswitch/status", params={"profile": "local-first"})
        .json()
    )
    assert body["ok"] is True
    assert body["autoswitch"]["status"] == "OK"
    assert seen == {"dry_run": True, "profile": "local-first", "toggle": False}


def test_openclaw_model_autoswitch_status_default_profile() -> None:
    """Default profile='current'."""
    seen: dict = {}

    def _helper(*, dry_run: bool, profile: str, toggle: bool) -> dict:
        seen.update(dry_run=dry_run, profile=profile, toggle=toggle)
        return {"status": "OK"}

    _client(_build_ctx_wave_ee(autoswitch_helper=_helper)).get(
        "/api/openclaw/model-autoswitch/status"
    ).json()
    assert seen == {"dry_run": True, "profile": "current", "toggle": False}


def test_openclaw_model_autoswitch_status_helper_unavailable() -> None:
    resp = _client(_build_ctx_wave_ee()).get("/api/openclaw/model-autoswitch/status")
    assert resp.status_code == 500
    assert resp.json()["detail"] == "helper_unavailable"


def test_openclaw_model_compat_probe_async_helper() -> None:
    """Async helper тоже awaitsя."""

    async def _helper(*, model: str, reasoning: str, skip_reasoning: bool) -> dict:
        return {"async_ok": True, "model": model}

    body = (
        _client(_build_ctx_wave_ee(compat_probe_helper=_helper))
        .get("/api/openclaw/model-compat/probe", params={"model": "x"})
        .json()
    )
    assert body["ok"] is True
    assert body["probe"]["async_ok"] is True
