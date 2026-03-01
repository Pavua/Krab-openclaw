# -*- coding: utf-8 -*-
"""E2E smoke (in-process): OpenClaw endpoints панели не должны отдавать 500."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.modules.web_app import WebApp


class _DummyOpenClawClient:
    """Тестовый клиент без deep API, чтобы проверить graceful fallback."""

    async def health_check(self) -> bool:
        return True


def _make_web_client() -> TestClient:
    deps = {
        "router": None,
        "openclaw_client": _DummyOpenClawClient(),
        "black_box": None,
        "health_service": None,
        "provisioning_service": None,
        "ai_runtime": None,
        "reaction_engine": None,
        "voice_gateway_client": None,
        "krab_ear_client": None,
        "perceptor": None,
        "watchdog": None,
        "queue": None,
    }
    app = WebApp(deps, port=18080, host="127.0.0.1")
    return TestClient(app.app)


def test_web_panel_openclaw_endpoints_no_500() -> None:
    client = _make_web_client()

    endpoints = [
        "/api/openclaw/report",
        "/api/openclaw/deep-check",
        "/api/openclaw/remediation-plan",
        "/api/openclaw/cloud/runtime-check",
        "/api/openclaw/model-autoswitch/status",
    ]

    for endpoint in endpoints:
        response = client.get(endpoint)
        assert response.status_code != 500, f"endpoint {endpoint} вернул 500"
        assert "application/json" in (response.headers.get("content-type") or "").lower()
