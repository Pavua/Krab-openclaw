# -*- coding: utf-8 -*-
"""
Wave 239: Dynamic LM Studio models discovery.

Зачем
-----
LM Studio (:1234) держит десятки локальных моделей (на dev-боксе ~85 шт.).
Раньше Krab показывал только хардкоженный белый список из 4 имён и live
``loaded_models`` из ``resolve_local_runtime_truth``. Все остальные были
"невидимы" в `/admin/models` picker.

Что делает
----------
- ``discover_lm_studio_models()`` — async вызов ``GET /v1/models`` с
  опциональным ``Authorization: Bearer <LM_STUDIO_API_KEY>``.
- Парсит OpenAI-совместимый формат ``{"data": [{"id", "object",
  "owned_by"}, ...]}``.
- Фильтрует embedding/rerank/CLIP/whisper модели (не chat-LLM).
- Кэш 60 секунд (TTL) — чтобы при каждом открытии страницы admin'а
  не дёргать LM Studio.
- Любые ошибки (timeout, connection refused, 401) → пустой список +
  warning лог + prometheus counter с label ``error``.

Контракт
--------
Возвращаемый shape: ``list[dict]``::

    [{"id": "gemma-3-12b-it-qat-4bit",
      "object": "model",
      "owned_by": "organization_owner",
      "label": "gemma-3-12b-it-qat-4bit"}, ...]

Алиас-резолвер
--------------
В picker модели приходят с prefix ``lm-studio-local/`` (Wave 239).
При отправке запроса к LM Studio Krab snimает prefix и шлёт чистый id.
См. ``src/core/lm_studio_aliases.py``.
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

import structlog

from src.core.lm_studio_auth import build_lm_studio_auth_headers
from src.core.metrics.lm_studio_discovery import inc_lm_studio_discovery

logger = structlog.get_logger(__name__)

# Дефолтные параметры. Можно переопределить через ENV.
_DEFAULT_URL = "http://127.0.0.1:1234"
_DEFAULT_CACHE_TTL_SEC = 60
_DEFAULT_TIMEOUT_SEC = 5.0

# Эвристика для фильтрации не-LLM моделей. Совпадение по подстроке
# в lower-cased id ИЛИ совпадение по object (`"embeddings"`, `"rerank"`).
_NON_LLM_NAME_MARKERS: tuple[str, ...] = (
    "embed",  # nomic-embed-text, BGE-embed, text-embedding-*
    "embedding",
    "rerank",  # bge-reranker, jina-rerank
    "reranker",
    "clip",  # CLIP vision encoders
    "whisper",  # speech-to-text (Krab не нужен)
    "moondream",  # vision-only encoder (если попадётся)
)
_NON_LLM_OBJECT_MARKERS: tuple[str, ...] = (
    "embeddings",
    "embedding",
    "rerank",
)

# In-memory cache. tuple (timestamp, models). Используем module-level чтобы
# жил между HTTP-запросами в рамках одного процесса.
_cache_lock_state: dict[str, Any] = {
    "ts": 0.0,
    "models": [],
}


def _get_base_url() -> str:
    return (os.environ.get("LM_STUDIO_URL") or _DEFAULT_URL).rstrip("/")


def _get_cache_ttl_sec() -> int:
    raw = (os.environ.get("KRAB_LM_DISCOVERY_CACHE_TTL_SEC") or "").strip()
    if not raw:
        return _DEFAULT_CACHE_TTL_SEC
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_CACHE_TTL_SEC
    return max(5, value)


def _is_non_llm_model(entry: dict[str, Any]) -> bool:
    """True если модель похожа на embedding/rerank/CLIP/Whisper."""
    obj = str(entry.get("object") or "").lower()
    for marker in _NON_LLM_OBJECT_MARKERS:
        if marker in obj:
            return True
    name = str(entry.get("id") or "").lower()
    for marker in _NON_LLM_NAME_MARKERS:
        if marker in name:
            return True
    return False


def _normalize_entry(entry: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Нормализуем сырую запись из /v1/models. Возвращает None если id пустой."""
    mid = str(entry.get("id") or "").strip()
    if not mid:
        return None
    return {
        "id": mid,
        "object": str(entry.get("object") or "model"),
        "owned_by": str(entry.get("owned_by") or ""),
        "label": mid,  # UI-friendly. Для коротких имён id и label совпадают.
    }


