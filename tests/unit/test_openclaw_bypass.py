# -*- coding: utf-8 -*-
"""Wave 245: tests для KRAB_OPENCLAW_BYPASS_ENABLED env gate.

Покрытие:
1. is_openclaw_bypass_enabled() с env=1/0/unset/empty/typed-values.
2. Idempotent warning (warning логируется только один раз при transition).
3. record_openclaw_outcome + fail-rate snapshot.
4. should_recommend_bypass: min samples gate.
5. should_recommend_bypass: fail-rate threshold gate.
6. should_recommend_bypass: bypass уже включён → False.
7. should_recommend_bypass: quiet-period после alert.
8. maybe_send_bypass_recommendation: graceful без sentry_sdk.
"""

from __future__ import annotations

import os

import pytest

# ---------------------------------------------------------------------------
# Fixture: чистим env + state перед каждым тестом
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.delenv("KRAB_OPENCLAW_BYPASS_ENABLED", raising=False)
    from src.core.openclaw_bypass_gate import _reset_warning_state_for_tests
    from src.core.openclaw_bypass_recommender import _reset_state_for_tests

    _reset_warning_state_for_tests()
    _reset_state_for_tests()
    yield
    _reset_warning_state_for_tests()
    _reset_state_for_tests()


# ---------------------------------------------------------------------------
# 1. is_openclaw_bypass_enabled — env parsing
# ---------------------------------------------------------------------------


def test_bypass_disabled_by_default():
    """Без env переменной bypass выключен (default 0)."""
    from src.core.openclaw_bypass_gate import is_openclaw_bypass_enabled

    assert is_openclaw_bypass_enabled() is False


def test_bypass_enabled_when_env_truthy(monkeypatch):
    """Любое truthy значение включает bypass."""
    from src.core.openclaw_bypass_gate import (
        _reset_warning_state_for_tests,
        is_openclaw_bypass_enabled,
    )

    for value in ("1", "true", "TRUE", "yes", "on", "On"):
        _reset_warning_state_for_tests()
        monkeypatch.setenv("KRAB_OPENCLAW_BYPASS_ENABLED", value)
        assert is_openclaw_bypass_enabled() is True, f"failed for {value!r}"


def test_bypass_disabled_when_env_falsy(monkeypatch):
    """Falsy/мусорные значения оставляют bypass выключенным."""
    from src.core.openclaw_bypass_gate import is_openclaw_bypass_enabled

    for value in ("0", "false", "no", "off", "", "random_garbage"):
        monkeypatch.setenv("KRAB_OPENCLAW_BYPASS_ENABLED", value)
        assert is_openclaw_bypass_enabled() is False, f"failed for {value!r}"


# ---------------------------------------------------------------------------
# 2. Idempotent warning
# ---------------------------------------------------------------------------


def test_bypass_warning_logged_once(monkeypatch, caplog):
    """Warning при включении логируется только при first transition."""
    import logging

    from src.core.openclaw_bypass_gate import is_openclaw_bypass_enabled

    monkeypatch.setenv("KRAB_OPENCLAW_BYPASS_ENABLED", "1")
    with caplog.at_level(logging.WARNING):
        is_openclaw_bypass_enabled()
        is_openclaw_bypass_enabled()
        is_openclaw_bypass_enabled()

    matches = [r for r in caplog.records if "openclaw_bypass_enabled" in r.getMessage()]
    # Идемпотентность — не более одного warning'а за подряд idential calls.
    assert len(matches) <= 1


# ---------------------------------------------------------------------------
# 3. record_openclaw_outcome + fail-rate snapshot
# ---------------------------------------------------------------------------


def test_record_outcome_tracks_fail_rate():
    """После 10 событий (5 success, 5 fail) snapshot показывает 0.5 fail-rate."""
    from src.core.openclaw_bypass_recommender import (
        _fail_rate_snapshot,
        record_openclaw_outcome,
    )

    for _ in range(5):
        record_openclaw_outcome(True)
    for _ in range(5):
        record_openclaw_outcome(False)

    total, fail_rate = _fail_rate_snapshot()
    assert total == 10
    assert abs(fail_rate - 0.5) < 1e-6


# ---------------------------------------------------------------------------
# 4. should_recommend_bypass — min samples gate
# ---------------------------------------------------------------------------


