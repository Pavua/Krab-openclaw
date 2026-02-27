"""
Model Manager - Умное управление моделями LM Studio

Функции:
- Автодетект доступных моделей через API
- Мониторинг RAM
- Smart fallback на облачные модели (делегирует в core.cloud_gateway)
- Загрузка/выгрузка моделей (Smart Loading)
- Maintenance loop для авто-выгрузки
"""
import asyncio
import time
from typing import Optional

import httpx
import psutil
import structlog

from .config import config
from .core.cloud_gateway import (
    fetch_google_models as cloud_fetch_google_models,
    get_cloud_fallback_chain,
    verify_gemini_access as cloud_verify_gemini_access,
)
from .core.cost_analytics import cost_analytics
from .core.local_health import discover_models as discover_models_impl
from .core.model_config import (
    DEFAULT_UNKNOWN_MODEL_SIZE_GB,
    FALLBACK_CHAIN_LOCAL,
    IDLE_UNLOAD_SEC,
    LM_LOAD_TIMEOUT_SEC,
    LM_LOAD_TTL,
    MAINTENANCE_INTERVAL_SEC,
    RAM_BUFFER_GB,
)
from .core.model_router import ModelRouter
from .core.model_types import ModelInfo, ModelStatus, ModelType

logger = structlog.get_logger(__name__)


