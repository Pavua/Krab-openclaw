"""
Unit tests for LM Studio health utility (Фаза 2.3 / 4.1 — реализация в local_health).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.lm_studio_health import fetch_lm_studio_models_list, is_lm_studio_available

# Патчим модуль, где реально используется httpx (Фаза 4.1)
_httpx_patch_target = "src.core.local_health.httpx"


@pytest.mark.asyncio
async def test_is_lm_studio_available_returns_true_on_200():
    with patch(_httpx_patch_target) as mock_httpx:
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_httpx.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await is_lm_studio_available("http://127.0.0.1:1234", timeout=2.0)
    assert result is True


@pytest.mark.asyncio
async def test_is_lm_studio_available_returns_false_on_non_200():
    with patch(_httpx_patch_target) as mock_httpx:
        mock_resp = AsyncMock()
        mock_resp.status_code = 503
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_httpx.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await is_lm_studio_available("http://127.0.0.1:1234", timeout=2.0)
    assert result is False


@pytest.mark.asyncio
async def test_fetch_lm_studio_models_list_returns_list_on_success():
    with patch(_httpx_patch_target) as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [
                {
                    "key": "local",
                    "display_name": "Local",
                    "capabilities": {"vision": True},
                    "size_bytes": 2147483648,
                }
            ]
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_httpx.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await fetch_lm_studio_models_list("http://127.0.0.1:1234", timeout=2.0)
    assert len(result) == 1
    assert result[0]["id"] == "local"
    assert result[0]["name"] == "Local"
    assert result[0]["vision"] is True
    assert result[0]["size_gb"] == 2.0


@pytest.mark.asyncio
async def test_fetch_lm_studio_models_list_returns_empty_on_failure():
    import httpx
    # Патчим только AsyncClient, чтобы httpx.HTTPError оставался настоящим классом в except
    with patch(_httpx_patch_target + ".AsyncClient") as mock_ac:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        mock_ac.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ac.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await fetch_lm_studio_models_list("http://127.0.0.1:1234", timeout=2.0)
    assert result == []
