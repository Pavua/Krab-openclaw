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


def _bytes_to_gb(value: int | float) -> float:
    """Переводит байты в гигабайты (GiB) с округлением."""
    return round(float(value) / (1024**3), 2)


def _normalize_lm_models(raw: list[dict]) -> list[dict]:
    """Нормализует ответ LM Studio (v1 или OpenAI-compat) в список {id, name, vision, size_gb}."""
    out: list[dict] = []
    for m in raw:
        mid = m.get("key") or m.get("id", "")
        name = m.get("display_name") or m.get("name", mid)
        caps = m.get("capabilities") or {}
        vision = caps.get("vision", False) if isinstance(caps, dict) else False
        size_gb = 0.0
        size_bytes = m.get("size_bytes")
        try:
            if size_bytes is not None and float(size_bytes) > 0:
                size_gb = _bytes_to_gb(float(size_bytes))
        except (TypeError, ValueError):
            size_gb = 0.0
        out.append({**m, "id": mid, "name": name, "vision": vision, "size_gb": size_gb})
    return out


async def fetch_lm_studio_models_list(
    base_url: str,
    *,
    timeout: float = DEFAULT_LM_STUDIO_TIMEOUT,
    client: Optional["AsyncClient"] = None,
) -> list[dict]:
    """
    Запрашивает список моделей LM Studio с fallback: /api/v1/models -> /v1/models.

    v1 API: {"models": [{"key": "...", "display_name": "...", "capabilities": {"vision": bool}}]}.
    OpenAI-compat: {"data": [{"id": "...", "name": "..."}]}.
    """
    base = base_url.rstrip("/")
    urls = [f"{base}/api/v1/models", f"{base}/v1/models"]
    used_client = client

    for url in urls:
        try:
            if used_client:
                resp = await used_client.get(url, timeout=timeout)
            else:
                async with httpx.AsyncClient(timeout=timeout) as ac:
                    resp = await ac.get(url, timeout=timeout)
            if resp.status_code != 200:
                continue
            data = resp.json()
            # v1: {"models": [...]}
            raw = data.get("models", data.get("data", []))
            if raw:
                return _normalize_lm_models(raw)
        except (httpx.HTTPError, OSError, ValueError):
            continue
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
            # В приоритете используем фактический размер из LM Studio API v1.
            size = float(model_data.get("size_gb") or 0.0)
            if size <= 0:
                # Fallback-эвристика для старого/урезанного API.
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
            # Vision: из capabilities.vision (v1 API) или эвристика
            vision_api = model_data.get("vision", False)
            vision_heuristic = (
                "vl" in model_id.lower()
                or "vision" in model_id.lower()
                or "glm-4" in model_id.lower()
            )
            model = ModelInfo(
                id=model_id,
                name=model_data.get("name", model_id),
                type=model_type,
                status=ModelStatus.AVAILABLE,
                size_gb=size,
                supports_vision=vision_api or vision_heuristic,
            )
            models.append(model)
            models_cache[model_id] = model
        logger.debug("models_discovered", count=len(models))

    google_models = await fetch_google_models_async()
    models.extend(google_models)
    return models
