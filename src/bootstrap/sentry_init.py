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
- LoggingIntegration: level=INFO (breadcrumbs), event_level=ERROR (capture
  logger.error / logger.exception). Drop noise через before_send-фильтр
  (_BENIGN_ERROR_MARKERS).
- FastApiIntegration: 500-events из owner panel.
- AsyncioIntegration: unhandled task crashes.
- HttpxIntegration: breadcrumbs HTTP-вызовов (openclaw_client, gateway).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# Маркеры benign-ошибок, которые НЕ должны попадать в Sentry.
# Все три — transient HTTPException во время Krab boot (15-30s):
#   userbot_not_ready: 503 пока userbot ещё не connected;
#   router_not_configured: 503 пока model_router не успел init;
#   Client has not been started yet: pyrogram client startup race;
# Проявляются если запрос приходит в окно ~5-15s после старта web_app, до
# полной инициализации userbot/router. Не runtime bug — клиент должен retry
# по Retry-After.
_BENIGN_ERROR_MARKERS: tuple[str, ...] = (
    "userbot_not_ready",
    "router_not_configured",
    "Client has not been started yet",
)


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Drop benign events (например userbot_not_ready во время boot).

    Sentry hook: возвращает None → событие не отправляется.
    """
    try:
        # 1. Прямая проверка extra.error_code
        extra = event.get("extra") or {}
        if isinstance(extra, dict):
            error_code = str(extra.get("error_code") or "").strip()
            if error_code in _BENIGN_ERROR_MARKERS:
                return None

        # 2. HTTPException(503, "userbot_not_ready") — detail попадает в exception value
        for ex in (event.get("exception", {}) or {}).get("values", []) or []:
            if not isinstance(ex, dict):
                continue
            value = str(ex.get("value") or "")
            for marker in _BENIGN_ERROR_MARKERS:
                if marker in value:
                    return None

        # 3. logentry / message — на случай если warning попал через logging integration
        message = event.get("message")
        if isinstance(message, str):
            for marker in _BENIGN_ERROR_MARKERS:
                if marker in message:
                    return None
    except Exception:  # noqa: BLE001
        # Никогда не ломаем error reporting из-за бага в фильтре.
        return event
    return event


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

        # Опциональные integrations: если import падает (старая версия sentry-sdk
        # или extras не установлены) — продолжаем с тем что есть, не валим init.
        integrations: list[Any] = [
            LoggingIntegration(
                level=logging.INFO,  # breadcrumbs от INFO+
                event_level=logging.ERROR,  # capture logger.error / logger.exception
            ),
        ]
        try:
            from sentry_sdk.integrations.fastapi import FastApiIntegration

            integrations.append(FastApiIntegration(transaction_style="endpoint"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("sentry_integration_skipped", name="fastapi", error=str(exc))
        try:
            from sentry_sdk.integrations.asyncio import AsyncioIntegration

            integrations.append(AsyncioIntegration())
        except Exception as exc:  # noqa: BLE001
            logger.debug("sentry_integration_skipped", name="asyncio", error=str(exc))
        try:
            from sentry_sdk.integrations.httpx import HttpxIntegration

            integrations.append(HttpxIntegration())
        except Exception as exc:  # noqa: BLE001
            logger.debug("sentry_integration_skipped", name="httpx", error=str(exc))

        # default: production — чтобы prod-state не проваливался в dev-bucket
        env = os.getenv("KRAB_ENV", "production").strip()
        default_sample = 1.0 if env == "dev" else 0.1
        traces_sample_rate = _read_float_env("SENTRY_TRACES_SAMPLE_RATE", default_sample)
        profiles_sample_rate = _read_float_env("SENTRY_PROFILES_SAMPLE_RATE", default_sample)
        release = f"krab@{os.getenv('KRAB_VERSION', 'dev')}"

        sentry_sdk.init(
            dsn=dsn,
            environment=env,
            release=release,
            traces_sample_rate=traces_sample_rate,
            profiles_sample_rate=profiles_sample_rate,
            integrations=integrations,
            # Не шлём PII (Telegram user data может оседать в locals)
            send_default_pii=False,
            # Доп. защита: не слать локальные переменные (могут содержать tokens).
            include_local_variables=False,
            # Drop benign transient ошибки (userbot_not_ready во время boot)
            before_send=_before_send,
        )
        try:
            sentry_sdk.set_tag("agent_kin", "krab")
            sentry_sdk.set_tag("service", "krab-main")
        except Exception as exc:  # noqa: BLE001
            logger.debug("sentry_set_tag_failed", error=str(exc))
        logger.info(
            "sentry_initialized",
            environment=env,
            release=release,
            traces_sample_rate=traces_sample_rate,
            profiles_sample_rate=profiles_sample_rate,
            integrations=[type(i).__name__ for i in integrations],
        )
        return True
    except Exception as exc:  # noqa: BLE001
        # Sentry не должна ронять runtime при сбое инициализации
        logger.warning("sentry_init_failed", error=str(exc))
        return False
