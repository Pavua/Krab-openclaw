# -*- coding: utf-8 -*-
"""Unit-тесты LRU eviction policy для ModelManager (VA Phase 1.6)."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.model_manager import ModelInfo, ModelManager, ModelType


@pytest.fixture
def manager_with_lru() -> ModelManager:
    """Fixture для ModelManager с LRU-политикой."""
    with patch("src.model_manager.config") as mock_config:
        mock_config.LM_STUDIO_URL = "http://mock-url"
        mock_config.LM_STUDIO_API_KEY = ""
        mock_config.MAX_RAM_GB = 24
        mock_config.GEMINI_API_KEY = "dummy"
        mock_config.GEMINI_API_KEY_FREE = ""
        mock_config.GEMINI_API_KEY_PAID = ""
        mock_config.FORCE_CLOUD = False
        mock_config.LOCAL_PREFERRED_MODEL = ""
        mock_config.LOCAL_PREFERRED_VISION_MODEL = ""
        mock_config.MODEL = "google/gemini-2.0-flash"
        mock_config.LOCAL_POST_LOAD_VERIFY_SEC = 90.0
        mock_config.KRAB_LRU_EVICT_AFTER_SEC = 300.0  # 5 min
        mm = ModelManager()
        mm._http_client = AsyncMock()
        mm._cloud_http_client = AsyncMock()
        mm._wait_until_model_loaded = AsyncMock(return_value=True)  # type: ignore[method-assign]
        return mm


@pytest.mark.asyncio
async def test_record_usage_updates_last_access(manager_with_lru: ModelManager) -> None:
    """record_usage должна обновлять _last_access."""
    model_id = "qwen3-30b-a3b-instruct-2507"

    # Проверяем что модель ещё не в _last_access
    assert model_id not in manager_with_lru._last_access

    # Записываем использование
    manager_with_lru.record_usage(model_id)

    # Проверяем что now в _last_access
    assert model_id in manager_with_lru._last_access
    assert manager_with_lru._last_access[model_id] > 0


@pytest.mark.asyncio
async def test_maybe_evict_idle_no_action_when_fresh(manager_with_lru: ModelManager) -> None:
    """maybe_evict_idle не выгружает модели если они fresh (idle < timeout)."""
    manager_with_lru._models_cache = {
        "qwen3-30b-a3b-instruct-2507": ModelInfo(
            "qwen3-30b-a3b-instruct-2507", "Qwen 30B", ModelType.LOCAL_MLX, size_gb=17.2
        ),
        "qwen3-4b-instruct": ModelInfo(
            "qwen3-4b-instruct", "Qwen 4B", ModelType.LOCAL_MLX, size_gb=3.0
        ),
    }

    # Обе модели loaded, обе fresh
    manager_with_lru._http_client.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "models": [
                {
                    "key": "qwen3-30b-a3b-instruct-2507",
                    "loaded_instances": [{"id": "qwen3-30b-a3b-instruct-2507"}],
                },
                {"key": "qwen3-4b-instruct", "loaded_instances": [{"id": "qwen3-4b-instruct"}]},
            ]
        },
    )

    # Записываем usage прямо сейчас (fresh)
    manager_with_lru.record_usage("qwen3-30b-a3b-instruct-2507")
    manager_with_lru.record_usage("qwen3-4b-instruct")

    evicted = await manager_with_lru.maybe_evict_idle(
        keep_model="qwen3-30b-a3b-instruct-2507", max_total_models=1
    )

    # Ничего не должно было выгрузиться (обе fresh)
    assert evicted == []


@pytest.mark.asyncio
async def test_maybe_evict_idle_removes_stale_model(manager_with_lru: ModelManager) -> None:
    """maybe_evict_idle выгружает idle модель (idle > timeout)."""
    manager_with_lru._models_cache = {
        "qwen3-30b-a3b-instruct-2507": ModelInfo(
            "qwen3-30b-a3b-instruct-2507", "Qwen 30B", ModelType.LOCAL_MLX, size_gb=17.2
        ),
        "qwen3-4b-instruct": ModelInfo(
            "qwen3-4b-instruct", "Qwen 4B", ModelType.LOCAL_MLX, size_gb=3.0
        ),
    }

    # Обе модели loaded
    manager_with_lru._http_client.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "models": [
                {
                    "key": "qwen3-30b-a3b-instruct-2507",
                    "loaded_instances": [{"id": "qwen3-30b-a3b-instruct-2507"}],
                },
                {"key": "qwen3-4b-instruct", "loaded_instances": [{"id": "qwen3-4b-instruct"}]},
            ]
        },
    )

    unload_resp = MagicMock()
    unload_resp.status_code = 200
    manager_with_lru._http_client.post.return_value = unload_resp

    # qwen3-30b fresh, qwen3-4b idle > 300 sec
    now = time.time()
    manager_with_lru._last_access["qwen3-30b-a3b-instruct-2507"] = now
    manager_with_lru._last_access["qwen3-4b-instruct"] = now - 400  # 400 sec ago

    evicted = await manager_with_lru.maybe_evict_idle(
        keep_model="qwen3-30b-a3b-instruct-2507", max_total_models=1
    )

    # qwen3-4b должна была выгрузиться
    assert "qwen3-4b-instruct" in evicted
    # Проверяем что был вызван unload
    assert manager_with_lru._http_client.post.called


@pytest.mark.asyncio
async def test_maybe_evict_idle_preserves_keep_model(manager_with_lru: ModelManager) -> None:
    """maybe_evict_idle не выгружает keep_model даже если idle."""
    manager_with_lru._models_cache = {
        "qwen3-30b-a3b-instruct-2507": ModelInfo(
            "qwen3-30b-a3b-instruct-2507", "Qwen 30B", ModelType.LOCAL_MLX, size_gb=17.2
        ),
    }

    # qwen3-30b loaded и idle > timeout
    manager_with_lru._http_client.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "models": [
                {
                    "key": "qwen3-30b-a3b-instruct-2507",
                    "loaded_instances": [{"id": "qwen3-30b-a3b-instruct-2507"}],
                },
            ]
        },
    )

    now = time.time()
    manager_with_lru._last_access["qwen3-30b-a3b-instruct-2507"] = now - 400

    evicted = await manager_with_lru.maybe_evict_idle(
        keep_model="qwen3-30b-a3b-instruct-2507", max_total_models=1
    )

    # Ничего не должно было выгрузиться (keep_model)
    assert evicted == []
    # unload не должен был вызваться
    assert not manager_with_lru._http_client.post.called


@pytest.mark.asyncio
async def test_maybe_evict_idle_clears_current_model_if_evicted(
    manager_with_lru: ModelManager,
) -> None:
    """maybe_evict_idle очищает _current_model если она выгружена."""
    manager_with_lru._models_cache = {
        "qwen3-30b-a3b-instruct-2507": ModelInfo(
            "qwen3-30b-a3b-instruct-2507", "Qwen 30B", ModelType.LOCAL_MLX, size_gb=17.2
        ),
        "qwen3-4b-instruct": ModelInfo(
            "qwen3-4b-instruct", "Qwen 4B", ModelType.LOCAL_MLX, size_gb=3.0
        ),
    }

    # Обе модели loaded
    manager_with_lru._http_client.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "models": [
                {
                    "key": "qwen3-30b-a3b-instruct-2507",
                    "loaded_instances": [{"id": "qwen3-30b-a3b-instruct-2507"}],
                },
                {"key": "qwen3-4b-instruct", "loaded_instances": [{"id": "qwen3-4b-instruct"}]},
            ]
        },
    )

    unload_resp = MagicMock()
    unload_resp.status_code = 200
    manager_with_lru._http_client.post.return_value = unload_resp

    # qwen3-4b is current, but idle
    manager_with_lru._current_model = "qwen3-4b-instruct"
    now = time.time()
    manager_with_lru._last_access["qwen3-30b-a3b-instruct-2507"] = now
    manager_with_lru._last_access["qwen3-4b-instruct"] = now - 400

    evicted = await manager_with_lru.maybe_evict_idle(
        keep_model="qwen3-30b-a3b-instruct-2507", max_total_models=1
    )

    # qwen3-4b выгружена
    assert "qwen3-4b-instruct" in evicted
    # _current_model должна быть None
    assert manager_with_lru._current_model is None
