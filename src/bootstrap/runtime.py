# -*- coding: utf-8 -*-
"""
Жизненный цикл приложения: health checks, старт/остановка userbot + web panel (Фаза 4/6.2).
"""
from __future__ import annotations

import asyncio
import os
import signal

import structlog

from ..config import config
from ..model_manager import model_manager
from ..openclaw_client import openclaw_client
from ..userbot_bridge import KraabUserbot

logger = structlog.get_logger(__name__)


async def _start_web_panel() -> object | None:
    """Starts the web panel on WEB_PORT (default 8080). Returns the WebApp instance or None."""
    try:
        from ..modules.web_app import WebApp
        from ..modules.web_router_compat import WebRouterCompat
        from ..core.ecosystem_health import EcosystemHealthService
        from ..core.provisioning_service import ProvisioningService

        router_compat = WebRouterCompat(model_manager, openclaw_client)

        deps = {
            "router": router_compat,
            "openclaw_client": openclaw_client,
            "black_box": None,
            "health_service": EcosystemHealthService(
                router=router_compat,
                openclaw_client=openclaw_client,
            ),
            "provisioning_service": ProvisioningService(),
            "ai_runtime": None,
            "reaction_engine": None,
            "voice_gateway_client": None,
            "krab_ear_client": None,
            "perceptor": None,
            "watchdog": None,
            "queue": None,
        }

        port = int(os.getenv("WEB_PORT", "8080"))
        host = os.getenv("WEB_HOST", "127.0.0.1")
        web = WebApp(deps, port=port, host=host)
        await web.start()
        logger.info("web_panel_started", url=f"http://{host}:{port}")
        return web
    except Exception as e:
        logger.warning("web_panel_start_failed", error=str(e))
        return None


async def run_app() -> None:
    """
    Запускает приложение: баннер, проверки здоровья, web panel, userbot start → wait → stop.
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

    web_panel = await _start_web_panel()

    kraab = KraabUserbot()
    stop_event = asyncio.Event()

    def _request_stop(reason: str) -> None:
        """Запрашивает штатную остановку приложения без форс-килла."""
        if not stop_event.is_set():
            logger.info("stop_requested", reason=reason)
            stop_event.set()

    loop = asyncio.get_running_loop()
    for sig, reason in ((signal.SIGTERM, "sigterm"), (signal.SIGINT, "sigint")):
        try:
            loop.add_signal_handler(sig, lambda r=reason: _request_stop(r))
        except NotImplementedError:
            # На некоторых окружениях add_signal_handler недоступен (например, ограниченный runtime).
            pass

    try:
        await kraab.start()
        logger.info("kraab_running")
        await stop_event.wait()
    except asyncio.CancelledError:
        logger.info("stopping_signal_received")
    except Exception as e:
        logger.error("fatal_error", error=str(e))
    finally:
        await kraab.stop()
        logger.info("kraab_stopped")
