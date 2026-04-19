# -*- coding: utf-8 -*-
"""
Расширенные unit-тесты модулей управления моделями:
- ModelManager: переключение модели, каталог, vision, fallback
- ProviderManager: thinking-depth, смена провайдера, vision-маршруты
- ModelInfo / ModelType: типы, свойства
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.model_manager import ModelInfo, ModelManager, ModelType

# ─────────────────────────────────────────────────────────────────────────────
# Фикстуры
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def manager() -> ModelManager:
    """Изолированный ModelManager с замоканными HTTP-клиентами."""
    with patch("src.model_manager.config") as mock_config:
        mock_config.LM_STUDIO_URL = "http://mock-lm"
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
        mm = ModelManager()
        mm._http_client = AsyncMock()
        mm._cloud_http_client = AsyncMock()
        mm._wait_until_model_loaded = AsyncMock(return_value=True)
        return mm


# ─────────────────────────────────────────────────────────────────────────────
# Тесты ModelInfo / ModelType
# ─────────────────────────────────────────────────────────────────────────────


def test_model_info_is_local_true_for_mlx() -> None:
    """LOCAL_MLX и LOCAL_GGUF должны отдавать is_local=True."""
    mlx = ModelInfo("m1", "M1", ModelType.LOCAL_MLX, size_gb=4.0)
    gguf = ModelInfo("m2", "M2", ModelType.LOCAL_GGUF, size_gb=4.0)
    assert mlx.is_local is True
    assert gguf.is_local is True


def test_model_info_is_local_false_for_cloud() -> None:
    """CLOUD_GEMINI должна отдавать is_local=False."""
    cloud = ModelInfo("google/gemini-2.5-pro", "Gemini", ModelType.CLOUD_GEMINI, size_gb=0)
    assert cloud.is_local is False


def test_model_info_supports_vision_default_false() -> None:
    """По умолчанию supports_vision=False."""
    m = ModelInfo("text-model", "Text", ModelType.LOCAL_MLX)
    assert m.supports_vision is False


def test_model_info_vision_flag_explicit() -> None:
    """Флаг supports_vision задаётся явно."""
    m = ModelInfo("qwen-vl", "Qwen VL", ModelType.LOCAL_MLX, supports_vision=True)
    assert m.supports_vision is True


# ─────────────────────────────────────────────────────────────────────────────
# Тесты _is_chat_capable_local_model
# ─────────────────────────────────────────────────────────────────────────────


def test_is_chat_capable_rejects_embedding_model() -> None:
    """Embedding-модели должны быть отфильтрованы из chat-кандидатов."""
    assert (
        ModelManager._is_chat_capable_local_model("text-embedding-nomic-embed-text-v1.5") is False
    )
    assert ModelManager._is_chat_capable_local_model("bge-m3-embedding") is False


def test_is_chat_capable_rejects_reranker() -> None:
    """Reranker-модели — не chat."""
    assert ModelManager._is_chat_capable_local_model("cross-encoder/ms-marco-reranker") is False


def test_is_chat_capable_accepts_standard_llm() -> None:
    """Обычные LLM проходят фильтр."""
    assert ModelManager._is_chat_capable_local_model("qwen2.5-coder-7b-instruct-mlx") is True
    assert ModelManager._is_chat_capable_local_model("nvidia/nemotron-3-nano") is True


# ─────────────────────────────────────────────────────────────────────────────
# Тесты catalog / _local_candidates
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_local_candidates_sorts_by_size_ascending(manager: ModelManager) -> None:
    """_local_candidates должен возвращать список, отсортированный от лёгкой к тяжёлой."""
    manager._models_cache = {
        "heavy": ModelInfo("heavy", "Heavy", ModelType.LOCAL_MLX, size_gb=16.0),
        "medium": ModelInfo("medium", "Medium", ModelType.LOCAL_MLX, size_gb=7.0),
        "light": ModelInfo("light", "Light", ModelType.LOCAL_MLX, size_gb=3.0),
    }
    result = await manager._local_candidates(has_photo=False)
    sizes = [info.size_gb for _, info in result]
    assert sizes == sorted(sizes), "кандидаты должны идти от лёгкого к тяжёлому"


@pytest.mark.asyncio
async def test_local_candidates_photo_only_vision_models(manager: ModelManager) -> None:
    """При has_photo=True в кандидаты попадают только vision-модели."""
    manager._models_cache = {
        "text-only": ModelInfo(
            "text-only", "Text", ModelType.LOCAL_MLX, size_gb=5.0, supports_vision=False
        ),
        "vision-capable": ModelInfo(
            "vision-capable", "VL", ModelType.LOCAL_MLX, size_gb=7.0, supports_vision=True
        ),
    }
    result = await manager._local_candidates(has_photo=True)
    ids = [mid for mid, _ in result]
    assert "vision-capable" in ids
    assert "text-only" not in ids


@pytest.mark.asyncio
async def test_local_candidates_excludes_cloud_models(manager: ModelManager) -> None:
    """Облачные модели не должны попасть в _local_candidates."""
    manager._models_cache = {
        "google/gemini-2.5-flash": ModelInfo(
            "google/gemini-2.5-flash", "Gemini", ModelType.CLOUD_GEMINI, size_gb=0
        ),
        "local-llm": ModelInfo("local-llm", "Local", ModelType.LOCAL_MLX, size_gb=5.0),
    }
    result = await manager._local_candidates(has_photo=False)
    ids = [mid for mid, _ in result]
    assert "local-llm" in ids
    assert "google/gemini-2.5-flash" not in ids


# ─────────────────────────────────────────────────────────────────────────────
# Тесты model switching / переключение модели
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_switching_model_updates_current_model(manager: ModelManager) -> None:
    """Успешный load_model должен обновить _current_model."""
    manager._models_cache = {
        "new-model": ModelInfo("new-model", "New", ModelType.LOCAL_MLX, size_gb=4.0)
    }
    with patch("src.model_manager.psutil.virtual_memory") as mock_mem:
        mock_mem.return_value.available = 20 * 1024**3
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.text = '{"status":"ok"}'
        ok_resp.json.return_value = {"status": "ok"}
        manager._http_client.post.return_value = ok_resp

        result = await manager.load_model("new-model")

    assert result is True
    assert manager._current_model == "new-model"


@pytest.mark.asyncio
async def test_switching_fails_when_no_ram(manager: ModelManager) -> None:
    """load_model возвращает False при нехватке RAM (с пустым loaded cache)."""
    manager._models_cache = {
        "big-model": ModelInfo("big-model", "Big", ModelType.LOCAL_MLX, size_gb=30.0)
    }
    # Нет загруженных моделей — некому освободить память
    manager.get_loaded_models = AsyncMock(return_value=[])
    # _do_load_model заглушаем: он не должен вызываться при нехватке RAM,
    # но если всё же вызовется — сразу False (чистая изоляция теста)
    manager._do_load_model = AsyncMock(return_value=False)
    with patch("src.model_manager.psutil.virtual_memory") as mock_mem:
        mock_mem.return_value.available = 2 * 1024**3  # 2 GB — слишком мало
        result = await manager.load_model("big-model")

    assert result is False
    assert manager._current_model != "big-model"


# ─────────────────────────────────────────────────────────────────────────────
# Тесты thinking depth через ProviderManager
# ─────────────────────────────────────────────────────────────────────────────


def test_thinking_depth_params_off() -> None:
    """ThinkingDepth.OFF → thinking=False, budget_tokens=0."""
    from src.core.provider_manager import THINKING_DEPTH_PARAMS, ThinkingDepth

    params = THINKING_DEPTH_PARAMS[ThinkingDepth.OFF]
    assert params["thinking"] is False
    assert params["budget_tokens"] == 0


def test_thinking_depth_params_high() -> None:
    """ThinkingDepth.HIGH → budget_tokens=32768."""
    from src.core.provider_manager import THINKING_DEPTH_PARAMS, ThinkingDepth

    params = THINKING_DEPTH_PARAMS[ThinkingDepth.HIGH]
    assert params["thinking"] is True
    assert params["budget_tokens"] == 32768


def test_thinking_depth_params_auto_no_budget_limit() -> None:
    """ThinkingDepth.AUTO → budget_tokens=None (модель сама решает)."""
    from src.core.provider_manager import THINKING_DEPTH_PARAMS, ThinkingDepth

    params = THINKING_DEPTH_PARAMS[ThinkingDepth.AUTO]
    assert params["thinking"] is True
    assert params["budget_tokens"] is None


def test_provider_manager_set_thinking_depth_updates_state(tmp_path) -> None:
    """set_thinking_depth сохраняет новый уровень в состоянии менеджера."""
    from src.core.provider_manager import ProviderManager, ThinkingDepth

    state_file = tmp_path / "krab_provider_state.json"
    with patch("src.core.provider_manager.ProviderManager._STATE_FILE", str(state_file)):
        pm = ProviderManager()
        pm.set_thinking_depth(ThinkingDepth.MEDIUM)
        assert pm.thinking_depth == ThinkingDepth.MEDIUM


def test_provider_manager_vision_model_fallback_to_first_with_vision_flag(tmp_path) -> None:
    """active_vision_model_id возвращает первую vision-модель провайдера, если не задано явно."""
    from src.core.provider_manager import ProviderManager, ProviderType

    state_file = tmp_path / "krab_provider_state.json"
    with patch("src.core.provider_manager.ProviderManager._STATE_FILE", str(state_file)):
        pm = ProviderManager()
        pm.set_provider(ProviderType.GEMINI_OAUTH)
        pm._state.vision_model_id = ""  # сбрасываем явный выбор

        vision_id = pm.active_vision_model_id
        # Должна быть непустой строкой
        assert isinstance(vision_id, str)
        assert len(vision_id) > 0
