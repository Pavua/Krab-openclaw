# -*- coding: utf-8 -*-
"""
Tests Phase 2 Wave 19+20: src/handlers/commands/info_commands.py.

Проверяем:
  - Все handlers и helpers re-exported из command_handlers идентичны новому модулю.
  - Helper-функции (_do_convert, _parse_color_input, _emoji_search,
    _parse_define_args, _parse_currency_args) работают корректно.
  - Dual-namespace lookup: monkeypatch на command_handlers видим из info_commands.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers import command_handlers as ch
from src.handlers.commands import info_commands as ic


def test_module_re_exports_identity():
    """Все handlers/helpers re-exported в command_handlers совпадают с info_commands."""
    pairs = [
        "handle_weather",
        "handle_define",
        "handle_urban",
        "handle_currency",
        "handle_convert",
        "handle_color",
        "handle_emoji",
        "handle_news",
        "_fetch_wttr",
        "_parse_define_args",
        "_build_define_prompt",
        "_parse_currency_args",
        "fetch_exchange_rate",
        "_fmt_currency",
        "_normalize_unit",
        "_do_convert",
        "_format_convert_result",
        "_parse_color_input",
        "_rgb_to_hex",
        "_rgb_to_hsl",
        "_emoji_search",
    ]
    for name in pairs:
        assert getattr(ch, name) is getattr(ic, name), f"{name} mismatch"


def test_constants_re_exported():
    """Constants должны быть доступны через оба namespace."""
    assert ch._CSS_NAMED_COLORS is ic._CSS_NAMED_COLORS
    assert ch._EMOJI_DB is ic._EMOJI_DB
    assert ch._NEWS_LANG_MAP is ic._NEWS_LANG_MAP
    assert ch._CONVERT_UNITS is ic._CONVERT_UNITS
    assert ch._WTTR_URL == ic._WTTR_URL


def test_dual_namespace_monkeypatch_visible(monkeypatch):
    """Patch на command_handlers должен видеться через _ch_attr lookup."""
    # Подменяем _NEWS_LANG_MAP на command_handlers и проверяем что info_commands
    # видит изменение через _ch_attr.
    custom = {"xx": "на тестовом"}
    monkeypatch.setattr(ch, "_NEWS_LANG_MAP", custom)
    resolved = ic._ch_attr("_NEWS_LANG_MAP", ic._NEWS_LANG_MAP)
    assert resolved is custom


# ---------------------------------------------------------------------------
# Helper-функции — smoke
# ---------------------------------------------------------------------------


def test_parse_define_args_default():
    assert ic._parse_define_args("Python") == ("Python", "ru", False)


def test_parse_define_args_en():
    assert ic._parse_define_args("Python en") == ("Python", "en", False)


def test_parse_define_args_detailed():
    assert ic._parse_define_args("Python подробно") == ("Python", "ru", True)


def test_emoji_search_exact_match():
    matches = ic._emoji_search("fire")
    assert "🔥" in matches


def test_emoji_search_no_match():
    assert ic._emoji_search("xyzzyzzyzzy") == []


def test_do_convert_meters_to_km():
    assert ic._do_convert(1000, "m", "km") == pytest.approx(1.0)


def test_do_convert_temperature():
    assert ic._do_convert(0, "c", "f") == pytest.approx(32.0)
    assert ic._do_convert(100, "c", "k") == pytest.approx(373.15)


def test_do_convert_incompatible_raises():
    with pytest.raises(ValueError):
        ic._do_convert(10, "kg", "m")


def test_parse_color_named():
    assert ic._parse_color_input("red") == (255, 0, 0)


def test_parse_color_hex():
    assert ic._parse_color_input("#FF5733") == (255, 87, 51)


def test_parse_color_invalid():
    assert ic._parse_color_input("notacolor") is None


def test_fmt_currency():
    assert ic._fmt_currency(1234.5678) == "1,234.57"
    assert ic._fmt_currency(0.5) == "0.5"


def test_parse_currency_args_minimum():
    assert ic._parse_currency_args("100 USD") == (100.0, "USD", None)


def test_parse_currency_args_full():
    assert ic._parse_currency_args("50.5 EUR USD") == (50.5, "EUR", "USD")


def test_parse_currency_args_negative_raises():
    with pytest.raises(UserInputError):
        ic._parse_currency_args("-10 USD")


# ---------------------------------------------------------------------------
# Async smoke — handle_emoji empty/no-match (без сетевых вызовов)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_emoji_no_args_shows_help():
    bot = MagicMock()
    bot._get_command_args.return_value = ""
    msg = MagicMock()
    msg.reply = AsyncMock()
    await ic.handle_emoji(bot, msg)
    msg.reply.assert_awaited_once()
    text = msg.reply.await_args.args[0]
    assert "!emoji" in text


@pytest.mark.asyncio
async def test_handle_emoji_no_match():
    bot = MagicMock()
    bot._get_command_args.return_value = "xyzzyzzyzzy"
    msg = MagicMock()
    msg.reply = AsyncMock()
    await ic.handle_emoji(bot, msg)
    msg.reply.assert_awaited_once()
    text = msg.reply.await_args.args[0]
    assert "не найдены" in text
