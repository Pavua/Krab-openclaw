# -*- coding: utf-8 -*-
"""
Тесты для !calc — безопасного калькулятора (safe_calc + handle_calc).
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_calc, safe_calc

# ---------------------------------------------------------------------------
# safe_calc — unit-тесты вычислений
# ---------------------------------------------------------------------------


class TestSafeCalcBasic:
    """Базовая арифметика."""

    def test_addition(self) -> None:
        assert safe_calc("2+2") == 4

    def test_subtraction(self) -> None:
        assert safe_calc("10-3") == 7

    def test_multiplication(self) -> None:
        assert safe_calc("3*4") == 12

    def test_division(self) -> None:
        assert safe_calc("10/4") == 2.5

    def test_floor_division(self) -> None:
        assert safe_calc("10//3") == 3

    def test_modulo(self) -> None:
        assert safe_calc("10%3") == 1

    def test_power(self) -> None:
        assert safe_calc("2**10") == 1024

    def test_operator_precedence(self) -> None:
        """2+2*3 должно быть 8, а не 12."""
        assert safe_calc("2+2*3") == 8

    def test_parentheses(self) -> None:
        assert safe_calc("(2+2)*3") == 12

    def test_unary_minus(self) -> None:
        assert safe_calc("-5+10") == 5

    def test_unary_plus(self) -> None:
        assert safe_calc("+5") == 5

    def test_float_literal(self) -> None:
        assert safe_calc("1.5 + 0.5") == 2.0

    def test_whitespace_stripped(self) -> None:
        assert safe_calc("  4 + 4  ") == 8


class TestSafeCalcMathFunctions:
    """Математические функции."""

    def test_sqrt(self) -> None:
        assert safe_calc("sqrt(144)") == 12.0

    def test_sqrt_non_perfect(self) -> None:
        assert abs(safe_calc("sqrt(2)") - math.sqrt(2)) < 1e-12

    def test_sin_pi_over_2(self) -> None:
        assert abs(safe_calc("sin(pi/2)") - 1.0) < 1e-12

    def test_cos_zero(self) -> None:
        assert abs(safe_calc("cos(0)") - 1.0) < 1e-12

    def test_tan_pi_over_4(self) -> None:
        assert abs(safe_calc("tan(pi/4)") - 1.0) < 1e-12

    def test_log_e(self) -> None:
        assert abs(safe_calc("log(e)") - 1.0) < 1e-12

    def test_log2(self) -> None:
        assert abs(safe_calc("log2(8)") - 3.0) < 1e-12

    def test_log10(self) -> None:
        assert abs(safe_calc("log10(1000)") - 3.0) < 1e-12

    def test_abs_negative(self) -> None:
        assert safe_calc("abs(-42)") == 42

    def test_abs_positive(self) -> None:
        assert safe_calc("abs(7)") == 7

    def test_round_down(self) -> None:
        assert safe_calc("round(2.3)") == 2

    def test_round_up(self) -> None:
        assert safe_calc("round(2.7)") == 3

    def test_round_ndigits(self) -> None:
        assert safe_calc("round(3.14159, 2)") == 3.14


class TestSafeCalcConstants:
    """Математические константы."""

    def test_pi(self) -> None:
        assert safe_calc("pi") == math.pi

    def test_e(self) -> None:
        assert safe_calc("e") == math.e

    def test_pi_in_expression(self) -> None:
        result = safe_calc("2 * pi")
        assert abs(result - 2 * math.pi) < 1e-12


class TestSafeCalcComplex:
    """Составные выражения."""

    def test_nested_functions(self) -> None:
        # sqrt(sin(pi/2) * 4) = sqrt(4) = 2.0
        result = safe_calc("sqrt(sin(pi/2) * 4)")
        assert abs(result - 2.0) < 1e-12

    def test_mixed_ops_and_functions(self) -> None:
        # abs(-3) ** 2 + sqrt(16) = 9 + 4 = 13
        result = safe_calc("abs(-3)**2 + sqrt(16)")
        assert abs(result - 13.0) < 1e-12

    def test_large_power(self) -> None:
        assert safe_calc("10**6") == 1_000_000


class TestSafeCalcErrors:
    """Ошибки при некорректном вводе."""

    def test_empty_expression(self) -> None:
        with pytest.raises(UserInputError):
            safe_calc("")

    def test_whitespace_only(self) -> None:
        with pytest.raises(UserInputError):
            safe_calc("   ")

    def test_too_long(self) -> None:
        with pytest.raises(UserInputError):
            safe_calc("1+" * 101)

    def test_syntax_error(self) -> None:
        # "2+-2" — настоящая синтаксическая ошибка в выражении
        with pytest.raises(UserInputError):
            safe_calc("2 @ 2")  # @ — неизвестный оператор AST MatMult

    def test_division_by_zero(self) -> None:
        exc = pytest.raises(UserInputError, safe_calc, "1/0")
        assert "Деление на ноль" in exc.value.user_message

    def test_unknown_function(self) -> None:
        with pytest.raises(UserInputError):
            safe_calc("evil(42)")

    def test_unknown_variable(self) -> None:
        with pytest.raises(UserInputError):
            safe_calc("x + 1")

    def test_string_literal_rejected(self) -> None:
        with pytest.raises(UserInputError):
            safe_calc("'hello'")

    def test_import_rejected(self) -> None:
        """Попытка инъекции через __import__ должна вызвать ошибку."""
        with pytest.raises((UserInputError, SyntaxError)):
            safe_calc("__import__('os').system('ls')")

    def test_lambda_rejected(self) -> None:
        with pytest.raises((UserInputError, SyntaxError)):
            safe_calc("(lambda: 42)()")

    def test_attribute_access_rejected(self) -> None:
        with pytest.raises((UserInputError, SyntaxError)):
            safe_calc("math.sqrt(4)")

    def test_comparison_rejected(self) -> None:
        with pytest.raises((UserInputError, SyntaxError)):
            safe_calc("1 == 1")

    def test_boolean_keyword_rejected(self) -> None:
        """True/False — недопустимы (bool отклоняется явной проверкой)."""
        with pytest.raises(UserInputError):
            safe_calc("True + 1")

    def test_log_of_negative_raises(self) -> None:
        """log от отрицательного числа — ValueError из math."""
        with pytest.raises(UserInputError):
            safe_calc("log(-1)")

    def test_sqrt_of_negative_raises(self) -> None:
        """sqrt от отрицательного числа — ValueError из math."""
        with pytest.raises(UserInputError):
            safe_calc("sqrt(-1)")

    def test_keyword_args_rejected(self) -> None:
        with pytest.raises(UserInputError):
            safe_calc("abs(x=-5)")


# ---------------------------------------------------------------------------
# handle_calc — тесты Telegram-хендлера
# ---------------------------------------------------------------------------


def _make_bot(expr: str) -> SimpleNamespace:
    return SimpleNamespace(_get_command_args=lambda msg: expr)


def _make_message() -> SimpleNamespace:
    return SimpleNamespace(reply=AsyncMock(), chat=SimpleNamespace(id=123))


@pytest.mark.asyncio
async def test_handle_calc_simple_addition() -> None:
    bot = _make_bot("2+2")
    msg = _make_message()
    await handle_calc(bot, msg)
    msg.reply.assert_awaited_once_with("= 4")


@pytest.mark.asyncio
async def test_handle_calc_precedence() -> None:
    bot = _make_bot("2+2*3")
    msg = _make_message()
    await handle_calc(bot, msg)
    msg.reply.assert_awaited_once_with("= 8")


@pytest.mark.asyncio
async def test_handle_calc_sqrt() -> None:
    bot = _make_bot("sqrt(144)")
    msg = _make_message()
    await handle_calc(bot, msg)
    msg.reply.assert_awaited_once_with("= 12")


@pytest.mark.asyncio
async def test_handle_calc_sin_pi_over_2() -> None:
    bot = _make_bot("sin(pi/2)")
    msg = _make_message()
    await handle_calc(bot, msg)
    msg.reply.assert_awaited_once_with("= 1")


@pytest.mark.asyncio
async def test_handle_calc_float_result() -> None:
    """Нецелый результат отображается как float."""
    bot = _make_bot("1/3")
    msg = _make_message()
    await handle_calc(bot, msg)
    called_arg: str = msg.reply.call_args[0][0]
    assert called_arg.startswith("= ")
    # Результат — дробное число
    assert "." in called_arg or "3" in called_arg


@pytest.mark.asyncio
async def test_handle_calc_integer_float_formatted_without_dot() -> None:
    """Число вроде 12.0 отображается как '= 12', а не '= 12.0'."""
    bot = _make_bot("sqrt(144)")
    msg = _make_message()
    await handle_calc(bot, msg)
    msg.reply.assert_awaited_once_with("= 12")


@pytest.mark.asyncio
async def test_handle_calc_empty_raises() -> None:
    bot = _make_bot("")
    msg = _make_message()
    with pytest.raises(UserInputError):
        await handle_calc(bot, msg)


@pytest.mark.asyncio
async def test_handle_calc_division_by_zero_raises() -> None:
    bot = _make_bot("5/0")
    msg = _make_message()
    with pytest.raises(UserInputError) as exc_info:
        await handle_calc(bot, msg)
    assert "Деление на ноль" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_handle_calc_unknown_function_raises() -> None:
    bot = _make_bot("hack(1)")
    msg = _make_message()
    with pytest.raises(UserInputError):
        await handle_calc(bot, msg)


@pytest.mark.asyncio
async def test_handle_calc_pi_constant() -> None:
    bot = _make_bot("round(pi, 4)")
    msg = _make_message()
    await handle_calc(bot, msg)
    msg.reply.assert_awaited_once_with("= 3.1416")


@pytest.mark.asyncio
async def test_handle_calc_power() -> None:
    bot = _make_bot("2**10")
    msg = _make_message()
    await handle_calc(bot, msg)
    msg.reply.assert_awaited_once_with("= 1024")
