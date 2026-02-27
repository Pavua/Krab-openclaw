# -*- coding: utf-8 -*-
"""
Роутер выбора лучшей модели: локаль (LM Studio) или облако (Фаза 4.1, Шаг 3).

Использует local_health для проверки LM Studio и cloud_gateway для облачного fallback.
Типы из model_types.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import structlog

from .cloud_gateway import (
    get_best_cloud_model,
    verify_gemini_access as cloud_verify_gemini_access,
)
from .local_health import is_lm_studio_available
from .model_types import ModelInfo, ModelStatus, ModelType

if TYPE_CHECKING:
    from httpx import AsyncClient

logger = structlog.get_logger(__name__)

# Дефолтная облачная модель при отсутствии выбора
DEFAULT_CLOUD_MODEL = "google/gemini-2.0-flash"


class ModelRouter:
    """
    Выбор лучшей доступной модели по цепочке fallback: локаль (LM Studio) затем облако.
    Использует local_health и cloud_gateway, не держит конфиг — зависимости передаются в __init__.
    """

    def __init__(
        self,
        lm_studio_url: str,
        gemini_api_key: Optional[str],
        http_client: "AsyncClient",
        fallback_chain: list[str],
        *,
        config_model: Optional[str] = None,
    ):
        self.lm_studio_url = lm_studio_url
        self.gemini_api_key = gemini_api_key
        self._http_client = http_client
        self.fallback_chain = fallback_chain
        self.config_model = config_model

    async def get_best_model(self) -> str:
        """
        Возвращает лучшую доступную модель: явная из конфига, затем локаль (LM Studio), затем облако.
        """
        if self.config_model and self.config_model != "auto":
            return self.config_model

        # Сначала пробуем локаль по цепочке
        for model_id in self.fallback_chain:
            if "local" in model_id.lower() or "mlx" in model_id.lower():
                if self.lm_studio_url:
                    try:
                        if await is_lm_studio_available(
                            self.lm_studio_url,
                            client=self._http_client,
                        ):
                            return "local"
                    except Exception as e:
                        logger.debug(
                            "lm_studio_check_failed",
                            error=str(e),
                        )
                        continue
                break  # дальше по цепочке не ищем локаль

        # Облачный fallback через cloud_gateway
        return await get_best_cloud_model(
            self.gemini_api_key,
            self._http_client,
            config_model=self.config_model,
            verify_fn=cloud_verify_gemini_access,
        )
