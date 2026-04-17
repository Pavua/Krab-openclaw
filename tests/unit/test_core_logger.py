# -*- coding: utf-8 -*-
"""Тесты для src/core/logger.py — structlog setup и get_logger."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from src.core.logger import _resolve_log_file, get_logger, setup_logger


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


# --- _resolve_log_file env-branch coverage (lines 29-37) ---


def test_resolve_log_file_disabled_via_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """KRAB_LOG_FILE=none → возвращаем None (логирование в файл выключено)."""
    monkeypatch.setenv("KRAB_LOG_FILE", "none")
    assert _resolve_log_file() is None


def test_resolve_log_file_disabled_via_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """KRAB_LOG_FILE="" тоже означает отключено."""
    monkeypatch.setenv("KRAB_LOG_FILE", "")
    assert _resolve_log_file() is None


def test_resolve_log_file_disabled_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """NONE / None / none — регистр не важен."""
    monkeypatch.setenv("KRAB_LOG_FILE", "NONE")
    assert _resolve_log_file() is None


def test_resolve_log_file_custom_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """KRAB_LOG_FILE=/tmp/.../custom.log — возвращаем Path на этот файл."""
    custom = tmp_path / "custom.log"
    monkeypatch.setenv("KRAB_LOG_FILE", str(custom))
    result = _resolve_log_file()
    assert result == custom


def test_resolve_log_file_expands_tilde(monkeypatch: pytest.MonkeyPatch) -> None:
    """KRAB_LOG_FILE=~/foo.log — тильда должна быть раскрыта."""
    monkeypatch.setenv("KRAB_LOG_FILE", "~/test_krab.log")
    result = _resolve_log_file()
    assert result is not None
    assert "~" not in str(result)
    assert str(result).endswith("test_krab.log")


def test_resolve_log_file_default_from_runtime_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Без KRAB_LOG_FILE, но с KRAB_RUNTIME_STATE_DIR — default путь внутри него."""
    monkeypatch.delenv("KRAB_LOG_FILE", raising=False)
    monkeypatch.setenv("KRAB_RUNTIME_STATE_DIR", str(tmp_path))
    result = _resolve_log_file()
    assert result is not None
    assert str(result).startswith(str(tmp_path))
    assert result.name == "krab_main.log"


def test_resolve_log_file_default_home_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без env вообще — fallback на ~/.openclaw/krab_runtime_state/krab_main.log."""
    monkeypatch.delenv("KRAB_LOG_FILE", raising=False)
    monkeypatch.delenv("KRAB_RUNTIME_STATE_DIR", raising=False)
    result = _resolve_log_file()
    assert result is not None
    assert result.name == "krab_main.log"
    assert ".openclaw" in str(result)


# --- setup_logger body coverage (lines 47-87) ---


def test_setup_logger_creates_file_handler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """setup_logger() должен создать файл лога и добавить FileHandler в root."""
    log_file = tmp_path / "krab_main.log"
    monkeypatch.setenv("KRAB_LOG_FILE", str(log_file))
    setup_logger("INFO")
    # Проверяем, что файл (и его parent) создан
    assert log_file.parent.exists()
    # В root handlers должен быть хотя бы один FileHandler
    root = logging.getLogger()
    assert any(isinstance(h, logging.FileHandler) for h in root.handlers)


def test_setup_logger_with_disabled_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """При KRAB_LOG_FILE=none — только StreamHandler, без FileHandler."""
    monkeypatch.setenv("KRAB_LOG_FILE", "none")
    setup_logger("INFO")
    root = logging.getLogger()
    assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)
    # FileHandler не должен быть добавлен (т.к. log_file=None)
    file_handlers = [
        h for h in root.handlers if type(h).__name__ == "FileHandler"
    ]
    assert file_handlers == []


def test_setup_logger_respects_level(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """level='DEBUG' → root.level = DEBUG."""
    monkeypatch.setenv("KRAB_LOG_FILE", str(tmp_path / "debug.log"))
    setup_logger("DEBUG")
    assert logging.getLogger().level == logging.DEBUG


def test_setup_logger_invalid_level_falls_back_to_info(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Несуществующий level → INFO (getattr default)."""
    monkeypatch.setenv("KRAB_LOG_FILE", str(tmp_path / "bogus.log"))
    setup_logger("NOT_A_REAL_LEVEL")
    assert logging.getLogger().level == logging.INFO


def test_setup_logger_clears_prior_handlers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Повторный setup_logger не должен плодить дубликаты handlers."""
    monkeypatch.setenv("KRAB_LOG_FILE", str(tmp_path / "a.log"))
    setup_logger("INFO")
    first_count = len(logging.getLogger().handlers)
    monkeypatch.setenv("KRAB_LOG_FILE", str(tmp_path / "b.log"))
    setup_logger("INFO")
    second_count = len(logging.getLogger().handlers)
    # handlers должны быть переинициализированы, а не удвоены
    assert second_count == first_count
