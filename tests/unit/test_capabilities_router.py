# -*- coding: utf-8 -*-
"""
Unit tests для capabilities_router (Phase 2 Wave R, Session 25).

RouterContext-based extraction. Создаёт RouterContext напрямую без полного
WebApp instance — proves router self-contained.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.capabilities_router import build_capabilities_router

_FAKE_MATRIX = {
    "roles": {
        "owner": {"can_write": True},
        "full": {"can_write": True},
        "partial": {"can_write": False},
        "guest": {"can_write": False},
    },
    "guardrails": {"web_write_requires_key": False},
    "summary": {"role_count": 4},
    "collected_at": "2026-04-26T00:00:00Z",
}


def _build_ctx(
    *,
    deps: dict[str, Any] | None = None,
    runtime_lite: dict[str, Any] | None = None,
) -> RouterContext:
    async def _provider() -> dict[str, Any]:
        return runtime_lite or {}

    return RouterContext(
        deps=deps or {},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
        runtime_lite_provider=_provider if runtime_lite is not None else None,
    )


def _client(ctx: RouterContext) -> TestClient:
    app = FastAPI()
    app.include_router(build_capabilities_router(ctx))
    return TestClient(app)


# ---------------------------------------------------------------------------
# /api/capabilities/registry
# ---------------------------------------------------------------------------


def test_capabilities_registry_ok() -> None:
    """/api/capabilities/registry проходит helper и возвращает payload."""
    expected = {"ok": True, "contours": {}, "summary": {}, "operator": {}}

    async def _helper(*, runtime_lite=None):
        return expected

    ctx = _build_ctx(deps={"capability_registry_snapshot_helper": _helper})
    resp = _client(ctx).get("/api/capabilities/registry")
    assert resp.status_code == 200
    assert resp.json() == expected


def test_capabilities_registry_helper_missing() -> None:
    """Без helper'а endpoint возвращает структурированный error."""
    ctx = _build_ctx(deps={})
    resp = _client(ctx).get("/api/capabilities/registry")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "helper" in data["error"]


def test_capabilities_registry_passes_runtime_lite() -> None:
    """runtime_lite_provider пробрасывается в helper."""
    captured: dict[str, Any] = {}

    async def _helper(*, runtime_lite=None):
        captured["runtime_lite"] = runtime_lite
        return {"ok": True}

    payload = {"lmstudio_model_state": "loaded"}
    ctx = _build_ctx(
        deps={"capability_registry_snapshot_helper": _helper},
        runtime_lite=payload,
    )
    _client(ctx).get("/api/capabilities/registry")
    assert captured["runtime_lite"] == payload


# ---------------------------------------------------------------------------
# /api/channels/capabilities
# ---------------------------------------------------------------------------


def test_channel_capabilities_ok() -> None:
    """/api/channels/capabilities оборачивает helper в {ok, channel_capabilities}."""
    chan_snap = {"primary": {"status": "live"}, "reserve": {"status": "ready"}}

    def _helper(*, runtime_lite=None, policy_matrix=None):
        return chan_snap

    ctx = _build_ctx(deps={"channel_capabilities_snapshot_helper": _helper})
    with patch(
        "src.modules.web_routers._helpers.collect_policy_matrix_snapshot",
        return_value=_FAKE_MATRIX,
    ):
        resp = _client(ctx).get("/api/channels/capabilities")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["channel_capabilities"] == chan_snap


def test_channel_capabilities_helper_missing() -> None:
    """Без helper'а — error."""
    ctx = _build_ctx(deps={})
    with patch(
        "src.modules.web_routers._helpers.collect_policy_matrix_snapshot",
        return_value=_FAKE_MATRIX,
    ):
        resp = _client(ctx).get("/api/channels/capabilities")
    data = resp.json()
    assert data["ok"] is False
    assert "helper" in data["error"]


def test_channel_capabilities_passes_runtime_lite_and_matrix() -> None:
    """runtime_lite + policy_matrix пробрасываются в helper."""
    captured: dict[str, Any] = {}

    def _helper(*, runtime_lite=None, policy_matrix=None):
        captured["runtime_lite"] = runtime_lite
        captured["policy_matrix"] = policy_matrix
        return {}

    payload = {"foo": "bar"}
    ctx = _build_ctx(
        deps={"channel_capabilities_snapshot_helper": _helper},
        runtime_lite=payload,
    )
    with patch(
        "src.modules.web_routers._helpers.collect_policy_matrix_snapshot",
        return_value=_FAKE_MATRIX,
    ):
        _client(ctx).get("/api/channels/capabilities")
    assert captured["runtime_lite"] == payload
    assert captured["policy_matrix"] == _FAKE_MATRIX


# ---------------------------------------------------------------------------
# /api/policy
# ---------------------------------------------------------------------------


class _FakeAIRuntime:
    def get_policy_snapshot(self) -> dict[str, Any]:
        return {"queue": {"depth": 0}, "guardrails": {"strict": True}}


def test_policy_ok_with_ai_runtime() -> None:
    """/api/policy: ai_runtime + policy_matrix → ok=True."""
    ctx = _build_ctx(deps={"ai_runtime": _FakeAIRuntime()})
    with patch(
        "src.modules.web_routers._helpers.collect_policy_matrix_snapshot",
        return_value=_FAKE_MATRIX,
    ):
        resp = _client(ctx).get("/api/policy")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["policy"] == {"queue": {"depth": 0}, "guardrails": {"strict": True}}
    assert data["policy_matrix"] == _FAKE_MATRIX


def test_policy_no_ai_runtime() -> None:
    """Без ai_runtime: ok=False, но policy_matrix всё равно отдаётся."""
    ctx = _build_ctx(deps={})
    with patch(
        "src.modules.web_routers._helpers.collect_policy_matrix_snapshot",
        return_value=_FAKE_MATRIX,
    ):
        resp = _client(ctx).get("/api/policy")
    data = resp.json()
    assert data["ok"] is False
    assert data["error"] == "ai_runtime_not_configured"
    assert data["policy_matrix"] == _FAKE_MATRIX
