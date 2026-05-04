# -*- coding: utf-8 -*-
"""Unit tests для Wave 20-B: Prometheus metrics Google direct bypass.

Тестируем:
1. record_google_bypass_call инкрементирует counter (success)
2. record_google_bypass_call с outcome=error — корректный лейбл
3. latency записывается в histogram
4. thoughts_tokens записывается в histogram при thoughts_tokens > 0
5. record_google_bypass_call не бросает исключений даже если prometheus_client сломан
"""

from __future__ import annotations

import importlib
import sys
import time
from types import ModuleType
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Вспомогательная утилита: получить текущее значение counter-лейбла
# ---------------------------------------------------------------------------


def _counter_value(counter, **labels) -> float:
    """Получить значение prometheus_client Counter для заданных лейблов."""
    return counter.labels(**labels)._value.get()


def _histogram_count(histogram, **labels) -> float:
    """Получить _count накопителя prometheus_client Histogram через samples."""
    child = histogram.labels(**labels)
    # prometheus_client >= 0.9: нет публичного _count — берём из _samples()
    for sample in child._samples():
        if sample.name == "_count":
            return sample.value
    return 0.0


def _histogram_sum(histogram, **labels) -> float:
    """Получить _sum накопителя prometheus_client Histogram."""
    return histogram.labels(**labels)._sum.get()


# ---------------------------------------------------------------------------
# 1. success outcome инкрементирует krab_google_direct_bypass_total
# ---------------------------------------------------------------------------


def test_record_success_increments_counter():
    """record_google_bypass_call(outcome='success') увеличивает counter на 1."""
    from src.core.prometheus_metrics import (
        krab_google_direct_bypass_total,
        record_google_bypass_call,
    )

    if krab_google_direct_bypass_total is None:
        # prometheus_client недоступен в этой среде — пропускаем
        return

    before = _counter_value(
        krab_google_direct_bypass_total,
        model="google/gemini-3-pro-preview",
        outcome="success",
    )
    record_google_bypass_call(
        model="google/gemini-3-pro-preview",
        outcome="success",
        latency_sec=1.5,
    )
    after = _counter_value(
        krab_google_direct_bypass_total,
        model="google/gemini-3-pro-preview",
        outcome="success",
    )
    assert after == before + 1.0


# ---------------------------------------------------------------------------
# 2. error outcome записывается с корректным лейблом
# ---------------------------------------------------------------------------


def test_record_error_records_with_correct_outcome_label():
    """record_google_bypass_call(outcome='error') использует лейбл 'error'."""
    from src.core.prometheus_metrics import (
        krab_google_direct_bypass_total,
        record_google_bypass_call,
    )

    if krab_google_direct_bypass_total is None:
        return

    before_error = _counter_value(
        krab_google_direct_bypass_total,
        model="google/gemini-2.5-flash",
        outcome="error",
    )
    before_success = _counter_value(
        krab_google_direct_bypass_total,
        model="google/gemini-2.5-flash",
        outcome="success",
    )

    record_google_bypass_call(
        model="google/gemini-2.5-flash",
        outcome="error",
        latency_sec=0.3,
    )

    after_error = _counter_value(
        krab_google_direct_bypass_total,
        model="google/gemini-2.5-flash",
        outcome="error",
    )
    after_success = _counter_value(
        krab_google_direct_bypass_total,
        model="google/gemini-2.5-flash",
        outcome="success",
    )

    # error counter вырос, success — нет
    assert after_error == before_error + 1.0
    assert after_success == before_success


# ---------------------------------------------------------------------------
# 3. latency наблюдается в histogram
# ---------------------------------------------------------------------------


def test_record_latency_observed_in_histogram():
    """latency_sec попадает в krab_google_direct_bypass_latency_seconds."""
    from src.core.prometheus_metrics import (
        krab_google_direct_bypass_latency_seconds,
        record_google_bypass_call,
    )

    if krab_google_direct_bypass_latency_seconds is None:
        return

    model_key = "google/gemini-3-flash-preview"
    before_count = _histogram_count(krab_google_direct_bypass_latency_seconds, model=model_key)
    before_sum = _histogram_sum(krab_google_direct_bypass_latency_seconds, model=model_key)

    record_google_bypass_call(model=model_key, outcome="success", latency_sec=2.7)

    after_count = _histogram_count(krab_google_direct_bypass_latency_seconds, model=model_key)
    after_sum = _histogram_sum(krab_google_direct_bypass_latency_seconds, model=model_key)

    assert after_count == before_count + 1
    assert abs((after_sum - before_sum) - 2.7) < 1e-6


# ---------------------------------------------------------------------------
# 4. thoughts_tokens наблюдается при thoughts_tokens > 0
# ---------------------------------------------------------------------------


def test_record_thoughts_tokens_observed():
    """thoughts_tokens > 0 попадает в krab_google_direct_bypass_thoughts_tokens."""
    from src.core.prometheus_metrics import (
        krab_google_direct_bypass_thoughts_tokens,
        record_google_bypass_call,
    )

    if krab_google_direct_bypass_thoughts_tokens is None:
        return

    model_key = "google/gemini-3-pro-preview-thinking"
    before_count = _histogram_count(krab_google_direct_bypass_thoughts_tokens, model=model_key)

    record_google_bypass_call(
        model=model_key,
        outcome="success",
        latency_sec=5.0,
        thoughts_tokens=800,
    )

    after_count = _histogram_count(krab_google_direct_bypass_thoughts_tokens, model=model_key)
    assert after_count == before_count + 1


# ---------------------------------------------------------------------------
# 5. При сломанных метриках record_google_bypass_call молча не бросает
# ---------------------------------------------------------------------------


def test_record_failure_silent_no_raise():
    """record_google_bypass_call не бросает даже если объекты метрик None (prometheus_client отсутствует)."""
    import src.core.prometheus_metrics as pm_module

    # Временно обнуляем все объекты метрик (симуляция отсутствия prometheus_client)
    original_total = pm_module.krab_google_direct_bypass_total
    original_latency = pm_module.krab_google_direct_bypass_latency_seconds
    original_thoughts = pm_module.krab_google_direct_bypass_thoughts_tokens

    pm_module.krab_google_direct_bypass_total = None
    pm_module.krab_google_direct_bypass_latency_seconds = None
    pm_module.krab_google_direct_bypass_thoughts_tokens = None

    try:
        # Не должно бросать никаких исключений
        pm_module.record_google_bypass_call(
            model="google/gemini-3-pro-preview",
            outcome="success",
            latency_sec=1.0,
            thoughts_tokens=100,
        )
    finally:
        # Восстанавливаем оригинальные объекты
        pm_module.krab_google_direct_bypass_total = original_total
        pm_module.krab_google_direct_bypass_latency_seconds = original_latency
        pm_module.krab_google_direct_bypass_thoughts_tokens = original_thoughts
