# -*- coding: utf-8 -*-
"""
Wave 230: реальный routing запросов Краба на выбранный в /admin/models backend.

Цель
----
Кнопка «Set primary» в `/admin/models` (Wave 144) должна не просто менять
лейбл и `MODEL` env — она должна реально перенаправить все последующие
chat-completion запросы Краба на выбранный backend:

- ``mlx-local-kv4/*``  → локальный ``mlx_lm.server`` (`http://127.0.0.1:8088`)
- ``google-vertex/*``  → текущий cloud (Vertex direct bypass / OpenClaw gateway)
- ``openclaw/main``    → OpenClaw gateway (`http://127.0.0.1:18789`)
- любое другое cloud-id (например, ``google/gemini-3-pro-preview``) → cloud
  default (OpenClaw gateway), это поведение до Wave 230.

Контракт
--------
Источник истины — JSON-файл
``~/.openclaw/krab_runtime_state/active_model.json`` следующего вида::

    {
      "model": "mlx-local-kv4/gemma-4-26b",
      "switched_at": 1715680000.0,
      "switched_by": "owner_panel",
      "reason": "set_model"
    }

ENV ``KRAB_PRIMARY_MODEL_ID`` (если задан и не пуст) перекрывает значение из
файла — это полезно для CI/recovery, когда нужно «жёстко» закрепить backend
независимо от того, что сохранил `/api/admin/model/switch`.

Все читатели идут через :func:`get_active_model_id` (TTL-кэш 30 s) и
:func:`resolve_active_target` (возвращает ``(base_url, model_id)``); кэш
сбрасывается явно `:func:`invalidate_cache` после записи.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

# ── Пути / константы ─────────────────────────────────────────────────────────

# Хранилище: тот же каталог что и chat_response_policies.json, inbox_state.json.
STATE_PATH: Path = Path.home() / ".openclaw" / "krab_runtime_state" / "active_model.json"

# ENV override — если задан и не пуст, имеет приоритет над файлом.
ENV_VAR = "KRAB_PRIMARY_MODEL_ID"

# Префиксы / маркеры backend'ов
_MLX_LOCAL_PREFIX = "mlx-local-kv4/"
_OPENCLAW_PREFIX = "openclaw"  # `openclaw` или `openclaw/main`

# Endpoint'ы — берём из env при каждом ресолве, чтобы тесты могли
# подменять URL через monkeypatch.setenv без полной перезагрузки модуля.
_DEFAULT_MLX_LOCAL_URL = "http://127.0.0.1:8088"
_DEFAULT_OPENCLAW_URL = "http://127.0.0.1:18789"

# TTL чтения файла. 30s достаточно, чтобы не дёргать диск в hot-path,
# но и не задерживать применение switch'а дольше 30s.
_CACHE_TTL_SEC = 30.0


# ── Cache ────────────────────────────────────────────────────────────────────


@dataclass
class _CacheEntry:
    value: Optional[str]
    ts: float


_cache_lock = threading.Lock()
_cache: Optional[_CacheEntry] = None


def invalidate_cache() -> None:
    """Сбрасывает in-memory TTL-кэш активной модели.

    Вызывается после записи через :func:`set_active_model`, чтобы следующий
    :func:`get_active_model_id` сразу увидел новое значение.
    """
    global _cache
    with _cache_lock:
        _cache = None


# ── Helpers ──────────────────────────────────────────────────────────────────


def _mlx_local_url() -> str:
    """Endpoint локального MLX backend (override через `MLX_LOCAL_KV4_URL`)."""
    return (os.getenv("MLX_LOCAL_KV4_URL") or _DEFAULT_MLX_LOCAL_URL).strip()


def _openclaw_url() -> str:
    """Endpoint OpenClaw gateway (override через `OPENCLAW_URL`)."""
    return (os.getenv("OPENCLAW_URL") or _DEFAULT_OPENCLAW_URL).strip().rstrip("/")


def _env_override() -> Optional[str]:
    """Возвращает значение из ENV ``KRAB_PRIMARY_MODEL_ID`` или None."""
    raw = (os.getenv(ENV_VAR) or "").strip()
    return raw or None


def _read_state_file() -> Optional[str]:
    """Читает model id из JSON-файла. Возвращает None при отсутствии/ошибке."""
    if not STATE_PATH.exists():
        return None
    try:
        with STATE_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning("active_model_state_read_failed", path=str(STATE_PATH), error=str(exc))
        return None
    if not isinstance(data, dict):
        return None
    model = data.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    return None


# ── Public API ──────────────────────────────────────────────────────────────


def get_active_model_id() -> Optional[str]:
    """Возвращает текущий выбранный model id (с учётом ENV и TTL-кэша).

    Приоритет:
    1. ENV ``KRAB_PRIMARY_MODEL_ID`` (если задан, не кэшируем — env-driven
       тестам нужна мгновенная видимость изменений).
    2. JSON-файл ``active_model.json`` (TTL-кэш 30s).
    3. ``None`` — если ничего не задано (Krab продолжает работать как до
       Wave 230, не меняя routing).
    """
    env_value = _env_override()
    if env_value:
        return env_value

    global _cache
    now = time.time()
    with _cache_lock:
        if _cache is not None and (now - _cache.ts) < _CACHE_TTL_SEC:
            return _cache.value
        value = _read_state_file()
        _cache = _CacheEntry(value=value, ts=now)
        return value


def set_active_model(
    model_id: str,
    *,
    by: str = "owner_panel",
    reason: str = "",
) -> dict[str, Any]:
    """Atomic-записывает выбранную модель в `active_model.json` + сбрасывает кэш.

    Возвращает записанный payload (для логов и UI). Атомарность — через
    ``tempfile + os.replace`` (стандартный приём, чтобы избежать половинных
    файлов при kill -9 в момент записи).
    """
    model_id = (model_id or "").strip()
    if not model_id:
        raise ValueError("model_id must be a non-empty string")

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": model_id,
        "switched_at": time.time(),
        "switched_by": str(by or "owner_panel"),
        "reason": str(reason or ""),
    }
    # Atomic write: tempfile в той же директории + os.replace.
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=".active_model.",
        suffix=".json.tmp",
        dir=str(STATE_PATH.parent),
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, STATE_PATH)
    except Exception:
        # Если что-то пошло не так — удаляем tempfile, чтобы не оставлять мусор.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    invalidate_cache()
    logger.info(
        "active_model_state_written",
        model=model_id,
        by=payload["switched_by"],
        reason=payload["reason"],
    )
    return payload


def is_mlx_local_model(model_id: Optional[str]) -> bool:
    """True если id похож на mlx-local backend (`mlx-local-kv4/*`)."""
    if not model_id:
        return False
    return model_id.strip().lower().startswith(_MLX_LOCAL_PREFIX)


def is_openclaw_model(model_id: Optional[str]) -> bool:
    """True если id это `openclaw` или `openclaw/<agent>`."""
    if not model_id:
        return False
    norm = model_id.strip().lower()
    return norm == _OPENCLAW_PREFIX or norm.startswith(_OPENCLAW_PREFIX + "/")


def resolve_active_target(
    *,
    default_base_url: str,
    default_model: str = "openclaw",
) -> tuple[str, str]:
    """Решает, куда отправить запрос: возвращает ``(base_url, model_id_for_payload)``.

    Параметры
    ---------
    default_base_url : текущий ``self.base_url`` из OpenClawClient
        (обычно ``http://127.0.0.1:18789``). Используется, если активная
        модель — cloud / openclaw / не задана.
    default_model : значение по умолчанию для ``payload["model"]`` когда
        активная модель не выбрана (обычно ``"openclaw"`` — gateway routes
        сам по agents.defaults.model.primary).

    Правила
    -------
    - ``mlx-local-kv4/*`` → (MLX_LOCAL_KV4_URL, <short_id>) — alias-резолвер
      из Wave 222/225 потом превратит short_id в полный путь.
    - ``openclaw`` / ``openclaw/<agent>`` → (default_base_url, "openclaw").
    - любая другая модель → (default_base_url, default_model) — то есть
      Краб отправит запрос в gateway с обычным ``model="openclaw"`` и
      gateway сам выберет cloud-провайдер согласно своей конфигурации.
    - модель не выбрана (None) → (default_base_url, default_model) — pre-230
      поведение.
    """
    active = get_active_model_id()
    if active is None:
        return default_base_url, default_model

    if is_mlx_local_model(active):
        return _mlx_local_url(), active

    if is_openclaw_model(active):
        return default_base_url, "openclaw"

    # Cloud / прочие — продолжаем ходить через gateway. Конкретную модель
    # внутри cloud выбирает сам gateway по agents.defaults; payload["model"]
    # остаётся "openclaw", иначе gateway вернёт 400 на незнакомое имя.
    return default_base_url, default_model


__all__ = [
    "ENV_VAR",
    "STATE_PATH",
    "get_active_model_id",
    "invalidate_cache",
    "is_mlx_local_model",
    "is_openclaw_model",
    "resolve_active_target",
    "set_active_model",
]
