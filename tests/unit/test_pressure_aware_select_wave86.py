# -*- coding: utf-8 -*-
"""
Wave 86: тесты pressure-aware model selection.

Покрытие:
- high RAM (>= 4 GB) → preferred сохраняется
- soft pressure (2-4 GB) + local preferred → выбирается самая маленькая local
- soft pressure + нет local candidates → cloud fallback
- hard pressure (< 2 GB) + local preferred → cloud_fallback
- hard pressure + уже cloud preferred → preferred сохраняется
- env-gate KRAB_PRESSURE_AWARE_SELECTION=0 → bypass (всегда preferred)
- get_free_memory_gb returns None → pre-filter skip (preferred сохраняется)
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.core import pressure_aware_select as pas


@pytest.fixture(autouse=True)
def _reset_counter():
    """Изолируем счётчик между тестами."""
    from src.core.prometheus_metrics import _PRESSURE_AWARE_FALLBACK_COUNTER

    _PRESSURE_AWARE_FALLBACK_COUNTER.clear()
    yield
    _PRESSURE_AWARE_FALLBACK_COUNTER.clear()


@pytest.fixture(autouse=True)
def _env_on(monkeypatch):
    """По умолчанию pre-filter включён в тестах."""
    monkeypatch.setenv("KRAB_PRESSURE_AWARE_SELECTION", "1")


def test_high_ram_keeps_preferred():
    """≥ 4 GB free — preferred модель сохраняется без вмешательства."""
    result = pas.pressure_aware_model_select(
        "local",
        candidate_models=[{"id": "small", "size_gb": 1.0}],
        free_gb_override=10.0,
    )
    assert result == "local"


def test_soft_pressure_picks_smallest_local():
    """2-4 GB free + local preferred → берём самую маленькую local."""
    candidates = [
        {"id": "local/gemma-12b-mlx", "size_gb": 7.5},
        {"id": "local/gemma-4b-mlx", "size_gb": 2.4},
        {"id": "local/llama-8b-mlx", "size_gb": 5.0},
    ]
    result = pas.pressure_aware_model_select(
        "local/gemma-12b-mlx",
        candidate_models=candidates,
        free_gb_override=3.0,
    )
    assert result == "local/gemma-4b-mlx"


def test_soft_pressure_no_local_candidates_falls_back_cloud():
    """SOFT pressure + нет local candidates с size → cloud fallback."""
    result = pas.pressure_aware_model_select(
        "local",
        candidate_models=[{"id": "google/gemini-3-pro-preview"}],
        free_gb_override=3.0,
        cloud_fallback="google/gemini-3-flash-preview",
    )
    assert result == "google/gemini-3-flash-preview"


def test_hard_pressure_forces_cloud_when_local_preferred():
    """< 2 GB free + local preferred → принудительно cloud_fallback."""
    result = pas.pressure_aware_model_select(
        "local",
        candidate_models=[{"id": "local/gemma-4b", "size_gb": 2.5}],
        free_gb_override=1.0,
        cloud_fallback="google/gemini-3-flash-preview",
    )
    assert result == "google/gemini-3-flash-preview"


def test_hard_pressure_keeps_cloud_when_already_cloud():
    """< 2 GB free + preferred уже cloud → preferred сохраняется (нет дублирующего fallback)."""
    result = pas.pressure_aware_model_select(
        "google/gemini-3-pro-preview",
        candidate_models=[],
        free_gb_override=0.5,
    )
    assert result == "google/gemini-3-pro-preview"


def test_env_gate_off_bypasses_prefilter(monkeypatch):
    """KRAB_PRESSURE_AWARE_SELECTION=0 полностью обходит pre-filter."""
    monkeypatch.setenv("KRAB_PRESSURE_AWARE_SELECTION", "0")
    result = pas.pressure_aware_model_select(
        "local",
        candidate_models=[{"id": "local/tiny", "size_gb": 0.5}],
        free_gb_override=0.1,  # extreme pressure
        cloud_fallback="google/gemini-3-flash-preview",
    )
    # Несмотря на 0.1 GB free — preferred остаётся, потому что gate OFF
    assert result == "local"


def test_unknown_free_memory_skips_prefilter():
    """get_free_memory_gb returns None → pre-filter skip, preferred сохраняется."""
    with patch.object(pas, "get_free_memory_gb", return_value=None):
        result = pas.pressure_aware_model_select(
            "local",
            candidate_models=[{"id": "local/gemma-12b", "size_gb": 7.5}],
        )
    assert result == "local"


def test_fallback_records_metric_counter():
    """SOFT fallback инкрементирует prometheus counter."""
    from src.core.prometheus_metrics import _PRESSURE_AWARE_FALLBACK_COUNTER

    candidates = [
        {"id": "local/big", "size_gb": 12.0},
        {"id": "local/small", "size_gb": 1.5},
    ]
    pas.pressure_aware_model_select(
        "local/big",
        candidate_models=candidates,
        free_gb_override=3.0,
    )
    # Один fallback зафиксирован
    keys = list(_PRESSURE_AWARE_FALLBACK_COUNTER.keys())
    assert len(keys) == 1
    from_m, to_m, reason = keys[0]
    assert from_m == "local/big"
    assert to_m == "local/small"
    assert reason == "soft_pressure"
    assert _PRESSURE_AWARE_FALLBACK_COUNTER[keys[0]] == 1


def test_size_bytes_fallback_when_no_size_gb():
    """Если в candidate только size_bytes — должен быть конвертирован в GB."""
    candidates = [
        {"id": "local/a", "size_bytes": 8 * (1024**3)},
        {"id": "local/b", "size_bytes": 2 * (1024**3)},
    ]
    result = pas.pressure_aware_model_select(
        "local/a",
        candidate_models=candidates,
        free_gb_override=3.0,
    )
    assert result == "local/b"


def test_get_free_memory_gb_returns_number():
    """Smoke: на dev-машине должен вернуть положительное число (psutil OK)."""
    val = pas.get_free_memory_gb()
    assert val is None or (isinstance(val, float) and val >= 0.0)
