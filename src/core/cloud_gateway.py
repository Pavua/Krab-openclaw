# -*- coding: utf-8 -*-
"""
Облачной шлюз: fallback на облачные модели, выбор по тирам, работа с внешними провайдерами (Фаза 4.1).

Содержит:
- Цепочка облачных тиров (cloud_tier_1, cloud_tier_2, ...).
- Взаимодействие с Gemini API (список моделей, проверка доступа).
- Обработка специфичных ошибок облачных провайдеров (quota, auth, timeout).
Зависимости передаются аргументами (api_key, client, cache).
"""
from __future__ import annotations

import json
from enum import Enum
from typing import TYPE_CHECKING, Callable, Optional

import httpx
import structlog

from .model_types import ModelInfo, ModelStatus, ModelType

if TYPE_CHECKING:
    from httpx import AsyncClient

logger = structlog.get_logger(__name__)

# --- Облачные тиры (порядок приоритета: tier_1 быстрее/дешевле, далее дороже/мощнее) ---
CLOUD_TIER_1_IDS = [
    "google/gemini-2.0-flash-exp",
    "google/gemini-2.0-flash",
    "google/gemini-flash-latest",
]
CLOUD_TIER_2_IDS = [
    "google/gemini-1.5-pro-latest",
    "google/gemini-1.5-pro",
]
CLOUD_TIER_3_IDS = [
    "google/gemini-pro-latest",
]

DEFAULT_CLOUD_MODEL = "google/gemini-2.0-flash"


class CloudErrorKind(Enum):
    """Категория ошибки облачного провайдера."""
    AUTH = "auth"           # 401/403
    QUOTA = "quota"         # 429
    TIMEOUT = "timeout"
    NETWORK = "network"
    UNKNOWN = "unknown"


def classify_gemini_error(
    status_code: Optional[int] = None,
    exc: Optional[Exception] = None,
) -> CloudErrorKind:
    """
    Классифицирует ошибку Gemini API по коду ответа или исключению.
    Используется для fail-fast и пользовательских подсказок (Фаза 2.2).
    """
    if status_code is not None:
        if status_code in (401, 403):
            return CloudErrorKind.AUTH
        if status_code == 429:
            return CloudErrorKind.QUOTA
        if status_code == 408:
            return CloudErrorKind.TIMEOUT
    if exc is not None:
        if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
            return CloudErrorKind.TIMEOUT
        if isinstance(exc, (httpx.ConnectError, httpx.NetworkError, OSError)):
            return CloudErrorKind.NETWORK
    return CloudErrorKind.UNKNOWN


async def fetch_google_models(
    api_key: Optional[str],
    client: "AsyncClient",
    *,
    models_cache: dict[str, ModelInfo],
    timeout: float = 30.0,
) -> list[ModelInfo]:
    """
    Запрашивает список моделей у Google Gemini API.

    Зависимости передаются аргументами. При отсутствии api_key возвращает [].
    """
    if not api_key:
        return []

    models: list[ModelInfo] = []
    url = "https://generativelanguage.googleapis.com/v1beta/models"
    params = {"key": api_key}

    try:
        response = await client.get(url, params=params, timeout=timeout)
        if response.status_code != 200:
            kind = classify_gemini_error(status_code=response.status_code)
            logger.warning(
                "google_api_error",
                status=response.status_code,
                error_kind=kind.value,
            )
            return []
        data = response.json()
    except (httpx.HTTPError, OSError) as e:
        kind = classify_gemini_error(exc=e)
        logger.error("google_api_exception", error=str(e), error_kind=kind.value)
        return []
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("google_api_parse_error", error=str(e))
        return []

    for m in data.get("models", []):
        if "gemini" not in m.get("name", "").lower():
            continue
        name = m.get("name", "")
        m_id = name.replace("models/", "google/")
        is_vision = (
            "vision" in m_id or "flash" in m_id or "pro" in m_id
        )
        model = ModelInfo(
            id=m_id,
            name=m.get("displayName", m_id),
            type=ModelType.CLOUD_GEMINI,
            status=ModelStatus.AVAILABLE,
            size_gb=0.0,
            supports_vision=is_vision,
        )
        models.append(model)
        models_cache[m_id] = model
    return models


