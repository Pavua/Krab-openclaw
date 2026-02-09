"""
Точка входа в приложение Краб
"""
import asyncio
import signal
import sys

import structlog
import logging

from .config import config
from .model_manager import model_manager
from .openclaw_client import openclaw_client
from .config import config
from .model_manager import model_manager
from .openclaw_client import openclaw_client
from .userbot_bridge import KraabUserbot

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = structlog.get_logger()


async def main():
    """Запуск приложения"""
    print(f"""
    🦀 KRAB USERBOT STARTED 🦀
    Owner: {config.OWNER_USERNAME}
    Mode: {config.LOG_LEVEL}
    RAM Limit: {config.MAX_RAM_GB}GB
    """)
    
    # Valdiate Config
    if not config.is_valid():
        logger.error("config_invalid", errors=config.validate())
        sys.exit(1)

    # Health Checks
    lm_health = await model_manager.health_check()
    claw_health = await openclaw_client.health_check()
    
    logger.info("system_check", lm_studio=lm_health, openclaw=claw_health)
    
    if not claw_health:
        logger.warning("openclaw_unreachable", url=config.OPENCLAW_URL)
        # Не выходим, может поднимется позже
        
    # Start Userbot (Lazy Initialization)
    kraab = KraabUserbot()
    try:
        await kraab.start()
        logger.info("kraab_running")
        
        # Ждем сигнала остановки (Ctrl+C вызовет CancelledError)
        stop_event = asyncio.Event()
        await stop_event.wait()
    except asyncio.CancelledError:
        logger.info("stopping_signal_received")
    except Exception as e:
        logger.error("fatal_error", error=str(e))
    finally:
        await kraab.stop()
        logger.info("kraab_stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
