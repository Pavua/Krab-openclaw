# -*- coding: utf-8 -*-
"""Unit tests для Sentry breadcrumbs в Google direct bypass.

Wave 20-E оригинальный тест, обновлён в Wave 30-B:
_add_sentry_breadcrumb заменена на _add_genai_breadcrumb + add_bypass_breadcrumb helper.

Проверяем:
1. test_breadcrumb_added_on_start    — breadcrumb bypass.google-direct при engaged
2. test_breadcrumb_added_on_success  — breadcrumb bypass.google-direct при success
3. test_breadcrumb_added_on_error    — breadcrumb bypass.google-direct при failure
4. test_breadcrumb_added_on_empty_retry — breadcrumb при empty_retry
5. test_breadcrumb_silent_when_sentry_not_installed — silent при ImportError sentry_sdk
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Вспомогательные утилиты
# ---------------------------------------------------------------------------


def _get_helper():
    """Импортируем _add_genai_breadcrumb из модуля (Wave 30-B: новое имя)."""
    from src.integrations.google_genai_direct import _add_genai_breadcrumb

    return _add_genai_breadcrumb


# ---------------------------------------------------------------------------
# 1. Breadcrumb при старте bypass
# ---------------------------------------------------------------------------


def test_breadcrumb_added_on_start():
    """_add_genai_breadcrumb вызывает sentry_sdk.add_breadcrumb с category bypass.google-direct."""
    _add_genai_breadcrumb = _get_helper()
    mock_sdk = MagicMock()

    with patch.dict("sys.modules", {"sentry_sdk": mock_sdk}):
        with patch.dict("os.environ", {"KRAB_BYPASS_SENTRY_BREADCRUMBS_ENABLED": "1"}):
            _add_genai_breadcrumb(
                "engaged",
                "gemini-3-pro-preview",
                is_gemma=False,
                has_system=True,
                contents_count=3,
            )

    mock_sdk.add_breadcrumb.assert_called_once()
    call_kwargs = mock_sdk.add_breadcrumb.call_args[1]
    assert call_kwargs["category"] == "bypass.google-direct"
    assert call_kwargs["level"] == "info"
    assert call_kwargs["data"]["model"] == "gemini-3-pro-preview"
    assert call_kwargs["data"]["has_system"] is True


# ---------------------------------------------------------------------------
# 2. Breadcrumb при успехе
# ---------------------------------------------------------------------------


def test_breadcrumb_added_on_success():
    """_add_genai_breadcrumb с event=success передаёт latency_sec и response_len."""
    _add_genai_breadcrumb = _get_helper()
    mock_sdk = MagicMock()

    with patch.dict("sys.modules", {"sentry_sdk": mock_sdk}):
        with patch.dict("os.environ", {"KRAB_BYPASS_SENTRY_BREADCRUMBS_ENABLED": "1"}):
            _add_genai_breadcrumb(
                "success",
                "gemini-3-pro-preview",
                is_gemma=False,
                latency_sec=1.23,
                response_len=512,
                is_empty=False,
            )

    mock_sdk.add_breadcrumb.assert_called_once()
    call_kwargs = mock_sdk.add_breadcrumb.call_args[1]
    assert call_kwargs["category"] == "bypass.google-direct"
    assert call_kwargs["message"] == "google-direct_success"
    assert call_kwargs["data"]["latency_sec"] == 1.23
    assert call_kwargs["data"]["response_len"] == 512
    assert call_kwargs["data"]["is_empty"] is False


# ---------------------------------------------------------------------------
# 3. Breadcrumb при ошибке
# ---------------------------------------------------------------------------


def test_breadcrumb_added_on_error():
    """_add_genai_breadcrumb с event=failure передаёт error_type и level=warning."""
    _add_genai_breadcrumb = _get_helper()
    mock_sdk = MagicMock()

    with patch.dict("sys.modules", {"sentry_sdk": mock_sdk}):
        with patch.dict("os.environ", {"KRAB_BYPASS_SENTRY_BREADCRUMBS_ENABLED": "1"}):
            _add_genai_breadcrumb(
                "failure",
                "gemini-3-pro-preview",
                is_gemma=False,
                level="warning",
                error="connection refused",
                error_type="RuntimeError",
                latency_sec=0.5,
            )

    mock_sdk.add_breadcrumb.assert_called_once()
    call_kwargs = mock_sdk.add_breadcrumb.call_args[1]
    assert call_kwargs["category"] == "bypass.google-direct"
    assert call_kwargs["level"] == "warning"
    assert call_kwargs["data"]["error_type"] == "RuntimeError"
    assert call_kwargs["data"]["model"] == "gemini-3-pro-preview"


# ---------------------------------------------------------------------------
# 4. Breadcrumb при empty_retry (Wave 18-I: Gemini thinking budget exhausted)
# ---------------------------------------------------------------------------


def test_breadcrumb_added_on_empty_retry():
    """_add_genai_breadcrumb с event=empty_retry передаёт thoughts_tokens."""
    _add_genai_breadcrumb = _get_helper()
    mock_sdk = MagicMock()

    with patch.dict("sys.modules", {"sentry_sdk": mock_sdk}):
        with patch.dict("os.environ", {"KRAB_BYPASS_SENTRY_BREADCRUMBS_ENABLED": "1"}):
            _add_genai_breadcrumb(
                "empty_retry",
                "gemini-3-pro-preview",
                is_gemma=False,
                thoughts_tokens=8192,
                prompt_tokens=15,
            )

    mock_sdk.add_breadcrumb.assert_called_once()
    call_kwargs = mock_sdk.add_breadcrumb.call_args[1]
    assert call_kwargs["category"] == "bypass.google-direct"
    assert call_kwargs["message"] == "google-direct_empty_retry"
    assert call_kwargs["data"]["thoughts_tokens"] == 8192
    assert call_kwargs["data"]["prompt_tokens"] == 15
    # empty_retry — info level по умолчанию
    assert call_kwargs["level"] == "info"


# ---------------------------------------------------------------------------
# 5. Silent когда sentry_sdk не установлен (ImportError)
# ---------------------------------------------------------------------------


def test_breadcrumb_silent_when_sentry_not_installed():
    """_add_genai_breadcrumb не бросает исключение при отсутствии sentry_sdk."""
    _add_genai_breadcrumb = _get_helper()

    # Убираем sentry_sdk из sys.modules чтобы симулировать ImportError
    original = sys.modules.pop("sentry_sdk", None)
    # Сбрасываем _bypass_sentry чтобы он пересмотрел sys.modules
    if "src.integrations._bypass_sentry" in sys.modules:
        del sys.modules["src.integrations._bypass_sentry"]

    try:
        # НЕ должно бросить исключение даже если sentry_sdk недоступен
        _add_genai_breadcrumb(
            "engaged",
            "gemini-flash",
            is_gemma=False,
            has_system=False,
        )
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"_add_genai_breadcrumb бросил исключение без sentry_sdk: {exc}")
    finally:
        # Восстанавливаем исходное состояние
        if original is not None:
            sys.modules["sentry_sdk"] = original
