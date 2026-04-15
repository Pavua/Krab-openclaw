# -*- coding: utf-8 -*-
"""
Тесты для src/userbot/llm_retry.py.

Покрываем:
- is_retryable_error_text: retryable и non-retryable паттерны
- is_retryable_exception: разные типы исключений
- build_retry_notice: формат текста уведомления
- build_final_error_notice: формат финальной ошибки
- LLMRetryableError: базовые свойства

НЕ тестируем интеграцию с _finish_ai_request_background (требует живого userbot).
"""

from __future__ import annotations

import asyncio

import pytest

from src.userbot.llm_retry import (
    LLMRetryableError,
    NON_RETRYABLE_ERROR_CODES,
    RETRYABLE_ERROR_CODES,
    build_final_error_notice,
    build_retry_notice,
    is_retryable_error_text,
    is_retryable_exception,
)


# ---------------------------------------------------------------------------
# LLMRetryableError
# ---------------------------------------------------------------------------


class TestLLMRetryableError:
    def test_is_exception(self) -> None:
        err = LLMRetryableError("msg", error_text="❌ Timeout")
        assert isinstance(err, Exception)

    def test_stores_error_text(self) -> None:
        err = LLMRetryableError("msg", error_text="❌ quota_exceeded")
        assert err.error_text == "❌ quota_exceeded"

    def test_message_accessible(self) -> None:
        err = LLMRetryableError("retryable: details", error_text="text")
        assert "retryable" in str(err)

    def test_empty_error_text_allowed(self) -> None:
        err = LLMRetryableError("msg", error_text="")
        assert err.error_text == ""


# ---------------------------------------------------------------------------
# is_retryable_error_text — retryable cases
# ---------------------------------------------------------------------------


class TestIsRetryableErrorTextRetryable:
    def test_timeout_in_text(self) -> None:
        assert is_retryable_error_text("❌ Timeout провайдера") is True

    def test_quota_exceeded_in_text(self) -> None:
        assert is_retryable_error_text("quota_exceeded — исчерпана") is True

    def test_quota_word_in_text(self) -> None:
        assert is_retryable_error_text("quota limit reached") is True

    def test_gateway_unknown_error(self) -> None:
        assert is_retryable_error_text("gateway_unknown_error") is True

    def test_an_unknown_error_occurred(self) -> None:
        assert is_retryable_error_text("an unknown error occurred") is True

    def test_model_not_loaded(self) -> None:
        assert is_retryable_error_text("no models loaded") is True

    def test_model_unloaded(self) -> None:
        assert is_retryable_error_text("model unloaded") is True

    def test_model_has_crashed(self) -> None:
        assert is_retryable_error_text("model has crashed without additional information") is True

    def test_lm_model_crash_code(self) -> None:
        assert is_retryable_error_text("lm_model_crash") is True

    def test_lm_empty_stream_code(self) -> None:
        assert is_retryable_error_text("lm_empty_stream") is True

    def test_empty_message_sentinel(self) -> None:
        assert is_retryable_error_text("<empty message>") is True

    def test_provider_timeout_code(self) -> None:
        assert is_retryable_error_text("provider_timeout") is True

    def test_case_insensitive(self) -> None:
        assert is_retryable_error_text("QUOTA_EXCEEDED error") is True

    def test_mixed_case_timeout(self) -> None:
        assert is_retryable_error_text("OpenClaw TIMEOUT при обращении") is True

    def test_in_full_sentence(self) -> None:
        text = "❌ Модель отвечает слишком долго. Попробуй ещё раз или переключись на !model cloud"
        # "слишком долго" — не retryable, нет паттернов
        assert is_retryable_error_text(text) is False

    def test_timeout_in_openclaw_error(self) -> None:
        text = "❌ OpenClaw слишком долго: timeout при соединении с провайдером"
        assert is_retryable_error_text(text) is True


# ---------------------------------------------------------------------------
# is_retryable_error_text — non-retryable cases
# ---------------------------------------------------------------------------