async def verify_gemini_access(
    model_id: str,
    api_key: Optional[str],
    client: "AsyncClient",
    *,
    timeout: float = 15.0,
) -> bool:
    """
    Проверяет доступ к модели Gemini (generateContent с одним токеном).

    Возвращает True при status 200, иначе False.
    """
    if not api_key:
        return False
    model_name = model_id.replace("google/", "")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
    )
    params = {"key": api_key}
    body = {"contents": [{"parts": [{"text": "Hi"}]}]}

    try:
        response = await client.post(
            url, params=params, json=body, timeout=timeout
        )
        if response.status_code == 200:
            return True
        kind = classify_gemini_error(status_code=response.status_code)
        logger.debug(
            "gemini_verify_failed",
            model_id=model_id,
            status=response.status_code,
            error_kind=kind.value,
        )
        return False
    except (httpx.HTTPError, OSError):
        return False


def get_cloud_fallback_chain(
    *,
    tier_1: Optional[list[str]] = None,
    tier_2: Optional[list[str]] = None,
    tier_3: Optional[list[str]] = None,
    default: str = DEFAULT_CLOUD_MODEL,
) -> list[str]:
    """
    Собирает упорядоченную цепочку облачных моделей по тирам.

    По умолчанию: tier_1 (flash), tier_2 (pro), tier_3 (pro-latest).
    """
    t1 = tier_1 if tier_1 is not None else CLOUD_TIER_1_IDS
    t2 = tier_2 if tier_2 is not None else CLOUD_TIER_2_IDS
    t3 = tier_3 if tier_3 is not None else CLOUD_TIER_3_IDS
    chain: list[str] = []
    seen: set[str] = set()
    for mid in t1 + t2 + t3:
        if mid not in seen:
            chain.append(mid)
            seen.add(mid)
    if default not in seen:
        chain.append(default)
    return chain


async def resolve_working_gemini_key(
    api_key_free: Optional[str],
    api_key_paid: Optional[str],
    client: "AsyncClient",
    *,
    test_model: str = "gemini-2.0-flash",
    _cache: dict[str, Optional[str]] = {},
) -> Optional[str]:
    """
    Returns the first working Gemini API key: tries free first, then paid.
    Caches the result for the process lifetime so we don't re-verify every request.
    """
    if "resolved" in _cache:
        return _cache["resolved"]

    for label, key in [("free", api_key_free), ("paid", api_key_paid)]:
        if not key:
            continue
        ok = await verify_gemini_access(test_model, key, client, timeout=10.0)
        if ok:
            logger.info("gemini_key_resolved", tier=label)
            _cache["resolved"] = key
            return key
        logger.warning("gemini_key_failed", tier=label)

    _cache["resolved"] = api_key_free or api_key_paid or None
    return _cache["resolved"]


def reset_gemini_key_cache() -> None:
    """Clear the resolved key cache (useful when keys change at runtime)."""
    resolve_working_gemini_key.__defaults__[3].pop("resolved", None)  # type: ignore[union-attr]


async def fetch_google_models_with_fallback(
    api_key_free: Optional[str],
    api_key_paid: Optional[str],
    client: "AsyncClient",
    *,
    models_cache: dict[str, ModelInfo],
    timeout: float = 30.0,
) -> list[ModelInfo]:
    """Tries free key first for model listing; falls back to paid."""
    for key in [api_key_free, api_key_paid]:
        if not key:
            continue
        result = await fetch_google_models(key, client, models_cache=models_cache, timeout=timeout)
        if result:
            return result
    return []


async def get_best_cloud_model(
    api_key: Optional[str],
    client: "AsyncClient",
    *,
    fallback_chain: Optional[list[str]] = None,
    config_model: Optional[str] = None,
    verify_fn: Optional[
        Callable[[str, Optional[str], "AsyncClient"], object]
    ] = None,
) -> str:
    """
    Возвращает лучшую доступную облачную модель из цепочки fallback.

    Если передан config_model и он не "auto", возвращается config_model.
    Иначе — первый элемент цепочки при наличии api_key (опционально с проверкой
    через verify_fn). По умолчанию без верификации возвращает первый облачный
    модель из цепочки при наличии ключа.
    """
    if config_model and config_model != "auto":
        return config_model
    if not api_key:
        return DEFAULT_CLOUD_MODEL
    chain = fallback_chain or get_cloud_fallback_chain()
    if verify_fn:
        for model_id in chain:
            if "gemini" not in model_id.lower():
                continue
            try:
                ok = await verify_fn(model_id, api_key, client)
                if ok:
                    return model_id
            except Exception:
                continue
        return config_model or DEFAULT_CLOUD_MODEL
    # Без верификации — первый облачный из цепочки
    for model_id in chain:
        if "gemini" in model_id.lower():
            return model_id
    return config_model or DEFAULT_CLOUD_MODEL
