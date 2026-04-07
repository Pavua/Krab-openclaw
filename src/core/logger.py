"""
Единая настройка логирования через structlog.

Использование:
    from src.core.logger import setup_logger, get_logger

    setup_logger(level="INFO")
    logger = get_logger(__name__)
    logger.info("message", key="value")
"""

import logging
import os
import sys
from pathlib import Path

import structlog
from structlog.processors import CallsiteParameter, CallsiteParameterAdder


def _resolve_log_file() -> Path | None:
    """
    Определяет путь к файлу лога runtime.

    По умолчанию — ~/.openclaw/krab_runtime_state/krab_main.log.
    Можно переопределить через KRAB_LOG_FILE=/path/to/log или отключить
    вообще через KRAB_LOG_FILE="" / KRAB_LOG_FILE=none.
    """
    raw = os.environ.get("KRAB_LOG_FILE")
    if raw is not None:
        if raw == "" or raw.lower() == "none":
            return None
        return Path(raw).expanduser()

    base = os.environ.get("KRAB_RUNTIME_STATE_DIR")
    base_dir = Path(base).expanduser() if base else Path.home() / ".openclaw" / "krab_runtime_state"
    return base_dir / "krab_main.log"


def setup_logger(level: str = "INFO") -> None:
    """
    Настраивает structlog для параллельного вывода в stdout и runtime log file.

    Args:
        level: Минимальный уровень логирования ("DEBUG", "INFO", "WARNING", "ERROR").
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # File logger: writes structured log lines into krab_main.log.
    # stdlib logging передаёт событие structlog как final string, поэтому достаточно
    # простого StreamHandler → FileHandler без дополнительных форматтеров.
    log_file = _resolve_log_file()
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
            handlers.append(file_handler)
        except OSError as exc:  # pragma: no cover - не валим runtime из-за ФС
            print(f"[logger] file handler disabled: {exc}", file=sys.stderr)
            log_file = None

    # Сбрасываем root handlers, чтобы не плодить дубли при повторных setup.
    root = logging.getLogger()
    root.handlers.clear()
    for h in handlers:
        h.setLevel(log_level)
        root.addHandler(h)
    root.setLevel(log_level)

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            CallsiteParameterAdder([CallsiteParameter.MODULE]),
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    structlog.get_logger(__name__).info(
        "logger_configured",
        message="structlog setup complete",
        log_file=str(log_file) if log_file else None,
    )


def get_logger(name: str | None = None):
    """Возвращает логгер с заданным именем (обычно __name__)."""
    return structlog.get_logger(name)
