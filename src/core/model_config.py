# -*- coding: utf-8 -*-
"""
Конфигурация моделей: пулы fallback, лимиты контекста, таймауты и константы (Фаза 4.1, Шаг 5).

Все хардкод-словари, конфигурации пулов и лимиты контекстных окон вынесены сюда
из model_manager и связанных модулей.
"""
from __future__ import annotations

# --- Цепочка fallback (локальная часть до облачных тиров) ---
# Первые элементы: "local" (псевдоним LM Studio), затем конкретная локальная модель по умолчанию
FALLBACK_CHAIN_LOCAL: list[str] = [
    "local",
    "nvidia/nemotron-3-nano",
]

# --- Лимиты контекстных окон ---
# Дефолтное окно для ModelInfo и расчётов (токены)
DEFAULT_CONTEXT_WINDOW: int = 8192

# --- RAM и размеры моделей ---
# Буфер (GB), добавляемый к размеру модели при проверке can_load_model
RAM_BUFFER_GB: float = 2.0
# Размер (GB) по умолчанию для неизвестной модели при загрузке
DEFAULT_UNKNOWN_MODEL_SIZE_GB: float = 8.0

# --- LM Studio: загрузка/выгрузка ---
# TTL при load: -1 = без авто-выгрузки со стороны LM Studio
LM_LOAD_TTL: int = -1
# Таймаут (сек) запроса на загрузку модели
LM_LOAD_TIMEOUT_SEC: float = 600.0

# --- Maintenance loop (авто-выгрузка простаивающих моделей) ---
# Интервал проверки (сек)
MAINTENANCE_INTERVAL_SEC: int = 300
# Выгружать модель, если простой больше (сек)
IDLE_UNLOAD_SEC: int = 900
