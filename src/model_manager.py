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
import errno
import time
from pathlib import Path
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

try:
    import fcntl  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - не на POSIX
    fcntl = None  # type: ignore[assignment]


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
        self._last_any_activity_ts: float = time.time()
        self._active_requests: int = 0
        self._lock = asyncio.Lock()
        self._maintenance_task: Optional[asyncio.Task] = None
        # Временное исключение локальных моделей, которые гарантированно не загружаются
        # (например, битые пути/удалённые файлы в LM Studio registry).
        self._local_model_excluded_until: dict[str, float] = {}
        self._local_model_exclude_reason: dict[str, str] = {}
        # Поддержка legacy /v1/models/load|unload: определяем один раз и дальше
        # не долбим неподдерживаемый endpoint на каждой загрузке/выгрузке.
        self._legacy_load_endpoint_supported: Optional[bool] = None
        self._legacy_unload_endpoint_supported: Optional[bool] = None
        base_dir = Path(getattr(config, "BASE_DIR", Path.cwd()))
        self._interprocess_load_lock_path = base_dir / "data" / "locks" / "lmstudio_model_load.lock"

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

    @staticmethod
    def _is_chat_capable_local_model(model_id: str, info: Optional[ModelInfo] = None) -> bool:
        """
        Возвращает True только для локальных моделей, пригодных для chat/completions.

        Почему это важно:
        - LM Studio может отдавать embedding/reranker/audio модели в общем списке;
        - если автоподбор выберет такую модель, получаем `EMPTY MESSAGE`/`No models loaded`
          на рабочем chat-маршруте.
        """
        low_id = str(model_id or "").strip().lower()
        low_name = str(getattr(info, "name", "") or "").strip().lower()
        haystack = f"{low_id} {low_name}".strip()

        non_chat_markers = (
            "embedding",
            "embed",
            "rerank",
            "reranker",
            "cross-encoder",
            "colbert",
            "whisper",
            "speech-to-text",
            "transcrib",
            "asr",
            "stt",
            "text-to-speech",
            "tts",
            "nomic-embed",
            "bge-",
            "e5-",
            "gte-",
        )
        return not any(marker in haystack for marker in non_chat_markers)

    def _is_local_model_temporarily_excluded(self, model_id: str) -> bool:
        """Проверяет, исключена ли локальная модель из кандидатов до истечения TTL."""
        until = float(self._local_model_excluded_until.get(model_id, 0.0) or 0.0)
        if until <= 0:
            return False
        now = time.time()
        if now >= until:
            self._local_model_excluded_until.pop(model_id, None)
            self._local_model_exclude_reason.pop(model_id, None)
            return False
        return True

    def _exclude_local_model(self, model_id: str, *, reason: str, ttl_sec: float) -> None:
        """Временно исключает локальную модель из авто-кандидатов."""
        self._local_model_excluded_until[model_id] = time.time() + max(30.0, float(ttl_sec))
        self._local_model_exclude_reason[model_id] = reason
        logger.warning(
            "local_model_temporarily_excluded",
            model=model_id,
            reason=reason,
            ttl_sec=round(float(ttl_sec), 2),
        )

    async def _acquire_interprocess_model_lock(self, timeout_sec: float = 180.0):
        """
        Межпроцессный lock на загрузку локальной модели.

        Нужен, чтобы два Python-процесса Krab не запускали `POST /models/load`
        одновременно (иначе LM Studio может поднять `model` и `model:2`).
        """
        self._interprocess_load_lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._interprocess_load_lock_path.open("a+", encoding="utf-8")
        if fcntl is None:
            return handle

        deadline = time.time() + max(5.0, float(timeout_sec))
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return handle
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    handle.close()
                    raise
                if time.time() >= deadline:
                    handle.close()
                    raise TimeoutError("interprocess_model_lock_timeout")
                await asyncio.sleep(0.25)

    def _release_interprocess_model_lock(self, handle) -> None:
        """Освобождает межпроцессный lock."""
        if handle is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            handle.close()
        except OSError:
            pass

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
            preferred_vision = await self.resolve_preferred_local_model(has_photo=True)
            if preferred_vision:
                return preferred_vision

        # В auto режиме local-first: если LM Studio жив, используем local/предпочтительную локальную.
        if self.lm_studio_url and await is_lm_studio_available(self.lm_studio_url, client=self._http_client):
            preferred_local = await self.resolve_preferred_local_model(has_photo=False)
            if preferred_local:
                return preferred_local

        return await self._router.get_best_model(has_photo=has_photo)

    def is_local_model(self, model_id: str) -> bool:
        """True if model_id refers to a local (LM Studio) model, not a cloud one."""
        return self._detect_model_type(model_id) != ModelType.CLOUD_GEMINI

    async def _local_candidates(self, *, has_photo: bool = False) -> list[tuple[str, ModelInfo]]:
        """Возвращает локальные кандидаты, отсортированные от лёгких к тяжёлым."""
        if not self._models_cache:
            await self.discover_models()
        candidates = [
            (mid, info)
            for mid, info in self._models_cache.items()
            if info.type in (ModelType.LOCAL_MLX, ModelType.LOCAL_GGUF)
            and self._is_chat_capable_local_model(mid, info)
            and not self._is_local_model_temporarily_excluded(mid)
            and (not has_photo or bool(getattr(info, "supports_vision", False)))
        ]
        candidates.sort(key=lambda item: float(item[1].size_gb or DEFAULT_UNKNOWN_MODEL_SIZE_GB))
        return candidates

    async def resolve_preferred_local_model(self, *, has_photo: bool = False) -> Optional[str]:
        """
        Возвращает целевую локальную модель:
        1) уже активная (если подходит),
        2) preferred из конфига,
        3) самая лёгкая доступная локальная.
        """
        candidates = await self._local_candidates(has_photo=has_photo)
        if not candidates:
            return None

        candidate_ids = {mid for mid, _ in candidates}
        if self._current_model and self._current_model in candidate_ids:
            return self._current_model

        preferred_key = "LOCAL_PREFERRED_VISION_MODEL" if has_photo else "LOCAL_PREFERRED_MODEL"
        preferred = str(getattr(config, preferred_key, "") or "").strip().lower()
        if preferred and preferred not in {"auto", "smallest"}:
            for mid, _ in candidates:
                if preferred in mid.lower():
                    return mid
        return candidates[0][0]

    async def get_best_cloud_model(self, *, has_photo: bool = False) -> str:
        """Явно выбирает облачную модель (используется при local-recovery сбоях)."""
        return await self._router.get_best_model(has_photo=has_photo)

    def get_current_model(self) -> Optional[str]:
        """Текущая активная локальная модель (если есть)."""
        return self._current_model

    async def ensure_model_loaded(self, model_id: str, *, has_photo: bool = False) -> bool:
        """
        Гарантирует, что локальная модель реально загружена.

        Если preferred модель не загрузилась (частый кейс после idle-unload/перегруза),
        пытается несколько более лёгких локальных кандидатов.
        """
        resolved_model = model_id
        if model_id.lower() in ("local", "lmstudio"):
            resolved_model = await self.resolve_preferred_local_model(has_photo=has_photo) or ""
            if not resolved_model:
                logger.warning("no_local_candidates_found", has_photo=has_photo)
                return False

        if self._current_model == resolved_model:
            self.touch(resolved_model)
            return True

        if await self.load_model(resolved_model):
            return True

        fallback_limit = max(0, int(getattr(config, "LOCAL_AUTOLOAD_FALLBACK_LIMIT", 3)))
        if fallback_limit == 0:
            logger.error("local_primary_load_failed_no_fallback", model=resolved_model)
            return False

        candidates = [mid for mid, _ in await self._local_candidates(has_photo=has_photo) if mid != resolved_model]
        for candidate in candidates[:fallback_limit]:
            if await self.load_model(candidate):
                logger.warning(
                    "local_model_autoload_fallback_success",
                    requested=resolved_model,
                    loaded=candidate,
                )
                return True
            logger.warning(
                "local_model_autoload_fallback_failed",
                requested=resolved_model,
                candidate=candidate,
            )

        logger.error(
            "local_autoload_failed_all_candidates",
            requested=resolved_model,
            candidates_checked=min(len(candidates), fallback_limit),
        )
        return False

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

    @staticmethod
    def _classify_lm_load_failure(resp: httpx.Response) -> str:
        """
        Классифицирует причину фейла загрузки локальной модели.
        Нужна для runtime-исключения "мертвых" записей LM Studio.
        """
        body = (resp.text or "").lower()
        if "no such file or directory" in body or "filenotfounderror" in body:
            return "model_path_missing"
        if "unexpected endpoint or method" in body:
            return "endpoint_unsupported"
        if "model_load_failed" in body:
            return "model_load_failed"
        if "out of memory" in body or "insufficient" in body:
            return "memory_error"
        return "load_failed"

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
            ("v1", f"{self.lm_studio_url}/api/v1/models/load", {"model": model_id}),
        ]
        if self._legacy_load_endpoint_supported is not False:
            # Legacy fallback (старые версии LM Studio / OpenAI-compatible shim).
            load_endpoints.append(
                ("legacy", f"{self.lm_studio_url}/v1/models/load", {"model": model_id, "ttl": LM_LOAD_TTL})
            )
        strongest_failure = ""
        for endpoint_kind, url, payload in load_endpoints:
            try:
                resp = await self._http_client.post(
                    url, json=payload, timeout=LM_LOAD_TIMEOUT_SEC
                )
                if self._is_successful_lm_response(resp):
                    if endpoint_kind == "legacy" and self._legacy_load_endpoint_supported is None:
                        self._legacy_load_endpoint_supported = True
                    return True
                failure_code = self._classify_lm_load_failure(resp)
                if endpoint_kind == "legacy" and failure_code == "endpoint_unsupported":
                    self._legacy_load_endpoint_supported = False
                if failure_code == "model_path_missing":
                    strongest_failure = "model_path_missing"
                elif not strongest_failure:
                    strongest_failure = failure_code
                logger.warning(
                    "lm_load_endpoint_failed",
                    model=model_id,
                    url=url,
                    status=resp.status_code,
                    body=(resp.text or "")[:240],
                )
            except (httpx.HTTPError, OSError):
                continue
        if strongest_failure == "model_path_missing":
            # Файлы модели отсутствуют на диске — охлаждаем попытки надолго.
            self._exclude_local_model(
                model_id,
                reason="model_path_missing",
                ttl_sec=float(getattr(config, "LOCAL_MISSING_MODEL_COOLDOWN_SEC", 3600)),
            )
        elif strongest_failure == "endpoint_unsupported":
            self._exclude_local_model(
                model_id,
                reason="endpoint_unsupported",
                ttl_sec=float(getattr(config, "LOCAL_LOAD_FAIL_COOLDOWN_SEC", 300)),
            )
        elif strongest_failure:
            self._exclude_local_model(
                model_id,
                reason=strongest_failure,
                ttl_sec=float(getattr(config, "LOCAL_LOAD_FAIL_COOLDOWN_SEC", 300)),
            )
        return False

    async def _do_unload_model(self, model_id: str) -> bool:
        """Внутренняя выгрузка с fallback API v1 (instance_id) -> v0 (model)."""
        unload_endpoints = [
            ("v1", f"{self.lm_studio_url}/api/v1/models/unload", {"instance_id": model_id}),
        ]
        if self._legacy_unload_endpoint_supported is not False:
            unload_endpoints.append(("legacy", f"{self.lm_studio_url}/v1/models/unload", {"model": model_id}))

        for endpoint_kind, url, payload in unload_endpoints:
            try:
                resp = await self._http_client.post(url, json=payload, timeout=30.0)
                if self._is_successful_lm_response(resp):
                    if endpoint_kind == "legacy" and self._legacy_unload_endpoint_supported is None:
                        self._legacy_unload_endpoint_supported = True
                    return True
                if endpoint_kind == "legacy" and self._classify_lm_load_failure(resp) == "endpoint_unsupported":
                    self._legacy_unload_endpoint_supported = False
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
        lock_handle = None
        try:
            lock_handle = await self._acquire_interprocess_model_lock(
                timeout_sec=float(getattr(config, "LOCAL_MODEL_LOAD_LOCK_TIMEOUT_SEC", 240))
            )
            already_pruned_models: set[str] = set()
            async with self._lock:
                single_local_mode = bool(getattr(config, "SINGLE_LOCAL_MODEL_MODE", True))
                if not self._models_cache:
                    await self.discover_models()
                model_info = self._models_cache.get(model_id)
                size_gb = model_info.size_gb if model_info else DEFAULT_UNKNOWN_MODEL_SIZE_GB
                if not model_info:
                    logger.warning("model_unknown_loading_anyway", model=model_id)

                loaded = await self.get_loaded_models()
                if single_local_mode and loaded:
                    # SINGLE_LOCAL_MODEL_MODE: в памяти должна оставаться только целевая модель.
                    # Выгружаем любые лишние инстансы/модели (включая клоны вида `model:2`).
                    extra_loaded = [
                        mid
                        for mid in dict.fromkeys(loaded)
                        if mid and mid != model_id and mid not in already_pruned_models
                    ]
                    if extra_loaded:
                        for extra_model in extra_loaded:
                            await self._do_unload_model(extra_model)
                        already_pruned_models.update(extra_loaded)
                        logger.info(
                            "single_local_mode_pruned_models",
                            target=model_id,
                            unloaded=extra_loaded,
                        )
                        if self._current_model in set(extra_loaded):
                            self._current_model = None
                        loaded = [mid for mid in loaded if mid == model_id]

                if model_id in loaded:
                    self._current_model = model_id
                    self.touch(model_id)
                    return True

                # Политика single-model: не держим несколько локальных моделей в памяти.
                # Это снижает риск ухода в swap на 36GB RAM машинах.
                if (
                    single_local_mode
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
                # Повторная проверка после wait/free: модель могла уже загрузиться
                # другим процессом, который держал lock до нас.
                loaded = await self.get_loaded_models()
                if single_local_mode and loaded:
                    extra_loaded = [
                        mid
                        for mid in dict.fromkeys(loaded)
                        if mid and mid != model_id and mid not in already_pruned_models
                    ]
                    if extra_loaded:
                        for extra_model in extra_loaded:
                            await self._do_unload_model(extra_model)
                        already_pruned_models.update(extra_loaded)
                        logger.info(
                            "single_local_mode_pruned_models_post_wait",
                            target=model_id,
                            unloaded=extra_loaded,
                        )
                        if self._current_model in set(extra_loaded):
                            self._current_model = None
                        loaded = [mid for mid in loaded if mid == model_id]
                if model_id in loaded:
                    self._current_model = model_id
                    self.touch(model_id)
                    return True

                logger.info("loading_model_start", model=model_id)
                ok = await self._do_load_model(model_id, size_gb)
                if ok:
                    logger.info("model_loaded", model=model_id)
                    self._current_model = model_id
                    self.touch(model_id)
                    return True
                logger.error("load_failed", model=model_id)
                return False
        finally:
            self._release_interprocess_model_lock(lock_handle)

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
        now = time.time()
        self._last_access[model_id] = now
        self._last_any_activity_ts = now

    def mark_request_started(self) -> None:
        """
        Отмечает старт пользовательского запроса.
        Используется guarded idle-unload, чтобы не выгружать модель во время ответа.
        """
        self._active_requests += 1
        self._last_any_activity_ts = time.time()

    def mark_request_finished(self) -> None:
        """
        Отмечает завершение пользовательского запроса.
        Счётчик не уходит в отрицательные значения.
        """
        if self._active_requests > 0:
            self._active_requests -= 1
        self._last_any_activity_ts = time.time()

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
                        # Guarded mode: выгружаем только при реальном простое.
                        # 1) не выгружаем, пока есть активные запросы;
                        # 2) не выгружаем сразу после недавней активности (grace window).
                        if getattr(config, "GUARDED_IDLE_UNLOAD", True):
                            if self._active_requests > 0:
                                logger.info(
                                    "auto_unload_skipped_active_requests",
                                    model=self._current_model,
                                    active_requests=self._active_requests,
                                )
                                continue
                            grace_sec = float(getattr(config, "GUARDED_IDLE_UNLOAD_GRACE_SEC", 90.0))
                            any_idle = now - float(self._last_any_activity_ts or 0.0)
                            if any_idle < grace_sec:
                                logger.info(
                                    "auto_unload_skipped_guarded_grace",
                                    model=self._current_model,
                                    any_idle_sec=round(any_idle, 2),
                                    grace_sec=grace_sec,
                                )
                                continue
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
