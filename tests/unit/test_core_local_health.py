# -*- coding: utf-8 -*-
"""Тесты для src/core/local_health.py — проверка LM Studio и обнаружение локальных моделей."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.local_health import (
    _bytes_to_gb,
    _detect_model_type,
    _normalize_lm_models,
    discover_models,
    fetch_lm_studio_models_list,
    is_lm_studio_available,
)
from src.core.model_types import ModelStatus, ModelType

# ---------------------------------------------------------------------------
# _detect_model_type
# ---------------------------------------------------------------------------


class TestDetectModelType:
    """Классификация типа модели по строковому ID."""

    def test_mlx_in_id(self):
        assert _detect_model_type("llama-3.2-mlx-q4") == ModelType.LOCAL_MLX

    def test_gguf_in_id(self):
        assert _detect_model_type("mistral-7b-instruct.gguf") == ModelType.LOCAL_GGUF

    def test_gemini_in_id(self):
        assert _detect_model_type("google/gemini-2.5-flash") == ModelType.CLOUD_GEMINI

    def test_unknown_defaults_to_mlx(self):
        """Неизвестный ID — дефолтный тип LOCAL_MLX."""
        assert _detect_model_type("some-unknown-model") == ModelType.LOCAL_MLX

    def test_case_insensitive(self):
        assert _detect_model_type("MyModel-GGUF") == ModelType.LOCAL_GGUF


# ---------------------------------------------------------------------------
# _bytes_to_gb
# ---------------------------------------------------------------------------


class TestBytesToGb:
    """Конвертация байтов в гигабайты."""

    def test_one_gib(self):
        assert _bytes_to_gb(1024**3) == 1.0

    def test_zero(self):
        assert _bytes_to_gb(0) == 0.0

    def test_rounding(self):
        # 1.5 GiB
        assert _bytes_to_gb(int(1.5 * 1024**3)) == 1.5

    def test_small_value(self):
        # 512 MB == 0.5 GiB
        assert _bytes_to_gb(512 * 1024 * 1024) == 0.5


# ---------------------------------------------------------------------------
# _normalize_lm_models
# ---------------------------------------------------------------------------


class TestNormalizeLmModels:
    """Нормализация ответов LM Studio v1 и OpenAI-compat форматов."""

    def test_v1_format(self):
        """v1 API с полями key/display_name/capabilities."""
        raw = [
            {
                "key": "lmstudio-community/llama-3.2-1b-instruct",
                "display_name": "Llama 3.2 1B",
                "capabilities": {"vision": True},
                "size_bytes": 1024**3,
            }
        ]
        result = _normalize_lm_models(raw)
        assert len(result) == 1
        assert result[0]["id"] == "lmstudio-community/llama-3.2-1b-instruct"
        assert result[0]["name"] == "Llama 3.2 1B"
        assert result[0]["vision"] is True
        assert result[0]["size_gb"] == 1.0

    def test_openai_compat_format(self):
        """OpenAI-compat формат с полями id/name без capabilities."""
        raw = [{"id": "mistral-7b", "name": "Mistral 7B"}]
        result = _normalize_lm_models(raw)
        assert result[0]["id"] == "mistral-7b"
        assert result[0]["name"] == "Mistral 7B"
        assert result[0]["vision"] is False
        assert result[0]["size_gb"] == 0.0

    def test_missing_size_bytes(self):
        """Отсутствующий size_bytes -> size_gb = 0.0."""
        raw = [{"id": "no-size-model"}]
        result = _normalize_lm_models(raw)
        assert result[0]["size_gb"] == 0.0

    def test_invalid_size_bytes(self):
        """Невалидный size_bytes не крашит, возвращает 0.0."""
        raw = [{"id": "m", "size_bytes": "not-a-number"}]
        result = _normalize_lm_models(raw)
        assert result[0]["size_gb"] == 0.0

    def test_empty_list(self):
        assert _normalize_lm_models([]) == []

    def test_non_dict_capabilities(self):
        """capabilities не dict — vision безопасно падает в False."""
        raw = [{"id": "m", "capabilities": "some-string"}]
        result = _normalize_lm_models(raw)
        assert result[0]["vision"] is False


# ---------------------------------------------------------------------------
# is_lm_studio_available
# ---------------------------------------------------------------------------


class TestIsLmStudioAvailable:
    """Проверка доступности LM Studio через HTTP."""

    @pytest.mark.asyncio
    async def test_returns_true_on_200(self):
        """Первый URL возвращает 200 -> True."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        client = AsyncMock()
        client.get.return_value = mock_resp

        with patch("src.core.local_health.build_lm_studio_auth_headers", return_value={}):
            result = await is_lm_studio_available("http://localhost:1234", client=client)

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_non_200(self):
        """Оба URL возвращают не-200 -> False."""
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        client = AsyncMock()
        client.get.return_value = mock_resp

        with patch("src.core.local_health.build_lm_studio_auth_headers", return_value={}):
            result = await is_lm_studio_available("http://localhost:1234", client=client)

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_network_error(self):
        """HTTPError при подключении -> False (не крашит)."""
        client = AsyncMock()
        client.get.side_effect = httpx.ConnectError("refused")

        with patch("src.core.local_health.build_lm_studio_auth_headers", return_value={}):
            result = await is_lm_studio_available("http://localhost:1234", client=client)

        assert result is False

    @pytest.mark.asyncio
    async def test_fallback_second_url_on_first_fail(self):
        """Первый URL упал с ошибкой, второй возвращает 200 -> True."""
        ok_resp = MagicMock()
        ok_resp.status_code = 200

        call_count = 0

        async def side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("refused")
            return ok_resp

        client = AsyncMock()
        client.get.side_effect = side_effect

        with patch("src.core.local_health.build_lm_studio_auth_headers", return_value={}):
            result = await is_lm_studio_available("http://localhost:1234", client=client)

        assert result is True
        assert call_count == 2


