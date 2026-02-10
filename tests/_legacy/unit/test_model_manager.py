"""
Unit tests for ModelManager (Smart Loading)
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.model_manager import ModelManager, ModelInfo, ModelType

@pytest.fixture
def model_manager():
    with patch("src.model_manager.config") as mock_config:
        mock_config.LM_STUDIO_URL = "http://mock-url"
        mock_config.MAX_RAM_GB = 24
        mock_config.GEMINI_API_KEY = "dummy"
        mm = ModelManager()
        mm._http_client = AsyncMock()
        return mm

@pytest.mark.asyncio
async def test_discover_models(model_manager):
    # Mock Response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [
            {"id": "qwen/qwen3-coder-30b-instruct-mlx", "object": "model"},
            {"id": "google/gemini-2.0-flash-exp", "object": "model"}
        ]
    }
    model_manager._http_client.get.return_value = mock_response
    
    models = await model_manager.discover_models()
    
    assert len(models) == 2
    assert models[0].type == ModelType.LOCAL_MLX
    assert models[1].type == ModelType.CLOUD_GEMINI
    assert model_manager._models_cache["qwen/qwen3-coder-30b-instruct-mlx"].size_gb == 18.0

@pytest.mark.asyncio
async def test_load_model_success(model_manager):
    # Setup cache
    model_manager._models_cache = {
        "model-1": ModelInfo("model-1", "Model 1", ModelType.LOCAL_MLX, size_gb=5.0)
    }
    
    # Mock RAM check
    with patch("src.model_manager.psutil.virtual_memory") as mock_mem:
        mock_mem.return_value.available = 10 * 1024**3 # 10GB available
        
        # Mock Load API
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        model_manager._http_client.post.return_value = mock_resp
        
        result = await model_manager.load_model("model-1")
        
        assert result is True
        assert model_manager._current_model == "model-1"
        model_manager._http_client.post.assert_called_with(
            "http://mock-url/v1/models/load",
            json={"model": "model-1", "ttl": -1},
            timeout=600.0
        )

@pytest.mark.asyncio
async def test_smart_load_memory_pressure(model_manager):
    # Setup loaded model
    model_manager._current_model = "big-model"
    model_manager._models_cache = {
        "big-model": ModelInfo("big-model", "Big", ModelType.LOCAL_MLX, size_gb=20.0),
        "new-model": ModelInfo("new-model", "New", ModelType.LOCAL_MLX, size_gb=10.0)
    }
    model_manager._last_access["big-model"] = 1000
    
    # Mock RAM: Not enough for new model
    with patch("src.model_manager.psutil.virtual_memory") as mock_mem:
        mock_mem.return_value.available = 5 * 1024**3 # Only 5GB free
        
        # Unload mock
        unload_resp = MagicMock()
        unload_resp.status_code = 200
        
        # Load mock
        load_resp = MagicMock()
        load_resp.status_code = 200
        
        model_manager._http_client.post.side_effect = [unload_resp, load_resp]
        
        result = await model_manager.load_model("new-model")
        
        assert result is True
        # Should have unloaded big-model
        assert model_manager._http_client.post.call_count == 2
        args_unload = model_manager._http_client.post.call_args_list[0]
        assert args_unload[0][0].endswith("/unload")
        assert args_unload[1]["json"]["model"] == "big-model"

@pytest.mark.asyncio
async def test_select_best_model_fallback(model_manager):
    # No models available locally
    mock_response = MagicMock()
    mock_response.json.return_value = {"data": []}
    model_manager._http_client.get.return_value = mock_response
    
    best = await model_manager.select_best_model("chat")
    assert best == "google/gemini-2.0-flash-exp"
