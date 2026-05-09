# -*- coding: utf-8 -*-
"""
Wave 44-A: тесты coercion для statsPeriod в MCP-инструментах krab_sentry_*.

Sentry SaaS принимает только {'', '24h', '14d'} на /issues/?statsPeriod=...
Любое другое значение → HTTP 400 "Invalid stats_period".

_coerce_stats_period нормализует невалидные значения к ближайшему допустимому,
логируя факт coercion. Этот тест фиксирует mapping table.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

_SERVER_PATH = Path(__file__).parents[2] / "mcp-servers" / "telegram" / "server.py"
assert _SERVER_PATH.exists(), f"server.py не найден: {_SERVER_PATH}"


@pytest.fixture(scope="module")
def coerce_fn():
    """Загружаем _coerce_stats_period напрямую из server.py с заглушками тяжёлых импортов."""
    # Минимальные env, без которых модуль не импортируется
    os.environ.setdefault("TELEGRAM_API_ID", "1")
    os.environ.setdefault("TELEGRAM_API_HASH", "x")
    spec = importlib.util.spec_from_file_location("_mcp_server_tg_w44a", _SERVER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Stubs для модулей которые могут потребовать сетевые/железные ресурсы
    fakes: dict[str, ModuleType] = {}
    sys.modules.update(fakes)
    spec.loader.exec_module(module)
    return module._coerce_stats_period


class TestCoerceStatsPeriod:
    """Mapping table для Wave 44-A coercion."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            # Валидные — keep as-is
            ("", ""),
            ("24h", "24h"),
            ("14d", "14d"),
            # Часы и минуты — все коротят до '24h'
            ("1h", "24h"),
            ("6h", "24h"),
            ("12h", "24h"),
            ("60m", "24h"),
            ("1m", "24h"),
            # Дни — 1d→24h, 2d+→14d
            ("1d", "24h"),
            ("2d", "14d"),
            ("7d", "14d"),
            ("30d", "14d"),
            ("90d", "14d"),
            ("365d", "14d"),
            # Непарсимое — sane default
            ("garbage", "24h"),
            ("xyz", "24h"),
        ],
    )
    def test_mapping(self, coerce_fn, raw: str, expected: str) -> None:
        assert coerce_fn(raw) == expected, f"{raw!r} → {coerce_fn(raw)!r}, expected {expected!r}"

    def test_idempotent_on_valid(self, coerce_fn) -> None:
        """Двойной coerce валидного значения не меняет результат."""
        for valid in ("", "24h", "14d"):
            assert coerce_fn(coerce_fn(valid)) == valid

    def test_returns_str_for_all_inputs(self, coerce_fn) -> None:
        """Никаких None/исключений — всегда str."""
        for inp in ("", "24h", "1h", "garbage", "100d", "0h"):
            result = coerce_fn(inp)
            assert isinstance(result, str)
            assert result in {"", "24h", "14d"}
