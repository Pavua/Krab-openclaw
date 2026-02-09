import pytest
from unittest.mock import AsyncMock, patch
from src.config import config
from src.model_manager import model_manager
from src.openclaw_client import openclaw_client

@pytest.mark.asyncio
async def test_gemini_config():
    """Проверка наличия Gemini моделей в конфигурации"""
    assert "google/gemini-2.0-flash-exp" in config.GEMINI_MODELS
    assert config.GEMINI_API_KEY is not None

@pytest.mark.asyncio
async def test_model_discovery_with_gemini():
    """Проверка, что ModelManager видит Gemini модели"""
    models = await model_manager.discover_models()
    gemini_ids = [m.id for m in models if "google/" in m.id]
    assert len(gemini_ids) > 0
    assert "google/gemini-2.0-flash-exp" in gemini_ids

@pytest.mark.asyncio
async def test_select_best_model_fallback():
    """Проверка fallback на Gemini"""
    with patch.object(model_manager, 'discover_models', return_value=[]):
        best = await model_manager.select_best_model("chat")
        assert best == config.MODEL
