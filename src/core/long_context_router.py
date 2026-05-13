# -*- coding: utf-8 -*-
"""Wave 223: opt-in routing long-context задач на локальный MLX :8088.

ENV-driven; по умолчанию OFF — поведение Krab не меняется без явного opt-in.

Env vars:
- KRAB_LONG_CONTEXT_PROVIDER      — "cloud" (default) | "mlx-local-kv4"
- MLX_LOCAL_KV4_URL               — endpoint URL (default http://127.0.0.1:8088)
- KRAB_LONG_CONTEXT_THRESHOLD_TOKENS — порог токенов (default 8000)
- KRAB_MLX_LOCAL_TASK_TYPES       — comma-sep список task_type
                                    (default "summarization,rag_retrieval")

Идея: long-context tasks и summarization/rag_retrieval — типично дешевле и
быстрее на локальном MLX, чем гонять в облако. Активируется только если
KRAB_LONG_CONTEXT_PROVIDER == "mlx-local-kv4".
"""

from __future__ import annotations

import os

from .metrics.long_context_routing import inc_mlx_local_routing

# Маркеры провайдеров
PROVIDER_CLOUD = "cloud"
PROVIDER_MLX_LOCAL = "mlx-local-kv4"

# Дефолты ENV
_DEFAULT_THRESHOLD_TOKENS = 8000
_DEFAULT_TASK_TYPES = "summarization,rag_retrieval"
_DEFAULT_MLX_URL = "http://127.0.0.1:8088"


def _env_provider() -> str:
    """Чтение KRAB_LONG_CONTEXT_PROVIDER. Default — "cloud"."""
    return (os.getenv("KRAB_LONG_CONTEXT_PROVIDER") or PROVIDER_CLOUD).strip()


def _env_threshold_tokens() -> int:
    """Порог токенов для перехода в local. Best-effort parse, fallback default."""
    raw = (os.getenv("KRAB_LONG_CONTEXT_THRESHOLD_TOKENS") or "").strip()
    if not raw:
        return _DEFAULT_THRESHOLD_TOKENS
    try:
        return int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_THRESHOLD_TOKENS


def _env_task_types() -> set[str]:
    """Множество task_type, для которых надо роутить в local."""
    raw = os.getenv("KRAB_MLX_LOCAL_TASK_TYPES")
    if raw is None:
        raw = _DEFAULT_TASK_TYPES
    return {t.strip() for t in raw.split(",") if t.strip()}


def get_mlx_local_url() -> str:
    """Endpoint локального MLX (override через MLX_LOCAL_KV4_URL)."""
    return (os.getenv("MLX_LOCAL_KV4_URL") or _DEFAULT_MLX_URL).strip()


def select_provider_for_task(task_type: str, prompt_tokens: int) -> str:
    """Wave 223: возвращает имя провайдера для задачи.

    Args:
        task_type: тип задачи (например, "summarization", "rag_retrieval",
                   "chat", и т.д.)
        prompt_tokens: оценка токенов в запросе.

    Returns:
        "mlx-local-kv4" если активирован opt-in и сработало одно из правил,
        иначе "cloud".

    Поведение по умолчанию (env vars не выставлены) — всегда "cloud".
    """
    # Если opt-in не включён — сразу cloud, метрику считаем как fallback.
    if _env_provider() != PROVIDER_MLX_LOCAL:
        inc_mlx_local_routing(reason="fallback")
        return PROVIDER_CLOUD

    # Правило 1: длинный контекст → local.
    threshold = _env_threshold_tokens()
    try:
        tokens_i = int(prompt_tokens)
    except (TypeError, ValueError):
        tokens_i = 0
    if tokens_i > threshold:
        inc_mlx_local_routing(reason="long_context")
        return PROVIDER_MLX_LOCAL

    # Правило 2: task_type в whitelist → local.
    allowed = _env_task_types()
    if task_type and str(task_type).strip() in allowed:
        inc_mlx_local_routing(reason="task_type")
        return PROVIDER_MLX_LOCAL

    # Иначе — остаёмся в cloud.
    inc_mlx_local_routing(reason="fallback")
    return PROVIDER_CLOUD


__all__ = [
    "PROVIDER_CLOUD",
    "PROVIDER_MLX_LOCAL",
    "get_mlx_local_url",
    "select_provider_for_task",
]
