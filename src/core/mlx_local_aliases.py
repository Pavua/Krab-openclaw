# -*- coding: utf-8 -*-
"""
Wave 222: Alias-слой для локального MLX backend (`mlx_lm.server` на :8088).

Проблема
--------
`mlx_lm.server` отдаёт в `/v1/models` полный путь до каталога модели как `id`:
    "/Volumes/4TB SSD/LMStudio_models/mlx-community/gemma-4-26B-A4B-it-OptiQ-4bit"

В то время как Krab/RotorQuant работают с короткими идентификаторами вида
`mlx-local-kv4/gemma-4-26b`. Если отправить короткое имя в `model=` поле
запроса — сервер возвращает 404 (model not found).

Решение
-------
Тонкий двунаправленный alias-слой:
- `resolve_mlx_local_alias(model_id, *, target_url)` — короткое → полный путь
  применяется только когда запрос уходит на MLX local backend (по умолчанию
  по совпадению host:port с :8088).
- `reverse_mlx_local_alias(full_path)` — полный путь → короткое имя
  для чистых логов / Telegram footer.

Маппинг описывается:
1. встроенным `_DEFAULT_ALIASES` (расширяемым по мере добавления моделей),
2. ENV `MLX_LOCAL_MODEL_ALIASES_JSON` (JSON-строка) — RotorQuant-сессии могут
   обновлять без правок кода.
"""

from __future__ import annotations

import json
import os
from typing import Optional
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger(__name__)

# Дефолтный встроенный маппинг. Расширяется по мере добавления моделей в
# локальный MLX backend (RotorQuant). Полные пути соответствуют тому, что
# `mlx_lm.server` отдаёт в `/v1/models`.
_DEFAULT_ALIASES: dict[str, str] = {
    "mlx-local-kv4/gemma-4-26b": (
        "/Volumes/4TB SSD/LMStudio_models/mlx-community/gemma-4-26B-A4B-it-OptiQ-4bit"
    ),
    # Гипотеза: реальный id проверяется через `curl :8088/v1/models` после
    # загрузки модели. Если совпадёт — alias сработает; иначе нужно поправить.
    "mlx-local-kv4/qwen3-4b-kv4": (
        "/Volumes/4TB SSD/LMStudio_models/mlx-community/"
        "Qwen3-4B-Instruct-2507-Huihui-abliterated-MLX-4bit"
    ),
}

# ENV для динамического оверрайда без правки кода. Формат — JSON dict:
#   {"mlx-local-kv4/foo": "/abs/path/to/foo", ...}
_ENV_VAR = "MLX_LOCAL_MODEL_ALIASES_JSON"

# Хост-сигнатуры, по которым считаем, что target — MLX local backend.
# Дефолтно — `mlx_lm.server` слушает 8088. Дополнительные порты можно
# задать через ENV `MLX_LOCAL_PORTS` (CSV, например "8088,8089").
_DEFAULT_MLX_LOCAL_PORTS: tuple[int, ...] = (8088,)


def _load_env_aliases() -> dict[str, str]:
    """Читает override-маппинг из ENV. Возвращает пустой dict при ошибке."""
    raw = (os.getenv(_ENV_VAR) or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError) as exc:
        logger.warning(
            "mlx_local_aliases_env_parse_failed",
            env_var=_ENV_VAR,
            error=str(exc),
        )
        return {}
    if not isinstance(parsed, dict):
        logger.warning(
            "mlx_local_aliases_env_not_dict",
            env_var=_ENV_VAR,
            type=type(parsed).__name__,
        )
        return {}
    # Приводим к str/str — чтобы не словить TypeError при подстановке.
    result: dict[str, str] = {}
    for key, value in parsed.items():
        if isinstance(key, str) and isinstance(value, str) and key and value:
            result[key] = value
    return result


def get_alias_map() -> dict[str, str]:
    """Возвращает effective alias-map: defaults + ENV override (ENV приоритет)."""
    merged = dict(_DEFAULT_ALIASES)
    merged.update(_load_env_aliases())
    return merged


def _mlx_local_ports() -> tuple[int, ...]:
    """Список портов, на которых ожидаем MLX local backend."""
    raw = (os.getenv("MLX_LOCAL_PORTS") or "").strip()
    if not raw:
        return _DEFAULT_MLX_LOCAL_PORTS
    ports: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ports.append(int(chunk))
        except ValueError:
            continue
    return tuple(ports) if ports else _DEFAULT_MLX_LOCAL_PORTS


def is_mlx_local_target(target_url: Optional[str]) -> bool:
    """True если URL указывает на локальный MLX backend (по порту)."""
    if not target_url:
        return False
    try:
        parsed = urlparse(target_url)
    except (ValueError, TypeError):
        return False
    port = parsed.port
    if port is None:
        return False
    return port in _mlx_local_ports()


def resolve_mlx_local_alias(
    model_id: Optional[str],
    *,
    target_url: Optional[str] = None,
    force: bool = False,
) -> Optional[str]:
    """Короткое имя модели → полный путь для MLX local backend.

    Параметры
    ---------
    model_id : короткий id (например, ``mlx-local-kv4/gemma-4-26b``) или
        уже полный путь / любое произвольное значение.
    target_url : URL, куда уходит запрос. Подстановка выполняется только если
        URL похож на MLX local backend (см. ``is_mlx_local_target``). Это
        защищает от случайной подмены имени для cloud / LM Studio.
    force : если True — подставлять без проверки target_url. Полезно в тестах.

    Возвращает либо resolved full path, либо исходный ``model_id`` без изменений
    (если alias не найден). ``None`` если на вход подали ``None``.
    """
    if model_id is None:
        return None
    if not force and not is_mlx_local_target(target_url):
        return model_id
    alias_map = get_alias_map()
    full = alias_map.get(model_id)
    if full:
        logger.debug(
            "mlx_local_alias_applied",
            short=model_id,
            full=full,
        )
        return full
    return model_id


def reverse_mlx_local_alias(full_path: Optional[str]) -> Optional[str]:
    """Полный путь модели → короткое имя (для логов/UI).

    Если короткого имени нет — возвращает исходный full_path.
    """
    if not full_path:
        return full_path
    alias_map = get_alias_map()
    for short, full in alias_map.items():
        if full == full_path:
            return short
    return full_path


__all__ = [
    "get_alias_map",
    "is_mlx_local_target",
    "resolve_mlx_local_alias",
    "reverse_mlx_local_alias",
]
