# -*- coding: utf-8 -*-
"""
Единый модуль ошибок роутинга (Фаза 2.1).

Таксономия: auth, quota, model_not_loaded, network, timeout.
Используется Telegram- и Web-поверхностями для единообразных сообщений и fail-fast
для некорректируемых ошибок (auth, quota) — без общих retry.
"""

from dataclasses import dataclass
from typing import Any, Optional


# Коды ошибок для унификации диагностики (commands + web API)
CODE_AUTH = "auth"
CODE_QUOTA = "quota"
CODE_MODEL_NOT_LOADED = "model_not_loaded"
CODE_NETWORK = "network"
CODE_TIMEOUT = "timeout"
CODE_UNKNOWN = "unknown"


@dataclass
class RouterError(Exception):
    """Базовое исключение ошибки роутинга."""

    code: str
    user_message: str
    retryable: bool = False
    details: Optional[dict[str, Any]] = None

    def __post_init__(self) -> None:
        if self.details is None:
            self.details = {}

    def __str__(self) -> str:
        return self.user_message


class RouterAuthError(RouterError):
    """Ошибка аутентификации (API key неверный/отозван). Fail-fast, не повторять."""

    def __init__(
        self,
        user_message: str = "Ошибка доступа: неверный или отсутствующий API-ключ.",
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            code=CODE_AUTH,
            user_message=user_message,
            retryable=False,
            details=details or {},
        )


class RouterQuotaError(RouterError):
    """Исчерпана квота/лимит. Fail-fast, не повторять."""

    def __init__(
        self,
        user_message: str = "Квота исчерпана. Попробуй позже или переключись на локальную модель (!model local).",
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            code=CODE_QUOTA,
            user_message=user_message,
            retryable=False,
            details=details or {},
        )


class RouterModelNotLoadedError(RouterError):
    """Локальная модель не загружена (LM Studio и т.п.)."""

    def __init__(
        self,
        user_message: str = "Локальная модель не загружена. Загрузи модель в LM Studio или выбери облачную.",
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            code=CODE_MODEL_NOT_LOADED,
            user_message=user_message,
            retryable=True,
            details=details or {},
        )


class RouterNetworkError(RouterError):
    """Сетевая ошибка (connection refused, DNS и т.д.)."""

    def __init__(
        self,
        user_message: str = "Сетевая ошибка. Проверь подключение или попробуй позже.",
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            code=CODE_NETWORK,
            user_message=user_message,
            retryable=True,
            details=details or {},
        )


class RouterTimeoutError(RouterError):
    """Таймаут запроса."""

    def __init__(
        self,
        user_message: str = "Превышено время ожидания. Сократи запрос или повтори позже. Можно переключиться на локальную модель: !model local.",
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            code=CODE_TIMEOUT,
            user_message=user_message,
            retryable=True,
            details=details or {},
        )


def diagnostic_payload(err: RouterError) -> dict[str, Any]:
    """
    Унифицированный формат диагностики для команд и web API.

    Возвращает словарь: error_code, user_message, retryable, [details].
    """
    payload: dict[str, Any] = {
        "error_code": err.code,
        "user_message": err.user_message,
        "retryable": err.retryable,
    }
    if err.details:
        payload["details"] = err.details
    return payload


def is_fail_fast(err: RouterError) -> bool:
    """True для ошибок, при которых не следует делать общий retry (auth, quota)."""
    return not err.retryable


def user_message_for_surface(err: RouterError, telegram: bool = True) -> str:
    """
    Сообщение для показа пользователю (Telegram или Web).
    При необходимости можно добавить эмодзи/форматирование только для Telegram.
    """
    prefix = "🦀 " if telegram else ""
    return f"{prefix}{err.user_message}"
