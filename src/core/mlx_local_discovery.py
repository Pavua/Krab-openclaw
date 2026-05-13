# -*- coding: utf-8 -*-
"""
Wave 240: Динамический discovery моделей в локальном MLX backend (:8088).

Контекст
--------
Wave 182 hard-code'нул 4 модели в группу ``mlx-local-kv4`` (`_CLOUD_PROVIDERS`)
в model picker:
- gemma-4-26b
- qwen3-4b-kv4
- qwen3-14b-kv4
- llama-3.3-8b-kv4

В реальности RotorQuant сессия (см. ``com.user.mlx-lm-server.plist`` —
наш scope не трогать) может swap'нуть загруженную модель в любой момент
(draft model для speculative decoding, эксперимент с другой quant'ом, и т.д.),
и `:8088/v1/models` начнёт отдавать совершенно другой список. UI-список
становится stale.

Решение
-------
Discovery-функция, вызываемая по запросу из ``_CLOUD_PROVIDERS`` (или его
наследников). Кэш 30s — RotorQuant может swap'ать модели часто, нужен баланс
между свежестью и нагрузкой на :8088.

Алгоритм:
1. ``_discover_mlx_local_models()`` — HTTP GET к ``/v1/models`` без auth.
2. Cache hit (30s TTL) → отдаём предыдущий результат.
3. Для каждого ``id`` (full path) генерируем короткое имя:
   - basename полного пути,
   - lowercased,
   - cleaned (alphanum/dash only).
4. Persist в ``~/.openclaw/krab_runtime_state/mlx_local_aliases_runtime.json``
   (cache переживает restart Krab — это helpful когда :8088 временно down
   на старте Krab).
5. Prometheus counter ``krab_mlx_local_discovery_total{result=...}`` —
   ``success`` / ``error`` / ``fallback``.

Coordination с Wave 222 (``mlx_local_aliases``)
-----------------------------------------------
- ``_DEFAULT_ALIASES`` остаётся как fallback (RotorQuant перезагружается /
  :8088 down — мы всё ещё знаем минимум 2 модели).
- ``get_runtime_extended_alias_map()`` объединяет defaults + ENV + discovery
  (приоритет: discovery > ENV > defaults).

Fallback chain (при ошибке discovery)
-------------------------------------
1. Свежий persisted JSON (если есть и < 24h).
2. Wave 182 static list (Gemma + 3 hypothetical Qwen/Llama).
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx
import structlog

from src.core.mlx_local_aliases import _DEFAULT_ALIASES, get_alias_map

logger = structlog.get_logger(__name__)

# ── Конфигурация ────────────────────────────────────────────────────────────

# Дефолтный URL — оверрайдим через ENV (Wave 222 уже использует то же имя).
_DEFAULT_MLX_LOCAL_URL = "http://127.0.0.1:8088"

# TTL кэша in-memory. 30s — RotorQuant может swap модели часто (draft model
# при speculative decoding меняется per-experiment).
_CACHE_TTL_SEC = 30.0

# Wave 182 static fallback — те же 4 модели, что были hard-code'нуты.
# Используется когда discovery провалился и нет валидного persisted кэша.
_STATIC_FALLBACK_MODELS: list[dict[str, str]] = [
    {
        "id": "mlx-local-kv4/gemma-4-26b",
        "label": "Gemma-4-26B-A4B Heretic (Baseline)",
        "full_path": (
            "/Volumes/4TB SSD/LMStudio_models/mlx-community/gemma-4-26B-A4B-it-OptiQ-4bit"
        ),
    },
    {
        "id": "mlx-local-kv4/qwen3-4b-kv4",
        "label": "Qwen3-4B Huihui (KV4, 105 tok/s)",
        "full_path": (
            "/Volumes/4TB SSD/LMStudio_models/mlx-community/"
            "Qwen3-4B-Instruct-2507-Huihui-abliterated-MLX-4bit"
        ),
    },
    {
        "id": "mlx-local-kv4/qwen3-14b-kv4",
        "label": "Qwen3-14B Huihui v2 (KV4, 41.5 tok/s)",
        "full_path": "",
    },
    {
        "id": "mlx-local-kv4/llama-3.3-8b-kv4",
        "label": "Llama-3.3-8B Abl 128K (KV4, 39.5 tok/s)",
        "full_path": "",
    },
]

# ── Persistent кэш ──────────────────────────────────────────────────────────


def _runtime_state_dir() -> Path:
    """Путь к Krab runtime state dir."""
    return Path.home() / ".openclaw" / "krab_runtime_state"


def _persisted_cache_path() -> Path:
    """Путь к persisted JSON с runtime aliases."""
    return _runtime_state_dir() / "mlx_local_aliases_runtime.json"


def _persist_runtime_aliases(aliases: dict[str, str]) -> None:
    """Сохраняет resolved aliases в JSON (survives Krab restart).

    Шейп::

        {
            "ts": 1715000000.0,
            "aliases": {"mlx-local-kv4/gemma-4-26b": "/Volumes/.../gemma..."}
        }
    """
    path = _persisted_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"ts": time.time(), "aliases": dict(aliases)}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        tmp.replace(path)
    except OSError as exc:
        logger.debug("mlx_local_discovery_persist_failed", error=str(exc))


def _load_persisted_aliases(*, max_age_sec: float = 86400.0) -> dict[str, str]:
    """Читает persisted JSON. Возвращает пустой dict если файл missing/stale."""
    path = _persisted_cache_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        logger.debug("mlx_local_discovery_load_failed", error=str(exc))
        return {}
    if not isinstance(raw, dict):
        return {}
    ts = raw.get("ts")
    aliases = raw.get("aliases")
    if not isinstance(aliases, dict):
        return {}
    # Если max_age_sec <= 0 — игнорируем возраст (always trust).
    if max_age_sec > 0 and isinstance(ts, (int, float)):
        if time.time() - float(ts) > max_age_sec:
            return {}
    result: dict[str, str] = {}
    for key, value in aliases.items():
        if isinstance(key, str) and isinstance(value, str) and key and value:
            result[key] = value
    return result


# ── In-memory cache ──────────────────────────────────────────────────────────

# Защита от частых HTTP-запросов: discovery → cache 30s. Простая глобальная
# структура — discovery вызывается из request handlers (asyncio но без
# concurrent contention для одного процесса).
_CACHE: dict[str, Any] = {
    "ts": 0.0,
    "models": [],  # list[dict] same shape as _STATIC_FALLBACK_MODELS
    "source": "none",  # "live" | "persisted" | "static" | "none"
}


def _cache_fresh() -> bool:
    """True если in-memory cache ещё свеж (< TTL)."""
    return (time.time() - float(_CACHE.get("ts") or 0.0)) < _CACHE_TTL_SEC


def _reset_cache() -> None:
    """Сбрасывает in-memory кэш (для тестов / force refresh)."""
    _CACHE["ts"] = 0.0
    _CACHE["models"] = []
    _CACHE["source"] = "none"


# ── Алиас generation ────────────────────────────────────────────────────────

# Чистка basename до alphanum/dash. Wave 222 alias prefix — "mlx-local-kv4/".
_ALIAS_CLEAN_RE = re.compile(r"[^a-z0-9\-]+")


def _short_alias_for(full_path: str) -> tuple[str, str]:
    """Генерит ``(short_id, short_basename)`` для full path модели.

    Пример::

        full = "/Volumes/.../gemma-4-26B-A4B-it-OptiQ-4bit"
        → short_id   = "mlx-local-kv4/gemma-4-26b-a4b-it-optiq-4bit"
          short_base = "gemma-4-26b-a4b-it-optiq-4bit"
    """
    base = os.path.basename(full_path or "").strip()
    if not base:
        return "", ""
    cleaned = _ALIAS_CLEAN_RE.sub("-", base.lower()).strip("-")
    if not cleaned:
        return "", ""
    return f"mlx-local-kv4/{cleaned}", cleaned


# ── Prometheus counter ──────────────────────────────────────────────────────

# Регистрация — try/except как везде в prometheus_metrics.py. None если
# prometheus_client не установлен → record_*() становится no-op.
try:
    from prometheus_client import Counter as _CounterDiscovery  # type: ignore[import-not-found]

    krab_mlx_local_discovery_total = _CounterDiscovery(
        "krab_mlx_local_discovery_total",
        "MLX :8088 model discovery invocations (success/error/fallback)",
        ["result"],
    )
except Exception:  # noqa: BLE001 - prometheus_client optional
    krab_mlx_local_discovery_total = None  # type: ignore[assignment]


def _record_discovery_result(result: str) -> None:
    """Безопасный инкремент counter — никогда не падает."""
    try:
        if krab_mlx_local_discovery_total is not None:
            r = (result or "unknown")[:20]
            krab_mlx_local_discovery_total.labels(result=r).inc()
    except Exception:  # noqa: BLE001
        pass


# ── Discovery core ──────────────────────────────────────────────────────────


def _backend_url() -> str:
    """Effective URL :8088 backend (ENV override поддерживается)."""
    return os.getenv("MLX_LOCAL_BACKEND_URL", _DEFAULT_MLX_LOCAL_URL).rstrip("/")


def _fetch_live_models(*, timeout_sec: float = 3.0) -> list[dict[str, str]]:
    """HTTP GET :8088/v1/models → list of models в нашем шейпе.

    Бросает любые httpx-исключения наружу — caller обработает fallback.
    """
    base = _backend_url()
    endpoint = f"{base}/v1/models"
    # Sync httpx тут — discovery вызывается из FastAPI handlers, которые
    # могут быть sync или async. Простой sync клиент работает в обоих случаях
    # (вызывают через asyncio.to_thread если из async).
    with httpx.Client(timeout=timeout_sec) as client:
        response = client.get(endpoint)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") or []
    if not isinstance(data, list):
        return []
    out: list[dict[str, str]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        full_path = str(entry.get("id") or "").strip()
        if not full_path:
            continue
        short_id, short_base = _short_alias_for(full_path)
        if not short_id:
            continue
        out.append(
            {
                "id": short_id,
                "label": short_base,
                "full_path": full_path,
            }
        )
    return out


def _discover_mlx_local_models(
    *,
    force_refresh: bool = False,
) -> tuple[list[dict[str, str]], str]:
    """Возвращает (models, source).

    source ∈ {"live", "cached", "persisted", "static"}.

    Параметры
    ---------
    force_refresh : пропустить in-memory cache (но в случае ошибки всё равно
        try persisted/static).
    """
    if not force_refresh and _cache_fresh() and _CACHE.get("models"):
        return list(_CACHE["models"]), "cached"

    try:
        live = _fetch_live_models()
        if live:
            _CACHE["ts"] = time.time()
            _CACHE["models"] = live
            _CACHE["source"] = "live"
            # Persist для следующего restart'а.
            persisted = {m["id"]: m["full_path"] for m in live if m.get("full_path")}
            if persisted:
                _persist_runtime_aliases(persisted)
            _record_discovery_result("success")
            return list(live), "live"
        # Пустой ответ — не ошибка, но и не success: trigger fallback.
        logger.warning("mlx_local_discovery_empty_response")
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("mlx_local_discovery_failed", error=str(exc))
        _record_discovery_result("error")
    except Exception as exc:  # noqa: BLE001
        logger.warning("mlx_local_discovery_unexpected", error=str(exc))
        _record_discovery_result("error")

    # Fallback 1: persisted JSON (< 24h).
    persisted = _load_persisted_aliases()
    if persisted:
        models = []
        for short_id, full in persisted.items():
            base = os.path.basename(full) or short_id.split("/", 1)[-1]
            models.append({"id": short_id, "label": base, "full_path": full})
        _CACHE["ts"] = time.time()
        _CACHE["models"] = models
        _CACHE["source"] = "persisted"
        _record_discovery_result("fallback")
        return models, "persisted"

    # Fallback 2: Wave 182 static list.
    static = [dict(m) for m in _STATIC_FALLBACK_MODELS]
    _CACHE["ts"] = time.time()
    _CACHE["models"] = static
    _CACHE["source"] = "static"
    _record_discovery_result("fallback")
    return static, "static"


# ── Public API ──────────────────────────────────────────────────────────────


def discover_mlx_local_models(
    *,
    force_refresh: bool = False,
) -> list[dict[str, str]]:
    """Public wrapper: возвращает list of {id, label, full_path}.

    Используется в model picker (``_CLOUD_PROVIDERS``) и любом UI, которому
    нужен текущий live-список загруженных в :8088 моделей.
    """
    models, _ = _discover_mlx_local_models(force_refresh=force_refresh)
    return models


def get_runtime_extended_alias_map() -> dict[str, str]:
    """Возвращает merged alias map: defaults + ENV + live discovery.

    Приоритет (высший → низший): live discovery > ENV > _DEFAULT_ALIASES.
    Используется в ``mlx_local_aliases.resolve_mlx_local_alias`` через
    monkey-patch hook (см. Wave 240 wiring в этом модуле).
    """
    merged = dict(_DEFAULT_ALIASES)
    # ENV override через существующий механизм Wave 222.
    base = get_alias_map()
    merged.update(base)
    # Discovery — поверх всего (priority).
    try:
        models, _ = _discover_mlx_local_models()
        for m in models:
            fp = m.get("full_path")
            if m.get("id") and fp:
                merged[m["id"]] = fp
    except Exception as exc:  # noqa: BLE001
        logger.debug("mlx_local_runtime_extended_alias_map_failed", error=str(exc))
    return merged


def get_discovery_cache_info() -> dict[str, Any]:
    """Diagnostic: возвращает текущее состояние in-memory кэша.

    Полезно для admin endpoint / отладки RotorQuant interop.
    """
    return {
        "ts": float(_CACHE.get("ts") or 0.0),
        "age_sec": max(0.0, time.time() - float(_CACHE.get("ts") or 0.0)),
        "ttl_sec": _CACHE_TTL_SEC,
        "fresh": _cache_fresh(),
        "source": _CACHE.get("source", "none"),
        "count": len(_CACHE.get("models") or []),
        "backend_url": _backend_url(),
    }


def build_mlx_local_provider_group(
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Wave 240: shape, который подставляется в ``_CLOUD_PROVIDERS``.

    Совместим с pre-Wave-224 picker форматом — ``models`` это list of
    ``(id, label)`` кортежей. Discovery всегда возвращает хотя бы static
    fallback (4 Wave 182 модели), так что shape стабилен.

    Provider ``id="mlx-local-kv4"`` НЕ меняется — это контракт с UI.
    """
    models, source = _discover_mlx_local_models(force_refresh=force_refresh)
    return {
        "id": "mlx-local-kv4",
        "label": "MLX KV4 (Local :8088)",
        "type": "local",
        "models": [(m["id"], m["label"]) for m in models],
        "discovery_source": source,  # debug only
    }


# ── Fallback static map (для тестов) ────────────────────────────────────────


def _static_fallback_aliases() -> dict[str, str]:
    """Static fallback мапа: для тестов и emergency path."""
    return {m["id"]: m["full_path"] for m in _STATIC_FALLBACK_MODELS if m.get("full_path")}


__all__ = [
    "discover_mlx_local_models",
    "get_runtime_extended_alias_map",
    "get_discovery_cache_info",
    "build_mlx_local_provider_group",
    "krab_mlx_local_discovery_total",
]
