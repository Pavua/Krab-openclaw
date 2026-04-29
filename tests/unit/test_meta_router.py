# -*- coding: utf-8 -*-
"""
Phase 2 extraction — meta_router (Session 25).

Verify что extraction в src/modules/web_routers/meta_router.py
сохраняет существующий контракт endpoints /api/system/info и
/api/system/clock_drift.
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers.meta_router import router as meta_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(meta_router)
    return TestClient(app)


def test_system_info_returns_200() -> None:
    """GET /api/system/info → 200."""
    resp = _client().get("/api/system/info")
    assert resp.status_code == 200


def test_system_info_response_shape() -> None:
    """Все известные ключи присутствуют в ответе."""
    data = _client().get("/api/system/info").json()
    required = {
        "ok",
        "hostname",
        "platform",
        "python",
        "cpu_count",
        "ram_total_gb",
        "ram_used_pct",
        "disk_used_pct",
    }
    assert required.issubset(data.keys())
    assert data["ok"] is True
    assert isinstance(data["cpu_count"], int)
    assert isinstance(data["ram_total_gb"], (int, float))


def test_clock_drift_endpoint_calls_check_clock_drift() -> None:
    """GET /api/system/clock_drift → проксирует check_clock_drift result."""

    class _FakeResult:
        local_ts = 1234567890.5
        ntp_offset_sec = 0.05
        status = "ok"
        message = "drift within tolerance"

    async def _fake_check_clock_drift():
        return _FakeResult()

    with patch(
        "src.core.clock_drift_check.check_clock_drift",
        side_effect=_fake_check_clock_drift,
    ):
        resp = _client().get("/api/system/clock_drift")

    assert resp.status_code == 200
    data = resp.json()
    assert data["local_ts"] == 1234567890.5
    assert data["ntp_offset_sec"] == 0.05
    assert data["status"] == "ok"
    assert data["message"] == "drift within tolerance"