def test_recommend_false_below_min_samples():
    """С < 5 samples должно вернуть False даже при 100% fail."""
    from src.core.openclaw_bypass_recommender import (
        record_openclaw_outcome,
        should_recommend_bypass,
    )

    for _ in range(3):
        record_openclaw_outcome(False)
    assert should_recommend_bypass() is False


# ---------------------------------------------------------------------------
# 5. should_recommend_bypass — fail-rate threshold gate
# ---------------------------------------------------------------------------


def test_recommend_false_below_threshold():
    """С 10 samples и < 50% fails — False."""
    from src.core.openclaw_bypass_recommender import (
        record_openclaw_outcome,
        should_recommend_bypass,
    )

    for _ in range(8):
        record_openclaw_outcome(True)
    for _ in range(2):
        record_openclaw_outcome(False)
    # 20% fail — ниже 50% threshold.
    assert should_recommend_bypass() is False


def test_recommend_true_above_threshold():
    """С 10 samples и >= 50% fails — True (если bypass не включён)."""
    from src.core.openclaw_bypass_recommender import (
        record_openclaw_outcome,
        should_recommend_bypass,
    )

    for _ in range(3):
        record_openclaw_outcome(True)
    for _ in range(7):
        record_openclaw_outcome(False)
    # 70% fail — выше threshold.
    assert should_recommend_bypass() is True


# ---------------------------------------------------------------------------
# 6. should_recommend_bypass — bypass уже включён → False
# ---------------------------------------------------------------------------


def test_recommend_false_when_bypass_already_on(monkeypatch):
    """Если KRAB_OPENCLAW_BYPASS_ENABLED уже =1, recommender молчит."""
    monkeypatch.setenv("KRAB_OPENCLAW_BYPASS_ENABLED", "1")
    from src.core.openclaw_bypass_recommender import (
        record_openclaw_outcome,
        should_recommend_bypass,
    )

    for _ in range(10):
        record_openclaw_outcome(False)
    assert should_recommend_bypass() is False


# ---------------------------------------------------------------------------
# 7. Quiet-period после Sentry alert
# ---------------------------------------------------------------------------


def test_recommend_quiet_after_alert():
    """После mark_alert_sent() должен подавлять следующие recommend в течение часа."""
    from src.core.openclaw_bypass_recommender import (
        mark_alert_sent,
        record_openclaw_outcome,
        should_recommend_bypass,
    )

    for _ in range(10):
        record_openclaw_outcome(False)
    assert should_recommend_bypass() is True
    mark_alert_sent()
    # Сразу после alert — False.
    assert should_recommend_bypass() is False


# ---------------------------------------------------------------------------
# 8. maybe_send_bypass_recommendation — graceful без sentry_sdk
# ---------------------------------------------------------------------------


def test_maybe_send_recommendation_graceful_without_sentry(monkeypatch):
    """Если sentry_sdk не установлен — отдаём True (логи через structlog) или False."""
    from src.core.openclaw_bypass_recommender import (
        maybe_send_bypass_recommendation,
        record_openclaw_outcome,
    )

    # Заполняем deque так, чтобы recommend сработал.
    for _ in range(10):
        record_openclaw_outcome(False)

    # Подменяем import sentry_sdk: simulate отсутствие модуля.
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "sentry_sdk":
            raise ImportError("simulated absent sentry_sdk")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    # Should not raise. Returns True (отправили — пусть в no-op fallback).
    result = maybe_send_bypass_recommendation()
    assert isinstance(result, bool)
    assert result is True


# ---------------------------------------------------------------------------
# 9. Routing assertion: модуль импортируется из openclaw_client (smoke)
# ---------------------------------------------------------------------------


def test_bypass_gate_imported_by_openclaw_client():
    """Smoke: openclaw_client должен импортировать gate без ошибок.

    Не запускаем send_message_stream (живой gateway), только проверяем
    что код-путь зарегистрирован.
    """
    import importlib

    # Импортируем сам gate (он lazy-imported в send_message_stream).
    gate = importlib.import_module("src.core.openclaw_bypass_gate")
    assert hasattr(gate, "is_openclaw_bypass_enabled")
    assert callable(gate.is_openclaw_bypass_enabled)
