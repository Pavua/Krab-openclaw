# -*- coding: utf-8 -*-
"""
Тесты для extracted text_utils domain — Phase 2 (Session 27).

Проверяют:
1. Хендлеры доступны через src.handlers.commands.text_utils.
2. Re-exports через src.handlers.command_handlers сохранились.
3. Helpers (safe_calc, _b64_*, _parse_sed_expr, _build_diff_output, _format_regex_result)
   работают idempotently после extraction.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.exceptions import UserInputError

# Прямой импорт из нового домена
from src.handlers.commands.text_utils import (
    _b64_decode,
    _b64_encode,
    _b64_is_valid,
    _build_diff_output,
    _format_regex_result,
    _parse_sed_expr,
    handle_b64,
    handle_calc,
    handle_hash,
    handle_json,
    handle_len,
    handle_rand,
    safe_calc,
)

# ---------------------------------------------------------------------------
# Module integrity / re-export verification
# ---------------------------------------------------------------------------


class TestReExports:
    """API stability: command_handlers re-exports text_utils handlers/helpers."""

    def test_command_handlers_reexports_handlers(self) -> None:
        from src.handlers import command_handlers as ch

        assert ch.handle_calc is handle_calc
        assert ch.handle_b64 is handle_b64
        assert ch.handle_hash is handle_hash
        assert ch.handle_len is handle_len
        assert ch.handle_json is handle_json
        assert ch.handle_rand is handle_rand

    def test_command_handlers_reexports_helpers(self) -> None:
        from src.handlers import command_handlers as ch

        assert ch.safe_calc is safe_calc
        assert ch._b64_encode is _b64_encode
        assert ch._b64_decode is _b64_decode
        assert ch._parse_sed_expr is _parse_sed_expr
        assert ch._build_diff_output is _build_diff_output
        assert ch._format_regex_result is _format_regex_result


# ---------------------------------------------------------------------------
# Pure helpers — fast unit tests
# ---------------------------------------------------------------------------


class TestSafeCalc:
    def test_basic_arithmetic(self) -> None:
        assert safe_calc("2+2") == 4
        assert safe_calc("10-3") == 7
        assert safe_calc("3*4") == 12

    def test_div_by_zero_raises(self) -> None:
        with pytest.raises(UserInputError):
            safe_calc("1/0")

    def test_empty_raises(self) -> None:
        with pytest.raises(UserInputError):
            safe_calc("")

    def test_too_long_raises(self) -> None:
        with pytest.raises(UserInputError):
            safe_calc("1+" * 200)


class TestB64Helpers:
    def test_encode_decode_roundtrip(self) -> None:
        assert _b64_decode(_b64_encode("hello мир")) == "hello мир"

    def test_is_valid_rejects_garbage(self) -> None:
        assert _b64_is_valid("!!!not base64!!!") is False

    def test_is_valid_accepts_padded(self) -> None:
        assert _b64_is_valid(_b64_encode("test")) is True


class TestParseSedExpr:
    def test_simple_replacement(self) -> None:
        compiled, replacement, count = _parse_sed_expr("s/foo/bar/")
        assert compiled.pattern == "foo"
        assert replacement == "bar"
        assert count == 1  # default = first match

    def test_global_flag(self) -> None:
        _compiled, _replacement, count = _parse_sed_expr("s/a/b/g")
        assert count == 0  # 0 = all in re.sub

    def test_invalid_format_raises(self) -> None:
        with pytest.raises(ValueError):
            _parse_sed_expr("bogus")


class TestBuildDiffOutput:
    def test_identical_returns_empty(self) -> None:
        assert _build_diff_output("abc\n", "abc\n").strip() == ""

    def test_change_marks_lines(self) -> None:
        out = _build_diff_output("old\n", "new\n")
        assert "-old" in out
        assert "+new" in out


class TestFormatRegexResult:
    def test_no_match(self) -> None:
        result = _format_regex_result(r"\d+", "abc")
        assert "Совпадений не найдено" in result

    def test_with_matches(self) -> None:
        result = _format_regex_result(r"\d+", "abc 123 def 456")
        assert "Matches: 2" in result


# ---------------------------------------------------------------------------
# Async handlers — happy-path smoke tests
# ---------------------------------------------------------------------------


def _make_message(args: str = "", reply_text: str | None = None) -> SimpleNamespace:
    """Создаёт mock Message для handlers."""
    reply = None
    if reply_text is not None:
        reply = SimpleNamespace(text=reply_text, caption=None)

    return SimpleNamespace(
        text=args,
        command=["cmd"] + args.split() if args else ["cmd"],
        reply_to_message=reply,
        reply=AsyncMock(),
    )


def _make_bot(args: str) -> SimpleNamespace:
    """Mock bot с _get_command_args."""
    bot = SimpleNamespace()
    bot._get_command_args = lambda _msg: args
    return bot


class TestHandleCalcAsync:
    @pytest.mark.asyncio
    async def test_simple_expression(self) -> None:
        msg = _make_message()
        bot = _make_bot("2+2")
        await handle_calc(bot, msg)
        msg.reply.assert_called_once()
        # Result should contain "= 4"
        assert "= 4" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_empty_raises(self) -> None:
        msg = _make_message()
        bot = _make_bot("")
        with pytest.raises(UserInputError):
            await handle_calc(bot, msg)


class TestHandleB64Async:
    @pytest.mark.asyncio
    async def test_encode_explicit(self) -> None:
        msg = _make_message()
        bot = _make_bot("encode hello")
        await handle_b64(bot, msg)
        reply = msg.reply.call_args[0][0]
        assert "encode" in reply.lower()
        assert _b64_encode("hello") in reply


class TestHandleHashAsync:
    @pytest.mark.asyncio
    async def test_md5_only(self) -> None:
        msg = _make_message()
        bot = _make_bot("md5 hello")
        await handle_hash(bot, msg)
        reply = msg.reply.call_args[0][0]
        # md5("hello") = 5d41402abc4b2a76b9719d911017c592
        assert "5d41402abc4b2a76b9719d911017c592" in reply

    @pytest.mark.asyncio
    async def test_no_args_no_reply_raises(self) -> None:
        msg = _make_message()
        bot = _make_bot("")
        with pytest.raises(UserInputError):
            await handle_hash(bot, msg)


class TestHandleLenAsync:
    @pytest.mark.asyncio
    async def test_simple_count(self) -> None:
        msg = _make_message()
        bot = _make_bot("Hello World")
        await handle_len(bot, msg)
        reply = msg.reply.call_args[0][0]
        assert "11" in reply  # 11 chars
        assert "2" in reply  # 2 words


class TestHandleJsonAsync:
    @pytest.mark.asyncio
    async def test_pretty_format(self) -> None:
        msg = _make_message()
        bot = _make_bot('{"a":1,"b":2}')
        await handle_json(bot, msg)
        reply = msg.reply.call_args[0][0]
        assert '"a": 1' in reply
        assert "```json" in reply

    @pytest.mark.asyncio
    async def test_validate_invalid(self) -> None:
        msg = _make_message()
        bot = _make_bot("validate {bogus}")
        await handle_json(bot, msg)
        reply = msg.reply.call_args[0][0]
        assert "невалид" in reply.lower()


class TestHandleRandAsync:
    @pytest.mark.asyncio
    async def test_default_range(self) -> None:
        msg = _make_message()
        bot = _make_bot("")
        await handle_rand(bot, msg)
        msg.reply.assert_called_once()

    @pytest.mark.asyncio
    async def test_coin(self) -> None:
        msg = _make_message()
        bot = _make_bot("coin")
        await handle_rand(bot, msg)
        reply = msg.reply.call_args[0][0]
        assert "Орёл" in reply or "Решка" in reply
