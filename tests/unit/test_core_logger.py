# -*- coding: utf-8 -*-
"""Тесты для src/core/logger.py — structlog setup и get_logger."""
from __future__ import annotations

from src.core.logger import get_logger, setup_logger


def test_get_logger_returns_bound_logger() -> None:
    log = get_logger("test_module")
    assert log is not None
    assert hasattr(log, "info")
    assert hasattr(log, "warning")
    assert hasattr(log, "error")


def test_get_logger_none_name() -> None:
    log = get_logger(None)
    assert log is not None


def test_get_logger_empty_name() -> None:
    log = get_logger("")
    assert log is not None


def test_setup_logger_does_not_crash() -> None:
    # Вызов setup_logger не должен бросать исключений
    setup_logger(level="WARNING")
    log = get_logger("post_setup")
    assert log is not None
