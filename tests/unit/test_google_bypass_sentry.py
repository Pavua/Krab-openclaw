# -*- coding: utf-8 -*-
"""Unit tests для Wave 20-E: Sentry breadcrumbs в Google direct bypass.

Проверяем:
1. test_breadcrumb_added_on_start    — breadcrumb krab.bypass.start при входе в complete_direct
2. test_breadcrumb_added_on_success  — breadcrumb krab.bypass.success при успешном ответе
3. test_breadcrumb_added_on_error    — breadcrumb krab.bypass.error при исключении
4. test_breadcrumb_added_on_empty_retry — breadcrumb krab.bypass.empty_retry при пустом ответе
5. test_breadcrumb_silent_when_sentry_not_installed — silent при ImportError sentry_sdk
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Вспомогательные утилиты
# ---------------------------------------------------------------------------


def _get_helper():
    """Импортируем _add_sentry_breadcrumb из модуля."""
    from src.integrations.google_genai_direct import _add_sentry_breadcrumb

    return _add_sentry_breadcrumb


# ---------------------------------------------------------------------------
# 1. Breadcrumb при старте bypass
# ---------------------------------------------------------------------------


def test_breadcrumb_added_on_start():
    """_add_sentry_breadcrumb вызывает sentry_sdk.add_breadcrumb с category krab.bypass.start."""
    _add_sentry_breadcrumb = _get_helper()
    mock_sdk = MagicMock()

    with patch("sentry_sdk.add_breadcrumb", mock_sdk.add_breadcrumb):
        # Симулируем sentry_sdk доступным
        with patch.dict("sys.modules", {"sentry_sdk": mock_sdk}):
            _add_sentry_breadcrumb(
                "start",
                "Google direct bypass для gemini-3-pro-preview",
                model="gemini-3-pro-preview",
                has_system=True,
                contents_count=3,
            )

    mock_sdk.add_breadcrumb.assert_called_once()
    call_kwargs = mock_sdk.add_breadcrumb.call_args[1]
    assert call_kwargs["category"] == "krab.bypass.start"
    assert call_kwargs["level"] == "info"
    assert call_kwargs["data"]["model"] == "gemini-3-pro-preview"
    assert call_kwargs["data"]["has_system"] is True


# ---------------------------------------------------------------------------
# 2. Breadcrumb при успехе
# ---------------------------------------------------------------------------


def test_breadcrumb_added_on_success():
    """_add_sentry_breadcrumb с category=success передаёт latency_sec и response_len."""
    _add_sentry_breadcrumb = _get_helper()
    mock_sdk = MagicMock()

    with patch.dict("sys.modules", {"sentry_sdk": mock_sdk}):
        _add_sentry_breadcrumb(
            "success",
            "Bypass completed",
            model="gemini-3-pro-preview",
            latency_sec=1.23,
            response_len=512,
            is_empty=False,
        )

    mock_sdk.add_breadcrumb.assert_called_once()
    call_kwargs = mock_sdk.add_breadcrumb.call_args[1]
    assert call_kwargs["category"] == "krab.bypass.success"
    assert call_kwargs["data"]["latency_sec"] == 1.23
    assert call_kwargs["data"]["response_len"] == 512
    assert call_kwargs["data"]["is_empty"] is False


# ---------------------------------------------------------------------------
# 3. Breadcrumb при ошибке
# ---------------------------------------------------------------------------


def test_breadcrumb_added_on_error():
    """_add_sentry_breadcrumb с category=error передаёт error_type и level=warning."""
    _add_sentry_breadcrumb = _get_helper()
    mock_sdk = MagicMock()

    with patch.dict("sys.modules", {"sentry_sdk": mock_sdk}):
        _add_sentry_breadcrumb(
            "error",
            "Bypass failed: RuntimeError",
            level="warning",
            model="gemini-3-pro-preview",
            error="connection refused",
            error_type="RuntimeError",
            latency_sec=0.5,
        )

    mock_sdk.add_breadcrumb.assert_called_once()
    call_kwargs = mock_sdk.add_breadcrumb.call_args[1]
    assert call_kwargs["category"] == "krab.bypass.error"
    assert call_kwargs["level"] == "warning"
    assert call_kwargs["data"]["error_type"] == "RuntimeError"
    assert call_kwargs["data"]["model"] == "gemini-3-pro-preview"


# ---------------------------------------------------------------------------
# 4. Breadcrumb при empty_retry (Wave 18-I: Gemini thinking budget exhausted)
# ---------------------------------------------------------------------------


def test_breadcrumb_added_on_empty_retry():
    """_add_sentry_breadcrumb с category=empty_retry передаёт thoughts_tokens."""
    _add_sentry_breadcrumb = _get_helper()
    mock_sdk = MagicMock()

    with patch.dict("sys.modules", {"sentry_sdk": mock_sdk}):
        _add_sentry_breadcrumb(
            "empty_retry",
            "Empty response — retrying с thinking_budget=0",
            model="gemini-3-pro-preview",
            thoughts_tokens=8192,
            prompt_tokens=15,
        )

    mock_sdk.add_breadcrumb.assert_called_once()
    call_kwargs = mock_sdk.add_breadcrumb.call_args[1]
    assert call_kwargs["category"] == "krab.bypass.empty_retry"
    assert call_kwargs["data"]["thoughts_tokens"] == 8192
    assert call_kwargs["data"]["prompt_tokens"] == 15
    # empty_retry — info level по умолчанию
    assert call_kwargs["level"] == "info"


# ---------------------------------------------------------------------------
# 5. Silent когда sentry_sdk не установлен (ImportError)
# ---------------------------------------------------------------------------


def test_breadcrumb_silent_when_sentry_not_installed():
    """_add_sentry_breadcrumb не бросает исключение при отсутствии sentry_sdk."""
    import sys

    _add_sentry_breadcrumb = _get_helper()

    # Убираем sentry_sdk из sys.modules чтобы симулировать ImportError
    original = sys.modules.pop("sentry_sdk", None)
    try:
        # НЕ должно бросить исключение даже если sentry_sdk недоступен
        _add_sentry_breadcrumb(
            "start",
            "Google direct bypass для gemini-flash",
            model="gemini-flash",
            has_system=False,
        )
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"_add_sentry_breadcrumb бросил исключение без sentry_sdk: {exc}")
    finally:
        # Восстанавливаем исходное состояние
        if original is not None:
            sys.modules["sentry_sdk"] = original
