# -*- coding: utf-8 -*-
"""
Проверка окружения и конфигурации при старте (Фаза 4/6.2 декомпозиция main.py).
"""
from __future__ import annotations

import sys

import structlog

from ..config import config

logger = structlog.get_logger(__name__)


def validate_config() -> bool:
    """
    Проверяет валидность конфигурации.
    Returns True если конфиг валиден, иначе логирует ошибки и возвращает False.
    """
    if config.is_valid():
        return True
    logger.error("config_invalid", errors=config.validate())
    return False
