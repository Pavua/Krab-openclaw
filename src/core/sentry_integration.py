"""
Sentry runtime error tracking для Krab.

Session 16: подключение к Sentry проекту po-zm/krab через DSN в .env.
Фильтрует PII (токены, ключи, phone numbers) перед отправкой events.
После init — captured exceptions автоматически летят в Sentry; MCP Seer может
их анализировать: `mcp__sentry__analyze_issue_with_seer`.

Design decisions:
- Lazy init: только если SENTRY_DSN не пустой (dev может работать без трекинга).
- Sample rates по умолчанию 10% для traces/profiles — минимальный impact на CPU.
- `before_send` redact — защита от утечки secrets через stack traces.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from src.config import config
from src.core.logger import get_logger

logger = get_logger(__name__)

# Паттерны для редактирования в stack traces / breadcrumbs / extra data.
# Порядок важен: специфичные паттерны раньше общих.
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Bot API / Telegram токены: цифры:буквы длиной 30+
    (re.compile(r"\b\d{9,11}:[A-Za-z0-9_-]{30,}\b"), "<TG_BOT_TOKEN>"),
    # Google API ключи: AIza… (obычно 35-39 chars, но используем жадный до границы)
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"), "<GOOGLE_API_KEY>"),
    # OpenAI / Anthropic / etc ключи: sk-…
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "<API_KEY>"),
    # Sentry DSN (может быть hex 32 или альфанумерик)
    (re.compile(r"https://[a-zA-Z0-9]{16,}@[a-zA-Z0-9]+\.ingest\.[a-z.]+/\d+"), "<SENTRY_DSN>"),
    # Phone numbers (E.164 с + и без): +380 93 264 99 97 и т.п.
    (re.compile(r"\+?\d{1,3}[\s\-]?\d{2,4}[\s\-]?\d{2,4}[\s\-]?\d{2,4}[\s\-]?\d{0,4}"), "<PHONE>"),
    # Bearer / OAuth tokens в headers
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9_.\-=]{20,}"), "Bearer <TOKEN>"),
]


def _redact_string(s: str) -> str:
    """Применяет все PII-паттерны к строке."""
    for pat, repl in _PII_PATTERNS:
        s = pat.sub(repl, s)
    return s


def _redact_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Рекурсивный redact values в dict. Keys не трогаем (это структура)."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, str):
            out[k] = _redact_string(v)
        elif isinstance(v, dict):
            out[k] = _redact_dict(v)
        elif isinstance(v, list):
            out[k] = [_redact_string(x) if isinstance(x, str) else x for x in v]
        else:
            out[k] = v
    return out


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """
    Sentry hook: редактирует PII в event перед отправкой.

    Может вернуть None чтобы drop event целиком (если слишком опасный).
    """
    try:
        # Redact message
        if "message" in event and isinstance(event["message"], str):
            event["message"] = _redact_string(event["message"])

        # Redact logentry.message / params
        logentry = event.get("logentry") or {}
        if isinstance(logentry, dict) and isinstance(logentry.get("message"), str):
            logentry["message"] = _redact_string(logentry["message"])

        # Redact exception values
        for ex in (event.get("exception", {}) or {}).get("values", []) or []:
            if isinstance(ex, dict) and isinstance(ex.get("value"), str):
                ex["value"] = _redact_string(ex["value"])

        # Redact breadcrumbs messages
        for crumb in (event.get("breadcrumbs", {}) or {}).get("values", []) or []:
            if isinstance(crumb, dict):
                if isinstance(crumb.get("message"), str):
                    crumb["message"] = _redact_string(crumb["message"])
                if isinstance(crumb.get("data"), dict):
                    crumb["data"] = _redact_dict(crumb["data"])

        # Redact extra context
        if isinstance(event.get("extra"), dict):
            event["extra"] = _redact_dict(event["extra"])

        # Redact tags (usually safe but just in case)
        if isinstance(event.get("tags"), dict):
            event["tags"] = _redact_dict(event["tags"])
    except Exception as exc:  # noqa: BLE001
        # Никогда не ломаем error reporting из-за бага в redact.
        logger.warning("sentry_redact_failed error=%s", exc)

    return event


def init_sentry() -> bool:
    """
    Инициализирует Sentry SDK. Возвращает True если успешно, False если skip.

    Вызывается один раз на старте Krab (в bootstrap/main).
    """
    if not config.SENTRY_DSN:
        logger.info("sentry_skipped reason=no_dsn")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.asyncio import AsyncioIntegration
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError as exc:
        logger.warning("sentry_import_failed error=%s", exc)
        return False

    try:
        sentry_sdk.init(
            dsn=config.SENTRY_DSN,
            environment=config.KRAB_ENV,
            release=f"krab@{os.getenv('KRAB_VERSION', 'dev')}",
            traces_sample_rate=config.SENTRY_TRACES_SAMPLE_RATE,
            profiles_sample_rate=config.SENTRY_PROFILES_SAMPLE_RATE,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                AsyncioIntegration(),
                LoggingIntegration(
                    level=logging.INFO,
                    event_level=logging.ERROR,
                ),
            ],
            before_send=_before_send,
            # Дополнительная защита: не слать локальные переменные (могут содержать tokens).
            include_local_variables=False,
            send_default_pii=False,
        )
        logger.info(
            "sentry_initialized",
            environment=config.KRAB_ENV,
            traces_rate=config.SENTRY_TRACES_SAMPLE_RATE,
            profiles_rate=config.SENTRY_PROFILES_SAMPLE_RATE,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("sentry_init_failed error=%s", exc, exc_info=True)
        return False


def capture_exception(exc: Exception, **extras: Any) -> None:
    """Wrapper для ручного capture с extras. Безопасен если Sentry не init."""
    try:
        import sentry_sdk

        with sentry_sdk.new_scope() as scope:
            for k, v in extras.items():
                scope.set_extra(k, v)
            sentry_sdk.capture_exception(exc)
    except Exception:  # noqa: BLE001
        pass  # Silent fallback


def capture_message(msg: str, level: str = "info", **extras: Any) -> None:
    """Wrapper для ручного capture message."""
    try:
        import sentry_sdk

        with sentry_sdk.new_scope() as scope:
            for k, v in extras.items():
                scope.set_extra(k, v)
            sentry_sdk.capture_message(msg, level=level)
    except Exception:  # noqa: BLE001
        pass
