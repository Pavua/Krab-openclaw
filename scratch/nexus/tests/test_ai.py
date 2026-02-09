import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from src.ai import AIManager
from src.db import DatabaseManager

@pytest.fixture
def mock_db():
    db = MagicMock(spec=DatabaseManager)
    db.get_setting.return_value = "local/test-model"
    return db

@pytest.fixture
def ai_manager(mock_db):
    return AIManager(mock_db)

@pytest.mark.asyncio
async def test_ask_success(ai_manager):
    with patch('aiohttp.ClientSession.post') as mock_post:
        # Mock successful response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json.return_value = {'choices': [{'message': {'content': 'Hello from AI'}}]}
        mock_post.return_value.__aenter__.return_value = mock_response

        response = await ai_manager.ask("Hi", "System Prompt")
        assert response == "Hello from AI"

@pytest.mark.asyncio
async def test_ask_fallback(ai_manager):
    with patch('aiohttp.ClientSession.post') as mock_post:
        # 1. Fail first request (Local)
        mock_fail = AsyncMock()
        mock_fail.status = 500
        mock_fail.text.return_value = "Server Error"
        
        # 2. Succeed second request (Cloud Fallback)
        mock_success = AsyncMock()
        mock_success.status = 200
        mock_success.json.return_value = {'choices': [{'message': {'content': 'Fallback Success'}}]}

        # Side effect: first call returns fail, second returns success
        mock_post.return_value.__aenter__.side_effect = [mock_fail, mock_success]

        response = await ai_manager.ask("Hi", "System Prompt")
        assert "Fallback" in response
