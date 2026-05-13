# -*- coding: utf-8 -*-
"""
Unit tests для ``src/core/routing_transparency.py`` (Wave 244).

Покрывают:
- ``classify_model_resolution`` (direct / openclaw / unknown)
- ``resolve_backend_for_model`` (mlx-local, lm-studio, cloud → gateway)
- ``read_active_model_file`` (read OK + missing → empty dict)
- ``get_actual_routing_state`` (агрегация с warnings при mismatch)
- ``get_actual_routing_state`` (cloud picked → warning о OpenClaw routing)
- HTTP endpoint ``/api/admin/routing-active``
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.routing_transparency import (
    classify_model_resolution,
    get_actual_routing_state,
    read_active_model_file,
    resolve_backend_for_model,
)
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.models_admin_router import build_models_admin_router


def test_classify_model_resolution_direct_and_openclaw() -> None:
    """MLX/LM Studio prefixes → direct; cloud/CLI → openclaw; пусто → unknown."""
    # MLX local — direct.
    assert classify_model_resolution("mlx-local-kv4/gemma-4-26b") == "direct"
    # LM Studio variants — все direct.
    assert classify_model_resolution("lm-studio-local/qwen-7b") == "direct"
    assert classify_model_resolution("lm-studio/qwen-7b") == "direct"
    # Cloud/CLI — openclaw.
    assert classify_model_resolution("google-vertex/gemini-3-pro-preview") == "openclaw"
    assert classify_model_resolution("codex-cli/gpt-5.5") == "openclaw"
    assert classify_model_resolution("anthropic-vertex/claude-opus-4") == "openclaw"
    # Пусто или None.
    assert classify_model_resolution("") == "unknown"
    assert classify_model_resolution(None) == "unknown"  # type: ignore[arg-type]


def test_resolve_backend_for_model_mlx_local() -> None:
    """mlx-local-kv4/* резолвится в :8088 (или MLX_LOCAL_BACKEND_URL env)."""
    result = resolve_backend_for_model("mlx-local-kv4/gemma-4-26b")
    assert result["resolution"] == "direct"
    assert result["backend_kind"] == "mlx-local"
    assert "8088" in result["backend_url"] or result["backend_url"].endswith(":8088")
    assert "MLX" in result["note"] or "OpenClaw" in result["note"]


def test_resolve_backend_for_model_cloud_via_gateway() -> None:
    """Cloud модели → openclaw-gateway с заметкой о non-determinism."""
    result = resolve_backend_for_model("google-vertex/gemini-3-pro-preview")
    assert result["resolution"] == "openclaw"
    assert result["backend_kind"] == "openclaw-gateway"
    # URL должен быть из config.OPENCLAW_URL (порт 18789 по дефолту).
    assert result["backend_url"]
    # Note должна явно сказать "Gateway".
    assert "Gateway" in result["note"]


def test_read_active_model_file_ok_and_missing(tmp_path: Path) -> None:
    """Чтение existing JSON + missing файла = empty dict."""
    # Сценарий 1: реальный файл.
    target = tmp_path / "active_model.json"
    payload = {
        "model": "mlx-local-kv4/gemma-4-26b",
        "switched_at": 1778715157,
        "switched_by": "manual_terminal",
        "reason": "force MLX direct test",
    }
    target.write_text(json.dumps(payload), encoding="utf-8")

    result = read_active_model_file(path=target)
    assert result["model"] == "mlx-local-kv4/gemma-4-26b"
    assert result["switched_at"] == 1778715157
    assert result["switched_by"] == "manual_terminal"
    assert result["reason"] == "force MLX direct test"

    # Сценарий 2: missing — пустой dict, без exception.
    missing = tmp_path / "nope.json"
    assert read_active_model_file(path=missing) == {}


def test_get_actual_routing_state_cloud_warning_and_mismatch(tmp_path: Path) -> None:
    """Cloud picked + mismatch с actually_used → 2 warnings."""
    # Готовим fake active_model.json через patch _ACTIVE_MODEL_PATH.
    active_file = tmp_path / "active_model.json"
    active_file.write_text(
        json.dumps(
            {
                "model": "google-vertex/gemini-3-pro-preview",
                "switched_at": 1778715157,
                "switched_by": "owner_panel",
                "reason": "test",
            }
        ),
        encoding="utf-8",
    )

    fake_route = {
        "timestamp": 1778715200,
        "channel": "cloud",
        "provider": "codex-cli",
        "model": "codex-cli/gpt-5.5",  # mismatch!
        "status": "ok",
    }

    with (
        patch("src.core.routing_transparency._ACTIVE_MODEL_PATH", active_file),
        patch(
            "src.core.routing_transparency._read_last_runtime_route",
            return_value=fake_route,
        ),
    ):
        state = get_actual_routing_state()

    assert state["ok"] is True
    assert state["picked"]["model"] == "google-vertex/gemini-3-pro-preview"
    assert state["picked"]["switched_by"] == "owner_panel"
    assert state["will_send_to"]["resolution"] == "openclaw"
    assert state["actually_used"]["model"] == "codex-cli/gpt-5.5"
    assert state["actually_used"]["provider"] == "codex-cli"

    # Должны быть две warnings: cloud routing + mismatch.
    warnings_text = " ".join(state["warnings"])
    assert "OpenClaw" in warnings_text or "Gateway" in warnings_text
    assert "не совпадает" in warnings_text or "перевыбрал" in warnings_text


def test_get_actual_routing_state_mlx_direct_no_cloud_warning(tmp_path: Path) -> None:
    """MLX picked + актуальный route совпадает → нет cloud-warning."""
    active_file = tmp_path / "active_model.json"
    active_file.write_text(
        json.dumps(
            {
                "model": "mlx-local-kv4/gemma-4-26b",
                "switched_at": 1778715157,
                "switched_by": "owner_panel",
                "reason": "stable local",
            }
        ),
        encoding="utf-8",
    )

    fake_route = {
        "timestamp": 1778715200,
        "channel": "local",
        "provider": "mlx-local-kv4",
        "model": "mlx-local-kv4/gemma-4-26b",
        "status": "ok",
    }

    with (
        patch("src.core.routing_transparency._ACTIVE_MODEL_PATH", active_file),
        patch(
            "src.core.routing_transparency._read_last_runtime_route",
            return_value=fake_route,
        ),
    ):
        state = get_actual_routing_state()

    assert state["will_send_to"]["resolution"] == "direct"
    assert state["will_send_to"]["backend_kind"] == "mlx-local"
    # Cloud-warning отсутствует.
    warnings_text = " ".join(state["warnings"])
    assert "OpenClaw" not in warnings_text
    # Mismatch warning тоже отсутствует — picked == actually_used.
    assert "перевыбрал" not in warnings_text


def test_routing_active_endpoint_returns_state(tmp_path: Path) -> None:
    """GET /api/admin/routing-active возвращает контракт из get_actual_routing_state."""
    active_file = tmp_path / "active_model.json"
    active_file.write_text(
        json.dumps(
            {
                "model": "mlx-local-kv4/gemma-4-26b",
                "switched_at": 1778715157,
                "switched_by": "manual_terminal",
                "reason": "force MLX",
            }
        ),
        encoding="utf-8",
    )

    fake_route: dict[str, Any] = {
        "timestamp": 1778715200,
        "channel": "local",
        "provider": "mlx-local-kv4",
        "model": "mlx-local-kv4/gemma-4-26b",
        "status": "ok",
    }

    ctx = RouterContext(
        deps={},
        project_root=tmp_path,
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *args, **kwargs: None,
    )
    app = FastAPI()
    app.include_router(build_models_admin_router(ctx))

    with (
        patch("src.core.routing_transparency._ACTIVE_MODEL_PATH", active_file),
        patch(
            "src.core.routing_transparency._read_last_runtime_route",
            return_value=fake_route,
        ),
    ):
        client = TestClient(app)
        resp = client.get("/api/admin/routing-active")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["picked"]["model"] == "mlx-local-kv4/gemma-4-26b"
    assert body["will_send_to"]["resolution"] == "direct"
    assert body["actually_used"]["model"] == "mlx-local-kv4/gemma-4-26b"
    assert isinstance(body["warnings"], list)
