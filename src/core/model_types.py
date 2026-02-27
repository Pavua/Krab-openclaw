# -*- coding: utf-8 -*-
"""
Типы моделей для роутера (Фаза 4.1).
Вынесены из model_manager для использования в local_health без циклических импортов.
"""
from dataclasses import dataclass
from enum import Enum


class ModelStatus(Enum):
    """Статус модели"""
    AVAILABLE = "available"      # Доступна для загрузки
    LOADED = "loaded"           # Загружена и готова
    LOADING = "loading"         # В процессе загрузки
    UNLOADING = "unloading"     # В процессе выгрузки
    ERROR = "error"             # Ошибка
    UNKNOWN = "unknown"


class ModelType(Enum):
    """Тип модели"""
    LOCAL_MLX = "mlx"
    LOCAL_GGUF = "gguf"
    CLOUD_GEMINI = "gemini"
    CLOUD_OPENROUTER = "openrouter"


@dataclass
class ModelInfo:
    """Информация о модели"""
    id: str
    name: str
    type: ModelType
    status: ModelStatus = ModelStatus.UNKNOWN
    size_gb: float = 8.0  # Default approx size
    context_window: int = 8192
    supports_vision: bool = False

    @property
    def is_local(self) -> bool:
        return self.type in (ModelType.LOCAL_MLX, ModelType.LOCAL_GGUF)
