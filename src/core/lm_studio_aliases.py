# -*- coding: utf-8 -*-
"""Wave 239: Alias-резолвер для prefix ``lm-studio-local/``.

Проблема
--------
В `/admin/models` picker модели из LM Studio показываются с prefix
``lm-studio-local/<short_name>`` (Wave 239). Это удобно для группировки и
лёгкого `_resolve_active_provider`. Но сам LM Studio /v1/chat/completions
ожидает в поле ``model=`` чистый ``<short_name>`` без prefix.

Решение
-------
Тонкий one-way alias: при отправке запроса в LM Studio prefix снимается.
В отличие от ``mlx_local_aliases`` (полные пути на файловой системе) —
здесь просто префикс-stripping. Двунаправленность не нужна: short_name
== label, в логах показываем то же что и в picker.

Контракт
--------
- ``is_lm_studio_local_model_id(model_id)`` — True если начинается с
  ``lm-studio-local/``.
- ``strip_lm_studio_local_prefix(model_id)`` — возвращает часть после
  слэша. Если префикса нет — возвращает исходное значение без изменений.
"""

from __future__ import annotations

from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

_PREFIX = "lm-studio-local/"


def is_lm_studio_local_model_id(model_id: Optional[str]) -> bool:
    """True если model_id с prefix ``lm-studio-local/`` (Wave 239)."""
    if not model_id:
        return False
    return str(model_id).startswith(_PREFIX)


def strip_lm_studio_local_prefix(model_id: Optional[str]) -> Optional[str]:
    """Сносим prefix ``lm-studio-local/``. Без префикса — возвращаем как есть.

    None → None (для удобства chain'инга с другими резолверами).
    """
    if model_id is None:
        return None
    raw = str(model_id)
    if raw.startswith(_PREFIX):
        stripped = raw[len(_PREFIX) :]
        logger.debug(
            "lm_studio_local_alias_stripped",
            original=raw,
            resolved=stripped,
        )
        return stripped
    return raw


__all__ = [
    "is_lm_studio_local_model_id",
    "strip_lm_studio_local_prefix",
]
