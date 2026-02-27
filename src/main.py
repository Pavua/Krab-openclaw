"""
Точка входа в приложение Краб (Фаза 4/6.2: декомпозиция на bootstrap).
"""

import asyncio
import sys

from src.core.logger import get_logger, setup_logger
from .bootstrap import validate_config, run_app

setup_logger(level="INFO")
logger = get_logger(__name__)


async def main() -> None:
    """Запуск приложения: валидация конфига → runtime."""
    if not validate_config():
        sys.exit(1)
    await run_app()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
