# -*- coding: utf-8 -*-
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from src.core.model_manager import ModelRouter

@pytest.mark.asyncio
async def test_cloud_tiered_fallback_logic():
    # Конфигурация с двумя тирами
    config = {
        "OPENCLAW_API_KEY": '{"free": "free_key_123", "paid": "paid_key_456"}',
        "OPENCLAW_BASE_URL": "http://localhost:18789"
    }
    
    router = ModelRouter(config)
    router.force_mode = "force_cloud"
    router.is_local_available = False
    router.cloud_max_candidates_force_cloud = 1
    
    # Мокаем _call_gemini, чтобы первая попытка вернула ошибку квоты, а вторая успех
    router._call_gemini = AsyncMock()
    
    # 1. Первая попытка (free) -> Quota Error
    # 2. Вторая попытка (paid) -> Success
    router._call_gemini.side_effect = [
        "❌ Cloud error: 429 Resource exhausted (Quota exceeded)",
        "✅ Success from Paid Tier"
    ]
    
    # Запускаем запрос
    response = await router.route_query("test prompt", is_owner=True)
    
    # Проверяем результат
    assert response == "✅ Success from Paid Tier"
    
    # Проверяем последовательность вызовов
    assert router._call_gemini.call_count == 2
    
    # Проверяем, что в конце тир сброшен на free
    assert router.openclaw_client.active_gateway_tier == "free"

@pytest.mark.asyncio
async def test_cloud_tiered_fallback_fatal_failure():
    # Если оба тира упали
    config = {
        "OPENCLAW_API_KEY": '{"free": "free_key", "paid": "paid_key"}',
    }
    router = ModelRouter(config)
    router.force_mode = "force_cloud"
    router.is_local_available = False
    router.cloud_max_candidates_force_cloud = 1
    router._call_gemini = AsyncMock(return_value="❌ Fatal error on both tiers")
    
    response = await router.route_query("test prompt", is_owner=True)
    
    assert "Ошибка Cloud (force_cloud)" in response
    assert router._call_gemini.call_count == 1
