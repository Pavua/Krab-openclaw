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


# ---------------------------------------------------------------------------
# Wave JJ: cron POST endpoints + model-autoswitch/apply
# ---------------------------------------------------------------------------


def _build_ctx_wave_jj(
    *,
    cli_helper: object | None = None,
    cron_helper: object | None = None,
    autoswitch_helper: object | None = None,
) -> RouterContext:
    """Контекст с Wave JJ helpers."""
    deps: dict = {}
    if cli_helper is not None:
        deps["openclaw_cli_runner_helper"] = cli_helper
    if cron_helper is not None:
        deps["openclaw_cron_snapshot_helper"] = cron_helper
    if autoswitch_helper is not None:
        deps["openclaw_model_autoswitch_helper"] = autoswitch_helper
    return RouterContext(
        deps=deps,
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )


_VALID_CREATE_BODY = {
    "name": "test-job",
    "every": "1h",
    "task_kind": "system",
    "payload_text": "ping",
}


def test_cron_create_success_via_router() -> None:
    """POST /api/openclaw/cron/jobs/create через helper injection."""
    seen: dict = {}

    async def _cli(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return {"ok": True, "exit_code": 0, "raw": "{}", "data": {"id": "j-1"}}

    async def _snap(*, include_all: bool = True):
        return {"ok": True, "summary": {"total": 1}, "jobs": [{"id": "j-1"}], "status": {}}

    body = (
        _client(_build_ctx_wave_jj(cli_helper=_cli, cron_helper=_snap))
        .post("/api/openclaw/cron/jobs/create", json=_VALID_CREATE_BODY)
        .json()
    )
    assert body["ok"] is True
    assert body["created"] == {"id": "j-1"}
    assert body["jobs"] == [{"id": "j-1"}]
    # Verify CLI args composed correctly
    assert "cron" in seen["args"] and "add" in seen["args"]
    assert "--name" in seen["args"]
    assert "test-job" in seen["args"]


def test_cron_create_missing_name_400() -> None:
    """POST cron/create без name → 400."""
    resp = _client(_build_ctx_wave_jj()).post(
        "/api/openclaw/cron/jobs/create",
        json={"every": "1h", "task_kind": "system", "payload_text": "ping"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "cron_name_required"


def test_cron_create_invalid_task_kind_400() -> None:
    resp = _client(_build_ctx_wave_jj()).post(
        "/api/openclaw/cron/jobs/create",
        json={**_VALID_CREATE_BODY, "task_kind": "weird"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "cron_task_kind_invalid"


def test_cron_create_helper_unavailable() -> None:
    """Без CLI helper — мягкая ошибка."""
    body = (
        _client(_build_ctx_wave_jj())
        .post("/api/openclaw/cron/jobs/create", json=_VALID_CREATE_BODY)
        .json()
    )
    assert body["ok"] is False
    assert body["error"] == "helper_unavailable"


def test_cron_create_cli_failed_propagates() -> None:
    async def _cli(*args, **kwargs):
        return {"ok": False, "error": "openclaw_timeout", "detail": "x", "raw": ""}

    body = (
        _client(_build_ctx_wave_jj(cli_helper=_cli))
        .post("/api/openclaw/cron/jobs/create", json=_VALID_CREATE_BODY)
        .json()
    )
    assert body["ok"] is False
    assert body["error"] == "openclaw_timeout"


def test_cron_toggle_success_enable() -> None:
    seen: dict = {}

    async def _cli(*args, **kwargs):
        seen["args"] = args
        return {"ok": True, "exit_code": 0, "raw": "enabled"}

    async def _snap(*, include_all: bool = True):
        return {"ok": True, "summary": {}, "jobs": [], "status": {}}

    body = (
        _client(_build_ctx_wave_jj(cli_helper=_cli, cron_helper=_snap))
        .post("/api/openclaw/cron/jobs/toggle", json={"id": "j-1", "enabled": True})
        .json()
    )
    assert body["ok"] is True
    assert "cron" in seen["args"] and "enable" in seen["args"] and "j-1" in seen["args"]


def test_cron_toggle_disable_command() -> None:
    seen: dict = {}

    async def _cli(*args, **kwargs):
        seen["args"] = args
        return {"ok": True, "exit_code": 0, "raw": "disabled"}

    async def _snap(*, include_all: bool = True):
        return {"ok": True, "summary": {}, "jobs": [], "status": {}}

    _client(_build_ctx_wave_jj(cli_helper=_cli, cron_helper=_snap)).post(
        "/api/openclaw/cron/jobs/toggle", json={"id": "j-1", "enabled": False}
    ).json()
    assert "disable" in seen["args"]


def test_cron_toggle_enabled_must_be_bool_400() -> None:
    resp = _client(_build_ctx_wave_jj()).post(
        "/api/openclaw/cron/jobs/toggle", json={"id": "j-1", "enabled": "yes"}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "cron_enabled_bool_required"


def test_cron_toggle_missing_id_400() -> None:
    resp = _client(_build_ctx_wave_jj()).post(
        "/api/openclaw/cron/jobs/toggle", json={"enabled": True}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "cron_id_required"


def test_cron_remove_success() -> None:
    async def _cli(*args, **kwargs):
        assert "rm" in args
        return {"ok": True, "exit_code": 0, "raw": "{}", "data": {"removed": "j-1"}}

    async def _snap(*, include_all: bool = True):
        return {"ok": True, "summary": {}, "jobs": [], "status": {}}

    body = (
        _client(_build_ctx_wave_jj(cli_helper=_cli, cron_helper=_snap))
        .post("/api/openclaw/cron/jobs/remove", json={"id": "j-1"})
        .json()
    )
    assert body["ok"] is True
    assert body["removed"] == {"removed": "j-1"}


def test_cron_remove_missing_id_400() -> None:
    resp = _client(_build_ctx_wave_jj()).post("/api/openclaw/cron/jobs/remove", json={})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "cron_id_required"


def test_autoswitch_apply_with_body_profile() -> None:
    seen: dict = {}

    def _helper(*, dry_run: bool, profile: str, toggle: bool):
        seen.update(dry_run=dry_run, profile=profile, toggle=toggle)
        return {"applied": True}

    body = (
        _client(_build_ctx_wave_jj(autoswitch_helper=_helper))
        .post(
            "/api/openclaw/model-autoswitch/apply",
            json={"profile": "local-first"},
        )
        .json()
    )
    assert body["ok"] is True
    assert body["autoswitch"] == {"applied": True}
    assert seen == {"dry_run": False, "profile": "local-first", "toggle": False}


def test_autoswitch_apply_toggle_true_when_no_profile() -> None:
    """Без profile — toggle всегда True (raw toggle behavior)."""
    seen: dict = {}

    def _helper(*, dry_run: bool, profile: str, toggle: bool):
        seen.update(dry_run=dry_run, profile=profile, toggle=toggle)
        return {"toggled": True}

    _client(_build_ctx_wave_jj(autoswitch_helper=_helper)).post(
        "/api/openclaw/model-autoswitch/apply", json={}
    ).json()
    assert seen["toggle"] is True
    assert seen["profile"] == ""


def test_autoswitch_apply_helper_unavailable() -> None:
    resp = _client(_build_ctx_wave_jj()).post(
        "/api/openclaw/model-autoswitch/apply", json={"profile": "x"}
    )
    assert resp.status_code == 500
    assert resp.json()["detail"] == "helper_unavailable"


# ============================================================================
# Wave KK: cloud diagnostics + control-compat/status
# ============================================================================


class _CloudDiagOpenClaw:
    """Stub поддерживающий get_cloud_provider_diagnostics."""

    def __init__(self, *, raise_exc: bool = False) -> None:
        self._raise = raise_exc
        self.last_providers: list[str] | None = None

    async def get_cloud_provider_diagnostics(self, providers: list[str] | None = None) -> dict:
        if self._raise:
            raise RuntimeError("boom")
        self.last_providers = providers
        return {"providers": providers or []}


def _build_ctx_kk(*, openclaw: object | None = ...) -> RouterContext:
    deps: dict = {}
    if openclaw is ...:
        deps["openclaw_client"] = _CloudDiagOpenClaw()
    elif openclaw is not None:
        deps["openclaw_client"] = openclaw
    return RouterContext(
        deps=deps,
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )


def test_cloud_diagnostics_no_openclaw_client() -> None:
    resp = _client(_build_ctx_kk(openclaw=None)).get("/api/openclaw/cloud")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"available": False, "error": "openclaw_client_not_configured"}


def test_cloud_diagnostics_method_not_supported() -> None:
    resp = _client(_build_ctx_kk(openclaw=_StubNoMethods())).get("/api/openclaw/cloud")
    assert resp.json() == {"available": False, "error": "cloud_diagnostics_not_supported"}


def test_cloud_diagnostics_empty_providers() -> None:
    stub = _CloudDiagOpenClaw()
    resp = _client(_build_ctx_kk(openclaw=stub)).get("/api/openclaw/cloud")
    body = resp.json()
    assert body["available"] is True
    assert body["report"] == {"providers": []}
    # Пустая строка → providers=None в helper.
    assert stub.last_providers is None


def test_cloud_diagnostics_with_providers_query() -> None:
    stub = _CloudDiagOpenClaw()
    resp = _client(_build_ctx_kk(openclaw=stub)).get(
        "/api/openclaw/cloud", params={"providers": "Google, OPENAI ,, "}
    )
    body = resp.json()
    assert body["available"] is True
    # Должна быть нормализация: lowercase + trim + filter empty.
    assert stub.last_providers == ["google", "openai"]


def test_cloud_diagnostics_legacy_alias() -> None:
    """Legacy /cloud/diagnostics endpoint должен иметь идентичный контракт."""
    stub = _CloudDiagOpenClaw()
    resp = _client(_build_ctx_kk(openclaw=stub)).get("/api/openclaw/cloud/diagnostics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["report"] == {"providers": []}


def test_control_compat_status_subprocess_failure() -> None:
    """Если subprocess CLI недоступен — endpoint возвращает graceful response."""
    import asyncio as _asyncio

    from src.modules.web_routers import openclaw_router as _mod

    async def _boom(*args, **kwargs):
        raise FileNotFoundError("openclaw CLI not found")

    orig = _mod.asyncio.create_subprocess_exec
    _mod.asyncio.create_subprocess_exec = _boom  # type: ignore[assignment]
    try:
        resp = _client(_build_ctx_kk(openclaw=None)).get("/api/openclaw/control-compat/status")
    finally:
        _mod.asyncio.create_subprocess_exec = orig  # type: ignore[assignment]
        del _asyncio  # silence unused import
    assert resp.status_code == 200
    body = resp.json()
    # CLI недоступен → runtime_ok=False, no warnings → impact_level=runtime_risk.
    assert body["runtime_channels_ok"] is False
    assert body["runtime_status"] == "FAIL"
    assert body["control_schema_warnings"] == []
    assert body["has_schema_warning"] is False
    assert body["impact_level"] == "runtime_risk"
    assert "ok" in body


# =============================================================================
# Wave LL: browser/photo smoke endpoints (Session 25)
# =============================================================================


def _build_ctx_wave_ll(
    *,
    browser_smoke=None,
    photo_smoke=None,
    launch_owner_chrome=None,
    assert_write_fn=None,
) -> RouterContext:
    deps: dict = {}
    if browser_smoke is not None:
        deps["openclaw_browser_smoke_helper"] = browser_smoke
    if photo_smoke is not None:
        deps["openclaw_photo_smoke_helper"] = photo_smoke
    if launch_owner_chrome is not None:
        deps["openclaw_launch_owner_chrome_helper"] = launch_owner_chrome
    return RouterContext(
        deps=deps,
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "secret",
        assert_write_access_fn=assert_write_fn or (lambda h, t: None),
    )


# ---------- /api/openclaw/browser-smoke -------------------------------------


def test_openclaw_browser_smoke_ok() -> None:
    captured: dict = {}

    async def _helper(url: str) -> dict:
        captured["url"] = url
        return {"browser_smoke": {"ok": True, "channel": "endpoint"}}

    body = (
        _client(_build_ctx_wave_ll(browser_smoke=_helper))
        .get("/api/openclaw/browser-smoke?url=https://test.example")
        .json()
    )
    assert body["available"] is True
    assert body["report"]["browser_smoke"]["ok"] is True
    assert captured["url"] == "https://test.example"


def test_openclaw_browser_smoke_default_url() -> None:
    seen: dict = {}

    async def _helper(url: str) -> dict:
        seen["url"] = url
        return {"browser_smoke": {"ok": False}}

    body = (
        _client(_build_ctx_wave_ll(browser_smoke=_helper)).get("/api/openclaw/browser-smoke").json()
    )
    assert body["available"] is True
    assert seen["url"] == "https://example.com"


def test_openclaw_browser_smoke_helper_missing() -> None:
    body = _client(_build_ctx_wave_ll()).get("/api/openclaw/browser-smoke").json()
    assert body["available"] is False
    assert body["error"] == "openclaw_browser_smoke_helper_unavailable"


def test_openclaw_browser_smoke_timeout_guard() -> None:
    import asyncio as _asyncio

    async def _slow_helper(url: str) -> dict:
        await _asyncio.sleep(10.0)
        return {"browser_smoke": {"ok": True}}

    # patching to short timeout - but easier to raise TimeoutError directly
    async def _timeout_helper(url: str) -> dict:
        raise _asyncio.TimeoutError()

    # Smoke helper that raises TimeoutError synchronously won't trigger; we need
    # asyncio.wait_for to fire — use a helper that is awaitable but never
    # completes. Simpler: monkeypatch wait_for via a tiny wrapper.
    body = (
        _client(_build_ctx_wave_ll(browser_smoke=_timeout_helper))
        .get("/api/openclaw/browser-smoke")
        .json()
    )
    # TimeoutError raised inside helper still bubbles up to the wait_for branch
    assert body["available"] is False
    assert body["error"] == "OpenClaw timeout (5s)"


# ---------- /api/openclaw/photo-smoke ---------------------------------------


def test_openclaw_photo_smoke_ok_async() -> None:
    async def _helper() -> dict:
        return {"available": True, "report": {"photo_smoke": {"ok": True}}}

    body = _client(_build_ctx_wave_ll(photo_smoke=_helper)).get("/api/openclaw/photo-smoke").json()
    assert body["available"] is True
    assert body["report"]["photo_smoke"]["ok"] is True


def test_openclaw_photo_smoke_helper_missing() -> None:
    body = _client(_build_ctx_wave_ll()).get("/api/openclaw/photo-smoke").json()
    assert body["available"] is False
    assert body["error"] == "openclaw_photo_smoke_helper_unavailable"


def test_openclaw_photo_smoke_sync_helper() -> None:
    """Helper может быть sync — endpoint должен корректно возвращать payload."""

    def _helper() -> dict:
        return {"available": False, "error": "router_unavailable"}

    body = _client(_build_ctx_wave_ll(photo_smoke=_helper)).get("/api/openclaw/photo-smoke").json()
    assert body["available"] is False
    assert body["error"] == "router_unavailable"


# ---------- /api/openclaw/browser/open-owner-chrome -------------------------


def test_openclaw_open_owner_chrome_ok() -> None:
    calls: list[bool] = []

    def _helper() -> dict:
        calls.append(True)
        return {"ok": True, "method": "command_helper", "path": "/tmp/x.command"}

    body = (
        _client(_build_ctx_wave_ll(launch_owner_chrome=_helper))
        .post(
            "/api/openclaw/browser/open-owner-chrome",
            headers={"X-Krab-Web-Key": "secret"},
        )
        .json()
    )
    assert body["ok"] is True
    assert body["method"] == "command_helper"
    assert calls == [True]


def test_openclaw_open_owner_chrome_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    """WEB_API_KEY установлен, header не передан → 403."""
    monkeypatch.setenv("WEB_API_KEY", "secret-key")

    def _helper() -> dict:
        return {"ok": True}

    resp = _client(_build_ctx_wave_ll(launch_owner_chrome=_helper)).post(
        "/api/openclaw/browser/open-owner-chrome"
    )
    # ctx.assert_write_access raises HTTPException(403) → FastAPI returns 403
    assert resp.status_code == 403


def test_openclaw_open_owner_chrome_helper_missing() -> None:
    resp = _client(_build_ctx_wave_ll()).post(
        "/api/openclaw/browser/open-owner-chrome",
        headers={"X-Krab-Web-Key": "secret"},
    )
    assert resp.status_code == 500
    assert resp.json()["detail"] == "helper_unavailable"


# =============================================================================
# Wave MM: browser readiness/start endpoints (Session 25)
# =============================================================================


def _build_ctx_wave_mm(
    *,
    smoke=None,
    probe=None,
    runtime=None,
    classify=None,
    mcp_snapshot=None,
    paths=None,
    cli_json=None,
    assert_write_fn=None,
) -> RouterContext:
    deps: dict = {}
    if smoke is not None:
        deps["openclaw_browser_smoke_helper"] = smoke
    if probe is not None:
        deps["openclaw_probe_owner_chrome_helper"] = probe
    if runtime is not None:
        deps["openclaw_collect_stable_browser_cli_runtime_helper"] = runtime
    if classify is not None:
        deps["openclaw_classify_browser_stage_helper"] = classify
    if mcp_snapshot is not None:
        deps["openclaw_build_mcp_readiness_snapshot_helper"] = mcp_snapshot
    if paths is not None:
        deps["openclaw_build_browser_access_paths_helper"] = paths
    if cli_json is not None:
        deps["openclaw_run_cli_json_helper"] = cli_json
    return RouterContext(
        deps=deps,
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "secret",
        assert_write_access_fn=assert_write_fn or (lambda h, t: None),
    )


def _default_wave_mm_helpers():
    """Базовый набор helpers для readiness happy-path."""

    async def _smoke(url: str) -> dict:
        return {
            "browser_smoke": {
                "relay_reachable": True,
                "browser_http_reachable": True,
                "browser_auth_required": False,
            }
        }

    async def _probe(url: str) -> dict:
        return {"reachable": True}

    async def _runtime(**kwargs) -> tuple:
        return ({"running": True}, None, {"tabs": []}, None)

    def _classify(status, tabs, smoke, **kwargs) -> dict:
        return {"readiness": "ready", "stage": "ready"}

    def _mcp(browser, *, owner_chrome) -> dict:
        return {"readiness": "ready"}

    def _paths(browser, mcp) -> dict:
        return {"primary": "/tmp/path"}

    return _smoke, _probe, _runtime, _classify, _mcp, _paths


# ---------- /api/openclaw/browser-mcp-readiness -----------------------------


def test_openclaw_browser_mcp_readiness_ok() -> None:
    smoke, probe, runtime, classify, mcp, paths = _default_wave_mm_helpers()
    body = (
        _client(
            _build_ctx_wave_mm(
                smoke=smoke,
                probe=probe,
                runtime=runtime,
                classify=classify,
                mcp_snapshot=mcp,
                paths=paths,
            )
        )
        .get("/api/openclaw/browser-mcp-readiness")
        .json()
    )
    assert body["available"] is True
    assert body["overall"]["readiness"] == "ready"
    assert body["mcp"]["readiness"] == "ready"
    assert body["browser"]["paths"] == {"primary": "/tmp/path"}


def test_openclaw_browser_mcp_readiness_attention() -> None:
    smoke, probe, runtime, _classify, _mcp, paths = _default_wave_mm_helpers()

    def _classify_attention(status, tabs, smoke_dict, **kwargs) -> dict:
        return {"readiness": "attention"}

    def _mcp_ready(browser, *, owner_chrome) -> dict:
        return {"readiness": "ready"}

    body = (
        _client(
            _build_ctx_wave_mm(
                smoke=smoke,
                probe=probe,
                runtime=runtime,
                classify=_classify_attention,
                mcp_snapshot=_mcp_ready,
                paths=paths,
            )
        )
        .get("/api/openclaw/browser-mcp-readiness")
        .json()
    )
    assert body["overall"]["readiness"] == "attention"


def test_openclaw_browser_mcp_readiness_blocked() -> None:
    smoke, probe, runtime, _classify, _mcp, paths = _default_wave_mm_helpers()

    def _mcp_blocked(browser, *, owner_chrome) -> dict:
        return {"readiness": "blocked"}

    def _classify_ready(status, tabs, smoke_dict, **kwargs) -> dict:
        return {"readiness": "ready"}

    body = (
        _client(
            _build_ctx_wave_mm(
                smoke=smoke,
                probe=probe,
                runtime=runtime,
                classify=_classify_ready,
                mcp_snapshot=_mcp_blocked,
                paths=paths,
            )
        )
        .get("/api/openclaw/browser-mcp-readiness")
        .json()
    )
    assert body["overall"]["readiness"] == "blocked"


def test_openclaw_browser_mcp_readiness_timeout() -> None:
    import asyncio as _asyncio

    async def _slow_smoke(url: str) -> dict:
        raise _asyncio.TimeoutError()

    smoke, probe, runtime, classify, mcp, paths = _default_wave_mm_helpers()
    body = (
        _client(
            _build_ctx_wave_mm(
                smoke=_slow_smoke,
                probe=probe,
                runtime=runtime,
                classify=classify,
                mcp_snapshot=mcp,
                paths=paths,
            )
        )
        .get("/api/openclaw/browser-mcp-readiness")
        .json()
    )
    assert body["available"] is False
    assert body["error"] == "OpenClaw timeout (5s)"


# ---------- /api/openclaw/browser/start -------------------------------------


def test_openclaw_browser_start_ok() -> None:
    smoke, probe, runtime, classify, mcp, paths = _default_wave_mm_helpers()

    async def _cli(args, timeout_sec=20.0) -> tuple:
        return ({"started": True, "args": args}, None)

    body = (
        _client(
            _build_ctx_wave_mm(
                smoke=smoke,
                probe=probe,
                runtime=runtime,
                classify=classify,
                cli_json=_cli,
            )
        )
        .post(
            "/api/openclaw/browser/start",
            headers={"X-Krab-Web-Key": "secret"},
        )
        .json()
    )
    assert body["ok"] is True
    assert body["start"]["started"] is True
    assert body["start"]["args"] == ["browser", "--json", "start"]


def test_openclaw_browser_start_cli_failed() -> None:
    smoke, probe, runtime, classify, mcp, paths = _default_wave_mm_helpers()

    async def _cli_fail(args, timeout_sec=20.0) -> tuple:
        return (None, "cli timeout")

    body = (
        _client(
            _build_ctx_wave_mm(
                smoke=smoke,
                probe=probe,
                runtime=runtime,
                classify=classify,
                cli_json=_cli_fail,
            )
        )
        .post(
            "/api/openclaw/browser/start",
            headers={"X-Krab-Web-Key": "secret"},
        )
        .json()
    )
    assert body["ok"] is False
    assert body["error"] == "browser_start_failed"
    assert body["detail"] == "cli timeout"


def test_openclaw_browser_start_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    resp = _client(_build_ctx_wave_mm()).post("/api/openclaw/browser/start")
    assert resp.status_code == 403


def test_openclaw_browser_start_helper_missing() -> None:
    resp = _client(_build_ctx_wave_mm()).post(
        "/api/openclaw/browser/start",
        headers={"X-Krab-Web-Key": "secret"},
    )
    assert resp.status_code == 500
    assert resp.json()["detail"] == "cli_helper_unavailable"


# ===========================================================================
# Wave NN — оставшиеся HARD endpoints через helper injection
# ===========================================================================


# ---------- POST /api/openclaw/cloud/switch-tier (Wave NN) ------------------


class _SwitchTierClient:
    def __init__(self, *, raise_in: bool = False) -> None:
        self._raise = raise_in
        self.calls: list[str] = []

    async def switch_cloud_tier(self, tier: str) -> dict:
        if self._raise:
            raise RuntimeError("boom-switch")
        self.calls.append(tier)
        return {"ok": True, "tier": tier, "secrets_reloaded": True}


def _build_ctx_wave_nn(
    *,
    openclaw: object | None = ...,
    router_obj: object | None = None,
    cli_env: dict | None = None,
    parse_result: dict | None = None,
    truth_helper=None,
    invalidator_target: list | None = None,
) -> RouterContext:
    deps: dict = {}
    if openclaw is ...:
        deps["openclaw_client"] = _SwitchTierClient()
    elif openclaw is not None:
        deps["openclaw_client"] = openclaw
    if router_obj is not None:
        deps["router"] = router_obj
    if cli_env is not None:
        deps["openclaw_cli_env_helper"] = lambda: dict(cli_env)
    if parse_result is not None:
        deps["openclaw_parse_channels_probe_helper"] = lambda raw: dict(parse_result)
    if truth_helper is not None:
        deps["resolve_local_runtime_truth_helper"] = truth_helper
    if invalidator_target is not None:
        deps["runtime_lite_cache_invalidator_helper"] = lambda: invalidator_target.append(
            "invalidated"
        )
    return RouterContext(
        deps=deps,
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )


def test_openclaw_cloud_switch_tier_invalid_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    body = (
        _client(_build_ctx_wave_nn())
        .post("/api/openclaw/cloud/switch-tier", json={"tier": "premium"})
        .json()
    )
    assert body["ok"] is False
    assert body["error"] == "invalid_tier"


def test_openclaw_cloud_switch_tier_ok_invalidates_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    invalidations: list[str] = []
    client_obj = _SwitchTierClient()
    ctx = _build_ctx_wave_nn(
        openclaw=client_obj,
        invalidator_target=invalidations,
    )
    body = _client(ctx).post("/api/openclaw/cloud/switch-tier", json={"tier": "paid"}).json()
    assert body["ok"] is True
    assert body["result"]["tier"] == "paid"
    assert client_obj.calls == ["paid"]
    assert invalidations == ["invalidated"]


def test_openclaw_cloud_switch_tier_no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    body = (
        _client(_build_ctx_wave_nn(openclaw=None))
        .post("/api/openclaw/cloud/switch-tier", json={"tier": "free"})
        .json()
    )
    assert body["ok"] is False
    assert body["error"] == "openclaw_client_not_configured"


def test_openclaw_cloud_switch_tier_not_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    body = (
        _client(_build_ctx_wave_nn(openclaw=_StubNoMethods()))
        .post("/api/openclaw/cloud/switch-tier", json={"tier": "free"})
        .json()
    )
    assert body["ok"] is False
    assert body["error"] == "switch_cloud_tier_not_supported"


def test_openclaw_cloud_switch_tier_exception_graceful(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    body = (
        _client(_build_ctx_wave_nn(openclaw=_SwitchTierClient(raise_in=True)))
        .post("/api/openclaw/cloud/switch-tier", json={"tier": "free"})
        .json()
    )
    assert body["ok"] is False
    assert body["error"] == "switch_cloud_tier_failed"
    assert "boom-switch" in body["detail"]


def test_openclaw_cloud_switch_tier_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    resp = _client(_build_ctx_wave_nn()).post(
        "/api/openclaw/cloud/switch-tier", json={"tier": "paid"}
    )
    # ctx.assert_write_access raises HTTPException(403)
    assert resp.status_code == 403


# ---------- GET /api/openclaw/cloud/runtime-check (Wave NN) -----------------


class _RuntimeCheckClient:
    def __init__(self, *, raise_in: bool = False) -> None:
        self._raise = raise_in

    async def get_cloud_runtime_check(self) -> dict:
        if self._raise:
            raise RuntimeError("boom-runtime-check")
        return {"providers": [{"name": "google", "ok": True}]}


def test_openclaw_cloud_runtime_check_ok_invalidates_cache() -> None:
    invalidations: list[str] = []
    ctx = _build_ctx_wave_nn(
        openclaw=_RuntimeCheckClient(),
        invalidator_target=invalidations,
    )
    body = _client(ctx).get("/api/openclaw/cloud/runtime-check").json()
    assert body["available"] is True
    assert body["report"]["providers"][0]["ok"] is True
    assert invalidations == ["invalidated"]


def test_openclaw_cloud_runtime_check_no_client() -> None:
    body = (
        _client(_build_ctx_wave_nn(openclaw=None)).get("/api/openclaw/cloud/runtime-check").json()
    )
    assert body["available"] is False
    assert body["error"] == "openclaw_client_not_configured"


def test_openclaw_cloud_runtime_check_not_supported() -> None:
    body = (
        _client(_build_ctx_wave_nn(openclaw=_StubNoMethods()))
        .get("/api/openclaw/cloud/runtime-check")
        .json()
    )
    assert body["available"] is False
    assert body["error"] == "cloud_runtime_check_not_supported"


def test_openclaw_cloud_runtime_check_exception_graceful() -> None:
    body = (
        _client(_build_ctx_wave_nn(openclaw=_RuntimeCheckClient(raise_in=True)))
        .get("/api/openclaw/cloud/runtime-check")
        .json()
    )
    assert body["available"] is False
    assert body["error"] == "cloud_runtime_check_failed"


# ---------- GET /api/openclaw/channels/status (Wave NN) ---------------------


def test_openclaw_channels_status_helpers_missing() -> None:
    """Без cli_env / parse helpers — graceful system_error."""
    body = _client(_build_ctx_wave_nn()).get("/api/openclaw/channels/status").json()
    assert body["ok"] is False
    assert body["error"] == "system_error"


def test_openclaw_channels_status_subprocess_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Subprocess бросает — endpoint возвращает ok=False с error."""

    async def _raise(*args, **kwargs):
        raise FileNotFoundError("openclaw not found")

    monkeypatch.setattr("asyncio.create_subprocess_exec", _raise)

    ctx = _build_ctx_wave_nn(
        cli_env={"OPENCLAW_GATEWAY_TOKEN": "x"},
        parse_result={"channels": [], "warnings": [], "gateway_reachable": False},
    )
    body = _client(ctx).get("/api/openclaw/channels/status").json()
    assert body["ok"] is False
    assert body["error"] == "system_error"
    assert "openclaw" in body["detail"].lower()


# ---------- GET /api/openclaw/routing/effective (Wave NN) -------------------


class _StubRouterMinimal:
    def __init__(self) -> None:
        self.force_mode = "auto"
        self.models = {"chat": "google/gemini-2.5-flash"}
        self.routing_policy = "free_first_hybrid"
        self.cloud_soft_cap_reached = False
        self.local_engine = "lm_studio"

    def get_last_route(self) -> dict:
        return {}


def test_openclaw_routing_effective_no_router() -> None:
    ctx = _build_ctx_wave_nn(router_obj=None)
    # router not in deps
    body = _client(ctx).get("/api/openclaw/routing/effective").json()
    assert body["ok"] is False
    assert body["error"] == "router_unavailable"


def test_openclaw_routing_effective_auto_mode_local_available() -> None:
    async def _truth(_router, **kwargs):
        return {
            "engine": "lm_studio",
            "runtime_reachable": True,
            "active_model": "nvidia/nemotron-3-nano",
        }

    ctx = _build_ctx_wave_nn(
        router_obj=_StubRouterMinimal(),
        truth_helper=_truth,
    )
    body = _client(ctx).get("/api/openclaw/routing/effective").json()
    assert body["ok"] is True
    assert body["requested_mode"] == "auto"
    assert body["effective_mode"] == "auto"
    assert body["cloud_fallback"] is True
    assert body["cloud_fallback_state"] == "standby"
    assert body["active_slot_or_model"] == "nvidia/nemotron-3-nano"
    assert any("lm_studio" in note for note in body["decision_notes"])


def test_openclaw_routing_effective_force_local_disables_cloud() -> None:
    router_obj = _StubRouterMinimal()
    router_obj.force_mode = "force_local"

    async def _truth(_router, **kwargs):
        return {"engine": "lm_studio", "runtime_reachable": True, "active_model": "x"}

    ctx = _build_ctx_wave_nn(router_obj=router_obj, truth_helper=_truth)
    body = _client(ctx).get("/api/openclaw/routing/effective").json()
    assert body["ok"] is True
    assert body["effective_mode"] == "local"
    assert body["cloud_fallback"] is False
    assert body["cloud_fallback_state"] == "disabled"
