"""
Unit tests for LM Studio health utility (Фаза 2.3).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.core.lm_studio_health import is_lm_studio_available, fetch_lm_studio_models_list


@pytest.mark.asyncio
async def test_is_lm_studio_available_returns_true_on_200():
    with patch("src.core.lm_studio_health.httpx") as mock_httpx:
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
    with patch("src.core.lm_studio_health.httpx") as mock_httpx:
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
    with patch("src.core.lm_studio_health.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"id": "local", "name": "Local"}]}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_httpx.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await fetch_lm_studio_models_list("http://127.0.0.1:1234", timeout=2.0)
    assert result == [{"id": "local", "name": "Local"}]


@pytest.mark.asyncio
async def test_fetch_lm_studio_models_list_returns_empty_on_failure():
    with patch("src.core.lm_studio_health.httpx") as mock_httpx:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
        mock_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_httpx.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await fetch_lm_studio_models_list("http://127.0.0.1:1234", timeout=2.0)
    assert result == []
