# -*- coding: utf-8 -*-
"""
Тесты helper'а device-auth для Codex CLI.

Проверяем только детерминированный парсинг URL/кода, чтобы не поднимать
реальный интерактивный login flow внутри unit-тестов.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "codex_cli_device_login.py"
if not MODULE_PATH.exists():
    pytest.skip("scripts/codex_cli_device_login.py not available", allow_module_level=True)
SPEC = importlib.util.spec_from_file_location("codex_cli_device_login", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
try:
    SPEC.loader.exec_module(MODULE)
except (ImportError, ModuleNotFoundError, FileNotFoundError):
    pytest.skip(
        "scripts/codex_cli_device_login.py dependencies not available", allow_module_level=True
    )


def test_extract_device_url_reads_exact_codex_device_path() -> None:
    """Helper должен вытаскивать именно device-auth URL, а не любой auth.openai.com."""
    sample = """
    Welcome to Codex
    1. Open this link in your browser and sign in to your account
       https://auth.openai.com/codex/device
    """

    assert MODULE.extract_device_url(sample) == "https://auth.openai.com/codex/device"


def test_extract_device_code_ignores_ansi_noise() -> None:
    """Код должен парситься даже из ANSI-раскрашенного TTY-вывода."""
    sample = (
        "\x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n"
        "2. Enter this one-time code \x1b[90m(expires in 15 minutes)\x1b[0m\n"
        "   \x1b[94m5QEW-L71LS\x1b[0m\n"
    )

    assert MODULE.extract_device_code(sample) == "5QEW-L71LS"
