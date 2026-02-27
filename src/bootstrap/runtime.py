# -*- coding: utf-8 -*-
"""
Жизненный цикл приложения: health checks, старт/остановка userbot (Фаза 4/6.2).
"""
from __future__ import annotations

import asyncio

import structlog

from ..config import config
from ..model_manager import model_manager
from ..openclaw_client import openclaw_client
from ..userbot_bridge import KraabUserbot

logger = structlog.get_logger(__name__)


async def run_app() -> None:
    """
    Запускает приложение: баннер, проверки здоровья, userbot start → wait → stop.
    Вызывать после validate_config().
    """
    print(f"""
    🦀 KRAB USERBOT STARTED 🦀
    Owner: {config.OWNER_USERNAME}
    Mode: {config.LOG_LEVEL}
    RAM Limit: {config.MAX_RAM_GB}GB
    """)

    lm_health = await model_manager.health_check()
    claw_health = await openclaw_client.health_check()
    logger.info("system_check", lm_studio=lm_health, openclaw=claw_health)

    if not claw_health:
        logger.warning("openclaw_unreachable", url=config.OPENCLAW_URL)

    kraab = KraabUserbot()
    try:
        await kraab.start()
        logger.info("kraab_running")
        stop_event = asyncio.Event()
        await stop_event.wait()
    except asyncio.CancelledError:
        logger.info("stopping_signal_received")
    except Exception as e:
        logger.error("fatal_error", error=str(e))
    finally:
        await kraab.stop()
        logger.info("kraab_stopped")
