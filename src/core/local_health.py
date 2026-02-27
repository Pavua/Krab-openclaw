# -*- coding: utf-8 -*-
"""
Проверка LM Studio и обнаружение локальных моделей (Фаза 4.1).

Содержит: is_lm_studio_available, fetch_lm_studio_models_list, discover_models.
Вся логика, связанная с LM Studio, передаётся зависимостями (url, client, callbacks).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable, Optional

import httpx
import structlog

from .model_types import ModelInfo, ModelStatus, ModelType

if TYPE_CHECKING:
    from httpx import AsyncClient

logger = structlog.get_logger(__name__)

# Таймаут по умолчанию для запросов к LM Studio
DEFAULT_LM_STUDIO_TIMEOUT = 30.0


def _detect_model_type(model_id: str) -> ModelType:
    """Определяет тип модели по ID (чистая функция для LM Studio списка)."""
    model_id_lower = model_id.lower()
    if "mlx" in model_id_lower:
        return ModelType.LOCAL_MLX
    if "gguf" in model_id_lower:
        return ModelType.LOCAL_GGUF
    if "gemini" in model_id_lower:
        return ModelType.CLOUD_GEMINI
    return ModelType.LOCAL_MLX


async def is_lm_studio_available(
    base_url: str,
    *,
    timeout: float = DEFAULT_LM_STUDIO_TIMEOUT,
    client: Optional["AsyncClient"] = None,
) -> bool:
    """
    Проверяет доступность LM Studio по GET {base_url}/v1/models.

    Returns True при status_code == 200, иначе False (включая сетевые ошибки).
    """
    url = f"{base_url.rstrip('/')}/v1/models"
    if client is not None:
        try:
            resp = await client.get(url, timeout=timeout)
            return resp.status_code == 200
        except (httpx.HTTPError, OSError):
            return False
    async with httpx.AsyncClient(timeout=timeout) as ac:
        try:
            resp = await ac.get(url)
            return resp.status_code == 200
        except (httpx.HTTPError, OSError):
            return False


async def fetch_lm_studio_models_list(
    base_url: str,
    *,
    timeout: float = DEFAULT_LM_STUDIO_TIMEOUT,
    client: Optional["AsyncClient"] = None,
) -> list[dict]:
    """
    Запрашивает GET {base_url}/v1/models и возвращает список моделей из JSON.

    Ответ LM Studio: {"data": [{"id": "...", "name": "...", ...}, ...]}.
    Возвращает data.get("data", []) при успехе, иначе [].
    """
    url = f"{base_url.rstrip('/')}/v1/models"
    if client is not None:
        try:
            resp = await client.get(url, timeout=timeout)
            if resp.status_code != 200:
                return []
            data = resp.json()
            return list(data.get("data", []))
        except (httpx.HTTPError, OSError, ValueError):
            return []
    async with httpx.AsyncClient(timeout=timeout) as ac:
        try:
            resp = await ac.get(url, timeout=timeout)
            if resp.status_code != 200:
                return []
            data = resp.json()
            return list(data.get("data", []))
        except (httpx.HTTPError, OSError, ValueError):
            return []


async def discover_models(
    lm_studio_url: str,
    client: "AsyncClient",
    *,
    models_cache: dict[str, ModelInfo],
    fetch_google_models_async: Callable[[], Awaitable[list[ModelInfo]]],
    timeout: float = DEFAULT_LM_STUDIO_TIMEOUT,
) -> list[ModelInfo]:
    """
    Обнаруживает все доступные модели: LM Studio + облачные (через callback).

    Зависимости передаются аргументами (url, client, кэш, callback для Google).
    """
    models: list[ModelInfo] = []
    model_list = await fetch_lm_studio_models_list(
        lm_studio_url, client=client, timeout=timeout
    )
    if not model_list:
        logger.warning("lm_studio_offline")
    else:
        for model_data in model_list:
            model_id = model_data.get("id", "")
            model_type = _detect_model_type(model_id)
            size = 8.0
            if "7b" in model_id.lower():
                size = 5.0
            if "13b" in model_id.lower():
                size = 10.0
            if "30b" in model_id.lower() or "32b" in model_id.lower():
                size = 18.0
            if "70b" in model_id.lower():
                size = 40.0
            if "q4" in model_id.lower():
                size *= 0.6
            model = ModelInfo(
                id=model_id,
                name=model_data.get("name", model_id),
                type=model_type,
                status=ModelStatus.AVAILABLE,
                size_gb=size,
                supports_vision=(
                    "vl" in model_id.lower() or "vision" in model_id.lower()
                ),
            )
            models.append(model)
            models_cache[model_id] = model
        logger.info("models_discovered", count=len(models))

    google_models = await fetch_google_models_async()
    models.extend(google_models)
    return models
