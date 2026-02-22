
import pytest
import time
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from src.core.model_manager import ModelRouter

@pytest.fixture
def mock_config():
    return {
        "CLOUD_FAIL_FAST_BUDGET_SECONDS": "10",
        "CLOUD_REQUEST_TIMEOUT_SECONDS": "5",
        "MODEL_ROUTING_MEMORY_PATH": "artifacts/test_memory.json"
    }

@pytest.mark.asyncio
async def test_preflight_gate_blocks_subsequent_calls(mock_config):
    # Инициализация роутера с моком OpenClawClient
    mock_client = MagicMock()
    # Первая попытка возвращает фатальную ошибку
    mock_client.chat_completions = AsyncMock(return_value="❌ Error: invalid api key")
    
    router = ModelRouter(mock_config)
    router.openclaw_client = mock_client
    router.force_mode = "force_cloud"

    # 1. Первый вызов - должен дойти до клиента
    resp1 = ""
    async for chunk in router.route_stream("test prompt", "chat", {}, "private", True):
        resp1 += chunk
    assert "API key провайдера невалидный" in resp1
    assert mock_client.chat_completions.call_count == 1

    # 2. Второй вызов - должен быть заблокирован Preflight Gate
    resp2 = ""
    async for chunk in router.route_stream("test prompt", "chat", {}, "private", True):
        resp2 += chunk
    assert "Preflight: провайдер 'model-b' заблокирован" in resp2
    # Счетчик вызовов клиента не должен увеличиться
    assert mock_client.chat_completions.call_count == 1

@pytest.mark.asyncio
async def test_preflight_gate_expiration(mock_config):
    mock_client = MagicMock()
    mock_client.chat_completions = AsyncMock(return_value="❌ Error: quota exceeded")
    
    router = ModelRouter(mock_config)
    router.openclaw_client = mock_client
    router.force_mode = "force_cloud"
    
    # Устанавливаем короткий TTL для теста
    router._preflight_ttl_seconds = 0.5

    # 1. Первый вызов (блокирует провайдера)
    async for _ in router.route_stream("test prompt", "chat", {}, "private", True):
        pass
    assert mock_client.chat_completions.call_count == 1
    
    # 2. Сразу второй вызов (все еще заблокирован)
    resp_blocked = ""
    async for chunk in router.route_stream("test prompt", "chat", {}, "private", True):
        resp_blocked += chunk
    assert "Preflight" in resp_blocked
    assert mock_client.chat_completions.call_count == 1
    
    # 3. Ждем истечения TTL
    await asyncio.sleep(0.6)
    
    # 4. Третий вызов - должен снова дойти до клиента
    async for _ in router.route_stream("test prompt", "chat", {}, "private", True):
        pass
    assert mock_client.chat_completions.call_count == 2
