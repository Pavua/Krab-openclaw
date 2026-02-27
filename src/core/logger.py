"""
Единая настройка логирования через structlog.

Использование:
    from src.core.logger import setup_logger, get_logger

    setup_logger(level="INFO")
    logger = get_logger(__name__)
    logger.info("message", key="value")
"""

import logging
import structlog
from structlog.processors import CallsiteParameter, CallsiteParameterAdder


def setup_logger(level: str = "INFO") -> None:
    """
    Настраивает structlog для консольного вывода с таймстемпом,
    уровнем лога и именем модуля.

    Args:
        level: Минимальный уровень логирования ("DEBUG", "INFO", "WARNING", "ERROR").
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    structlog.configure(
        processors=[
            structlog.processors.add_log_level(),
            CallsiteParameterAdder([CallsiteParameter.MODULE]),
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M.%S", utc=False),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    structlog.get_logger(__name__).info("logger_configured", message="structlog setup complete")


def get_logger(name: str | None = None):
    """Возвращает логгер с заданным именем (обычно __name__)."""
    return structlog.get_logger(name)
