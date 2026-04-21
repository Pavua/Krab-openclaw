# -*- coding: utf-8 -*-
"""
Вспомогательные утилиты для auto-retry LLM-запросов при инфраструктурных ошибках.

Логика retry применяется только к временным сбоям (quota, timeout, 5xx).
При ошибках пользователя (safety block, неверный ввод) — retry не нужен.
"""

from __future__ import annotations


class LLMRetryableError(Exception):
    """
    Поднимается из _run_llm_request_flow при обнаружении retryable ошибки.

    Позволяет обёртке (_finish_ai_request_background) отловить сигнал о
    необходимости повтора без изменения сигнатуры основного flow.
    """

    def __init__(self, message: str, error_text: str) -> None:
        super().__init__(message)
        # Текст ошибки как он был в full_response (для финального уведомления)
        self.error_text = error_text


# Паттерны в тексте ответа, которые сигнализируют о retryable-ошибке.
# Это случаи когда OpenClaw вернул 200 с текстом ошибки внутри (semantic error).
_RETRYABLE_TEXT_PATTERNS: tuple[str, ...] = (
    # Таймаут провайдера
    "provider_timeout",
    "timeout",
    # Квота (временный сбой, может быть разный формат)
    "quota_exceeded",
    "quota",
    # 5xx ошибки
    "gateway_unknown_error",
    "an unknown error occurred",
    # LM Studio специфичные (временные сбои загрузки)
    "model_not_loaded",
    "no models loaded",
    "model unloaded",
    "lm_model_crash",
    "model has crashed",
    # Пустой ответ — часто временный сбой
    "lm_empty_stream",
    "<empty message>",
)

# Коды semantic-ошибок из OpenClaw, которые считаются retryable.
RETRYABLE_ERROR_CODES: frozenset[str] = frozenset(
    {
        "provider_timeout",
        "quota_exceeded",
        "gateway_unknown_error",
        "model_not_loaded",
        "lm_model_crash",
        "lm_empty_stream",
        "lm_malformed_response",
        "transport_error",
    }
)

# Коды ошибок, при которых retry НЕ нужен (user error или permanent).
NON_RETRYABLE_ERROR_CODES: frozenset[str] = frozenset(
    {
        "auth_unauthorized",  # неверный API ключ — повтор не поможет
        "unsupported_key_type",  # структурная проблема конфигурации
        "safety_block",  # safety filter — не ретраить контент
        "vision_addon_missing",  # конфигурационная проблема
    }
)


def is_retryable_error_text(text: str) -> bool:
    """
    Проверяет, является ли текст ответа retryable инфраструктурной ошибкой.

    Возвращает True только при временных сбоях:
    - таймаут провайдера
    - квота исчерпана (временно)
    - 5xx / gateway ошибки
    - пустой ответ от LM Studio

    Возвращает False при ошибках пользователя или конфигурации.
    """
    if not text:
        return False
    low = text.lower()
    # Явно non-retryable — проверяем первыми
    if "safety" in low and "block" in low:
        return False
    if "invalid api key" in low or "unauthenticated" in low or "forbidden" in low:
        return False
    if "unauthorized" in low and "401" in low:
        return False
    if "vision add-on is not loaded" in low or "missing image config" in low:
        return False
    # Retryable patterns
    for pattern in _RETRYABLE_TEXT_PATTERNS:
        if pattern in low:
            return True
    return False


def is_retryable_exception(exc: Exception) -> bool:
    """
    Проверяет, является ли исключение retryable инфраструктурной ошибкой.

    Использует атрибут `retryable` если он есть (ProviderError, RouterError).
    Для asyncio.TimeoutError — всегда retryable.
    Для httpx transport errors — retryable.
    """
    import asyncio

    if isinstance(exc, asyncio.TimeoutError):
        return True

    # Проверяем атрибут .retryable (ProviderError, RouterError, KrabError)
    retryable = getattr(exc, "retryable", None)
    if retryable is not None:
        return bool(retryable)

    # httpx transport errors — временные сетевые сбои
    try:
        import httpx

        if isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            # 5xx retryable, 4xx — нет (кроме 429 quota)
            status = exc.response.status_code
            return status >= 500 or status == 429
    except ImportError:
        pass

    return False


def build_retry_notice(attempt: int, max_retries: int, delay_sec: float) -> str:
    """Формирует текст уведомления о повторной попытке."""
    return (
        f"🔄 Повторная попытка ({attempt}/{max_retries})... "
        f"Жду {int(delay_sec)} сек перед следующей попыткой."
    )


def build_final_error_notice(
    original_error: str,
    attempts_made: int,
    max_retries: int,
) -> str:
    """Формирует финальное сообщение об ошибке после исчерпания всех попыток."""
    tail = original_error.strip()
    if attempts_made > 0:
        return f"❌ Все попытки исчерпаны ({attempts_made}/{max_retries}).\n{tail}"
    return tail
