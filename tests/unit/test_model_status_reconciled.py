# -*- coding: utf-8 -*-
"""
Тесты Wave 24-E: reconciled_state в GET /api/model/status.

5 тестов покрывают:
1. configured_primary из openclaw.json
2. last_executed из последнего маршрута
3. active_display для случая нет-маршрута
4. active_display для successful route
5. active_display для failed route
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.model_router import build_model_router

# ---------------------------------------------------------------------------
# Вспомогательный factory контекста
# ---------------------------------------------------------------------------


def _make_ctx() -> RouterContext:
    """Создаёт минимальный RouterContext для тестов model router."""
    return RouterContext(
        deps={"router": MagicMock()},
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
    )


def _make_client(
    last_route: dict[str, Any],
    primary_model: str = "google/gemini-3-pro-preview",
) -> tuple[Any, "TestClient"]:
    """Строит FastAPI app + TestClient с патчами."""
    fake_oc = MagicMock()
    fake_oc.get_last_runtime_route.return_value = last_route

    fake_mm = MagicMock()
    fake_mm.active_model_id = primary_model

    ctx = _make_ctx()
    app = FastAPI()
    app.include_router(build_model_router(ctx))

    # TestClient без raise_server_exceptions — смотрим ответ API
    client = TestClient(app, raise_server_exceptions=False)
    return fake_oc, fake_mm, client


# ---------------------------------------------------------------------------
# Тест 1: configured_primary берётся из openclaw.json (через хелпер)
# ---------------------------------------------------------------------------


def test_reconciled_configured_primary() -> None:
    """reconciled_state.configured_primary отражает live primary из openclaw.json."""
    primary = "google/gemini-3-pro-preview"

    fake_oc = MagicMock()
    fake_oc.get_last_runtime_route.return_value = {}

    fake_mm = MagicMock()
    fake_mm.active_model_id = primary

    ctx = _make_ctx()
    app = FastAPI()
    app.include_router(build_model_router(ctx))

    with (
        patch("src.core.openclaw_runtime_models.get_runtime_primary_model", return_value=primary),
        patch("src.openclaw_client.openclaw_client", fake_oc),
        patch("src.model_manager.model_manager", fake_mm),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/model/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    rs = data["reconciled_state"]
    assert rs["configured_primary"] == primary


# ---------------------------------------------------------------------------
# Тест 2: last_executed берётся из последнего маршрута
# ---------------------------------------------------------------------------


def test_reconciled_last_executed_from_route() -> None:
    """reconciled_state.last_executed отражает model из route."""
    primary = "google/gemini-3-pro-preview"
    route_model = "openai/gpt-5.5"
    ts = int(time.time()) - 30  # 30s назад

    fake_oc = MagicMock()
    fake_oc.get_last_runtime_route.return_value = {
        "model": route_model,
        "status": "ok",
        "timestamp": ts,
        "provider": "openai",
    }

    fake_mm = MagicMock()
    fake_mm.active_model_id = primary

    ctx = _make_ctx()
    app = FastAPI()
    app.include_router(build_model_router(ctx))

    with (
        patch(
            "src.core.openclaw_runtime_models.get_runtime_primary_model",
            return_value=primary,
        ),
        patch("src.openclaw_client.openclaw_client", fake_oc),
        patch("src.model_manager.model_manager", fake_mm),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/model/status")

    assert resp.status_code == 200
    rs = resp.json()["reconciled_state"]
    assert rs["last_executed"] == route_model
    assert rs["last_executed_status"] == "success"
    # ISO timestamp должен быть строкой
    assert isinstance(rs["last_executed_at"], str)
    assert "T" in rs["last_executed_at"]


# ---------------------------------------------------------------------------
# Тест 3: active_display для no-route случая
# ---------------------------------------------------------------------------


def test_reconciled_active_display_no_route() -> None:
    """active_display корректен когда нет последнего маршрута."""
    primary = "google/gemini-3-pro-preview"

    fake_oc = MagicMock()
    fake_oc.get_last_runtime_route.return_value = {}  # нет маршрута

    fake_mm = MagicMock()
    fake_mm.active_model_id = primary

    ctx = _make_ctx()
    app = FastAPI()
    app.include_router(build_model_router(ctx))

    with (
        patch("src.core.openclaw_runtime_models.get_runtime_primary_model", return_value=primary),
        patch("src.openclaw_client.openclaw_client", fake_oc),
        patch("src.model_manager.model_manager", fake_mm),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/model/status")

    assert resp.status_code == 200
    rs = resp.json()["reconciled_state"]
    assert rs["last_executed"] is None
    assert rs["last_executed_status"] == "none"
    # active_display содержит primary и префикс "Active:"
    assert primary in rs["active_display"]
    assert "Active:" in rs["active_display"]


# ---------------------------------------------------------------------------
# Тест 4: active_display для successful route (другая модель)
# ---------------------------------------------------------------------------


def test_reconciled_active_display_success_route() -> None:
    """active_display для success route содержит ✓ и last: указание."""
    primary = "google/gemini-3-pro-preview"
    last_model = "openai/gpt-5.5"
    ts = int(time.time()) - 120  # 2min ago

    fake_oc = MagicMock()
    fake_oc.get_last_runtime_route.return_value = {
        "model": last_model,
        "status": "ok",
        "timestamp": ts,
        "provider": "openai",
    }

    fake_mm = MagicMock()
    fake_mm.active_model_id = primary

    ctx = _make_ctx()
    app = FastAPI()
    app.include_router(build_model_router(ctx))

    with (
        patch("src.core.openclaw_runtime_models.get_runtime_primary_model", return_value=primary),
        patch("src.openclaw_client.openclaw_client", fake_oc),
        patch("src.model_manager.model_manager", fake_mm),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/model/status")

    assert resp.status_code == 200
    rs = resp.json()["reconciled_state"]
    display = rs["active_display"]
    # Шаблон: "Active: <primary> (last: <last_model> ✓ Xm ago)"
    assert "Active:" in display
    assert primary in display
    assert last_model in display
    assert "✓" in display


# ---------------------------------------------------------------------------
# Тест 5: active_display для failed route
# ---------------------------------------------------------------------------


def test_reconciled_active_display_failed_route() -> None:
    """active_display для failed route содержит ✗ символ."""
    primary = "google/gemini-3-pro-preview"
    last_model = "openai/gpt-5.5"
    ts = int(time.time()) - 300  # 5min ago

    fake_oc = MagicMock()
    fake_oc.get_last_runtime_route.return_value = {
        "model": last_model,
        "status": "error",
        "timestamp": ts,
        "provider": "openai",
    }

    fake_mm = MagicMock()
    fake_mm.active_model_id = primary

    ctx = _make_ctx()
    app = FastAPI()
    app.include_router(build_model_router(ctx))

    with (
        patch("src.core.openclaw_runtime_models.get_runtime_primary_model", return_value=primary),
        patch("src.openclaw_client.openclaw_client", fake_oc),
        patch("src.model_manager.model_manager", fake_mm),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/model/status")

    assert resp.status_code == 200
    data = resp.json()
    rs = data["reconciled_state"]

    # Статус failed
    assert rs["last_executed_status"] == "failed"

    display = rs["active_display"]
    assert "Active:" in display
    assert primary in display
    assert last_model in display
    assert "✗" in display

    # Backward compat: старые поля сохранены
    assert "route" in data
    assert "active_model" in data
    assert "provider" in data
