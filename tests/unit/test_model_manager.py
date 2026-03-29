# -*- coding: utf-8 -*-
"""Unit-тесты ModelManager: v1 API, local-first routing, memory eviction."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.model_manager import ModelInfo, ModelManager, ModelType


@pytest.fixture
def manager() -> ModelManager:
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
        mm = ModelManager()
        mm._http_client = AsyncMock()
        mm._cloud_http_client = AsyncMock()
        mm._wait_until_model_loaded = AsyncMock(return_value=True)  # type: ignore[method-assign]
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
async def test_memory_pressure_triggers_unload_with_identifier(manager: ModelManager) -> None:
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
        assert unload_call.kwargs["json"].get("identifier") == "big-model"


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
async def test_load_model_requires_post_load_confirmation(manager: ModelManager) -> None:
    manager._models_cache = {
        "model-1": ModelInfo("model-1", "Model 1", ModelType.LOCAL_MLX, size_gb=5.0)
    }
    manager._wait_until_model_loaded = AsyncMock(return_value=False)  # type: ignore[method-assign]

    with patch("src.model_manager.psutil.virtual_memory") as mock_mem:
        mock_mem.return_value.available = 10 * 1024**3

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.text = '{"status":"ok"}'
        ok_resp.json.return_value = {"status": "ok"}
        manager._http_client.post.return_value = ok_resp

        result = await manager.load_model("model-1")

        assert result is False
        assert manager._current_model is None


@pytest.mark.asyncio
async def test_get_loaded_models_reuses_short_cache_for_repeated_truth_reads(manager: ModelManager) -> None:
    """Повторные read-only truth-запросы не должны каждый раз долбить LM Studio."""
    models_resp = MagicMock()
    models_resp.status_code = 200
    models_resp.json.return_value = {
        "models": [
            {
                "key": "model-1",
                "loaded_instances": [{"id": "model-1"}],
            }
        ]
    }
    manager._http_client.get.return_value = models_resp

    first = await manager.get_loaded_models()
    second = await manager.get_loaded_models()

    assert first == ["model-1"]
    assert second == ["model-1"]
    assert manager._http_client.get.call_count == 1


@pytest.mark.asyncio
async def test_wait_until_model_loaded_bypasses_short_cache(manager: ModelManager) -> None:
    """Post-load verify обязан читать свежий статус, а не короткий truth-cache."""
    manager._wait_until_model_loaded = ModelManager._wait_until_model_loaded.__get__(manager, ModelManager)  # type: ignore[method-assign]
    manager.get_loaded_models = AsyncMock(side_effect=[[], ["model-1"]])  # type: ignore[method-assign]

    ok = await manager._wait_until_model_loaded("model-1", timeout_sec=1.0, poll_sec=0.01)

    assert ok is True
    assert manager.get_loaded_models.await_count == 2
    for call in manager.get_loaded_models.await_args_list:
        assert call.kwargs["force_refresh"] is True


@pytest.mark.asyncio
async def test_discover_models_records_cloud_auth_error_and_short_backoff(manager: ModelManager) -> None:
    """Cloud discovery должен запоминать auth-ошибку и не долбить провайдера повторно в backoff-окне."""

    async def _fake_cloud_fetch(*args, diagnostics_sink=None, **kwargs):
        if diagnostics_sink is not None:
            diagnostics_sink.append(
                {
                    "status_code": 401,
                    "error_kind": "auth",
                    "detail": "API key rejected",
                    "key_tier": "free",
                    "key_source": "env:GEMINI_API_KEY_FREE",
                }
            )
        return []

    async def _fake_discover(_lm_url, _client, *, models_cache, fetch_google_models_async, timeout=None):
        await fetch_google_models_async()
        return []

    with patch("src.model_manager.cloud_fetch_google_models_fb", new=AsyncMock(side_effect=_fake_cloud_fetch)) as fetch_mock:
        with patch("src.model_manager.discover_models_impl", new=AsyncMock(side_effect=_fake_discover)):
            await manager.discover_models()
            state = manager.get_cloud_runtime_state_export()
            assert state["last_provider_status"] == "auth"
            assert state["last_error_code"] == "auth_invalid"
            assert fetch_mock.await_count == 1

            await manager.discover_models()
            assert fetch_mock.await_count == 1


@pytest.mark.asyncio
async def test_discover_models_uses_dedicated_cloud_client_without_lm_headers(manager: ModelManager) -> None:
    """Cloud discovery должен ходить отдельным клиентом, а local truth-read — LM клиентом."""

    async def _fake_discover(_lm_url, local_client, *, models_cache, fetch_google_models_async, timeout=None):
        assert local_client is manager._http_client
        await fetch_google_models_async()
        return []

    with patch("src.model_manager.cloud_fetch_google_models_fb", new=AsyncMock(return_value=[])) as fetch_mock:
        with patch("src.model_manager.discover_models_impl", new=AsyncMock(side_effect=_fake_discover)):
            await manager.discover_models()

    assert fetch_mock.await_count == 1
    assert fetch_mock.await_args.args[2] is manager._cloud_http_client


@pytest.mark.asyncio
async def test_get_best_model_local_first_in_auto(manager: ModelManager) -> None:
    with patch("src.model_manager.config") as mock_config:
        mock_config.FORCE_CLOUD = False
        mock_config.MODEL = "auto"
        mock_config.LM_STUDIO_URL = "http://mock-url"
        mock_config.LM_STUDIO_API_KEY = ""
        mock_config.LOCAL_PREFERRED_MODEL = ""
        mock_config.LOCAL_PREFERRED_VISION_MODEL = ""
        # lm_studio_url уже установлен в fixture, но config.FORCE_CLOUD — нет
        with patch("src.model_manager.is_lm_studio_available", new=AsyncMock(return_value=True)):
            with patch.object(manager, "resolve_preferred_local_model", new=AsyncMock(return_value="local/abc")):
                best = await manager.get_best_model()
    assert best == "local/abc"


@pytest.mark.asyncio
async def test_get_best_model_force_cloud_prefers_runtime_primary_over_stale_env(manager: ModelManager) -> None:
    """Cloud-маршрут должен брать primary из live runtime, а не из старого `.env`."""
    with patch("src.model_manager.get_runtime_primary_model", return_value="openai-codex/gpt-5.4"):
        with patch("src.model_manager.config") as mock_config:
            mock_config.FORCE_CLOUD = True
            mock_config.MODEL = "openai-codex/gpt-4.5-preview"
            mock_config.LM_STUDIO_URL = "http://mock-url"
            mock_config.LM_STUDIO_API_KEY = ""
            mock_config.LOCAL_PREFERRED_MODEL = ""
            mock_config.LOCAL_PREFERRED_VISION_MODEL = ""
            with patch.object(manager._router, "get_best_model", new=AsyncMock(return_value="openai-codex/gpt-5.4")):
                best = await manager.get_best_model()

    assert best == "openai-codex/gpt-5.4"
    assert manager._router.config_model == "openai-codex/gpt-5.4"


@pytest.mark.asyncio
async def test_get_best_model_photo_falls_back_to_cloud_when_no_local_vision_is_selected(manager: ModelManager) -> None:
    with patch.object(manager, "resolve_preferred_local_model", new=AsyncMock(return_value=None)):
        with patch.object(manager._router, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")) as router_best:
            best = await manager.get_best_model(has_photo=True)

    assert best == "google/gemini-2.5-flash"
    assert router_best.await_count == 1


def test_is_local_model_treats_openai_codex_as_cloud(manager: ModelManager) -> None:
    """OpenAI/Codex и CLI backend IDs не должны маскироваться под локальные LM Studio модели."""
    assert manager.is_local_model("openai-codex/gpt-5.4") is False
    assert manager.is_local_model("openai/gpt-4o-mini") is False
    assert manager.is_local_model("codex-cli/gpt-5.4") is False
    assert manager.is_local_model("claude-cli/opus-4.6") is False
    assert manager.is_local_model("google-antigravity/gemini-3.1-pro-preview") is False
    assert manager.is_local_model("google-gemini-cli/gemini-3.1-pro-preview") is False
    assert manager.is_local_model("qwen-portal/coder-model") is False


@pytest.mark.asyncio
async def test_verify_model_access_accepts_non_gemini_cloud_model_without_local_cache(manager: ModelManager) -> None:
    """Нелокальные non-Gemini модели не должны заваливаться на проверке как будто это LM Studio cache miss."""
    ok = await manager.verify_model_access("openai-codex/gpt-5.4")
    assert ok is True


@pytest.mark.asyncio
async def test_health_check_uses_fresh_loaded_cache_without_discovery(manager: ModelManager) -> None:
    manager._models_cache = {
        "model-1": ModelInfo("model-1", "Model 1", ModelType.LOCAL_MLX, size_gb=5.0)
    }
    manager._loaded_models_cache = ["model-1"]
    manager._loaded_models_cache_ts = time.time()

    with patch("src.model_manager.is_lm_studio_available", new=AsyncMock(side_effect=AssertionError("availability probe не должен вызываться"))):
        with patch.object(manager, "discover_models", new=AsyncMock(side_effect=AssertionError("discover_models не должен вызываться"))):
            result = await manager.health_check()

    assert result["status"] == "healthy"
    assert result["models_count"] == 1
    assert result["loaded_models"] == ["model-1"]


@pytest.mark.asyncio
async def test_health_check_uses_lightweight_availability_probe_when_loaded_cache_empty(manager: ModelManager) -> None:
    with patch("src.model_manager.is_lm_studio_available", new=AsyncMock(return_value=True)) as probe:
        with patch.object(manager, "discover_models", new=AsyncMock(side_effect=AssertionError("discover_models не должен вызываться"))):
            result = await manager.health_check()

    assert result["status"] == "healthy"
    assert result["loaded_models"] == []
    assert probe.await_count == 1


@pytest.mark.asyncio
async def test_get_best_model_cloud_when_force_cloud(manager: ModelManager) -> None:
    with patch("src.model_manager.config") as mock_config:
        mock_config.FORCE_CLOUD = True
        mock_config.LM_STUDIO_URL = "http://mock-url"
        mock_config.LM_STUDIO_API_KEY = ""
        mock_config.MAX_RAM_GB = 24
        mock_config.GEMINI_API_KEY = "dummy"
        mock_config.LOCAL_PREFERRED_MODEL = ""
        mock_config.MODEL = "google/gemini-2.0-flash"
        mm = ModelManager()
        with patch.object(mm._router, "get_best_model", new=AsyncMock(return_value="google/gemini-2.0-flash")):
            best = await mm.get_best_model()
            assert best.startswith("google/")


@pytest.mark.asyncio
async def test_ensure_model_loaded_fallbacks_to_lighter_local_candidate(manager: ModelManager) -> None:
    manager._models_cache = {
        "nvidia/nemotron-3-nano": ModelInfo("nvidia/nemotron-3-nano", "Heavy", ModelType.LOCAL_MLX, size_gb=16.57),
        "text-embedding-nomic-embed-text-v1.5": ModelInfo(
            "text-embedding-nomic-embed-text-v1.5",
            "Embedding",
            ModelType.LOCAL_MLX,
            size_gb=0.4,
        ),
        "qwen2.5-coder-7b-instruct-mlx": ModelInfo("qwen2.5-coder-7b-instruct-mlx", "Light", ModelType.LOCAL_MLX, size_gb=4.0),
    }

    with patch("src.model_manager.config") as mock_config:
        mock_config.LOCAL_PREFERRED_MODEL = "nemotron-3-nano"
        mock_config.LOCAL_PREFERRED_VISION_MODEL = ""
        mock_config.LOCAL_AUTOLOAD_FALLBACK_LIMIT = 3
        mock_config.FORCE_CLOUD = False
        mock_config.MODEL = "auto"

        manager.load_model = AsyncMock(side_effect=[False, True])  # type: ignore[method-assign]
        ok = await manager.ensure_model_loaded("local")

    assert ok is True
    assert manager.load_model.await_args_list[0].args[0] == "nvidia/nemotron-3-nano"
    assert manager.load_model.await_args_list[1].args[0] == "qwen2.5-coder-7b-instruct-mlx"


@pytest.mark.asyncio
async def test_resolve_preferred_local_model_skips_embedding_only_candidates(manager: ModelManager) -> None:
    manager._models_cache = {
        "text-embedding-nomic-embed-text-v1.5": ModelInfo(
            "text-embedding-nomic-embed-text-v1.5",
            "Embedding",
            ModelType.LOCAL_MLX,
            size_gb=0.4,
        ),
        "qwen2.5-coder-7b-instruct-mlx": ModelInfo(
            "qwen2.5-coder-7b-instruct-mlx",
            "Chat",
            ModelType.LOCAL_MLX,
            size_gb=4.0,
        ),
    }

    resolved = await manager.resolve_preferred_local_model(has_photo=False)
    assert resolved == "qwen2.5-coder-7b-instruct-mlx"


@pytest.mark.asyncio
async def test_resolve_preferred_local_model_photo_prefers_local_preferred_over_stale_current(
    manager: ModelManager,
) -> None:
    manager._models_cache = {
        "qwen3.5-27b": ModelInfo(
            "qwen3.5-27b",
            "Qwen 27B",
            ModelType.LOCAL_MLX,
            size_gb=16.0,
            supports_vision=True,
        ),
        "qwen3.5-9b-mlx": ModelInfo(
            "qwen3.5-9b-mlx",
            "Qwen 9B",
            ModelType.LOCAL_MLX,
            size_gb=6.0,
            supports_vision=True,
        ),
    }
    manager._current_model = "qwen3.5-27b"

    with patch("src.model_manager.config") as mock_config:
        mock_config.LOCAL_PREFERRED_VISION_MODEL = "auto"
        mock_config.LOCAL_PREFERRED_MODEL = "qwen3.5-9b-mlx"
        resolved = await manager.resolve_preferred_local_model(has_photo=True)

    assert resolved == "qwen3.5-9b-mlx"


@pytest.mark.asyncio
async def test_resolve_preferred_local_model_photo_vision_hint_has_priority(
    manager: ModelManager,
) -> None:
    manager._models_cache = {
        "qwen3.5-27b": ModelInfo(
            "qwen3.5-27b",
            "Qwen 27B",
            ModelType.LOCAL_MLX,
            size_gb=16.0,
            supports_vision=True,
        ),
        "qwen3.5-9b-mlx": ModelInfo(
            "qwen3.5-9b-mlx",
            "Qwen 9B",
            ModelType.LOCAL_MLX,
            size_gb=6.0,
            supports_vision=True,
        ),
    }
    manager._current_model = "qwen3.5-9b-mlx"

    with patch("src.model_manager.config") as mock_config:
        mock_config.LOCAL_PREFERRED_VISION_MODEL = "qwen3.5-27b"
        mock_config.LOCAL_PREFERRED_MODEL = "qwen3.5-9b-mlx"
        resolved = await manager.resolve_preferred_local_model(has_photo=True)

    assert resolved == "qwen3.5-27b"


@pytest.mark.asyncio
async def test_resolve_preferred_local_model_photo_auto_prefers_cloud_over_arbitrary_small_vision_model(
    manager: ModelManager,
) -> None:
    manager._models_cache = {
        "qwen2-vl-2b-instruct-abliterated-mlx": ModelInfo(
            "qwen2-vl-2b-instruct-abliterated-mlx",
            "Qwen VL 2B",
            ModelType.LOCAL_MLX,
            size_gb=3.2,
            supports_vision=True,
        ),
    }

    with patch("src.model_manager.config") as mock_config:
        mock_config.LOCAL_PREFERRED_VISION_MODEL = "auto"
        mock_config.LOCAL_PREFERRED_MODEL = "nvidia/nemotron-3-nano"
        resolved = await manager.resolve_preferred_local_model(has_photo=True)

    assert resolved is None


@pytest.mark.asyncio
async def test_resolve_preferred_local_model_photo_smallest_keeps_explicit_local_vision_opt_in(
    manager: ModelManager,
) -> None:
    manager._models_cache = {
        "qwen2-vl-2b-instruct-abliterated-mlx": ModelInfo(
            "qwen2-vl-2b-instruct-abliterated-mlx",
            "Qwen VL 2B",
            ModelType.LOCAL_MLX,
            size_gb=3.2,
            supports_vision=True,
        ),
        "qwen2.5-vl-7b-instruct-mlx": ModelInfo(
            "qwen2.5-vl-7b-instruct-mlx",
            "Qwen VL 7B",
            ModelType.LOCAL_MLX,
            size_gb=7.0,
            supports_vision=True,
        ),
    }

    with patch("src.model_manager.config") as mock_config:
        mock_config.LOCAL_PREFERRED_VISION_MODEL = "smallest"
        mock_config.LOCAL_PREFERRED_MODEL = "nvidia/nemotron-3-nano"
        resolved = await manager.resolve_preferred_local_model(has_photo=True)

    assert resolved == "qwen2-vl-2b-instruct-abliterated-mlx"


@pytest.mark.asyncio
async def test_missing_local_model_path_is_temporarily_excluded_from_candidates(manager: ModelManager) -> None:
    manager._models_cache = {
        "broken-local-model": ModelInfo(
            "broken-local-model",
            "Broken",
            ModelType.LOCAL_MLX,
            size_gb=2.0,
        ),
        "healthy-local-model": ModelInfo(
            "healthy-local-model",
            "Healthy",
            ModelType.LOCAL_MLX,
            size_gb=3.0,
        ),
    }

    missing_resp = MagicMock()
    missing_resp.status_code = 500
    missing_resp.text = (
        "FileNotFoundError: [Errno 2] No such file or directory: "
        "'/Volumes/4TB SSD/LMStudio_models/.../config.json'"
    )
    missing_resp.json.return_value = {"error": {"type": "model_load_failed"}}

    legacy_unsupported = MagicMock()
    legacy_unsupported.status_code = 200
    legacy_unsupported.text = '{"error":"Unexpected endpoint or method. (POST /v1/models/load)"}'
    legacy_unsupported.json.return_value = {"error": {"message": "Unexpected endpoint or method"}}

    manager._http_client.post.side_effect = [missing_resp, legacy_unsupported]
    ok = await manager._do_load_model("broken-local-model", size_gb=2.0)

    assert ok is False
    assert manager._is_local_model_temporarily_excluded("broken-local-model") is True
    assert manager._legacy_load_endpoint_supported is False

    candidates = await manager._local_candidates(has_photo=False)
    candidate_ids = [mid for mid, _ in candidates]
    assert "broken-local-model" not in candidate_ids
    assert "healthy-local-model" in candidate_ids


@pytest.mark.asyncio
async def test_single_local_mode_unloads_extra_models_when_target_already_loaded(
    manager: ModelManager,
) -> None:
    """
    В SINGLE_LOCAL_MODEL_MODE при наличии целевой модели и лишних loaded-инстансов
    менеджер должен выгрузить лишнее и оставить только target.
    """
    manager._models_cache = {
        "nvidia/nemotron-3-nano": ModelInfo(
            "nvidia/nemotron-3-nano",
            "Nemotron",
            ModelType.LOCAL_MLX,
            size_gb=17.79,
        ),
        "zai-org/glm-4.6v-flash": ModelInfo(
            "zai-org/glm-4.6v-flash",
            "GLM Vision",
            ModelType.LOCAL_MLX,
            size_gb=7.09,
        ),
    }
    manager.get_loaded_models = AsyncMock(
        return_value=["nvidia/nemotron-3-nano", "zai-org/glm-4.6v-flash"]
    )

    with patch("src.model_manager.psutil.virtual_memory") as mock_mem:
        mock_mem.return_value.available = 20 * 1024**3

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.text = "{}"
        ok_resp.json.return_value = {}
        manager._http_client.post.return_value = ok_resp

        result = await manager.load_model("nvidia/nemotron-3-nano")

        assert result is True
        assert manager._current_model == "nvidia/nemotron-3-nano"
        unload_call = manager._http_client.post.call_args_list[0]
        assert unload_call.args[0].endswith("/api/v1/models/unload")
        assert unload_call.kwargs["json"].get("identifier") == "zai-org/glm-4.6v-flash"