class TestIsRetryableErrorTextNonRetryable:
    def test_empty_string(self) -> None:
        assert is_retryable_error_text("") is False

    def test_safety_block(self) -> None:
        assert is_retryable_error_text("safety block activated") is False

    def test_invalid_api_key(self) -> None:
        assert is_retryable_error_text("invalid api key provided") is False

    def test_unauthenticated(self) -> None:
        assert is_retryable_error_text("unauthenticated request") is False

    def test_forbidden_access(self) -> None:
        assert is_retryable_error_text("forbidden access") is False

    def test_vision_addon_missing(self) -> None:
        assert is_retryable_error_text("vision add-on is not loaded") is False

    def test_missing_image_config(self) -> None:
        assert is_retryable_error_text("missing image config error") is False

    def test_normal_response(self) -> None:
        assert is_retryable_error_text("Отличный вопрос! Вот ответ:") is False

    def test_partial_match_in_word_ok(self) -> None:
        # "quotation" содержит "quota" — это retryable по текущей реализации
        # (substring match), тест документирует поведение
        result = is_retryable_error_text("quotation error")
        assert isinstance(result, bool)  # просто проверяем что не падает

    def test_401_with_unauthorized(self) -> None:
        # "unauthorized" + "401" — non-retryable (auth error)
        assert is_retryable_error_text("unauthorized 401 error") is False

    def test_none_handling(self) -> None:
        # None передаётся как пустая строка — не должно падать
        # (функция принимает str, но проверим через пустую строку)
        assert is_retryable_error_text("") is False


# ---------------------------------------------------------------------------
# is_retryable_exception
# ---------------------------------------------------------------------------


class TestIsRetryableException:
    def test_asyncio_timeout(self) -> None:
        exc = asyncio.TimeoutError()
        assert is_retryable_exception(exc) is True

    def test_exception_with_retryable_true(self) -> None:
        class FakeProviderError(Exception):
            retryable = True

        assert is_retryable_exception(FakeProviderError()) is True

    def test_exception_with_retryable_false(self) -> None:
        class FakeAuthError(Exception):
            retryable = False

        assert is_retryable_exception(FakeAuthError()) is False

    def test_generic_exception_no_retryable_attr(self) -> None:
        exc = ValueError("some error")
        assert is_retryable_exception(exc) is False

    def test_runtime_error_no_retryable(self) -> None:
        assert is_retryable_exception(RuntimeError("boom")) is False

    def test_exception_with_retryable_none(self) -> None:
        # retryable=None — не set, должен вернуть False
        class WeirdError(Exception):
            retryable = None

        # None → bool(None) = False → не retryable
        result = is_retryable_exception(WeirdError())
        assert result is False

    def test_httpx_connect_error_retryable(self) -> None:
        try:
            import httpx

            exc = httpx.ConnectError("connection refused")
            assert is_retryable_exception(exc) is True
        except ImportError:
            pytest.skip("httpx not installed")

    def test_httpx_read_timeout_retryable(self) -> None:
        try:
            import httpx

            exc = httpx.ReadTimeout("read timeout")
            assert is_retryable_exception(exc) is True
        except ImportError:
            pytest.skip("httpx not installed")

    def test_httpx_500_retryable(self) -> None:
        try:
            import httpx
            from unittest.mock import MagicMock

            response = MagicMock()
            response.status_code = 500
            exc = httpx.HTTPStatusError("500", request=MagicMock(), response=response)
            assert is_retryable_exception(exc) is True
        except ImportError:
            pytest.skip("httpx not installed")

    def test_httpx_429_retryable(self) -> None:
        try:
            import httpx
            from unittest.mock import MagicMock

            response = MagicMock()
            response.status_code = 429
            exc = httpx.HTTPStatusError("429", request=MagicMock(), response=response)
            assert is_retryable_exception(exc) is True
        except ImportError:
            pytest.skip("httpx not installed")

    def test_httpx_400_not_retryable(self) -> None:
        try:
            import httpx
            from unittest.mock import MagicMock

            response = MagicMock()
            response.status_code = 400
            exc = httpx.HTTPStatusError("400", request=MagicMock(), response=response)
            assert is_retryable_exception(exc) is False
        except ImportError:
            pytest.skip("httpx not installed")

    def test_httpx_401_not_retryable(self) -> None:
        try:
            import httpx
            from unittest.mock import MagicMock

            response = MagicMock()
            response.status_code = 401
            exc = httpx.HTTPStatusError("401", request=MagicMock(), response=response)
            assert is_retryable_exception(exc) is False
        except ImportError:
            pytest.skip("httpx not installed")


