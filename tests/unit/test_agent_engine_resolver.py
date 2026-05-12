# -*- coding: utf-8 -*-
"""Tests for get_engine_for_route() (Wave 17-B, Hermes Phase C).

Покрывает: dispatch OFF default, dispatch ON → openclaw, dispatch ON → hermes healthy,
dispatch ON → hermes unhealthy fallback, dispatch ON → bridge error fallback.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.agent_engine import EngineHealth
from src.core.agent_engine_openclaw import OpenClawAdapter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_openclaw():
    """Mock OpenClawClient с корректным health_check."""
    client = MagicMock()
    client.health_check = AsyncMock(return_value=True)
    return client


def _healthy_hermes_bridge():
    """Mock HermesACPBridge с healthy probe."""
    bridge = MagicMock()
    bridge.health = AsyncMock(
        return_value=EngineHealth(engine="hermes", is_healthy=True, last_check_at="2026-01-01T00:00:00+00:00")
    )
    bridge.kind = "hermes"
    return bridge


def _unhealthy_hermes_bridge(error: str = "subprocess failed to start"):
    """Mock HermesACPBridge с unhealthy probe."""
    bridge = MagicMock()
    bridge.health = AsyncMock(
        return_value=EngineHealth(
            engine="hermes", is_healthy=False, error=error, last_check_at="2026-01-01T00:00:00+00:00"
        )
    )
    bridge.kind = "hermes"
    return bridge


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_off_by_default(monkeypatch):
    """При KRAB_AGENT_ENGINE_DISPATCH_ENABLED=0 всегда возвращает OpenClaw."""
    monkeypatch.delenv("KRAB_AGENT_ENGINE_DISPATCH_ENABLED", raising=False)

    from src.core.agent_engine_resolver import get_engine_for_route

    client = _mock_openclaw()
    engine, requested, actual = await get_engine_for_route(
        chat_id="123", openclaw_client=client
    )

    assert isinstance(engine, OpenClawAdapter)
    assert requested == "openclaw"
    assert actual == "openclaw"


@pytest.mark.asyncio
async def test_dispatch_on_openclaw_route(monkeypatch):
    """Dispatch ON + engine resolved = openclaw → OpenClawAdapter."""
    monkeypatch.setenv("KRAB_AGENT_ENGINE_DISPATCH_ENABLED", "1")
    monkeypatch.setenv("KRAB_AGENT_ENGINE", "openclaw")

    from src.core.agent_engine_resolver import get_engine_for_route

    client = _mock_openclaw()
    engine, requested, actual = await get_engine_for_route(
        chat_id="123", openclaw_client=client
    )

    assert isinstance(engine, OpenClawAdapter)
    assert actual == "openclaw"


@pytest.mark.asyncio
async def test_dispatch_on_hermes_healthy(monkeypatch):
    """Dispatch ON + hermes healthy → возвращает bridge (hermes engine)."""
    monkeypatch.setenv("KRAB_AGENT_ENGINE_DISPATCH_ENABLED", "1")
    monkeypatch.setenv("KRAB_AGENT_ENGINE", "hermes")

    bridge = _healthy_hermes_bridge()

    # Wave 16-P: get_hermes_bridge async → нужно patch на async функцию (new=, не return_value).
    async def _fake_get_bridge():
        return bridge

    with patch("src.integrations.hermes_acp_bridge.get_hermes_bridge", new=_fake_get_bridge):
        import importlib

        import src.core.agent_engine_resolver as mod
        importlib.reload(mod)

        engine, requested, actual = await mod.get_engine_for_route(
            chat_id="123", openclaw_client=_mock_openclaw()
        )

    assert engine is bridge
    assert requested == "hermes"
    assert actual == "hermes"


@pytest.mark.asyncio
async def test_dispatch_on_hermes_unhealthy_fallback(monkeypatch):
    """Dispatch ON + hermes unhealthy → fallback на OpenClaw."""
    monkeypatch.setenv("KRAB_AGENT_ENGINE_DISPATCH_ENABLED", "1")
    monkeypatch.setenv("KRAB_AGENT_ENGINE", "hermes")

    bridge = _unhealthy_hermes_bridge()

    async def _fake_get_bridge():
        return bridge

    with patch("src.integrations.hermes_acp_bridge.get_hermes_bridge", new=_fake_get_bridge):
        import importlib

        import src.core.agent_engine_resolver as mod
        importlib.reload(mod)

        client = _mock_openclaw()
        engine, requested, actual = await mod.get_engine_for_route(
            chat_id="123", openclaw_client=client
        )

    assert isinstance(engine, OpenClawAdapter)
    assert requested == "hermes"
    assert actual == "openclaw"  # fallback!


@pytest.mark.asyncio
async def test_dispatch_on_bridge_import_error_fallback(monkeypatch):
    """Dispatch ON + bridge вызывает исключение → fallback на OpenClaw (fail-safe)."""
    monkeypatch.setenv("KRAB_AGENT_ENGINE_DISPATCH_ENABLED", "1")
    monkeypatch.setenv("KRAB_AGENT_ENGINE", "hermes")

    with patch(
        "src.integrations.hermes_acp_bridge.get_hermes_bridge",
        side_effect=RuntimeError("bridge error"),
    ):
        import importlib

        import src.core.agent_engine_resolver as mod
        importlib.reload(mod)

        client = _mock_openclaw()
        engine, requested, actual = await mod.get_engine_for_route(
            chat_id="123", openclaw_client=client
        )

    assert isinstance(engine, OpenClawAdapter)
    assert actual == "openclaw"
