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
    fetch_google_models_with_fallback as cloud_fetch_google_models_fb,
)
from .core.cloud_gateway import (
    get_cloud_fallback_chain,
    resolve_working_gemini_key,
)
from .core.cloud_gateway import (
    verify_gemini_access as cloud_verify_gemini_access,
)
from .core.cost_analytics import cost_analytics
from .core.local_health import discover_models as discover_models_impl
from .core.local_health import is_lm_studio_available
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
from .core.model_types import ModelInfo, ModelType

logger = structlog.get_logger(__name__)


class ModelManager:
    """
    Менеджер моделей с автодетектом и smart fallback
    """

    def __init__(self):
        self.lm_studio_url = config.LM_STUDIO_URL.rstrip("/")
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

    async def _resolve_gemini_key(self) -> Optional[str]:
        """Returns the best working Gemini API key (free -> paid -> legacy)."""
        return await resolve_working_gemini_key(
            config.GEMINI_API_KEY_FREE,
            config.GEMINI_API_KEY_PAID,
            self._http_client,
        )

    async def discover_models(self) -> list[ModelInfo]:
        """Обнаруживает все доступные модели (LM Studio + облако) через local_health и cloud_gateway."""
        async def _fetch_google() -> list[ModelInfo]:
            return await cloud_fetch_google_models_fb(
                config.GEMINI_API_KEY_FREE,
                config.GEMINI_API_KEY_PAID,
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
        key = await self._resolve_gemini_key()
        return await cloud_verify_gemini_access(
            model_id,
            key,
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

    async def get_best_model(self, *, has_photo: bool = False) -> str:
        """
        Возвращает лучшую доступную модель.
        При has_photo=True: локальная vision-модель (если есть), иначе облачная (gemini).
        """
        # Синхронизируем cloud-конфиг роутера с runtime-настройкой.
        self._router.config_model = config.MODEL

        # Режим cloud принудительный — сразу отдаём облачную ветку.
        if getattr(config, "FORCE_CLOUD", False):
            return await self._router.get_best_model(has_photo=has_photo)

        if has_photo and not getattr(config, "FORCE_CLOUD", False) and self.lm_studio_url:
            if not self._models_cache:
                await self.discover_models()
            if self._current_model:
                info = self._models_cache.get(self._current_model)
                if info and info.supports_vision:
                    return self._current_model
            preferred_vision = str(getattr(config, "LOCAL_PREFERRED_VISION_MODEL", "") or "").strip().lower()
            local_vision_models = [
                (mid, info)
                for mid, info in self._models_cache.items()
                if info.supports_vision and info.type in (ModelType.LOCAL_MLX, ModelType.LOCAL_GGUF)
            ]
            if local_vision_models:
                if preferred_vision and preferred_vision not in {"auto", "smallest"}:
                    for mid, _ in local_vision_models:
                        if preferred_vision in mid.lower():
                            return mid
                # По умолчанию выбираем самую лёгкую vision-модель (бережём RAM/Swap).
                local_vision_models.sort(key=lambda item: float(item[1].size_gb or DEFAULT_UNKNOWN_MODEL_SIZE_GB))
                return local_vision_models[0][0]

        # В auto режиме local-first: если LM Studio жив, используем local/предпочтительную локальную.
        if self.lm_studio_url and await is_lm_studio_available(self.lm_studio_url, client=self._http_client):
            preferred_local = await self.resolve_preferred_local_model()
            if preferred_local:
                return preferred_local
            return "local"

        return await self._router.get_best_model(has_photo=has_photo)

    def is_local_model(self, model_id: str) -> bool:
        """True if model_id refers to a local (LM Studio) model, not a cloud one."""
        return self._detect_model_type(model_id) != ModelType.CLOUD_GEMINI

    async def resolve_preferred_local_model(self) -> Optional[str]:
        """Finds a local model matching LOCAL_PREFERRED_MODEL substring in discovered models."""
        preferred = config.LOCAL_PREFERRED_MODEL
        if not preferred:
            return None
        if not self._models_cache:
            await self.discover_models()
        preferred_lower = preferred.lower()
        for mid, info in self._models_cache.items():
            if info.type in (ModelType.LOCAL_MLX, ModelType.LOCAL_GGUF):
                if preferred_lower in mid.lower():
                    return mid
        return None

    async def ensure_model_loaded(self, model_id: str) -> bool:
        """
        Ensures a model is loaded in LM Studio before sending a request.
        If model_id is 'local' or generic, resolves to LOCAL_PREFERRED_MODEL.
        Skips if the model is already loaded and recently accessed.
        """
        if model_id.lower() in ("local", "lmstudio"):
            resolved = await self.resolve_preferred_local_model()
            if resolved:
                model_id = resolved
            else:
                logger.warning("no_preferred_local_model_found")
                return False

        if self._current_model == model_id:
            self.touch(model_id)
            return True

        return await self.load_model(model_id)

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

    async def get_loaded_models(self) -> list[str]:
        """Запрашивает у LM Studio список загруженных моделей (API v1 или fallback)."""
        urls = [f"{self.lm_studio_url}/api/v1/models", f"{self.lm_studio_url}/v1/models"]
        for url in urls:
            try:
                resp = await self._http_client.get(url, timeout=10.0)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                loaded: list[str] = []
                models = data.get("models", data.get("data", []))
                for m in models:
                    instances = m.get("loaded_instances", [])
                    key = m.get("key") or m.get("id", "")
                    for inst in instances:
                        inst_id = inst.get("id", key) or key
                        if inst_id:
                            loaded.append(inst_id)
                        # Для корректной проверки "модель уже загружена"
                        # и совместимости unload по model_id сохраняем также key.
                        if key:
                            loaded.append(key)
                if loaded:
                    return list(dict.fromkeys(loaded))
                if self._current_model:
                    return [self._current_model]
                return []
            except (httpx.HTTPError, OSError, ValueError):
                continue
        return [self._current_model] if self._current_model else []

    @staticmethod
    def _response_payload_has_error(resp: httpx.Response) -> bool:
        """
        Определяет, что LM Studio вернул ошибку в теле ответа,
        даже если HTTP-статус 2xx.
        """
        body = (resp.text or "").strip()
        body_low = body.lower()
        if not body:
            return False
        if "unexpected endpoint or method" in body_low:
            return True
        if "unrecognized key" in body_low:
            return True
        try:
            payload = resp.json()
        except ValueError:
            # Не JSON и без явных маркеров ошибки — считаем успехом.
            return False
        if isinstance(payload, dict) and payload.get("error"):
            return True
        return False

    def _is_successful_lm_response(self, resp: httpx.Response) -> bool:
        """Единая проверка успешности LM Studio mutating-эндпоинтов."""
        if resp.status_code not in (200, 201, 202, 204):
            return False
        if self._response_payload_has_error(resp):
            return False
        return True

    async def _do_load_model(self, model_id: str, size_gb: float) -> bool:
        """Внутренняя загрузка с fallback API v1 -> v0."""
        load_endpoints = [
            # REST API v1: ttl больше не поддерживается в теле запроса.
            (f"{self.lm_studio_url}/api/v1/models/load", {"model": model_id}),
            # Legacy fallback (старые версии LM Studio / OpenAI-compatible shim).
            (f"{self.lm_studio_url}/v1/models/load", {"model": model_id, "ttl": LM_LOAD_TTL}),
        ]
        for url, payload in load_endpoints:
            try:
                resp = await self._http_client.post(
                    url, json=payload, timeout=LM_LOAD_TIMEOUT_SEC
                )
                if self._is_successful_lm_response(resp):
                    return True
                logger.warning(
                    "lm_load_endpoint_failed",
                    model=model_id,
                    url=url,
                    status=resp.status_code,
                    body=(resp.text or "")[:240],
                )
            except (httpx.HTTPError, OSError):
                continue
        return False

    async def _do_unload_model(self, model_id: str) -> bool:
        """Внутренняя выгрузка с fallback API v1 (instance_id) -> v0 (model)."""
        unload_endpoints = [
            (f"{self.lm_studio_url}/api/v1/models/unload", {"instance_id": model_id}),
            (f"{self.lm_studio_url}/v1/models/unload", {"model": model_id}),
        ]
        for url, payload in unload_endpoints:
            try:
                resp = await self._http_client.post(url, json=payload, timeout=30.0)
                if self._is_successful_lm_response(resp):
                    return True
                logger.warning(
                    "lm_unload_endpoint_failed",
                    model=model_id,
                    url=url,
                    status=resp.status_code,
                    body=(resp.text or "")[:240],
                )
            except (httpx.HTTPError, OSError):
                continue
        return False

    async def load_model(self, model_id: str) -> bool:
        """Загружает модель (Smart Loading) с Lock и API v1 fallback."""
        async with self._lock:
            if not self._models_cache:
                await self.discover_models()
            model_info = self._models_cache.get(model_id)
            size_gb = model_info.size_gb if model_info else DEFAULT_UNKNOWN_MODEL_SIZE_GB
            if not model_info:
                logger.warning("model_unknown_loading_anyway", model=model_id)

            loaded = await self.get_loaded_models()
            if model_id in loaded:
                self._current_model = model_id
                self.touch(model_id)
                return True

            # Политика single-model: не держим несколько локальных моделей в памяти.
            # Это снижает риск ухода в swap на 36GB RAM машинах.
            if (
                getattr(config, "SINGLE_LOCAL_MODEL_MODE", True)
                and self._current_model
                and self._current_model != model_id
            ):
                await self._do_unload_model(self._current_model)
                logger.info(
                    "model_unloaded_before_switch",
                    previous_model=self._current_model,
                    next_model=model_id,
                )
                self._current_model = None

            need_free = not self.can_load_model(size_gb)
        if need_free:
            logger.info("memory_pressure", needed=size_gb)
            await self.free_vram()
            await asyncio.sleep(2.0)

        async with self._lock:
            logger.info("loading_model_start", model=model_id)
            ok = await self._do_load_model(model_id, size_gb)
            if ok:
                logger.info("model_loaded", model=model_id)
                self._current_model = model_id
                self.touch(model_id)
                return True
            logger.error("load_failed", model=model_id)
            return False

    async def free_vram(self) -> None:
        """Выгружает все модели и синхронизирует _current_model. VRAM cooling 1.5s после выгрузки."""
        async with self._lock:
            loaded = await self.get_loaded_models()
            for mid in loaded:
                await self._do_unload_model(mid)
                logger.info("model_unloaded", model=mid)
            self._current_model = None
        await asyncio.sleep(1.5)

    async def unload_model(self, model_id: str) -> None:
        """Выгружает модель с Lock и cooling."""
        async with self._lock:
            await self._do_unload_model(model_id)
            if self._current_model == model_id:
                self._current_model = None
            logger.info("model_unloaded", model=model_id)
        await asyncio.sleep(1.5)

    async def unload_all(self) -> None:
        """Выгружает все модели (делегирует в free_vram)."""
        await self.free_vram()

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
                            await self._do_unload_model(self._current_model)
                            self._current_model = None
                        await asyncio.sleep(1.5)

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
