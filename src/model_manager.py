"""
Model Manager - Умное управление моделями LM Studio

Функции:
- Автодетект доступных моделей через API
- Мониторинг RAM
- Smart fallback на облачные модели
- Загрузка/выгрузка моделей (Smart Loading)
- Maintenance loop для авто-выгрузки
"""
import asyncio
import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import httpx
import psutil
import structlog

from .config import config
from .core.lm_studio_health import fetch_lm_studio_models_list

logger = structlog.get_logger(__name__)


class ModelStatus(Enum):
    """Статус модели"""
    AVAILABLE = "available"      # Доступна для загрузки
    LOADED = "loaded"           # Загружена и готова
    LOADING = "loading"         # В процессе загрузки
    UNLOADING = "unloading"     # В процессе выгрузки
    ERROR = "error"             # Ошибка
    UNKNOWN = "unknown"


class ModelType(Enum):
    """Тип модели"""
    LOCAL_MLX = "mlx"
    LOCAL_GGUF = "gguf"
    CLOUD_GEMINI = "gemini"
    CLOUD_OPENROUTER = "openrouter"


@dataclass
class ModelInfo:
    """Информация о модели"""
    id: str
    name: str
    type: ModelType
    status: ModelStatus = ModelStatus.UNKNOWN
    size_gb: float = 8.0  # Default approx size
    context_window: int = 8192
    supports_vision: bool = False
    
    @property
    def is_local(self) -> bool:
        return self.type in (ModelType.LOCAL_MLX, ModelType.LOCAL_GGUF)


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
        
        # Fallback chain
        self.fallback_chain = [
            "local",  # LM Studio loaded model
            "lmstudio/seed-oss-36b-instruct-mlx",
            "google/gemini-2.0-flash-exp",
            "google/gemini-1.5-pro-latest",
        ]
    
    async def discover_models(self) -> list[ModelInfo]:
        """
        Обнаруживает все доступные модели в LM Studio
        """
        models = []
        model_list = await fetch_lm_studio_models_list(
            self.lm_studio_url, client=self._http_client
        )
        if not model_list:
            logger.warning("lm_studio_offline")
        else:
            for model_data in model_list:
                model_id = model_data.get("id", "")
                model_type = self._detect_model_type(model_id)
                size = 8.0
                if "7b" in model_id.lower(): size = 5.0
                if "13b" in model_id.lower(): size = 10.0
                if "30b" in model_id.lower() or "32b" in model_id.lower(): size = 18.0
                if "70b" in model_id.lower(): size = 40.0
                if "q4" in model_id.lower(): size *= 0.6
                model = ModelInfo(
                    id=model_id,
                    name=model_data.get("name", model_id),
                    type=model_type,
                    status=ModelStatus.AVAILABLE,
                    size_gb=size,
                    supports_vision="vl" in model_id.lower() or "vision" in model_id.lower()
                )
                models.append(model)
                self._models_cache[model_id] = model
            logger.info("models_discovered", count=len(models))
            
        # Get Google Models
        google_models = await self._fetch_google_models()
        models.extend(google_models)
            
        return models

    async def _fetch_google_models(self) -> list[ModelInfo]:
        """Запрашивает список моделей у Google"""
        if not config.GEMINI_API_KEY:
            return []
            
        models = []
        try:
            url = "https://generativelanguage.googleapis.com/v1beta/models"
            params = {"key": config.GEMINI_API_KEY}
            
            response = await self._http_client.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                for m in data.get("models", []):
                    # Filter only gemini models to avoid clutter (e.g. text-bison)
                    if "gemini" not in m["name"].lower():
                        continue
                        
                    m_id = m["name"].replace("models/", "google/")
                    
                    # Check supports
                    is_vision = "vision" in m_id or "flash" in m_id or "pro" in m_id
                    
                    model = ModelInfo(
                        id=m_id,
                        name=m.get("displayName", m_id),
                        type=ModelType.CLOUD_GEMINI,
                        status=ModelStatus.AVAILABLE,
                        size_gb=0.0,
                        supports_vision=is_vision
                    )
                    models.append(model)
                    self._models_cache[m_id] = model
            else:
                 logger.warning("google_api_error", status=response.status_code)
                 
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, OSError) as e:
            logger.error("google_api_exception", error=str(e))
            
        return models

    async def verify_model_access(self, model_id: str) -> bool:
        """Проверяет доступность модели перед переключением"""
        # 1. Local check
        if self._detect_model_type(model_id) != ModelType.CLOUD_GEMINI:
             # For local/LMS, just check if it can be loaded or is in list
             if model_id in self._models_cache:
                  # Try to get info
                  # TODO: Add real ping check for LMS
                  return True
             return False

        # 2. Google check
        if not config.GEMINI_API_KEY: return False
        
        try:
            # Simple generateContent call with 1 token to test auth and model existence
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id.replace('google/', '')}:generateContent"
            params = {"key": config.GEMINI_API_KEY}
            json_body = {"contents": [{"parts": [{"text": "Hi"}]}]}
            
            response = await self._http_client.post(url, params=params, json=json_body)
            return response.status_code == 200
        except (httpx.HTTPError, OSError):
            return False
    
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
        """
        Возвращает лучшую доступную модель из цепочки fallback.
        """
        # 0. Если пользователь явно задал модель в конфиге
        if config.MODEL and config.MODEL != "auto":
             return config.MODEL

        # 1. Проверяем цепочку
        for model_id in self.fallback_chain:
            try:
                # Если это локальная, проверяем загружена ли она или доступна
                if "local" in model_id.lower() or "mlx" in model_id.lower():
                     # Простая проверка: считаем доступной если LMS отвечает
                     # Можно добавить более сложную логику
                     if self.lm_studio_url:
                         return "local" # Default local alias
                
                # Если Gemini
                if "gemini" in model_id.lower():
                    if config.GEMINI_API_KEY:
                        return model_id
                        
            except (httpx.HTTPError, OSError):
                continue
                
        # Default fallback
        return "google/gemini-2.0-flash"

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
        # Проверяем доступную память + буфер 2GB
        mem = psutil.virtual_memory()
        available_gb = mem.available / (1024**3)
        return available_gb > (size_gb + 2.0)
    
    async def load_model(self, model_id: str) -> bool:
        """Загружает модель (Smart Loading)"""
        async with self._lock:
            # Обновляем инфо о моделях
            if not self._models_cache:
                await self.discover_models()
                
            model_info = self._models_cache.get(model_id)
            if not model_info:
                logger.warning("model_unknown_loading_anyway", model=model_id)
                size_gb = 8.0
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
                    json={"model": model_id, "ttl": -1},  # No auto-unload by LMS itself
                    timeout=600.0
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
                await asyncio.sleep(300)  # Проверка каждые 5 мин
                
                now = time.time()
                # Выгружаем если простой > 15 мин
                if self._current_model:
                    last = self._last_access.get(self._current_model, 0)
                    if now - last > 900:  # 15 min
                        logger.info("auto_unload_idle", model=self._current_model)
                        async with self._lock:
                            await self.unload_model(self._current_model)
                            self._current_model = None
                            
            except asyncio.CancelledError:
                break
            except (httpx.HTTPError, OSError) as e:
                logger.error("maintenance_error", error=str(e))
            if target.split("/")[1] in m.id: # Simple match
                best_local = m.id
                break
        
        if best_local:
            if await self.load_model(best_local):
                return best_local
                
        # 2. Fallback to Gemini if configured
        if config.GEMINI_API_KEY:
            return config.MODEL
            
        return "local"

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
