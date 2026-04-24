# -*- coding: utf-8 -*-
"""
Sentry инициализация — вызывается один раз при старте runtime.

Правила:
- SENTRY_DSN пустой или отсутствует → Sentry не поднимается (no-op);
- KRAB_ENV управляет тегом environment (default: production);
- traces_sample_rate=0.1 в production (10%), 1.0 в dev; override через
  SENTRY_TRACES_SAMPLE_RATE (float 0.0..1.0);
- profiles_sample_rate=0.1 в production (10%); override через
  SENTRY_PROFILES_SAMPLE_RATE — включает Performance Monitoring
  (traces + profiles) для latency tracking (memory retrieval, LLM calls);
- LoggingIntegration уже ловит ERROR+ через structlog → не дублируем.
"""

from __future__ import annotations

import os

import structlog

logger = structlog.get_logger(__name__)


def _read_float_env(name: str, default: float) -> float:
    """Читает float из env с safe-clamp в [0.0, 1.0]. Invalid → default."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


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
        default_sample = 1.0 if env == "dev" else 0.1
        traces_sample_rate = _read_float_env("SENTRY_TRACES_SAMPLE_RATE", default_sample)
        profiles_sample_rate = _read_float_env("SENTRY_PROFILES_SAMPLE_RATE", default_sample)

        sentry_sdk.init(
            dsn=dsn,
            environment=env,
            traces_sample_rate=traces_sample_rate,
            profiles_sample_rate=profiles_sample_rate,
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
        logger.info(
            "sentry_initialized",
            environment=env,
            traces_sample_rate=traces_sample_rate,
            profiles_sample_rate=profiles_sample_rate,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        # Sentry не должна ронять runtime при сбое инициализации
        logger.warning("sentry_init_failed", error=str(exc))
        return False
