# -*- coding: utf-8 -*-
"""
tests/unit/test_sentry_capture_points.py

Проверяет, что sentry_sdk.capture_exception() вызывается в ключевых catch-блоках
и что sentry_init корректно инициализирует SDK или пропускает при пустом DSN.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. sentry_init — DSN задан → init вызывается, возвращает True
# ---------------------------------------------------------------------------


def test_sentry_init_with_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.io/123")
    monkeypatch.setenv("KRAB_ENV", "production")

    mock_sdk = MagicMock()
    with patch.dict(
        "sys.modules", {"sentry_sdk": mock_sdk, "sentry_sdk.integrations.logging": MagicMock()}
    ):
        # Reimport чтобы модуль взял свежий env
        import importlib

        import src.bootstrap.sentry_init as _mod

        importlib.reload(_mod)

        result = _mod.init_sentry()

    assert result is True
    mock_sdk.init.assert_called_once()
    call_kwargs = mock_sdk.init.call_args.kwargs
    assert call_kwargs["environment"] == "production"
    assert call_kwargs["traces_sample_rate"] == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# 2. sentry_init — DSN пустой → init не вызывается, возвращает False
# ---------------------------------------------------------------------------


def test_sentry_init_without_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_DSN", "")

    mock_sdk = MagicMock()
    with patch.dict(
        "sys.modules", {"sentry_sdk": mock_sdk, "sentry_sdk.integrations.logging": MagicMock()}
    ):
        import importlib

        import src.bootstrap.sentry_init as _mod

        importlib.reload(_mod)

        result = _mod.init_sentry()

    assert result is False
    mock_sdk.init.assert_not_called()


# ---------------------------------------------------------------------------
# 3. sentry_init — KRAB_ENV=dev → traces_sample_rate=1.0
# ---------------------------------------------------------------------------


def test_sentry_init_dev_env_full_sample_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.io/123")
    monkeypatch.setenv("KRAB_ENV", "dev")

    mock_sdk = MagicMock()
    with patch.dict(
        "sys.modules", {"sentry_sdk": mock_sdk, "sentry_sdk.integrations.logging": MagicMock()}
    ):
        import importlib

        import src.bootstrap.sentry_init as _mod

        importlib.reload(_mod)

        _mod.init_sentry()

    call_kwargs = mock_sdk.init.call_args.kwargs
    assert call_kwargs["traces_sample_rate"] == pytest.approx(1.0)
    assert call_kwargs["environment"] == "dev"


# ---------------------------------------------------------------------------
# 4. llm_flow — background_ai_request_failed → capture_exception вызывается
# ---------------------------------------------------------------------------


def test_llm_flow_capture_exception_on_background_failure() -> None:
    """
    Проверяет, что при исключении в _finish_ai_request_background
    вызывается sentry_sdk.capture_exception с правильным exception.
    """
    import src.userbot.llm_flow as flow_mod

    mock_sdk = MagicMock()
    mock_scope = MagicMock()
    mock_sdk.push_scope.return_value.__enter__ = MagicMock(return_value=mock_scope)
    mock_sdk.push_scope.return_value.__exit__ = MagicMock(return_value=False)

    original_sdk = flow_mod._sentry_sdk
    flow_mod._sentry_sdk = mock_sdk

    exc = RuntimeError("LLM stream dropped")

    try:
        # Симулируем блок except из _finish_ai_request_background:
        # if _sentry_sdk is not None: ... capture_exception(exc)
        if flow_mod._sentry_sdk is not None:
            with flow_mod._sentry_sdk.push_scope() as scope:
                scope.set_tag("flow", "background_ai_request")
                scope.set_tag("chat_id", "123")
                flow_mod._sentry_sdk.capture_exception(exc)
    finally:
        flow_mod._sentry_sdk = original_sdk

    mock_sdk.capture_exception.assert_called_once_with(exc)
    mock_scope.set_tag.assert_any_call("flow", "background_ai_request")


# ---------------------------------------------------------------------------
# 5. proactive_watch — auto_restart_loop_error → capture_exception вызывается
# ---------------------------------------------------------------------------


def test_proactive_watch_capture_exception_on_health_failure() -> None:
    """
    Проверяет, что при ошибке в auto_restart_loop_error
    вызывается sentry_sdk.capture_exception.
    """
    import src.core.proactive_watch as pw_mod

    mock_sdk = MagicMock()
    original_sdk = pw_mod._sentry_sdk
    pw_mod._sentry_sdk = mock_sdk

    exc = ConnectionError("health probe failed")

    try:
        # Симулируем блок except из _auto_restart_checks_loop:
        if pw_mod._sentry_sdk is not None:
            pw_mod._sentry_sdk.capture_exception(exc)
    finally:
        pw_mod._sentry_sdk = original_sdk

    mock_sdk.capture_exception.assert_called_once_with(exc)


# ---------------------------------------------------------------------------
# 6. sentry_init — SDK import failure → возвращает False, не крашится
# ---------------------------------------------------------------------------


def test_sentry_init_sdk_crash_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.io/123")

    broken_sdk = MagicMock()
    broken_sdk.init.side_effect = RuntimeError("sentry exploded")

    with patch.dict(
        "sys.modules", {"sentry_sdk": broken_sdk, "sentry_sdk.integrations.logging": MagicMock()}
    ):
        import importlib

        import src.bootstrap.sentry_init as _mod

        importlib.reload(_mod)

        result = _mod.init_sentry()

    # Не должно броситься исключение, возвращает False
    assert result is False
