# -*- coding: utf-8 -*-
"""
Тесты для !eval — безопасный eval Python-выражений (safe_eval + handle_eval).
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_eval, safe_eval

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_bot(expr: str) -> SimpleNamespace:
    return SimpleNamespace(_get_command_args=lambda msg: expr)


def _make_message() -> SimpleNamespace:
    return SimpleNamespace(reply=AsyncMock(), chat=SimpleNamespace(id=123))


# ---------------------------------------------------------------------------
# safe_eval — арифметика и числа
# ---------------------------------------------------------------------------


class TestSafeEvalArithmetic:
    """Базовая арифметика."""

    def test_power_large(self) -> None:
        assert safe_eval("2**100") == 2**100

    def test_addition(self) -> None:
        assert safe_eval("2 + 3") == 5

    def test_subtraction(self) -> None:
        assert safe_eval("10 - 4") == 6

    def test_multiplication(self) -> None:
        assert safe_eval("6 * 7") == 42

    def test_division(self) -> None:
        assert safe_eval("10 / 4") == 2.5

    def test_floor_division(self) -> None:
        assert safe_eval("10 // 3") == 3

    def test_modulo(self) -> None:
        assert safe_eval("10 % 3") == 1

    def test_bit_and(self) -> None:
        assert safe_eval("0b1010 & 0b1100") == 0b1000

    def test_bit_or(self) -> None:
        assert safe_eval("0b1010 | 0b1100") == 0b1110

    def test_lshift(self) -> None:
        assert safe_eval("1 << 4") == 16

    def test_rshift(self) -> None:
        assert safe_eval("256 >> 3") == 32

    def test_unary_minus(self) -> None:
        assert safe_eval("-5") == -5

    def test_unary_not(self) -> None:
        assert safe_eval("not True") is False
        assert safe_eval("not False") is True


# ---------------------------------------------------------------------------
# safe_eval — строки
# ---------------------------------------------------------------------------


class TestSafeEvalStrings:
    """Строковые выражения."""

    def test_len_string(self) -> None:
        assert safe_eval('len("hello")') == 5

    def test_string_literal(self) -> None:
        assert safe_eval('"abc"') == "abc"

    def test_string_repeat(self) -> None:
        assert safe_eval('"ab" * 3') == "ababab"

    def test_string_concat(self) -> None:
        assert safe_eval('"foo" + "bar"') == "foobar"

    def test_str_upper(self) -> None:
        assert safe_eval('str.upper("hello")') == "HELLO"

    def test_ord(self) -> None:
        assert safe_eval('ord("A")') == 65

    def test_chr(self) -> None:
        assert safe_eval("chr(65)") == "A"


# ---------------------------------------------------------------------------
# safe_eval — списки и коллекции
# ---------------------------------------------------------------------------


class TestSafeEvalCollections:
    """Списки, кортежи, множества, словари."""

    def test_sorted_list(self) -> None:
        assert safe_eval("sorted([3, 1, 2])") == [1, 2, 3]

    def test_list_literal(self) -> None:
        assert safe_eval("[1, 2, 3]") == [1, 2, 3]

    def test_tuple_literal(self) -> None:
        assert safe_eval("(1, 2, 3)") == (1, 2, 3)

    def test_set_literal(self) -> None:
        assert safe_eval("{1, 2, 3}") == {1, 2, 3}

    def test_dict_literal(self) -> None:
        assert safe_eval('{"a": 1, "b": 2}') == {"a": 1, "b": 2}

    def test_len_list(self) -> None:
        assert safe_eval("len([1, 2, 3, 4])") == 4

    def test_sum_list(self) -> None:
        assert safe_eval("sum([1, 2, 3, 4, 5])") == 15

    def test_min_list(self) -> None:
        assert safe_eval("min([3, 1, 4])") == 1

    def test_max_list(self) -> None:
        assert safe_eval("max([3, 1, 4])") == 4

    def test_list_subscript(self) -> None:
        assert safe_eval("[10, 20, 30][1]") == 20

    def test_list_slice(self) -> None:
        assert safe_eval("[1, 2, 3, 4, 5][1:3]") == [2, 3]

    def test_reversed_list(self) -> None:
        assert list(safe_eval("reversed([1, 2, 3])")) == [3, 2, 1]  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# safe_eval — comprehensions
# ---------------------------------------------------------------------------


class TestSafeEvalComprehensions:
    """List/set/dict comprehensions."""

    def test_list_comp(self) -> None:
        assert safe_eval("[x**2 for x in range(5)]") == [0, 1, 4, 9, 16]

    def test_list_comp_with_filter(self) -> None:
        assert safe_eval("[x for x in range(10) if x % 2 == 0]") == [0, 2, 4, 6, 8]

    def test_set_comp(self) -> None:
        assert safe_eval("{x % 3 for x in range(9)}") == {0, 1, 2}

    def test_dict_comp(self) -> None:
        assert safe_eval("{str(k): k*2 for k in range(3)}") == {"0": 0, "1": 2, "2": 4}

    def test_generator_sum(self) -> None:
        assert safe_eval("sum(x for x in range(5))") == 10


# ---------------------------------------------------------------------------
# safe_eval — bool и сравнения
# ---------------------------------------------------------------------------


class TestSafeEvalBoolCompare:
    """Булевые выражения и сравнения."""

    def test_true(self) -> None:
        assert safe_eval("True") is True

    def test_false(self) -> None:
        assert safe_eval("False") is False

    def test_none(self) -> None:
        assert safe_eval("None") is None

    def test_comparison_eq_true(self) -> None:
        assert safe_eval("1 == 1") is True

    def test_comparison_eq_false(self) -> None:
        assert safe_eval("1 == 2") is False

    def test_comparison_lt(self) -> None:
        assert safe_eval("3 < 5") is True

    def test_comparison_gt(self) -> None:
        assert safe_eval("5 > 10") is False

    def test_bool_and_false(self) -> None:
        assert safe_eval("True and False") is False

    def test_bool_or_true(self) -> None:
        assert safe_eval("True or False") is True

    def test_in_operator_true(self) -> None:
        assert safe_eval("3 in [1, 2, 3]") is True

    def test_in_operator_false(self) -> None:
        assert safe_eval("5 in [1, 2, 3]") is False

    def test_ternary_true(self) -> None:
        assert safe_eval("1 if True else 2") == 1

    def test_ternary_false(self) -> None:
        assert safe_eval("1 if False else 2") == 2

    def test_all_true(self) -> None:
        assert safe_eval("all([True, True])") is True

    def test_any_mixed(self) -> None:
        assert safe_eval("any([False, True])") is True


# ---------------------------------------------------------------------------
# safe_eval — math-функции и константы
# ---------------------------------------------------------------------------


class TestSafeEvalMath:
    """Математические функции через whitelisted namespace."""

    def test_sqrt(self) -> None:
        assert safe_eval("sqrt(16)") == 4.0

    def test_sin_pi_over_2(self) -> None:
        assert abs(safe_eval("sin(pi/2)") - 1.0) < 1e-10  # type: ignore[operator]

    def test_cos_zero(self) -> None:
        assert abs(safe_eval("cos(0)") - 1.0) < 1e-10  # type: ignore[operator]

    def test_log_e(self) -> None:
        assert abs(safe_eval("log(e)") - 1.0) < 1e-10  # type: ignore[operator]

    def test_ceil(self) -> None:
        assert safe_eval("ceil(2.3)") == 3

    def test_floor(self) -> None:
        assert safe_eval("floor(2.7)") == 2

    def test_pi_constant(self) -> None:
        assert abs(safe_eval("pi") - math.pi) < 1e-12  # type: ignore[operator]

    def test_tau(self) -> None:
        assert abs(safe_eval("tau") - math.tau) < 1e-12  # type: ignore[operator]

    def test_e_constant(self) -> None:
        assert abs(safe_eval("e") - math.e) < 1e-12  # type: ignore[operator]


# ---------------------------------------------------------------------------
# safe_eval — встроенные функции
# ---------------------------------------------------------------------------


class TestSafeEvalBuiltins:
    """Whitelisted built-in функции."""

    def test_abs(self) -> None:
        assert safe_eval("abs(-42)") == 42

    def test_round(self) -> None:
        assert safe_eval("round(3.14159, 2)") == 3.14

    def test_hex(self) -> None:
        assert safe_eval("hex(255)") == "0xff"

    def test_bin(self) -> None:
        assert safe_eval("bin(10)") == "0b1010"

    def test_oct(self) -> None:
        assert safe_eval("oct(8)") == "0o10"

    def test_isinstance_true(self) -> None:
        assert safe_eval("isinstance(42, int)") is True

    def test_isinstance_false(self) -> None:
        assert safe_eval("isinstance(42, str)") is False

    def test_divmod(self) -> None:
        assert safe_eval("divmod(10, 3)") == (3, 1)

    def test_pow(self) -> None:
        assert safe_eval("pow(2, 10)") == 1024

    def test_repr(self) -> None:
        assert safe_eval("repr(42)") == "42"

    def test_hash_int(self) -> None:
        assert safe_eval("hash(42)") == hash(42)

    def test_type_int(self) -> None:
        assert safe_eval("type(42)") is int

    def test_complex(self) -> None:
        assert safe_eval("complex(1, 2)") == 1 + 2j

    def test_bytes_from_ints(self) -> None:
        assert safe_eval("bytes([65, 66])") == b"AB"

    def test_list_conversion(self) -> None:
        assert safe_eval("list((1, 2, 3))") == [1, 2, 3]

    def test_tuple_conversion(self) -> None:
        assert safe_eval("tuple([1, 2, 3])") == (1, 2, 3)

    def test_set_conversion(self) -> None:
        assert safe_eval("set([1, 2, 2, 3])") == {1, 2, 3}

    def test_str_conversion(self) -> None:
        assert safe_eval("str(42)") == "42"

    def test_int_conversion(self) -> None:
        assert safe_eval('int("42")') == 42

    def test_float_conversion(self) -> None:
        assert safe_eval('float("3.14")') == 3.14

    def test_bool_conversion(self) -> None:
        assert safe_eval("bool(0)") is False
        assert safe_eval("bool(1)") is True


# ---------------------------------------------------------------------------
# safe_eval — ошибки и безопасность
# ---------------------------------------------------------------------------


class TestSafeEvalErrors:
    """Валидация входных данных и отклонение опасного кода."""

    def test_empty_expression(self) -> None:
        with pytest.raises(UserInputError):
            safe_eval("")

    def test_whitespace_only(self) -> None:
        with pytest.raises(UserInputError):
            safe_eval("   ")

    def test_too_long(self) -> None:
        with pytest.raises(UserInputError):
            safe_eval("a" * 501)

    def test_division_by_zero(self) -> None:
        with pytest.raises(UserInputError) as exc_info:
            safe_eval("1/0")
        assert "Деление на ноль" in exc_info.value.user_message

    def test_syntax_error(self) -> None:
        with pytest.raises(UserInputError):
            safe_eval("1 + + +")

    def test_import_statement_rejected(self) -> None:
        """import — statement, не expression — SyntaxError на этапе compile."""
        with pytest.raises((UserInputError, SyntaxError)):
            safe_eval("import os")

    def test_import_dunder_rejected(self) -> None:
        """__import__ — запрещённое имя."""
        with pytest.raises(UserInputError):
            safe_eval("__import__('os')")

    def test_exec_rejected(self) -> None:
        with pytest.raises(UserInputError):
            safe_eval("exec('x=1')")

    def test_eval_rejected(self) -> None:
        with pytest.raises(UserInputError):
            safe_eval("eval('1+1')")

    def test_open_rejected(self) -> None:
        with pytest.raises(UserInputError):
            safe_eval("open('/etc/passwd')")

    def test_dunder_attribute_rejected(self) -> None:
        """Доступ к __class__ через атрибут должен быть запрещён."""
        with pytest.raises(UserInputError):
            safe_eval("(1).__class__")

    def test_dunder_name_rejected(self) -> None:
        with pytest.raises(UserInputError):
            safe_eval("__builtins__")

    def test_globals_rejected(self) -> None:
        with pytest.raises(UserInputError):
            safe_eval("globals()")

    def test_locals_rejected(self) -> None:
        with pytest.raises(UserInputError):
            safe_eval("locals()")

    def test_compile_rejected(self) -> None:
        with pytest.raises(UserInputError):
            safe_eval("compile('x', '<>', 'eval')")

    def test_dir_rejected(self) -> None:
        with pytest.raises(UserInputError):
            safe_eval("dir()")

    def test_getattr_rejected(self) -> None:
        with pytest.raises(UserInputError):
            safe_eval("getattr(int, '__bases__')")

    def test_print_rejected(self) -> None:
        with pytest.raises(UserInputError):
            safe_eval("print('hello')")

    def test_input_rejected(self) -> None:
        with pytest.raises(UserInputError):
            safe_eval("input()")

    def test_assignment_rejected(self) -> None:
        """x = 1 — это statement, не expression."""
        with pytest.raises((UserInputError, SyntaxError)):
            safe_eval("x = 1")

    def test_lambda_rejected(self) -> None:
        """lambda — не в _EVAL_ALLOWED_NODES."""
        with pytest.raises(UserInputError):
            safe_eval("(lambda x: x)(42)")

    def test_unknown_name_raises(self) -> None:
        """Неизвестное имя — NameError при выполнении."""
        with pytest.raises(UserInputError):
            safe_eval("undefined_var_xyz")

    def test_moderate_list_ok(self) -> None:
        """Умеренно большой список — допустим."""
        result = safe_eval("[0] * 100")
        assert len(result) == 100  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# handle_eval — тесты Telegram-хендлера
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_eval_power() -> None:
    """2**100 возвращает правильный результат."""
    bot = _make_bot("2**100")
    msg = _make_message()
    await handle_eval(bot, msg)
    msg.reply.assert_awaited_once_with(f"= {repr(2**100)}")


@pytest.mark.asyncio
async def test_handle_eval_len_string() -> None:
    bot = _make_bot('len("hello")')
    msg = _make_message()
    await handle_eval(bot, msg)
    msg.reply.assert_awaited_once_with("= 5")


@pytest.mark.asyncio
async def test_handle_eval_sorted_list() -> None:
    bot = _make_bot("sorted([3, 1, 2])")
    msg = _make_message()
    await handle_eval(bot, msg)
    msg.reply.assert_awaited_once_with("= [1, 2, 3]")


@pytest.mark.asyncio
async def test_handle_eval_list_comp() -> None:
    bot = _make_bot("[x**2 for x in range(5)]")
    msg = _make_message()
    await handle_eval(bot, msg)
    msg.reply.assert_awaited_once_with("= [0, 1, 4, 9, 16]")


@pytest.mark.asyncio
async def test_handle_eval_empty_shows_help() -> None:
    """Пустой !eval возвращает справку (UserInputError)."""
    bot = _make_bot("")
    msg = _make_message()
    with pytest.raises(UserInputError) as exc_info:
        await handle_eval(bot, msg)
    assert "!eval" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_handle_eval_import_raises() -> None:
    """import — statement — UserInputError или SyntaxError."""
    bot = _make_bot("import os")
    msg = _make_message()
    with pytest.raises((UserInputError, SyntaxError)):
        await handle_eval(bot, msg)


@pytest.mark.asyncio
async def test_handle_eval_division_by_zero_raises() -> None:
    bot = _make_bot("1/0")
    msg = _make_message()
    with pytest.raises(UserInputError) as exc_info:
        await handle_eval(bot, msg)
    assert "Деление на ноль" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_handle_eval_string_result() -> None:
    """Строковый результат приходит с repr (в кавычках)."""
    bot = _make_bot('"hello"')
    msg = _make_message()
    await handle_eval(bot, msg)
    msg.reply.assert_awaited_once_with("= 'hello'")


@pytest.mark.asyncio
async def test_handle_eval_bool_result() -> None:
    bot = _make_bot("1 == 1")
    msg = _make_message()
    await handle_eval(bot, msg)
    msg.reply.assert_awaited_once_with("= True")


@pytest.mark.asyncio
async def test_handle_eval_none_result() -> None:
    bot = _make_bot("None")
    msg = _make_message()
    await handle_eval(bot, msg)
    msg.reply.assert_awaited_once_with("= None")


@pytest.mark.asyncio
async def test_handle_eval_long_result_truncated() -> None:
    """Результат длиннее 3000 символов — обрезается."""
    bot = _make_bot("list(range(10000))")
    msg = _make_message()
    await handle_eval(bot, msg)
    called_arg: str = msg.reply.call_args[0][0]
    assert called_arg.startswith("= ")
    assert len(called_arg) <= 3005  # "= " + 3000 + ellipsis


@pytest.mark.asyncio
async def test_handle_eval_dict_result() -> None:
    bot = _make_bot('{"a": 1}')
    msg = _make_message()
    await handle_eval(bot, msg)
    msg.reply.assert_awaited_once_with("= {'a': 1}")


@pytest.mark.asyncio
async def test_handle_eval_exec_rejected() -> None:
    bot = _make_bot("exec('x=1')")
    msg = _make_message()
    with pytest.raises(UserInputError):
        await handle_eval(bot, msg)


@pytest.mark.asyncio
async def test_handle_eval_open_rejected() -> None:
    bot = _make_bot("open('/etc/passwd')")
    msg = _make_message()
    with pytest.raises(UserInputError):
        await handle_eval(bot, msg)