class ModelManager:
    """
    Менеджер моделей с автодетектом и smart fallback
    """
    
    def __init__(self):
        self.lm_studio_url = config.LM_STUDIO_URL
        self.max_ram_gb = config.MAX_RAM_GB
        self._models_cache: dict[str, ModelInfo] = {}
        self._current_model: Optional[str] = None
        self._http_client = httpx.AsyncClient(timeout=30.0)
        
        # Smart Loading State
        self._last_access: dict[str, float] = {}  # model_id -> timestamp
        self._lock = asyncio.Lock()
        self._maintenance_task: Optional[asyncio.Task] = None
        
        # Fallback chain: local затем облачные тиры из cloud_gateway
        cloud_chain = get_cloud_fallback_chain()
        self.fallback_chain = [*FALLBACK_CHAIN_LOCAL, *cloud_chain]
        self._router = ModelRouter(
            lm_studio_url=self.lm_studio_url,
            gemini_api_key=config.GEMINI_API_KEY,
            http_client=self._http_client,
            fallback_chain=self.fallback_chain,
            config_model=config.MODEL,
        )
        # Аналитика затрат (Cost Engine, бюджет, отчёты) — Фаза 4.1, Шаг 4
        self._cost_analytics = cost_analytics

    @property
    def cost_analytics(self):
        """Аналитика затрат: токены, стоимость, бюджет, отчёты."""
        return self._cost_analytics

    async def discover_models(self) -> list[ModelInfo]:
        """Обнаруживает все доступные модели (LM Studio + облако) через local_health и cloud_gateway."""
        async def _fetch_google() -> list[ModelInfo]:
            return await cloud_fetch_google_models(
                config.GEMINI_API_KEY,
                self._http_client,
                models_cache=self._models_cache,
            )
        return await discover_models_impl(
            self.lm_studio_url,
            self._http_client,
            models_cache=self._models_cache,
            fetch_google_models_async=_fetch_google,
        )

    async def verify_model_access(self, model_id: str) -> bool:
        """Проверяет доступность модели перед переключением (локальные — по кэшу, облако — через cloud_gateway)."""
        if self._detect_model_type(model_id) != ModelType.CLOUD_GEMINI:
            if model_id in self._models_cache:
                return True
            return False
        return await cloud_verify_gemini_access(
            model_id,
            config.GEMINI_API_KEY,
            self._http_client,
        )
    
    def _detect_model_type(self, model_id: str) -> ModelType:
        """Определяет тип модели по ID"""
        model_id_lower = model_id.lower()
        
        if "mlx" in model_id_lower:
            return ModelType.LOCAL_MLX
        elif "gguf" in model_id_lower:
            return ModelType.LOCAL_GGUF
        elif "gemini" in model_id_lower:
            return ModelType.CLOUD_GEMINI
        else:
            return ModelType.LOCAL_MLX
    
    async def get_best_model(self) -> str:
        """Возвращает лучшую доступную модель из цепочки fallback (делегирует в ModelRouter)."""
        return await self._router.get_best_model()

    def get_ram_usage(self) -> dict:
        """Получает текущее использование RAM"""
        mem = psutil.virtual_memory()
        return {
            "total_gb": round(mem.total / (1024**3), 2),
            "used_gb": round(mem.used / (1024**3), 2),
            "available_gb": round(mem.available / (1024**3), 2),
            "percent": mem.percent
        }
    
    def can_load_model(self, size_gb: float) -> bool:
        """Проверяет можно ли загрузить модель"""
        mem = psutil.virtual_memory()
        available_gb = mem.available / (1024**3)
        return available_gb > (size_gb + RAM_BUFFER_GB)
    
    async def load_model(self, model_id: str) -> bool:
        """Загружает модель (Smart Loading)"""
        async with self._lock:
            # Обновляем инфо о моделях
            if not self._models_cache:
                await self.discover_models()
                
            model_info = self._models_cache.get(model_id)
            if not model_info:
                logger.warning("model_unknown_loading_anyway", model=model_id)
                size_gb = DEFAULT_UNKNOWN_MODEL_SIZE_GB
            else:
                size_gb = model_info.size_gb

            # Проверяем память
            if not self.can_load_model(size_gb):
                logger.info("memory_pressure", needed=size_gb)
                await self.unload_all()
                # Ждем освобождения
                await asyncio.sleep(2)
                
            try:
                # LM Studio Load API
                logger.info("loading_model_start", model=model_id)
                response = await self._http_client.post(
                    f"{self.lm_studio_url}/v1/models/load",
                    json={"model": model_id, "ttl": LM_LOAD_TTL},
                    timeout=LM_LOAD_TIMEOUT_SEC,
                )
                
                if response.status_code == 200:
                    logger.info("model_loaded", model=model_id)
                    self._current_model = model_id
                    self.touch(model_id)
                    return True
                else:
                    # Если API не поддерживает load (старые версии),
                    # то просто считаем что модель должна быть загружена пользователем
                    # Но пользователь просил авто-загрузку.
                    # Значит LM Studio версий 0.3+ поддерживает.
                    logger.error("load_failed", status=response.status_code)
                    return False
                    
            except (httpx.HTTPError, OSError) as e:
                logger.error("load_exception", error=str(e))
                return False

    async def unload_model(self, model_id: str):
        """Выгружает модель"""
        try:
            # LM Studio Unload API
            await self._http_client.post(
                f"{self.lm_studio_url}/v1/models/unload",
                json={"model": model_id}
            )
            logger.info("model_unloaded", model=model_id)
        except (httpx.HTTPError, OSError):
            pass

    async def unload_all(self):
        """Выгружает все модели"""
        # Нет API 'unload_all', выгружаем текущую известную
        if self._current_model:
            await self.unload_model(self._current_model)
            self._current_model = None

    def touch(self, model_id: str):
        """Обновляет время последнего доступа"""
        self._last_access[model_id] = time.time()

    async def start_maintenance(self):
        """Запускает фоновую задачу очистки"""
        if self._maintenance_task is None:
            self._maintenance_task = asyncio.create_task(self._maintenance_loop())

    async def _maintenance_loop(self):
        """Цикл очистки простаивающих моделей"""
        logger.info("maintenance_started")
        while True:
            try:
                await asyncio.sleep(MAINTENANCE_INTERVAL_SEC)
                now = time.time()
                if self._current_model:
                    last = self._last_access.get(self._current_model, 0)
                    if now - last > IDLE_UNLOAD_SEC:
                        logger.info("auto_unload_idle", model=self._current_model)
                        async with self._lock:
                            await self.unload_model(self._current_model)
                            self._current_model = None
                            
            except asyncio.CancelledError:
                break
            except (httpx.HTTPError, OSError) as e:
                logger.error("maintenance_error", error=str(e))

    async def health_check(self) -> dict:
        """Проверка здоровья"""
        try:
            models = await self.discover_models()
            return {
                "status": "healthy",
                "models_count": len(models),
                "ram": self.get_ram_usage()
            }
        except (httpx.HTTPError, OSError, KeyError, ValueError) as e:
            return {"status": "error", "error": str(e)}

    async def close(self):
        """Закрытие"""
        if self._maintenance_task:
            self._maintenance_task.cancel()
        await self._http_client.aclose()


model_manager = ModelManager()