# ---------------------------------------------------------------------------
# fetch_lm_studio_models_list
# ---------------------------------------------------------------------------


class TestFetchLmStudioModelsList:
    """Получение списка моделей LM Studio с fallback URL."""

    @pytest.mark.asyncio
    async def test_returns_normalized_models(self):
        """Успешный ответ v1 API — список нормализуется."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [
                {"key": "llama-mlx", "display_name": "Llama MLX", "capabilities": {"vision": False}}
            ]
        }
        client = AsyncMock()
        client.get.return_value = mock_resp

        with patch("src.core.local_health.build_lm_studio_auth_headers", return_value={}):
            result = await fetch_lm_studio_models_list("http://localhost:1234", client=client)

        assert len(result) == 1
        assert result[0]["id"] == "llama-mlx"

    @pytest.mark.asyncio
    async def test_returns_empty_on_non_200(self):
        """Не-200 ответ с обоих URL -> пустой список."""
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        client = AsyncMock()
        client.get.return_value = mock_resp

        with patch("src.core.local_health.build_lm_studio_auth_headers", return_value={}):
            result = await fetch_lm_studio_models_list("http://localhost:1234", client=client)

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_network_error(self):
        """Сетевая ошибка -> пустой список, не крашит."""
        client = AsyncMock()
        client.get.side_effect = httpx.ConnectError("refused")

        with patch("src.core.local_health.build_lm_studio_auth_headers", return_value={}):
            result = await fetch_lm_studio_models_list("http://localhost:1234", client=client)

        assert result == []

    @pytest.mark.asyncio
    async def test_openai_compat_data_field(self):
        """OpenAI-compat формат использует ключ 'data'."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"id": "gpt-local", "name": "GPT Local"}]}
        client = AsyncMock()
        client.get.return_value = mock_resp

        with patch("src.core.local_health.build_lm_studio_auth_headers", return_value={}):
            result = await fetch_lm_studio_models_list("http://localhost:1234", client=client)

        assert len(result) == 1
        assert result[0]["id"] == "gpt-local"

    @pytest.mark.asyncio
    async def test_strips_trailing_slash_from_url(self):
        """Базовый URL с trailing slash обрабатывается корректно."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": [{"key": "m", "display_name": "M"}]}
        client = AsyncMock()
        client.get.return_value = mock_resp

        with patch("src.core.local_health.build_lm_studio_auth_headers", return_value={}):
            result = await fetch_lm_studio_models_list("http://localhost:1234///", client=client)

        # Убедились что не крашит, получили результат
        assert len(result) == 1


# ---------------------------------------------------------------------------
# discover_models
# ---------------------------------------------------------------------------


class TestDiscoverModels:
    """Обнаружение всех моделей: локальных (LM Studio) + облачных (callback)."""

    @pytest.mark.asyncio
    async def test_discovers_local_and_cloud(self):
        """LM Studio возвращает модели, облачный callback тоже — все в результате."""
        from src.core.model_types import ModelInfo, ModelType

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [{"key": "llama-mlx", "display_name": "Llama MLX", "size_bytes": 0}]
        }
        client = AsyncMock()
        client.get.return_value = mock_resp

        cloud_model = ModelInfo(
            id="google/gemini-flash",
            name="Gemini Flash",
            type=ModelType.CLOUD_GEMINI,
            status=ModelStatus.AVAILABLE,
        )

        async def fake_google():
            return [cloud_model]

        cache: dict = {}
        with patch("src.core.local_health.build_lm_studio_auth_headers", return_value={}):
            result = await discover_models(
                "http://localhost:1234",
                client,
                models_cache=cache,
                fetch_google_models_async=fake_google,
            )

        local_ids = [m.id for m in result if m.type != ModelType.CLOUD_GEMINI]
        cloud_ids = [m.id for m in result if m.type == ModelType.CLOUD_GEMINI]
        assert "llama-mlx" in local_ids
        assert "google/gemini-flash" in cloud_ids

    @pytest.mark.asyncio
    async def test_lm_studio_offline_still_returns_cloud(self):
        """LM Studio недоступен — облачные модели всё равно добавляются."""
        from src.core.model_types import ModelInfo, ModelType

        client = AsyncMock()
        client.get.side_effect = httpx.ConnectError("refused")

        cloud_model = ModelInfo(
            id="google/gemini-pro",
            name="Gemini Pro",
            type=ModelType.CLOUD_GEMINI,
            status=ModelStatus.AVAILABLE,
        )

        async def fake_google():
            return [cloud_model]

        cache: dict = {}
        with patch("src.core.local_health.build_lm_studio_auth_headers", return_value={}):
            result = await discover_models(
                "http://localhost:1234",
                client,
                models_cache=cache,
                fetch_google_models_async=fake_google,
            )

        assert len(result) == 1
        assert result[0].id == "google/gemini-pro"

    @pytest.mark.asyncio
    async def test_vision_heuristic_applied(self):
        """Модель с 'vl' в названии получает supports_vision=True."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [{"key": "qwen2-vl-7b", "display_name": "Qwen2 VL 7B", "size_bytes": 0}]
        }
        client = AsyncMock()
        client.get.return_value = mock_resp

        async def no_cloud():
            return []

        cache: dict = {}
        with patch("src.core.local_health.build_lm_studio_auth_headers", return_value={}):
            result = await discover_models(
                "http://localhost:1234",
                client,
                models_cache=cache,
                fetch_google_models_async=no_cloud,
            )

        assert result[0].supports_vision is True

    @pytest.mark.asyncio
    async def test_model_added_to_cache(self):
        """Обнаруженные LM Studio модели добавляются в models_cache."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [{"key": "my-local-model", "display_name": "Local", "size_bytes": 0}]
        }
        client = AsyncMock()
        client.get.return_value = mock_resp

        async def no_cloud():
            return []

        cache: dict = {}
        with patch("src.core.local_health.build_lm_studio_auth_headers", return_value={}):
            await discover_models(
                "http://localhost:1234",
                client,
                models_cache=cache,
                fetch_google_models_async=no_cloud,
            )

        assert "my-local-model" in cache

    @pytest.mark.asyncio
    async def test_size_heuristics_for_known_suffixes(self):
        """Эвристика размера по суффиксам 7b/13b/70b применяется при size_gb=0."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [
                {"key": "llama-7b-instruct", "size_bytes": 0},
                {"key": "llama-70b-instruct", "size_bytes": 0},
            ]
        }
        client = AsyncMock()
        client.get.return_value = mock_resp

        async def no_cloud():
            return []

        cache: dict = {}
        with patch("src.core.local_health.build_lm_studio_auth_headers", return_value={}):
            result = await discover_models(
                "http://localhost:1234",
                client,
                models_cache=cache,
                fetch_google_models_async=no_cloud,
            )

        by_id = {m.id: m for m in result}
        assert by_id["llama-7b-instruct"].size_gb == 5.0
        assert by_id["llama-70b-instruct"].size_gb == 40.0
