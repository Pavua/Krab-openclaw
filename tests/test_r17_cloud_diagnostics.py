# -*- coding: utf-8 -*-
"""
tests/test_r17_cloud_diagnostics.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
R17: Тесты Cloud Diagnostics UX — проверяет `get_cloud_diagnostics_summary`
в ModelRouter, а также корректность маппинга error_code -> user_message.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_router():
    """Создаёт минимальный ModelRouter с мок openclaw_client."""
    with patch.dict("os.environ", {
        "OPENCLAW_BASE_URL": "http://localhost:18789",
        "OPENCLAW_API_KEY": "test_key",
    }):
        from src.core.model_manager import ModelRouter
        config = {
            "OPENCLAW_BASE_URL": "http://localhost:18789",
            "OPENCLAW_API_KEY": "test_key",
        }
        router = ModelRouter(config)
    return router


@pytest.mark.asyncio
async def test_cloud_diagnostics_ok():
    """При успешной диагностике возвращает ok=True и понятный UX-текст."""
    router = _make_router()
    router.openclaw_client = MagicMock()
    router.openclaw_client.get_cloud_provider_diagnostics = AsyncMock(return_value={
        "ok": True,
        "providers": {
            "google": {
                "ok": True,
                "error_code": "ok",
                "summary": "доступ подтверждён",
            }
        },
        "checked": ["google"],
    })
    router.openclaw_client.get_token_info = MagicMock(return_value={
        "active_tier": "free",
        "tiers": {"free": {"is_configured": True, "masked_key": "sk-...cdef"}},
    })

    result = await router.get_cloud_diagnostics_summary("google")

    assert result["ok"] is True
    assert result["error_code"] == "ok"
    assert "✅" in result["user_message"]
    assert result["active_tier"] == "free"
    assert result["preflight_blocked"] is False


@pytest.mark.asyncio
async def test_cloud_diagnostics_invalid_key():
    """При инвалидном ключе возвращает понятное UX-сообщение без JSON-мусора."""
    router = _make_router()
    router.openclaw_client = MagicMock()
    router.openclaw_client.get_cloud_provider_diagnostics = AsyncMock(return_value={
        "ok": False,
        "providers": {
            "google": {
                "ok": False,
                "error_code": "api_key_invalid",
                "summary": "API key провайдера невалидный",
            }
        },
        "checked": ["google"],
    })
    router.openclaw_client.get_token_info = MagicMock(return_value={"tiers": {}, "active_tier": "free"})

    result = await router.get_cloud_diagnostics_summary("google")

    assert result["ok"] is False
    assert result["error_code"] == "api_key_invalid"
    # Убеждаемся, что сообщение человекочитаемое, а не JSON
    assert "❌" in result["user_message"]
    assert "{" not in result["user_message"]  # нет JSON-мусора


@pytest.mark.asyncio
async def test_cloud_diagnostics_missing_key():
    """При отсутствии ключа возвращает понятное сообщение."""
    router = _make_router()
    router.openclaw_client = MagicMock()
    router.openclaw_client.get_cloud_provider_diagnostics = AsyncMock(return_value={
        "ok": False,
        "providers": {
            "google": {
                "ok": False,
                "error_code": "missing_api_key",
                "summary": "API key не задан",
            }
        },
        "checked": ["google"],
    })
    router.openclaw_client.get_token_info = MagicMock(return_value={"tiers": {}, "active_tier": "free"})

    result = await router.get_cloud_diagnostics_summary("google")

    assert result["ok"] is False
    assert result["error_code"] == "missing_api_key"
    assert "GEMINI_API_KEY" in result["user_message"]


@pytest.mark.asyncio
async def test_cloud_diagnostics_no_client():
    """При отсутствии клиента — безопасный возврат без исключений."""
    router = _make_router()
    router.openclaw_client = None

    result = await router.get_cloud_diagnostics_summary("google")

    assert result["ok"] is False
    assert result["error_code"] == "no_client"


@pytest.mark.asyncio
async def test_cloud_diagnostics_gateway_api_unavailable_message():
    """Если gateway API недоступен, UX должен отдать понятное сообщение."""
    router = _make_router()
    router.openclaw_client = MagicMock()
    router.openclaw_client.get_cloud_provider_diagnostics = AsyncMock(return_value={
        "ok": False,
        "providers": {
            "google": {
                "ok": False,
                "error_code": "gateway_api_unavailable",
                "summary": "gateway вернул HTML вместо JSON API",
            }
        },
        "checked": ["google"],
    })
    router.openclaw_client.get_token_info = MagicMock(return_value={"tiers": {}, "active_tier": "free"})

    result = await router.get_cloud_diagnostics_summary("google")

    assert result["ok"] is False
    assert result["error_code"] == "gateway_api_unavailable"
    assert "OpenClaw API недоступен" in result["user_message"]


@pytest.mark.asyncio
async def test_cloud_diagnostics_preflight_blocked():
    """Если провайдер заблокирован preflight — ответ содержит это."""
    router = _make_router()
    router.openclaw_client = MagicMock()
    router.openclaw_client.get_cloud_provider_diagnostics = AsyncMock(return_value={
        "ok": True,
        "providers": {
            "google": {"ok": True, "error_code": "ok", "summary": "ok"},
        },
        "checked": ["google"],
    })
    router.openclaw_client.get_token_info = MagicMock(return_value={"tiers": {}, "active_tier": "free"})

    # Имитируем заблокированный preflight
    import time
    router._preflight_cache["google"] = (time.time() + 300, "invalid api key")

    result = await router.get_cloud_diagnostics_summary("google")

    assert result["ok"] is False
    assert result["preflight_blocked"] is True
    assert result["error_code"] == "preflight_blocked"
