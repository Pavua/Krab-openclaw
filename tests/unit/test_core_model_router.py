# -*- coding: utf-8 -*-
"""Тесты для src/core/model_router.py — ModelRouter, выбор локали/облака."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.model_router import DEFAULT_CLOUD_MODEL, ModelRouter

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_router(
    *,
    lm_studio_url: str = "http://localhost:1234",
    gemini_api_key: str | None = "AIzaTestKey",
    fallback_chain: list[str] | None = None,
    config_model: str | None = None,
) -> ModelRouter:
    """Создаёт ModelRouter с фейковыми HTTP-клиентами."""
    if fallback_chain is None:
        fallback_chain = ["local/mlx-model", "google/gemini-2.5-flash"]
    return ModelRouter(
        lm_studio_url=lm_studio_url,
        gemini_api_key=gemini_api_key,
        local_http_client=AsyncMock(),
        cloud_http_client=AsyncMock(),
        fallback_chain=fallback_chain,
        config_model=config_model,
    )


# ---------------------------------------------------------------------------
# config_model pinned — облако/локаль обходятся
# ---------------------------------------------------------------------------


class TestModelRouterPinnedModel:
    """Если config_model задан и не 'auto' — возвращается без проверок."""

    @pytest.mark.asyncio
    async def test_pinned_model_returns_immediately(self):
        """config_model='my-model' возвращается без вызова lm_studio или cloud."""
        router = _make_router(config_model="my-model")
        with (
            patch("src.core.model_router.is_lm_studio_available") as mock_local,
            patch("src.core.model_router.get_best_cloud_model") as mock_cloud,
        ):
            result = await router.get_best_model()

        assert result == "my-model"
        mock_local.assert_not_called()
        mock_cloud.assert_not_called()

    @pytest.mark.asyncio
    async def test_pinned_model_with_has_photo(self):
        """config_model задан — has_photo не меняет поведение."""
        router = _make_router(config_model="pinned-vision")
        with patch("src.core.model_router.get_best_cloud_model") as mock_cloud:
            result = await router.get_best_model(has_photo=True)

        assert result == "pinned-vision"
        mock_cloud.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_config_model_not_pinned(self):
        """config_model='auto' НЕ считается pinned — выполняет routing."""
        router = _make_router(config_model="auto", fallback_chain=[])
        with patch(
            "src.core.model_router.get_best_cloud_model",
            new_callable=AsyncMock,
            return_value="google/gemini-2.5-flash",
        ) as mock_cloud:
            await router.get_best_model()

        mock_cloud.assert_called_once()

    @pytest.mark.asyncio
    async def test_none_config_model_not_pinned(self):
        """config_model=None — тоже не pinned."""
        router = _make_router(config_model=None, fallback_chain=[])
        with patch(
            "src.core.model_router.get_best_cloud_model",
            new_callable=AsyncMock,
            return_value=DEFAULT_CLOUD_MODEL,
        ) as mock_cloud:
            result = await router.get_best_model()

        mock_cloud.assert_called_once()
        assert result == DEFAULT_CLOUD_MODEL


# ---------------------------------------------------------------------------
# has_photo=True — форсируем облако
# ---------------------------------------------------------------------------


class TestModelRouterHasPhoto:
    """При has_photo=True всегда идём в облако (vision-модель)."""

    @pytest.mark.asyncio
    async def test_has_photo_calls_cloud_model(self):
        """has_photo=True вызывает get_best_cloud_model с gemini-2.5-flash."""
        router = _make_router()
        with patch(
            "src.core.model_router.get_best_cloud_model",
            new_callable=AsyncMock,
            return_value="google/gemini-2.5-flash",
        ) as mock_cloud:
            result = await router.get_best_model(has_photo=True)

        assert result == "google/gemini-2.5-flash"
        call_kwargs = mock_cloud.call_args.kwargs
        assert call_kwargs.get("config_model") == "google/gemini-2.5-flash"

    @pytest.mark.asyncio
    async def test_has_photo_skips_lm_studio(self):
        """has_photo=True — LM Studio не проверяется."""
        router = _make_router()
        with (
            patch("src.core.model_router.is_lm_studio_available") as mock_local,
            patch(
                "src.core.model_router.get_best_cloud_model",
                new_callable=AsyncMock,
                return_value="google/gemini-2.5-flash",
            ),
        ):
            await router.get_best_model(has_photo=True)

        mock_local.assert_not_called()


# ---------------------------------------------------------------------------
# LM Studio fallback logic
# ---------------------------------------------------------------------------


class TestModelRouterLocalFallback:
    """Приоритет: local/mlx → облако."""

    @pytest.mark.asyncio
    async def test_local_available_returns_local(self):
        """LM Studio доступен → возвращаем 'local'."""
        router = _make_router(
            fallback_chain=["local/mlx-model", "google/gemini-2.5-flash"],
        )
        with patch(
            "src.core.model_router.is_lm_studio_available",
            new_callable=AsyncMock,
            return_value=True,
        ):
            result = await router.get_best_model()

        assert result == "local"

    @pytest.mark.asyncio
    async def test_local_unavailable_falls_to_cloud(self):
        """LM Studio недоступен → переходим в облако."""
        router = _make_router(
            fallback_chain=["local/mlx-model", "google/gemini-2.5-flash"],
        )
        with (
            patch(
                "src.core.model_router.is_lm_studio_available",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "src.core.model_router.get_best_cloud_model",
                new_callable=AsyncMock,
                return_value="google/gemini-2.5-flash",
            ) as mock_cloud,
        ):
            result = await router.get_best_model()

        mock_cloud.assert_called_once()
        assert result == "google/gemini-2.5-flash"

    @pytest.mark.asyncio
    async def test_local_check_exception_falls_to_cloud(self):
        """Исключение при проверке LM Studio → fallback в облако."""
        router = _make_router(
            fallback_chain=["local/mlx-model"],
        )
        with (
            patch(
                "src.core.model_router.is_lm_studio_available",
                new_callable=AsyncMock,
                side_effect=OSError("connection refused"),
            ),
            patch(
                "src.core.model_router.get_best_cloud_model",
                new_callable=AsyncMock,
                return_value=DEFAULT_CLOUD_MODEL,
            ) as mock_cloud,
        ):
            await router.get_best_model()

        mock_cloud.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_lm_studio_url_skips_local_check(self):
        """Пустой lm_studio_url → локальная проверка пропускается."""
        router = _make_router(
            lm_studio_url="",
            fallback_chain=["local/mlx-model"],
        )
        with (
            patch("src.core.model_router.is_lm_studio_available") as mock_local,
            patch(
                "src.core.model_router.get_best_cloud_model",
                new_callable=AsyncMock,
                return_value=DEFAULT_CLOUD_MODEL,
            ),
        ):
            await router.get_best_model()

        mock_local.assert_not_called()

    @pytest.mark.asyncio
    async def test_chain_without_local_goes_straight_to_cloud(self):
        """Если в цепочке нет 'local'/'mlx' — сразу облако."""
        router = _make_router(
            fallback_chain=["google/gemini-2.5-flash", "google/gemini-pro"],
        )
        with (
            patch("src.core.model_router.is_lm_studio_available") as mock_local,
            patch(
                "src.core.model_router.get_best_cloud_model",
                new_callable=AsyncMock,
                return_value="google/gemini-2.5-flash",
            ) as mock_cloud,
        ):
            await router.get_best_model()

        mock_local.assert_not_called()
        mock_cloud.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_fallback_chain_goes_to_cloud(self):
        """Пустая fallback_chain → всё равно вызываем облако."""
        router = _make_router(fallback_chain=[])
        with patch(
            "src.core.model_router.get_best_cloud_model",
            new_callable=AsyncMock,
            return_value=DEFAULT_CLOUD_MODEL,
        ) as mock_cloud:
            await router.get_best_model()

        mock_cloud.assert_called_once()

    @pytest.mark.asyncio
    async def test_mlx_keyword_triggers_local_check(self):
        """'mlx' в названии модели тоже воспринимается как local."""
        router = _make_router(
            fallback_chain=["mlx/llama-3.2"],
        )
        with (
            patch(
                "src.core.model_router.is_lm_studio_available",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_local,
            patch("src.core.model_router.get_best_cloud_model") as mock_cloud,
        ):
            result = await router.get_best_model()

        mock_local.assert_called_once()
        mock_cloud.assert_not_called()
        assert result == "local"


# ---------------------------------------------------------------------------
# DEFAULT_CLOUD_MODEL — константа
# ---------------------------------------------------------------------------


class TestDefaultCloudModel:
    """Константа DEFAULT_CLOUD_MODEL задана и является строкой."""

    def test_default_cloud_model_is_string(self):
        assert isinstance(DEFAULT_CLOUD_MODEL, str)

    def test_default_cloud_model_not_empty(self):
        assert len(DEFAULT_CLOUD_MODEL) > 0

    def test_default_cloud_model_contains_gemini(self):
        assert "gemini" in DEFAULT_CLOUD_MODEL.lower()


# ---------------------------------------------------------------------------
# ModelRouter constructor stores attributes
# ---------------------------------------------------------------------------


class TestModelRouterInit:
    """Атрибуты сохраняются корректно."""

    def test_stores_all_constructor_params(self):
        local_client = MagicMock()
        cloud_client = MagicMock()
        chain = ["local/model", "google/gemini-2.5-flash"]

        router = ModelRouter(
            lm_studio_url="http://localhost:9999",
            gemini_api_key="key-xyz",
            local_http_client=local_client,
            cloud_http_client=cloud_client,
            fallback_chain=chain,
            config_model="my-model",
        )

        assert router.lm_studio_url == "http://localhost:9999"
        assert router.gemini_api_key == "key-xyz"
        assert router._local_http_client is local_client
        assert router._cloud_http_client is cloud_client
        assert router.fallback_chain == chain
        assert router.config_model == "my-model"

    def test_config_model_defaults_to_none(self):
        router = ModelRouter(
            lm_studio_url="url",
            gemini_api_key=None,
            local_http_client=MagicMock(),
            cloud_http_client=MagicMock(),
            fallback_chain=[],
        )
        assert router.config_model is None
