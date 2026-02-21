# -*- coding: utf-8 -*-
"""
Logging 2.0 для Krab v2.5.
Использует structlog для структурированного логирования (JSON/Console) и RotatingFileHandler для ротации.
"""

import os
import sys
import logging
import logging.handlers
import structlog
from datetime import datetime

LOGS_DIR = "logs"
MAIN_LOG = os.path.join(LOGS_DIR, "krab.log")
ERROR_LOG = os.path.join(LOGS_DIR, "errors.log")
AI_LOG = os.path.join(LOGS_DIR, "ai_decisions.log")
JSON_LOG = os.path.join(LOGS_DIR, "krab.json.log")

def setup_logger(debug=False):
    """Настройка структурированного логирования."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    
    # Стилизация для консоли
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # Console output (красивый текстовый формат)
    console_processors = processors + [
        structlog.dev.ConsoleRenderer()
    ]

    # JSON output (для парсинга машинами)
    json_processors = processors + [
        structlog.processors.JSONRenderer()
    ]

    # Стандартные хэндлеры для ротации
    max_bytes = 50 * 1024 * 1024 # 50 MB (согласно Phase 5.2)
    backup_count = 7

    # Основной лог
    main_handler = logging.handlers.RotatingFileHandler(
        MAIN_LOG, maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8'
    )
    
    # Лог ошибок
    error_handler = logging.handlers.RotatingFileHandler(
        ERROR_LOG, maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    
    # Лог решений AI
    ai_handler = logging.handlers.RotatingFileHandler(
        AI_LOG, maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8'
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.DEBUG if debug else logging.INFO,
    )

    structlog.configure(
        processors=console_processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    root_logger = logging.getLogger()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    for h in [main_handler, error_handler, ai_handler]:
        h.setFormatter(formatter)
        root_logger.addHandler(h)

    logger = structlog.get_logger("Krab")
    logger.info("🚀 Logging 2.0 (Phase 5) Initialized", logs_dir=LOGS_DIR, files=["krab.log", "errors.log", "ai_decisions.log"])
    return logger

def get_last_logs(lines=20):
    """Возвращает последние N строк из лог-файла."""
    if not os.path.exists(MAIN_LOG):
        return "Лог-файл не найден."
    
    try:
        with open(MAIN_LOG, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()
            return "".join(all_lines[-lines:])
    except Exception as e:
        return f"Ошибка при чтении логов: {e}"
