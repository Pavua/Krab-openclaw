# -*- coding: utf-8 -*-
"""
Wave 249: tests для wired ``GET /api/admin/routing-active`` endpoint.

Wave 244 создал модуль ``src/core/routing_transparency.py`` и сам endpoint
в ``models_admin_router.py``, но patch на регистрацию router'а в web_app.py
не был применён — endpoint отдавал 404. Wave 249 wires endpoint через
``model_router.include_router(build_models_admin_router(ctx))`` + добавляет
``/admin/models`` HTML панель.

Покрытие:
- endpoint доступен через зарегистрированный model_router (200 OK)
- JSON structure: ok, picked, will_send_to, actually_used, warnings
- error handling в transparency module не валит endpoint
- ``/admin/models`` HTML page возвращает 200 + ссылку на /api/admin/routing-active
- integration test: build_model_router сам включает models_admin_router
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.model_router import build_model_router


def _build_ctx() -> RouterContext:
    """Минимальный ctx для model_router (deps пустой, endpoints нужны только
    routing-active + /admin/models, остальной model_router фоновый)."""
    return RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(build_model_router(_build_ctx()))
    return TestClient(app)


# ---------- core endpoint ---------------------------------------------------


def test_routing_active_endpoint_returns_200_via_model_router() -> None:
    """После Wave 249 wiring endpoint доступен через model_router."""
    resp = _client().get("/api/admin/routing-active")
    assert resp.status_code == 200, resp.text


def test_routing_active_returns_expected_json_shape() -> None:
    """Контракт: ok, picked, will_send_to, actually_used, warnings."""
    resp = _client().get("/api/admin/routing-active")
    data = resp.json()
    assert data["ok"] is True
    assert "picked" in data
    assert "will_send_to" in data
    assert "actually_used" in data
    assert "warnings" in data
    assert isinstance(data["warnings"], list)
    # picked sub-shape
    picked = data["picked"]
    for key in ("model", "switched_at", "switched_by", "reason"):
        assert key in picked
    # will_send_to sub-shape
    wst = data["will_send_to"]
    for key in ("resolution", "backend_url", "backend_kind", "note"):
        assert key in wst
    # actually_used sub-shape
    au = data["actually_used"]
    for key in ("model", "provider", "channel", "status", "at"):
        assert key in au


def test_routing_active_with_picked_model_resolves_backend() -> None:
    """Если active_model.json содержит mlx-local-kv4/* — resolution=direct."""
    fake_state = {
        "ok": True,
        "picked": {
            "model": "mlx-local-kv4/gemma-4-26b",
            "switched_at": "2026-05-14T00:00:00+00:00",
            "switched_by": "owner",
            "reason": "primary pick",
        },
        "will_send_to": {
            "resolution": "direct",
            "backend_url": "http://127.0.0.1:8088",
            "backend_kind": "mlx-local",
            "note": "Krab отправит запрос напрямую в MLX backend.",
        },
        "actually_used": {
            "model": "mlx-local-kv4/gemma-4-26b",
            "provider": "mlx-local",
            "channel": "direct",
            "status": "ok",
            "at": None,
        },
        "warnings": [],
    }
    with patch(
        "src.core.routing_transparency.get_actual_routing_state",
        return_value=fake_state,
    ):
        resp = _client().get("/api/admin/routing-active")
    assert resp.status_code == 200
    data = resp.json()
    assert data["picked"]["model"] == "mlx-local-kv4/gemma-4-26b"
    assert data["will_send_to"]["resolution"] == "direct"


def test_routing_active_swallows_exceptions_from_transparency_module() -> None:
    """Wave 249: если get_actual_routing_state() бросает — endpoint должен
    вернуть 200 c ``ok=False`` и текстом ошибки (не 500), чтобы UI panel
    мог нормально отрендерить error-state.
    """
    with patch(
        "src.core.routing_transparency.get_actual_routing_state",
        side_effect=RuntimeError("boom"),
    ):
        resp = _client().get("/api/admin/routing-active")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is False
    assert "boom" in data.get("error", "")


# ---------- /admin/models HTML page ----------------------------------------


def test_admin_models_html_page_returns_200_and_html() -> None:
    """Wave 249: страница panel'а — read-only HTML, no auth."""
    resp = _client().get("/admin/models")
    assert resp.status_code == 200
    body = resp.text
    # Минимальные структурные маркеры.
    assert "<!doctype html>" in body.lower() or "<html" in body.lower()
    # Ссылка на endpoint (fetch URL внутри JS).
    assert "/api/admin/routing-active" in body
    # Заголовок панели.
    assert "Куда реально пойдёт запрос" in body
