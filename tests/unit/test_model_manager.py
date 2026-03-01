# -*- coding: utf-8 -*-
"""Unit-тесты ModelManager: v1 API, local-first routing, memory eviction."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.model_manager import ModelInfo, ModelManager, ModelType


@pytest.fixture
def manager() -> ModelManager:
    with patch("src.model_manager.config") as mock_config:
        mock_config.LM_STUDIO_URL = "http://mock-url"
        mock_config.MAX_RAM_GB = 24
        mock_config.GEMINI_API_KEY = "dummy"
        mock_config.FORCE_CLOUD = False
        mock_config.LOCAL_PREFERRED_MODEL = ""
        mock_config.MODEL = "google/gemini-2.0-flash"
        mm = ModelManager()
        mm._http_client = AsyncMock()
        return mm


@pytest.mark.asyncio
async def test_load_model_uses_v1_endpoint_first(manager: ModelManager) -> None:
    manager._models_cache = {
        "model-1": ModelInfo("model-1", "Model 1", ModelType.LOCAL_MLX, size_gb=5.0)
    }
    with patch("src.model_manager.psutil.virtual_memory") as mock_mem:
        mock_mem.return_value.available = 10 * 1024**3

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        manager._http_client.post.return_value = ok_resp

        result = await manager.load_model("model-1")

        assert result is True
        assert manager._current_model == "model-1"
        first_call = manager._http_client.post.call_args_list[0]
        assert first_call.args[0] == "http://mock-url/api/v1/models/load"
        assert "ttl" not in first_call.kwargs["json"]


@pytest.mark.asyncio
async def test_memory_pressure_triggers_unload_with_instance_id(manager: ModelManager) -> None:
    manager._current_model = "big-model"
    manager._models_cache = {
        "big-model": ModelInfo("big-model", "Big", ModelType.LOCAL_MLX, size_gb=20.0),
        "new-model": ModelInfo("new-model", "New", ModelType.LOCAL_MLX, size_gb=10.0),
    }

    with patch("src.model_manager.psutil.virtual_memory") as mock_mem:
        mock_mem.return_value.available = 5 * 1024**3

        unload_resp_1 = MagicMock()
        unload_resp_1.status_code = 200
        unload_resp_2 = MagicMock()
        unload_resp_2.status_code = 200
        load_resp = MagicMock()
        load_resp.status_code = 200

        # single-model unload -> free_vram unload -> load_model load
        manager._http_client.post.side_effect = [unload_resp_1, unload_resp_2, load_resp]
        manager._http_client.get.return_value = MagicMock(status_code=200, json=lambda: {"models": [{"key": "big-model", "loaded_instances": [{"id": "big-model"}]}]})

        result = await manager.load_model("new-model")
        assert result is True

        unload_call = manager._http_client.post.call_args_list[0]
        assert unload_call.args[0].endswith("/api/v1/models/unload")
        assert unload_call.kwargs["json"].get("instance_id") == "big-model"


@pytest.mark.asyncio
async def test_load_model_ignores_false_200_with_error_body(manager: ModelManager) -> None:
    manager._models_cache = {
        "model-1": ModelInfo("model-1", "Model 1", ModelType.LOCAL_MLX, size_gb=5.0)
    }
    with patch("src.model_manager.psutil.virtual_memory") as mock_mem:
        mock_mem.return_value.available = 10 * 1024**3

        false_ok = MagicMock()
        false_ok.status_code = 200
        false_ok.text = '{"error":{"message":"Unexpected endpoint or method"}}'
        false_ok.json.return_value = {"error": {"message": "Unexpected endpoint or method"}}

        real_ok = MagicMock()
        real_ok.status_code = 200
        real_ok.text = '{"status":"ok"}'
        real_ok.json.return_value = {"status": "ok"}

        manager._http_client.post.side_effect = [false_ok, real_ok]

        result = await manager.load_model("model-1")

        assert result is True
        assert manager._http_client.post.call_count == 2


@pytest.mark.asyncio
async def test_get_best_model_local_first_in_auto(manager: ModelManager) -> None:
    with patch("src.model_manager.is_lm_studio_available", new=AsyncMock(return_value=True)):
        with patch.object(manager, "resolve_preferred_local_model", new=AsyncMock(return_value="local/abc")):
            best = await manager.get_best_model()
    assert best == "local/abc"


@pytest.mark.asyncio
async def test_get_best_model_cloud_when_force_cloud(manager: ModelManager) -> None:
    with patch("src.model_manager.config") as mock_config:
        mock_config.FORCE_CLOUD = True
        mock_config.LM_STUDIO_URL = "http://mock-url"
        mock_config.MAX_RAM_GB = 24
        mock_config.GEMINI_API_KEY = "dummy"
        mock_config.LOCAL_PREFERRED_MODEL = ""
        mock_config.MODEL = "google/gemini-2.0-flash"
        mm = ModelManager()
        with patch.object(mm._router, "get_best_model", new=AsyncMock(return_value="google/gemini-2.0-flash")):
            best = await mm.get_best_model()
            assert best.startswith("google/")
