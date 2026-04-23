# -*- coding: utf-8 -*-
"""
Sentry инициализация — вызывается один раз при старте runtime.

Правила:
- SENTRY_DSN пустой или отсутствует → Sentry не поднимается (no-op);
- KRAB_ENV управляет тегом environment (default: production);
- traces_sample_rate=0.1 в production (10%), 1.0 в dev;
- LoggingIntegration уже ловит ERROR+ через structlog → не дублируем.
"""

from __future__ import annotations

import os

import structlog

logger = structlog.get_logger(__name__)


def init_sentry() -> bool:
    """
    Инициализирует Sentry SDK если SENTRY_DSN задан.
    Возвращает True если SDK поднят, False если пропущен.
    """
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        logger.debug("sentry_skipped", reason="SENTRY_DSN not set")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration

        # default: production — чтобы prod-state не проваливался в dev-bucket
        env = os.getenv("KRAB_ENV", "production").strip()
        sample_rate = 1.0 if env == "dev" else 0.1

        sentry_sdk.init(
            dsn=dsn,
            environment=env,
            traces_sample_rate=sample_rate,
            # LoggingIntegration перехватывает ERROR+ автоматически через stdlib logging.
            # structlog пишет в stdlib logging handler, значит ERROR-записи дойдут.
            integrations=[
                LoggingIntegration(
                    level=None,  # отключаем breadcrumb на INFO (шумно)
                    event_level=None,  # отключаем auto-event — управляем вручную
                ),
            ],
            # Не шлём PII (Telegram user data может оседать в locals)
            send_default_pii=False,
        )
        logger.info("sentry_initialized", environment=env, traces_sample_rate=sample_rate)
        return True
    except Exception as exc:  # noqa: BLE001
        # Sentry не должна ронять runtime при сбое инициализации
        logger.warning("sentry_init_failed", error=str(exc))
        return False
