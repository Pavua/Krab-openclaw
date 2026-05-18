# -*- coding: utf-8 -*-
"""Per-task-type local share policy — Phase 4 prep (S66 W4).

Реализует env-инфраструктуру для гранулярного контроля доли local LLM
по типу задачи. Сейчас — observability + admin only: routing решения
будут добавлены в Phase 4 (см. docs/PHASE_4_CONFIDENCE_GATED_ROUTING.md).

Env-переменные:
    KRAB_LOCAL_SHARE_TRANSLATION    — default 0.0
    KRAB_LOCAL_SHARE_QA             — default 0.0
    KRAB_LOCAL_SHARE_SUMMARIZATION  — default 0.0
    KRAB_LOCAL_SHARE_CHAT           — default 0.0
    KRAB_LOCAL_SHARE_CODE           — default 0.0

Значения нормализуются в [0.0, 1.0]; некорректные/нечисловые → 0.0.
Это conservative-дефолт: cloud-only пока Phase 4 не включит routing.
"""

from __future__ import annotations

import math
import os

from src.core.logger import get_logger

_logger = get_logger(__name__)

# Pure list — single source of truth для admin + tests.
TASK_TYPES: tuple[str, ...] = (
    "translation",
    "qa",
    "summarization",
    "chat",
    "code",
)

_ENV_PREFIX = "KRAB_LOCAL_SHARE_"


def _env_var_for(task_type: str) -> str:
    """Имя env-переменной для task_type (uppercase)."""
    return f"{_ENV_PREFIX}{task_type.upper()}"


def _clamp(value: float) -> float:
    """Зажимает float в [0.0, 1.0]."""
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def get_local_share_for_task(task_type: str) -> float:
    """Читает env KRAB_LOCAL_SHARE_<TASK_TYPE>, возвращает [0.0, 1.0].

    - Unknown task_type → 0.0 (cloud-only).
    - Env unset / пустой / нечисловой → 0.0.
    - Numeric value clamped в [0.0, 1.0].
    """
    normalized = (task_type or "").strip().lower()
    if normalized not in TASK_TYPES:
        return 0.0

    raw = os.environ.get(_env_var_for(normalized))
    if raw is None or raw == "":
        return 0.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        _logger.debug(
            "local_share_policy.invalid_value",
            task_type=normalized,
            raw=raw,
        )
        return 0.0
    if math.isnan(value) or math.isinf(value):
        return 0.0
    return _clamp(value)


def get_all_local_share_envs() -> dict[str, float]:
    """Возвращает mapping всех известных task_type → текущая local share.

    Используется admin-панелью для отображения сразу всех значений.
    """
    return {task: get_local_share_for_task(task) for task in TASK_TYPES}
