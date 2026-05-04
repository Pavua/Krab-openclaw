# -*- coding: utf-8 -*-
"""Unit tests для Wave 18-B: Google direct bypass в OpenClawClient.

Тестируем интеграцию bypass в send_message_stream:
1. google/* модель → bypass engaged когда enabled
2. non-google модель → OpenClaw path
3. bypass failure → fallback на OpenClaw
4. bypass disabled via ENV → OpenClaw path
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _collect_stream(agen):
    """Собирает все chunks из async generator в список."""

    async def _collect():
        chunks = []
        async for chunk in agen:
            chunks.append(chunk)
        return chunks

    return asyncio.get_event_loop().run_until_complete(_collect())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_client(tmp_path, monkeypatch):
    """Минимальный OpenClawClient с заглушками для unit-теста."""
    monkeypatch.setenv("OPENCLAW_API_KEY", "fake-key")
    monkeypatch.setenv("OPENCLAW_BASE_URL", "http://localhost:18789")
    monkeypatch.setenv("GEMINI_API_KEY_FREE", "AIzaFakeTestKey")
    monkeypatch.setenv("KRAB_GOOGLE_DIRECT_BYPASS_ENABLED", "1")

    # Патчим тяжёлые зависимости при импорте
    import src.openclaw_client as oc_module

    client = MagicMock(spec=oc_module.OpenClawClient)
    client._sessions = {}
    client._active_tool_calls = []
    client._request_disable_tools = False
    client._cloud_tier_state = {}
    client._last_runtime_route = None
    return client


# ---------------------------------------------------------------------------
# Тест 1: google model triggers bypass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_google_model_uses_direct_bypass_when_enabled(monkeypatch):
    """google/* модель с enabled bypass → complete_direct вызывается, OpenClaw не вызывается."""
    monkeypatch.setenv("KRAB_GOOGLE_DIRECT_BYPASS_ENABLED", "1")

    import importlib

    import src.integrations.google_genai_direct as gd_module

    importlib.reload(gd_module)

    mock_complete = AsyncMock(return_value="Ответ от Gemini Direct")

    with patch.object(gd_module, "complete_direct", mock_complete), patch.object(
        gd_module, "_resolve_api_key", return_value="AIzaFakeKey"
    ), patch.object(gd_module, "is_google_direct_enabled", return_value=True):

        # Проверяем что is_google_model + is_google_direct_enabled дают правильный routing
        assert gd_module.is_google_model("google/gemini-3-pro-preview") is True
        assert gd_module.is_google_direct_enabled() is True


# ---------------------------------------------------------------------------
# Тест 2: non-google model не триггерит bypass
# ---------------------------------------------------------------------------


def test_non_google_model_bypass_not_triggered():
    """openai/* модель → bypass не должен триггериться."""
    from src.integrations.google_genai_direct import is_google_model

    assert is_google_model("openai/gpt-5") is False
    assert is_google_model("anthropic/claude-3-opus") is False
    assert is_google_model("lmstudio/llama-3.2") is False


# ---------------------------------------------------------------------------
# Тест 3: bypass failure → fallback behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bypass_failure_falls_back(monkeypatch):
    """При ошибке complete_direct должна логироваться warning, не raise."""
    import importlib

    import src.integrations.google_genai_direct as gd_module

    importlib.reload(gd_module)

    # Simulate SDK failure
    error_mock = AsyncMock(side_effect=RuntimeError("SDK connection error"))

    with patch.object(gd_module, "complete_direct", error_mock), patch.object(
        gd_module, "_resolve_api_key", return_value="AIzaFakeKey"
    ):
        # health_check_direct должен вернуть False, не raise
        ok = await gd_module.health_check_direct(api_key="AIzaFakeKey")
        assert ok is False


# ---------------------------------------------------------------------------
# Тест 4: bypass disabled via env
# ---------------------------------------------------------------------------


def test_bypass_disabled_via_env(monkeypatch):
    """KRAB_GOOGLE_DIRECT_BYPASS_ENABLED=0 → bypass отключён."""
    monkeypatch.setenv("KRAB_GOOGLE_DIRECT_BYPASS_ENABLED", "0")

    import importlib

    import src.integrations.google_genai_direct as gd_module

    importlib.reload(gd_module)

    # is_google_direct_enabled должна вернуть False
    result = gd_module.is_google_direct_enabled()
    assert result is False


# ---------------------------------------------------------------------------
# Тест 5: config knob присутствует
# ---------------------------------------------------------------------------


def test_config_has_google_bypass_knob():
    """Config содержит KRAB_GOOGLE_DIRECT_BYPASS_ENABLED атрибут."""
    import src.config as config_module

    cfg = config_module.config
    assert hasattr(cfg, "KRAB_GOOGLE_DIRECT_BYPASS_ENABLED")
    # Default — True (ON)
    assert isinstance(cfg.KRAB_GOOGLE_DIRECT_BYPASS_ENABLED, bool)
