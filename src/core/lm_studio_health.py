# -*- coding: utf-8 -*-
"""
Реэкспорт LM Studio утилит из local_health (Фаза 4.1).

Оставлен для обратной совместимости. Импортируйте из .core.local_health.
"""
from .local_health import (
    fetch_lm_studio_models_list,
    is_lm_studio_available,
)

__all__ = ["is_lm_studio_available", "fetch_lm_studio_models_list"]
