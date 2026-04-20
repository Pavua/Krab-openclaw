"""
Точка входа в приложение Краб (Фаза 4/6.2: декомпозиция на bootstrap).
"""

import asyncio
import sys

from src.core.logger import get_logger, setup_logger

from .bootstrap import run_app, validate_config

setup_logger(level="INFO")
logger = get_logger(__name__)


# P1 C (stability): Pyrofork session может упасть по network drop
# (CancelledError, ConnectionError, OSError, TimeoutError на sleep/wake/VPN reconnect).
# До этого retry-loop'а процесс просто умирал и внешний bash watchdog поднимал
# его заново, теряя runtime state и open connections. Теперь reconnect in-process
# с exponential backoff (5s → 10 → 20 → 40 → 80 → 160 → 300 cap).
# SIGTERM/SIGINT — единственный clean-exit путь (run_app ловит stop_event).
_RETRY_BACKOFF_INITIAL_SEC = 5.0
_RETRY_BACKOFF_CAP_SEC = 300.0


async def _run_with_retry() -> None:
    """Перезапускает run_app при network-level сбоях, не трогая clean-shutdown путь."""
    backoff_sec = _RETRY_BACKOFF_INITIAL_SEC
    while True:
        try:
            await run_app()
        except (
            asyncio.CancelledError,
            ConnectionError,
            OSError,
            TimeoutError,
            asyncio.TimeoutError,
        ) as exc:
            logger.warning(
                "run_app_retrying_after_drop",
                error=str(exc),
                error_type=type(exc).__name__,
                backoff_sec=backoff_sec,
            )
            try:
                await asyncio.sleep(backoff_sec)
            except asyncio.CancelledError:
                # Второй cancel во время backoff — явный shutdown, выходим.
                logger.info("retry_backoff_cancelled_shutting_down")
                return
            backoff_sec = min(backoff_sec * 2, _RETRY_BACKOFF_CAP_SEC)
            continue
        # Чистый возврат run_app == shutdown по SIGTERM/SIGINT/stop_event.
        logger.info("run_app_clean_exit")
        return


async def main() -> None:
    """Запуск приложения: валидация конфига → Sentry init → retry-обёрнутый runtime."""
    if not validate_config():
        sys.exit(1)
    # Sentry подключается ДО run_app чтобы ловить ошибки bootstrap.
    # Если DSN не задан — безопасный skip, runtime продолжает работу.
    from src.core.sentry_integration import init_sentry

    init_sentry()
    await _run_with_retry()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
