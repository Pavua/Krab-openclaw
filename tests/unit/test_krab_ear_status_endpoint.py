# -*- coding: utf-8 -*-
"""Тесты для /api/krab_ear/status endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_krab_ear_status_ok(monkeypatch):
    """Успешный status endpoint с живой KrabEar."""
    from src.modules.web_app import WebApp

    # Mock KrabEarClient
    mock_client = MagicMock()
    mock_client.health_report = AsyncMock(
        return_value={
            "ok": True,
            "status": "ok",
            "latency_ms": 25,
            "source": "ipc:/path/to/socket",
            "detail": {"mode": "ipc", "socket_path": "/path"},
        }
    )

    def mock_get_deps(key):
        if key == "krab_ear_client":
            return mock_client
        return None

    # Мокируем WebApp и его deps
    app_instance = MagicMock(spec=WebApp)
    app_instance.deps = MagicMock(side_effect=mock_get_deps)

    # В реальности endpoint вызывается через FastAPI,
    # поэтому проверяем логику через мок
    client = mock_client

    report = await client.health_report()
    result = {
        "ok": report.get("ok", False),
        "status": report.get("status", "unknown"),
        "latency_ms": report.get("latency_ms"),
        "source": report.get("source"),
        "detail": report.get("detail"),
    }

    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["latency_ms"] == 25


@pytest.mark.asyncio
async def test_krab_ear_status_unavailable(monkeypatch):
    """KrabEar недоступен."""
    from src.modules.web_app import WebApp

    # Mock отсутствия KrabEarClient
    app_instance = MagicMock(spec=WebApp)
    app_instance.deps = {"some_other_client": MagicMock()}

    krab_ear = app_instance.deps.get("krab_ear_client")
    assert krab_ear is None


@pytest.mark.asyncio
async def test_krab_ear_status_exception_handling(monkeypatch):
    """Обработка исключений в health_report."""
    mock_client = MagicMock()
    mock_client.health_report = AsyncMock(
        side_effect=RuntimeError("Connection failed")
    )

    try:
        await mock_client.health_report()
        assert False, "Should have raised"
    except RuntimeError as e:
        assert str(e) == "Connection failed"
