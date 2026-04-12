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
    with patch(
        "src.core.local_health.build_lm_studio_auth_headers",
        return_value={"Authorization": "Bearer lm-token"},
    ):
        with patch(_httpx_patch_target) as mock_httpx:
            mock_resp = AsyncMock()
            mock_resp.status_code = 200
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await is_lm_studio_available("http://127.0.0.1:1234", timeout=2.0)
    assert result is True
    assert mock_httpx.AsyncClient.call_args.kwargs["headers"] == {
        "Authorization": "Bearer lm-token"
    }
    assert mock_client.get.await_args_list[0].args[0] == "http://127.0.0.1:1234/api/v1/models"


@pytest.mark.asyncio
async def test_is_lm_studio_available_returns_false_on_non_200():
    with patch(_httpx_patch_target) as mock_httpx:
        mock_resp_api = AsyncMock()
        mock_resp_api.status_code = 503
        mock_resp_compat = AsyncMock()
        mock_resp_compat.status_code = 503
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_resp_api, mock_resp_compat])
        mock_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_httpx.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await is_lm_studio_available("http://127.0.0.1:1234", timeout=2.0)
    assert result is False
    assert mock_client.get.await_count == 2
    assert mock_client.get.await_args_list[0].args[0] == "http://127.0.0.1:1234/api/v1/models"
    assert mock_client.get.await_args_list[1].args[0] == "http://127.0.0.1:1234/v1/models"


@pytest.mark.asyncio
async def test_is_lm_studio_available_falls_back_to_openai_compat():
    with patch(
        "src.core.local_health.build_lm_studio_auth_headers",
        return_value={"Authorization": "Bearer lm-token"},
    ):
        with patch(_httpx_patch_target) as mock_httpx:
            api_resp = AsyncMock()
            api_resp.status_code = 404
            compat_resp = AsyncMock()
            compat_resp.status_code = 200
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=[api_resp, compat_resp])
            mock_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await is_lm_studio_available("http://127.0.0.1:1234", timeout=2.0)
    assert result is True
    assert mock_client.get.await_args_list[0].args[0] == "http://127.0.0.1:1234/api/v1/models"
    assert mock_client.get.await_args_list[1].args[0] == "http://127.0.0.1:1234/v1/models"


@pytest.mark.asyncio
async def test_fetch_lm_studio_models_list_returns_list_on_success():
    with patch(
        "src.core.local_health.build_lm_studio_auth_headers",
        return_value={"Authorization": "Bearer lm-token"},
    ):
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
    assert mock_httpx.AsyncClient.call_args.kwargs["headers"] == {
        "Authorization": "Bearer lm-token"
    }
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
