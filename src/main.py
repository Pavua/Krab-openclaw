"""
Точка входа в приложение Краб (Фаза 4/6.2: декомпозиция на bootstrap).
"""
import asyncio
import logging
import sys

import structlog

from .bootstrap import validate_config, run_app

logging.basicConfig(level=logging.INFO)


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
