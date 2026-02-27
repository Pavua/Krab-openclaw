# -*- coding: utf-8 -*-
"""
Иерархия исключений Krab (Фаза 5 — Обработка ошибок).

Базовый KrabError с поддержкой retryable и user_message для единообразной
обработки и безопасного отображения сообщений пользователю.
"""


class KrabError(Exception):
    """
    Базовое исключение приложения Krab.

    Параметры:
        message: внутреннее сообщение для логов/отладки (первый позиционный аргумент).
        retryable: можно ли безопасно повторить запрос (по умолчанию False).
        user_message: текст, безопасный для показа пользователю (по умолчанию пустая строка).
    """

    def __init__(
        self,
        message: str = "",
        *,
        retryable: bool = False,
        user_message: str = "",
        **kwargs: object,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.user_message = user_message or message


class ProviderError(KrabError):
    """
    Ошибка API провайдера: таймаут, 5xx, недоступность сервиса.
    По умолчанию retryable=True (часто временные сбои).
    """

    def __init__(
        self,
        message: str = "",
        *,
        retryable: bool = True,
        user_message: str = "",
        **kwargs: object,
    ) -> None:
        super().__init__(
            message,
            retryable=retryable,
            user_message=user_message or "Ошибка сервиса. Попробуй позже.",
            **kwargs,
        )


class ProviderAuthError(ProviderError):
    """
    Ошибка аутентификации провайдера (401/403): неверный или отозванный API-ключ.
    Не повторять запрос (retryable=False).
    """

    def __init__(
        self,
        message: str = "",
        *,
        user_message: str = "",
        **kwargs: object,
    ) -> None:
        super().__init__(
            message,
            retryable=False,
            user_message=user_message or "Ошибка доступа: неверный или отсутствующий API-ключ.",
            **kwargs,
        )


class ModelLoadError(KrabError):
    """
    Ошибка при загрузке модели в LM Studio (или другом локальном бэкенде).
    """

    def __init__(
        self,
        message: str = "",
        *,
        retryable: bool = True,
        user_message: str = "",
        **kwargs: object,
    ) -> None:
        super().__init__(
            message,
            retryable=retryable,
            user_message=user_message or "Не удалось загрузить модель. Проверь LM Studio.",
            **kwargs,
        )


class UserInputError(KrabError):
    """
    Пользователь ввёл некорректную команду или неверные параметры.
    Повтор без изменения ввода бессмыслен (retryable=False).
    """

    def __init__(
        self,
        message: str = "",
        *,
        user_message: str = "",
        **kwargs: object,
    ) -> None:
        super().__init__(
            message,
            retryable=False,
            user_message=user_message or message,
            **kwargs,
        )


class RouterError(KrabError):
    """
    Ошибка при выборе или маршрутизации модели (выбор провайдера/модели).
    """

    def __init__(
        self,
        message: str = "",
        *,
        retryable: bool = False,
        user_message: str = "",
        **kwargs: object,
    ) -> None:
        super().__init__(
            message,
            retryable=retryable,
            user_message=user_message or message,
            **kwargs,
        )


class CacheError(KrabError):
    """
    Ошибка кэша (SQLite/Redis): недоступность, повреждение, таймаут.
    По умолчанию retryable=True (временные сбои хранилища).
    """

    def __init__(
        self,
        message: str = "",
        *,
        retryable: bool = True,
        user_message: str = "",
        **kwargs: object,
    ) -> None:
        super().__init__(
            message,
            retryable=retryable,
            user_message=user_message or "Ошибка кэша. Попробуй позже.",
            **kwargs,
        )