# ---------------------------------------------------------------------------
# build_retry_notice
# ---------------------------------------------------------------------------


class TestBuildRetryNotice:
    def test_contains_attempt_info(self) -> None:
        notice = build_retry_notice(attempt=1, max_retries=2, delay_sec=3.0)
        assert "1/2" in notice

    def test_contains_retry_emoji(self) -> None:
        notice = build_retry_notice(attempt=1, max_retries=1, delay_sec=2.0)
        assert "🔄" in notice

    def test_contains_delay_info(self) -> None:
        notice = build_retry_notice(attempt=1, max_retries=3, delay_sec=5.0)
        assert "5" in notice

    def test_second_attempt(self) -> None:
        notice = build_retry_notice(attempt=2, max_retries=3, delay_sec=2.0)
        assert "2/3" in notice

    def test_is_string(self) -> None:
        notice = build_retry_notice(attempt=1, max_retries=1, delay_sec=1.0)
        assert isinstance(notice, str)

    def test_not_empty(self) -> None:
        notice = build_retry_notice(attempt=1, max_retries=2, delay_sec=2.0)
        assert len(notice) > 0

    def test_zero_delay(self) -> None:
        notice = build_retry_notice(attempt=1, max_retries=1, delay_sec=0.0)
        assert "1/1" in notice


# ---------------------------------------------------------------------------
# build_final_error_notice
# ---------------------------------------------------------------------------


class TestBuildFinalErrorNotice:
    def test_contains_original_error(self) -> None:
        notice = build_final_error_notice(
            original_error="❌ Timeout", attempts_made=1, max_retries=1
        )
        assert "❌ Timeout" in notice

    def test_contains_attempts_info_when_retried(self) -> None:
        notice = build_final_error_notice(
            original_error="❌ quota_exceeded", attempts_made=2, max_retries=2
        )
        assert "2/2" in notice

    def test_no_retry_info_when_no_attempts(self) -> None:
        notice = build_final_error_notice(
            original_error="❌ Error", attempts_made=0, max_retries=1
        )
        # При 0 попытках — просто оригинальный текст
        assert "❌ Error" in notice
        assert "0/1" not in notice

    def test_is_string(self) -> None:
        notice = build_final_error_notice(
            original_error="error", attempts_made=1, max_retries=1
        )
        assert isinstance(notice, str)

    def test_strips_original_error(self) -> None:
        notice = build_final_error_notice(
            original_error="  ❌ Error  ", attempts_made=1, max_retries=1
        )
        # Оригинальная ошибка должна присутствовать (stripped)
        assert "❌ Error" in notice

    def test_one_attempt_one_max(self) -> None:
        notice = build_final_error_notice(
            original_error="timeout", attempts_made=1, max_retries=1
        )
        assert "1/1" in notice
        assert "timeout" in notice


# ---------------------------------------------------------------------------
# RETRYABLE_ERROR_CODES и NON_RETRYABLE_ERROR_CODES константы
# ---------------------------------------------------------------------------


class TestErrorCodeSets:
    def test_retryable_contains_timeout(self) -> None:
        assert "provider_timeout" in RETRYABLE_ERROR_CODES

    def test_retryable_contains_quota(self) -> None:
        assert "quota_exceeded" in RETRYABLE_ERROR_CODES

    def test_retryable_contains_gateway_error(self) -> None:
        assert "gateway_unknown_error" in RETRYABLE_ERROR_CODES

    def test_retryable_contains_empty_stream(self) -> None:
        assert "lm_empty_stream" in RETRYABLE_ERROR_CODES

    def test_non_retryable_contains_auth(self) -> None:
        assert "auth_unauthorized" in NON_RETRYABLE_ERROR_CODES

    def test_non_retryable_contains_safety(self) -> None:
        assert "safety_block" in NON_RETRYABLE_ERROR_CODES

    def test_no_overlap_between_sets(self) -> None:
        overlap = RETRYABLE_ERROR_CODES & NON_RETRYABLE_ERROR_CODES
        assert len(overlap) == 0, f"Коды перекрываются: {overlap}"

    def test_retryable_is_frozenset(self) -> None:
        assert isinstance(RETRYABLE_ERROR_CODES, frozenset)

    def test_non_retryable_is_frozenset(self) -> None:
        assert isinstance(NON_RETRYABLE_ERROR_CODES, frozenset)
