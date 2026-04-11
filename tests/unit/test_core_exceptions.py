# -*- coding: utf-8 -*-
"""Unit-тесты для src/core/exceptions.py: иерархия KrabError и подклассы."""

from __future__ import annotations

import pytest

from src.core.exceptions import (
    CacheError,
    KrabError,
    ModelLoadError,
    ProviderAuthError,
    ProviderError,
    RouterError,
    UserInputError,
)

# ---------------------------------------------------------------------------
# KrabError — базовый класс
# ---------------------------------------------------------------------------


def test_krab_error_is_exception() -> None:
    """KrabError является подклассом Exception."""
    assert issubclass(KrabError, Exception)


def test_krab_error_defaults() -> None:
    """По умолчанию retryable=False, user_message совпадает с message."""
    err = KrabError("что-то пошло не так")
    assert str(err) == "что-то пошло не так"
    assert err.retryable is False
    assert err.user_message == "что-то пошло не так"


def test_krab_error_explicit_user_message() -> None:
    """Явный user_message не перетирается основным message."""
    err = KrabError("internal detail", user_message="Ошибка для пользователя")
    assert err.user_message == "Ошибка для пользователя"
    assert str(err) == "internal detail"


def test_krab_error_retryable_flag() -> None:
    """Флаг retryable корректно устанавливается."""
    err = KrabError("retry me", retryable=True)
    assert err.retryable is True


def test_krab_error_empty_message() -> None:
    """Пустое сообщение не вызывает исключений при создании."""
    err = KrabError()
    assert str(err) == ""
    assert err.retryable is False


# ---------------------------------------------------------------------------
# ProviderError
# ---------------------------------------------------------------------------


def test_provider_error_inherits_krab_error() -> None:
    """ProviderError является подклассом KrabError."""
    assert issubclass(ProviderError, KrabError)


def test_provider_error_retryable_by_default() -> None:
    """ProviderError по умолчанию retryable=True."""
    err = ProviderError("timeout")
    assert err.retryable is True


def test_provider_error_default_user_message() -> None:
    """ProviderError содержит человекочитаемый user_message по умолчанию."""
    err = ProviderError("5xx from gemini")
    assert "Попробуй позже" in err.user_message


def test_provider_error_custom_user_message() -> None:
    """Явный user_message перекрывает дефолт ProviderError."""
    err = ProviderError("net err", user_message="Сервис временно недоступен")
    assert err.user_message == "Сервис временно недоступен"


# ---------------------------------------------------------------------------
# ProviderAuthError
# ---------------------------------------------------------------------------


def test_provider_auth_error_inherits_provider_error() -> None:
    """ProviderAuthError является подклассом ProviderError."""
    assert issubclass(ProviderAuthError, ProviderError)


def test_provider_auth_error_not_retryable() -> None:
    """Ошибка аутентификации никогда не retryable."""
    err = ProviderAuthError("401 Unauthorized")
    assert err.retryable is False


def test_provider_auth_error_default_user_message() -> None:
    """ProviderAuthError содержит сообщение об API-ключе."""
    err = ProviderAuthError()
    assert "API-ключ" in err.user_message


# ---------------------------------------------------------------------------
# ModelLoadError
# ---------------------------------------------------------------------------


def test_model_load_error_inherits_krab_error() -> None:
    """ModelLoadError является подклассом KrabError."""
    assert issubclass(ModelLoadError, KrabError)


def test_model_load_error_retryable_by_default() -> None:
    """ModelLoadError по умолчанию retryable=True."""
    err = ModelLoadError("VRAM OOM")
    assert err.retryable is True


def test_model_load_error_default_user_message() -> None:
    """ModelLoadError предлагает проверить LM Studio."""
    err = ModelLoadError("model not found")
    assert "LM Studio" in err.user_message


# ---------------------------------------------------------------------------
# UserInputError
# ---------------------------------------------------------------------------


def test_user_input_error_inherits_krab_error() -> None:
    """UserInputError является подклассом KrabError."""
    assert issubclass(UserInputError, KrabError)


def test_user_input_error_not_retryable() -> None:
    """Повтор некорректного ввода бессмысленен."""
    err = UserInputError("неверная команда")
    assert err.retryable is False


def test_user_input_error_user_message_defaults_to_message() -> None:
    """user_message совпадает с message при отсутствии явного значения."""
    err = UserInputError("Неверный формат аргументов")
    assert err.user_message == "Неверный формат аргументов"


# ---------------------------------------------------------------------------
# RouterError
# ---------------------------------------------------------------------------


def test_router_error_inherits_krab_error() -> None:
    """RouterError является подклассом KrabError."""
    assert issubclass(RouterError, KrabError)


def test_router_error_not_retryable_by_default() -> None:
    """RouterError по умолчанию не retryable."""
    err = RouterError("no model available")
    assert err.retryable is False


def test_router_error_can_be_retryable() -> None:
    """RouterError можно создать с retryable=True."""
    err = RouterError("transient routing issue", retryable=True)
    assert err.retryable is True


# ---------------------------------------------------------------------------
# CacheError
# ---------------------------------------------------------------------------


def test_cache_error_inherits_krab_error() -> None:
    """CacheError является подклассом KrabError."""
    assert issubclass(CacheError, KrabError)


def test_cache_error_retryable_by_default() -> None:
    """CacheError по умолчанию retryable=True — временные сбои хранилища."""
    err = CacheError("SQLite locked")
    assert err.retryable is True


def test_cache_error_default_user_message() -> None:
    """CacheError содержит человекочитаемый user_message."""
    err = CacheError("db timeout")
    assert "Попробуй позже" in err.user_message


# ---------------------------------------------------------------------------
# Полиморфизм: catch KrabError ловит все подклассы
# ---------------------------------------------------------------------------


def test_catch_krab_error_catches_subclasses() -> None:
    """Все кастомные исключения ловятся через KrabError."""
    subclasses = [
        ProviderError("p"),
        ProviderAuthError("a"),
        ModelLoadError("m"),
        UserInputError("u"),
        RouterError("r"),
        CacheError("c"),
    ]
    for exc in subclasses:
        with pytest.raises(KrabError):
            raise exc
