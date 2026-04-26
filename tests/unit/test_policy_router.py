# -*- coding: utf-8 -*-
"""
Unit tests для policy_router (Phase 2 Wave I, Session 25).

RouterContext-based extraction. Создаёт RouterContext напрямую
без полного WebApp instance — proves router self-contained.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.policy_router import build_policy_router


def _build_ctx(runtime_lite: dict[str, Any] | None = None) -> RouterContext:
    async def _provider() -> dict[str, Any]:
        return runtime_lite or {}

    return RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
        runtime_lite_provider=_provider if runtime_lite is not None else None,
    )


def _client(ctx: RouterContext) -> TestClient:
    app = FastAPI()
    app.include_router(build_policy_router(ctx))
    return TestClient(app)


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


def test_policy_matrix_ok() -> None:
    """/api/policy/matrix возвращает ok=True."""
    with patch(
        "src.modules.web_routers._helpers.collect_policy_matrix_snapshot",
        return_value=_FAKE_MATRIX,
    ):
        resp = _client(_build_ctx()).get("/api/policy/matrix")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["policy_matrix"] == _FAKE_MATRIX


def test_policy_matrix_uses_runtime_lite_provider() -> None:
    """runtime_lite_provider передаёт данные в helper."""
    captured: dict[str, Any] = {}

    def _capture(*, runtime_lite=None):
        captured["runtime_lite"] = runtime_lite
        return _FAKE_MATRIX

    runtime_lite_payload = {"lmstudio_model_state": "loaded"}
    ctx = _build_ctx(runtime_lite=runtime_lite_payload)
    with patch(
        "src.modules.web_routers._helpers.collect_policy_matrix_snapshot",
        side_effect=_capture,
    ):
        resp = _client(ctx).get("/api/policy/matrix")
    assert resp.status_code == 200
    assert captured["runtime_lite"] == runtime_lite_payload


def test_policy_matrix_no_provider_passes_empty() -> None:
    """Без provider'а helper получает пустой dict."""
    captured: dict[str, Any] = {}

    def _capture(*, runtime_lite=None):
        captured["runtime_lite"] = runtime_lite
        return _FAKE_MATRIX

    with patch(
        "src.modules.web_routers._helpers.collect_policy_matrix_snapshot",
        side_effect=_capture,
    ):
        resp = _client(_build_ctx()).get("/api/policy/matrix")
    assert resp.status_code == 200
    # collect_runtime_lite() возвращает {} когда provider не задан
    assert captured["runtime_lite"] == {}


def test_policy_matrix_response_shape() -> None:
    """Ответ всегда содержит ключи ok+policy_matrix."""
    with patch(
        "src.modules.web_routers._helpers.collect_policy_matrix_snapshot",
        return_value=_FAKE_MATRIX,
    ):
        data = _client(_build_ctx()).get("/api/policy/matrix").json()
    assert set(data.keys()) == {"ok", "policy_matrix"}


def test_policy_matrix_roles_passthrough() -> None:
    """Все роли из helper пробрасываются 1:1."""
    with patch(
        "src.modules.web_routers._helpers.collect_policy_matrix_snapshot",
        return_value=_FAKE_MATRIX,
    ):
        data = _client(_build_ctx()).get("/api/policy/matrix").json()
    assert set(data["policy_matrix"]["roles"].keys()) == {
        "owner",
        "full",
        "partial",
        "guest",
    }