def _read_cache(now: float) -> Optional[list[dict[str, Any]]]:
    """Возвращает кэшированный список если TTL не истёк, иначе None."""
    ttl = _get_cache_ttl_sec()
    ts = float(_cache_lock_state.get("ts") or 0.0)
    if ts <= 0 or (now - ts) > ttl:
        return None
    models = _cache_lock_state.get("models") or []
    if not isinstance(models, list):
        return None
    return list(models)


def _write_cache(models: list[dict[str, Any]]) -> None:
    _cache_lock_state["ts"] = time.time()
    _cache_lock_state["models"] = list(models)


def reset_cache() -> None:
    """Принудительный сброс кэша. Используется в тестах."""
    _cache_lock_state["ts"] = 0.0
    _cache_lock_state["models"] = []


async def _fetch_models_raw(base_url: str, *, timeout: float) -> list[dict[str, Any]]:
    """HTTP GET /v1/models с Bearer-auth. Бросает исключение при ошибке."""
    try:
        import httpx  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"httpx_import_failed: {exc}") from exc

    headers = build_lm_studio_auth_headers(include_json_accept=True)
    url = f"{base_url}/v1/models"

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code == 401:
            raise PermissionError("lm_studio_unauthorized_401")
        if resp.status_code != 200:
            raise RuntimeError(f"lm_studio_http_{resp.status_code}")
        payload = resp.json()

    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def filter_llm_models(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Отфильтровать embedding/rerank/etc, оставить только chat-LLM."""
    result: list[dict[str, Any]] = []
    for entry in raw:
        if _is_non_llm_model(entry):
            continue
        norm = _normalize_entry(entry)
        if norm is not None:
            result.append(norm)
    return result


async def discover_lm_studio_models(
    *,
    force_refresh: bool = False,
    timeout: float = _DEFAULT_TIMEOUT_SEC,
) -> list[dict[str, Any]]:
    """Главный entry point — возвращает список LLM моделей в LM Studio.

    Параметры
    ---------
    force_refresh : пропустить кэш и сходить в LM Studio даже если TTL
        не истёк. Используется при ручном "Refresh" в picker UI.
    timeout : timeout HTTP-запроса (сек).

    Возвращает
    ----------
    list[dict] : ``[{"id","object","owned_by","label"}, ...]``. Пустой при
    любой ошибке connectivity / auth.
    """
    now = time.time()
    if not force_refresh:
        cached = _read_cache(now)
        if cached is not None:
            inc_lm_studio_discovery(result="cache_hit")
            return cached

    base_url = _get_base_url()
    try:
        raw = await _fetch_models_raw(base_url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - все ошибки → пустой список
        logger.warning(
            "lm_studio_discovery_failed",
            base_url=base_url,
            error=str(exc),
        )
        inc_lm_studio_discovery(result="error")
        # Не затираем кэш — пусть UI получит последний known-good список
        # вместо пустоты если LM Studio временно недоступен.
        cached = _read_cache(now)
        if cached is not None:
            return cached
        return []

    filtered = filter_llm_models(raw)
    _write_cache(filtered)
    inc_lm_studio_discovery(result="success")
    logger.debug(
        "lm_studio_discovery_ok",
        base_url=base_url,
        total_raw=len(raw),
        kept=len(filtered),
    )
    return filtered


__all__ = [
    "discover_lm_studio_models",
    "filter_llm_models",
    "reset_cache",
]
