# -*- coding: utf-8 -*-
"""
Wave 217: тесты для production-enable pressure-aware select + safety guard.

Покрытие:
- default env gate = ON (после Wave 217)
- явное "0" / "false" / "off" выключает pre-filter
- safety guard auto-disable при > 10 fallback за окно
- safety guard блокирует дальнейшие fallback'и (bypass)
- reset_safety_guard() корректно сбрасывает state
- Sentry warning отправляется при срабатывании auto-disable
"""

from __future__ import annotations

import sys
from unittest import mock

import pytest

# Импортируем модуль, чтобы можно было трогать его глобалы.
from src.core import pressure_aware_select as pas


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Каждый тест стартует с чистым safety guard и без env override."""
    # Удаляем переменную, чтобы проверять именно default
    monkeypatch.delenv("KRAB_PRESSURE_AWARE_SELECTION", raising=False)
    pas.reset_safety_guard()
    yield
    pas.reset_safety_guard()


def test_default_env_gate_is_on_wave217():
    """Wave 217: без явного env var pre-filter должен быть ВКЛЮЧЁН."""
    assert pas._env_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", ""])
def test_env_gate_explicit_disable(monkeypatch, value):
    """Любое из значений отключения обходит pre-filter."""
    monkeypatch.setenv("KRAB_PRESSURE_AWARE_SELECTION", value)
    assert pas._env_enabled() is False
    # И сам selector возвращает preferred без изменений
    result = pas.pressure_aware_model_select(
        "local/llama-7b",
        [],
        free_gb_override=0.5,  # экстремальный pressure — но gate OFF
    )
    assert result == "local/llama-7b"


def test_safety_guard_trips_after_threshold():
    """После > MAX_FALLBACKS_PER_HOUR fallback'ов guard включается."""
    # 10 fallback'ов не должны переключить; 11-й — должен
    for _ in range(pas.MAX_FALLBACKS_PER_HOUR):
        pas._record_fallback_and_check_safety(
            from_model="local/x", to_model="__cloud__", reason="hard_pressure"
        )
    assert pas._safety_guard_active() is False
    pas._record_fallback_and_check_safety(
        from_model="local/x", to_model="__cloud__", reason="hard_pressure"
    )
    assert pas._safety_guard_active() is True


def test_safety_guard_bypasses_prefilter_after_trip():
    """После trip selector должен возвращать preferred без fallback."""
    # Принудительно тримим guard
    for _ in range(pas.MAX_FALLBACKS_PER_HOUR + 1):
        pas._record_fallback_and_check_safety(
            from_model="local/x", to_model="__cloud__", reason="hard_pressure"
        )
    assert pas._safety_guard_active() is True

    # Селектор должен обходить pre-filter и возвращать preferred
    result = pas.pressure_aware_model_select(
        "local/llama-7b",
        [{"id": "local/tiny", "size_gb": 1.0}],
        free_gb_override=0.1,  # HARD pressure, но guard ON
    )
    assert result == "local/llama-7b"


def test_reset_safety_guard_clears_state():
    """reset_safety_guard() сбрасывает trip flag и счётчики."""
    for _ in range(pas.MAX_FALLBACKS_PER_HOUR + 1):
        pas._record_fallback_and_check_safety(
            from_model="local/x", to_model="__cloud__", reason="hard_pressure"
        )
    assert pas._safety_guard_active() is True

    pas.reset_safety_guard()
    assert pas._safety_guard_active() is False
    assert len(pas._fallback_timestamps) == 0


def test_safety_trip_emits_sentry_warning():
    """При trip отправляется sentry_sdk.capture_message(level=warning)."""
    fake_sentry = mock.MagicMock()
    with mock.patch.dict(sys.modules, {"sentry_sdk": fake_sentry}):
        for _ in range(pas.MAX_FALLBACKS_PER_HOUR + 1):
            pas._record_fallback_and_check_safety(
                from_model="local/x", to_model="__cloud__", reason="hard_pressure"
            )
    assert fake_sentry.capture_message.called
    args, kwargs = fake_sentry.capture_message.call_args
    # сообщение содержит Wave 217 marker
    msg = args[0] if args else kwargs.get("message", "")
    assert "Wave 217" in msg
    assert kwargs.get("level") == "warning"


def test_safety_trip_only_once_no_double_sentry():
    """Повторные fallback'и после trip не дублируют Sentry events."""
    fake_sentry = mock.MagicMock()
    with mock.patch.dict(sys.modules, {"sentry_sdk": fake_sentry}):
        # Trip
        for _ in range(pas.MAX_FALLBACKS_PER_HOUR + 1):
            pas._record_fallback_and_check_safety(
                from_model="local/x", to_model="__cloud__", reason="hard_pressure"
            )
        # Ещё попытки после trip — должны no-op
        for _ in range(5):
            pas._record_fallback_and_check_safety(
                from_model="local/x", to_model="__cloud__", reason="hard_pressure"
            )
    assert fake_sentry.capture_message.call_count == 1
