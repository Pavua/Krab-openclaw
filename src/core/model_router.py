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
)
from .cloud_gateway import (
    verify_gemini_access as cloud_verify_gemini_access,
)
from .local_health import is_lm_studio_available
from .long_context_router import (
    PROVIDER_MLX_LOCAL,
    select_provider_for_task,
)
from .pressure_aware_select import pressure_aware_model_select
from .provider_quarantine import provider_quarantine

if TYPE_CHECKING:
    from httpx import AsyncClient

logger = structlog.get_logger(__name__)

# Дефолтная облачная модель при отсутствии выбора
DEFAULT_CLOUD_MODEL = "google/gemini-2.5-flash"


class ModelRouter:
    """
    Выбор лучшей доступной модели по цепочке fallback: локаль (LM Studio) затем облако.
    Использует local_health и cloud_gateway, не держит конфиг — зависимости передаются в __init__.
    """

    def __init__(
        self,
        lm_studio_url: str,
        gemini_api_key: Optional[str],
        local_http_client: "AsyncClient",
        cloud_http_client: "AsyncClient",
        fallback_chain: list[str],
        *,
        config_model: Optional[str] = None,
    ):
        self.lm_studio_url = lm_studio_url
        self.gemini_api_key = gemini_api_key
        # Важно не смешивать LM Studio auth-заголовки с облачными запросами:
        # Google Gemini должен ходить через отдельный "чистый" клиент.
        self._local_http_client = local_http_client
        self._cloud_http_client = cloud_http_client
        self.fallback_chain = fallback_chain
        self.config_model = config_model

    async def get_best_model(
        self,
        *,
        has_photo: bool = False,
        task_type: str = "",
        prompt_tokens: int = 0,
    ) -> str:
        """
        Возвращает лучшую доступную модель.
        При has_photo=True возвращает облачную vision-модель (fallback).

        Wave 223: opt-in роутинг long-context / summarization / rag_retrieval
        задач на локальный MLX :8088 через env KRAB_LONG_CONTEXT_PROVIDER.
        Default — поведение не меняется (env OFF).
        """
        if self.config_model and self.config_model != "auto":
            return self.config_model

        if has_photo:
            return await get_best_cloud_model(
                self.gemini_api_key,
                self._cloud_http_client,
                config_model="google/gemini-2.5-flash",
                verify_fn=cloud_verify_gemini_access,
            )

        # Wave 223: opt-in long-context routing. Если env-gate активен и задача
        # подпадает под threshold/task_type — короткозамыкаем на local MLX.
        # Доступность LM Studio проверяется ниже стандартной веткой, поэтому
        # здесь маршрутизация — это hint, реальный сетевой эндпоинт берётся
        # из MLX_LOCAL_KV4_URL вызывающим кодом.
        if task_type or prompt_tokens:
            chosen = select_provider_for_task(task_type, prompt_tokens)
            if chosen == PROVIDER_MLX_LOCAL and self.lm_studio_url:
                try:
                    if await is_lm_studio_available(
                        self.lm_studio_url,
                        client=self._local_http_client,
                    ):
                        return "local"
                except Exception as e:  # noqa: BLE001
                    logger.debug("mlx_local_routing_check_failed", error=str(e))

        # Wave 94/97: skip local loop если провайдер в quarantine.
        # Env-gate default-ON, но fail-safe: ошибка quarantine-cache не блокирует routing.
        import os as _os  # noqa: PLC0415

        _quarantine_enabled = _os.getenv("KRAB_PROVIDER_QUARANTINE_ENABLED", "1").strip() != "0"
        _local_quarantined = False
        if _quarantine_enabled:
            try:
                _local_quarantined = provider_quarantine.is_provider_quarantined("local")
            except Exception as exc:  # noqa: BLE001
                logger.debug("provider_quarantine_check_failed", error=str(exc))
        if _local_quarantined:
            logger.info("model_router_local_skipped_quarantine")
            return await get_best_cloud_model(
                self.gemini_api_key,
                self._cloud_http_client,
                config_model=self.config_model,
                verify_fn=cloud_verify_gemini_access,
            )

        # Сначала пробуем локаль по цепочке (если не в quarantine — проверено выше)
        for model_id in self.fallback_chain:
            if "local" in model_id.lower() or "mlx" in model_id.lower():
                if self.lm_studio_url:
                    try:
                        if await is_lm_studio_available(
                            self.lm_studio_url,
                            client=self._local_http_client,
                        ):
                            # Wave 86: memory-pressure pre-filter. Если RAM
                            # критична — отбрасываем local и идём в cloud.
                            cloud_default = (
                                self.config_model
                                if self.config_model and self.config_model != "auto"
                                else DEFAULT_CLOUD_MODEL
                            )
                            adjusted = pressure_aware_model_select(
                                "local",
                                self._pressure_aware_candidates(),
                                cloud_fallback=cloud_default,
                            )
                            if adjusted == "local":
                                return "local"
                            # Pressure forced cloud — выходим из локального цикла
                            break
                    except Exception as e:
                        logger.debug(
                            "lm_studio_check_failed",
                            error=str(e),
                        )
                        continue
                break

        return await get_best_cloud_model(
            self.gemini_api_key,
            self._cloud_http_client,
            config_model=self.config_model,
            verify_fn=cloud_verify_gemini_access,
        )

    def _pressure_aware_candidates(self) -> list[dict]:
        """Wave 86: список candidate моделей для pressure-aware selection.

        Стандартный путь пуст (size_gb для конкретной local модели здесь не
        известен — это уровень ModelRouter). Подклассы / model_manager могут
        переопределить через setter если потребуется выбирать самую маленькую
        локальную из нескольких загруженных.
        """
        return []
