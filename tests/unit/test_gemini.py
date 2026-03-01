# -*- coding: utf-8 -*-
"""Тесты cloud-конфига Gemini после стабилизации fallback-цепочки."""

from unittest.mock import AsyncMock, patch

import pytest

from src.config import config
from src.model_manager import model_manager


@pytest.mark.asyncio
async def test_gemini_config_has_google_models() -> None:
    """Проверяем, что в конфиге есть хотя бы одна google/gemini модель."""
    assert any(model_id.startswith("google/gemini") for model_id in config.GEMINI_MODELS)


@pytest.mark.asyncio
async def test_model_discovery_returns_cloud_candidates() -> None:
    """ModelManager должен обнаруживать cloud-кандидаты google/* при скане."""
    models = await model_manager.discover_models()
    gemini_ids = [m.id for m in models if m.id.startswith("google/")]
    assert len(gemini_ids) > 0


@pytest.mark.asyncio
async def test_get_best_model_returns_non_empty_value() -> None:
    """Fallback-роутинг всегда должен вернуть валидный model id."""
    with patch("src.model_manager.is_lm_studio_available", new=AsyncMock(return_value=False)):
        best = await model_manager.get_best_model()
    assert isinstance(best, str)
    assert best.strip() != ""
