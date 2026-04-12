# -*- coding: utf-8 -*-
"""Тесты для src/core/model_config.py — константы и конфигурация моделей."""

from __future__ import annotations

from src.core.model_config import (
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_UNKNOWN_MODEL_SIZE_GB,
    FALLBACK_CHAIN_LOCAL,
    IDLE_UNLOAD_SEC,
    LM_LOAD_TIMEOUT_SEC,
    LM_LOAD_TTL,
    MAINTENANCE_INTERVAL_SEC,
    RAM_BUFFER_GB,
)


def test_fallback_chain_not_empty() -> None:
    assert len(FALLBACK_CHAIN_LOCAL) >= 1
    assert "local" in FALLBACK_CHAIN_LOCAL


def test_context_window_positive() -> None:
    assert DEFAULT_CONTEXT_WINDOW > 0
    assert isinstance(DEFAULT_CONTEXT_WINDOW, int)


def test_ram_buffer_reasonable() -> None:
    assert 0 < RAM_BUFFER_GB < 16


def test_model_size_default() -> None:
    assert DEFAULT_UNKNOWN_MODEL_SIZE_GB > 0


def test_lm_load_ttl() -> None:
    assert LM_LOAD_TTL == -1  # без авто-выгрузки


def test_timeouts_positive() -> None:
    assert LM_LOAD_TIMEOUT_SEC > 0
    assert MAINTENANCE_INTERVAL_SEC > 0
    assert IDLE_UNLOAD_SEC > 0
