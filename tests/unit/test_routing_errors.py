# -*- coding: utf-8 -*-
"""
Unit tests for routing_errors (Фаза 2.1 — таксономия ошибок роутинга).
"""
import pytest

from src.core.routing_errors import (
    CODE_AUTH,
    CODE_QUOTA,
    RouterAuthError,
    RouterQuotaError,
    RouterModelNotLoadedError,
    RouterNetworkError,
    RouterTimeoutError,
    diagnostic_payload,
    is_fail_fast,
    user_message_for_surface,
)


def test_router_auth_error_fail_fast():
    e = RouterAuthError()
    assert e.code == CODE_AUTH
    assert e.retryable is False
    assert is_fail_fast(e)


def test_router_quota_error_fail_fast():
    e = RouterQuotaError()
    assert e.code == CODE_QUOTA
    assert e.retryable is False
    assert is_fail_fast(e)


def test_router_model_not_loaded_retryable():
    e = RouterModelNotLoadedError()
    assert e.retryable is True
    assert not is_fail_fast(e)


def test_diagnostic_payload():
    e = RouterAuthError(user_message="Custom auth message")
    payload = diagnostic_payload(e)
    assert payload["error_code"] == "auth"
    assert payload["user_message"] == "Custom auth message"
    assert payload["retryable"] is False


def test_diagnostic_payload_with_details():
    e = RouterTimeoutError(details={"status": 504})
    payload = diagnostic_payload(e)
    assert payload["error_code"] == "timeout"
    assert "details" in payload
    assert payload["details"]["status"] == 504


def test_user_message_for_surface_telegram():
    e = RouterQuotaError()
    msg = user_message_for_surface(e, telegram=True)
    assert msg.startswith("🦀 ")
    assert e.user_message in msg


def test_user_message_for_surface_web():
    e = RouterNetworkError()
    msg = user_message_for_surface(e, telegram=False)
    assert not msg.startswith("🦀 ")
    assert e.user_message in msg
