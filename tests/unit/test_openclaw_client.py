"""
Unit tests for OpenClawClient
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.openclaw_client import OpenClawClient

@pytest.fixture
def client():
    with patch("src.openclaw_client.config") as mock_config:
        mock_config.OPENCLAW_URL = "http://mock-claw"
        mock_config.OPENCLAW_TOKEN = "token"
        client = OpenClawClient()
        client._http_client = AsyncMock()
        return client

@pytest.mark.asyncio
async def test_health_check_success(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    client._http_client.get.return_value = mock_resp
    
    result = await client.health_check()
    assert result is True

@pytest.mark.asyncio
async def test_health_check_failure(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    client._http_client.get.return_value = mock_resp
    
    result = await client.health_check()
    assert result is False

@pytest.mark.asyncio
async def test_send_message_stream(client):
    # Mock stream response
    async def mock_aiter_lines():
        yield "data: {\"choices\": [{\"delta\": {\"content\": \"Hello\"}}]}"
        yield "data: {\"choices\": [{\"delta\": {\"content\": \" World\"}}]}"
        yield "data: [DONE]"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.aiter_lines = mock_aiter_lines
    
    # Mock stream context manager
    mock_stream = AsyncMock()
    mock_stream.__aenter__.return_value = mock_resp
    
    # ВАЖНО: stream не должен быть корутиной, он возвращает контекстный менеджер
    client._http_client.stream = MagicMock(return_value=mock_stream)
    
    chunks = []
    async for chunk in client.send_message_stream("Hi", "chat-1"):
        chunks.append(chunk)
        
    assert "".join(chunks) == "Hello World"
    assert len(client._sessions["chat-1"]) == 2 # User + Assistant
    assert client._sessions["chat-1"][1]["content"] == "Hello World"

@pytest.mark.asyncio
async def test_session_management(client):
    # Test session clearing and limiting
    client._sessions["chat-1"] = [{"role": "user", "content": "1"}] * 25
    
    # Simulate a message sends to trim history
    # We mock stream to return empty immediately
    async def mock_aiter_lines():
        yield "data: [DONE]"
        
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.aiter_lines = mock_aiter_lines
    mock_stream = AsyncMock()
    mock_stream.__aenter__.return_value = mock_resp
    
    # Fix mock type
    client._http_client.stream = MagicMock(return_value=mock_stream)
    
    async for _ in client.send_message_stream("New", "chat-1"):
        pass
        
    # Should be trimmed to 20 (plus new ones... logic is: new user + new assistant added, then trim to 20)
    # Start: 25. Add user: 26. Add assistant: 27. Trim to 20.
    assert len(client._sessions["chat-1"]) == 20

def test_clear_session(client):
    client._sessions["chat-1"] = []
    client.clear_session("chat-1")
    assert "chat-1" not in client._sessions


@pytest.mark.asyncio
async def test_force_cloud_no_lm_studio_fallback(client):
    """При force_cloud=True при ошибке OpenClaw не делаем fallback на LM Studio (Фаза 2.2)."""
    # Ошибка, попадающая в блок (HTTPError, OSError, ValueError, KeyError), не в RequestError
    client._http_client.stream = MagicMock(side_effect=ValueError("gateway down"))
    chunks = []
    async for chunk in client.send_message_stream("Hi", "chat-1", force_cloud=True):
        chunks.append(chunk)
    text = "".join(chunks)
    assert "Облачный сервис временно недоступен" in text
    assert "!model local" in text
    assert "Falling back to LM Studio" not in text
