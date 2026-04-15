# -*- coding: utf-8 -*-
"""
Тесты для !color — конвертер цветов (HEX ↔ RGB ↔ HSL + CSS named colors).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import (  # noqa: PLC2701
    _CSS_NAMED_COLORS,
    _parse_color_input,
    _rgb_to_hex,
    _rgb_to_hsl,
    handle_color,
)

# ---------------------------------------------------------------------------
# _rgb_to_hsl — юнит-тесты конвертации
# ---------------------------------------------------------------------------


class TestRgbToHsl:
    """Проверяет корректность конвертации RGB → HSL."""

    def test_red(self) -> None:
        h, s, lum = _rgb_to_hsl(255, 0, 0)
        assert h == 0
        assert s == 100
        assert lum == 50

    def test_green(self) -> None:
        h, s, lum = _rgb_to_hsl(0, 128, 0)
        assert h == 120
        assert s == 100
        assert lum == 25

    def test_blue(self) -> None:
        h, s, lum = _rgb_to_hsl(0, 0, 255)
        assert h == 240
        assert s == 100
        assert lum == 50

    def test_white(self) -> None:
        h, s, lum = _rgb_to_hsl(255, 255, 255)
        assert s == 0
        assert lum == 100

    def test_black(self) -> None:
        h, s, lum = _rgb_to_hsl(0, 0, 0)
        assert s == 0
        assert lum == 0

    def test_gray(self) -> None:
        h, s, lum = _rgb_to_hsl(128, 128, 128)
        assert s == 0
        # Светлота ~50%
        assert 49 <= lum <= 51

    def test_ff5733(self) -> None:
        """#FF5733 → H≈11°, S=100%, L=60%."""
        h, s, lum = _rgb_to_hsl(255, 87, 51)
        assert h == 11
        assert s == 100
        assert lum == 60

    def test_tomato(self) -> None:
        """rgb(255, 99, 71) — известный CSS цвет tomato."""
        h, s, lum = _rgb_to_hsl(255, 99, 71)
        # tomato: H≈9°, S=100%, L=64%
        assert 8 <= h <= 10
        assert s == 100

    def test_cyan(self) -> None:
        h, s, lum = _rgb_to_hsl(0, 255, 255)
        assert h == 180
        assert s == 100
        assert lum == 50

    def test_returns_integers(self) -> None:
        """Все три значения должны быть целыми числами."""
        result = _rgb_to_hsl(123, 45, 200)
        assert all(isinstance(v, int) for v in result)


# ---------------------------------------------------------------------------
# _parse_color_input — парсинг форматов
# ---------------------------------------------------------------------------


class TestParseColorInputHex:
    """Парсинг HEX форматов."""

    def test_hex6_uppercase(self) -> None:
        assert _parse_color_input("#FF5733") == (255, 87, 51)

    def test_hex6_lowercase(self) -> None:
        assert _parse_color_input("#ff5733") == (255, 87, 51)

    def test_hex6_mixed_case(self) -> None:
        assert _parse_color_input("#Ff5733") == (255, 87, 51)

    def test_hex3_expands(self) -> None:
        """#F57 должен раскрыться в #FF5577."""
        assert _parse_color_input("#F57") == (0xFF, 0x55, 0x77)

    def test_hex3_black(self) -> None:
        assert _parse_color_input("#000") == (0, 0, 0)

    def test_hex3_white(self) -> None:
        assert _parse_color_input("#FFF") == (255, 255, 255)

    def test_hex6_red(self) -> None:
        assert _parse_color_input("#FF0000") == (255, 0, 0)

    def test_hex6_green(self) -> None:
        assert _parse_color_input("#00FF00") == (0, 255, 0)

    def test_hex6_blue(self) -> None:
        assert _parse_color_input("#0000FF") == (0, 0, 255)

    def test_hex_with_leading_space(self) -> None:
        assert _parse_color_input("  #FF0000  ") == (255, 0, 0)

    def test_invalid_hex_5_chars(self) -> None:
        assert _parse_color_input("#FFFFF") is None

    def test_invalid_hex_no_hash(self) -> None:
        assert _parse_color_input("FF0000") is None

    def test_invalid_hex_chars(self) -> None:
        assert _parse_color_input("#GGGGGG") is None


class TestParseColorInputRgb:
    """Парсинг rgb(...) формата."""

    def test_rgb_no_spaces(self) -> None:
        assert _parse_color_input("rgb(255,87,51)") == (255, 87, 51)

    def test_rgb_with_spaces(self) -> None:
        assert _parse_color_input("rgb(255, 87, 51)") == (255, 87, 51)

    def test_rgb_uppercase_prefix(self) -> None:
        assert _parse_color_input("RGB(255, 0, 0)") == (255, 0, 0)

    def test_rgb_zeros(self) -> None:
        assert _parse_color_input("rgb(0,0,0)") == (0, 0, 0)

    def test_rgb_max(self) -> None:
        assert _parse_color_input("rgb(255,255,255)") == (255, 255, 255)

    def test_rgb_out_of_range(self) -> None:
        """Значения > 255 должны вернуть None."""
        assert _parse_color_input("rgb(256, 0, 0)") is None

    def test_rgb_negative_invalid(self) -> None:
        """Отрицательные значения не соответствуют паттерну (только цифры \\d)."""
        assert _parse_color_input("rgb(-1, 0, 0)") is None

    def test_rgb_mixed_spacing(self) -> None:
        assert _parse_color_input("rgb(10 , 20 , 30)") == (10, 20, 30)


class TestParseColorInputNamed:
    """Парсинг CSS named colors."""

    def test_red(self) -> None:
        assert _parse_color_input("red") == (255, 0, 0)

    def test_blue(self) -> None:
        assert _parse_color_input("blue") == (0, 0, 255)

    def test_green(self) -> None:
        assert _parse_color_input("green") == (0, 128, 0)

    def test_tomato(self) -> None:
        assert _parse_color_input("tomato") == (255, 99, 71)

    def test_case_insensitive(self) -> None:
        assert _parse_color_input("RED") == (255, 0, 0)
        assert _parse_color_input("Red") == (255, 0, 0)
        assert _parse_color_input("rEd") == (255, 0, 0)

    def test_coral(self) -> None:
        assert _parse_color_input("coral") == (255, 127, 80)

    def test_navy(self) -> None:
        assert _parse_color_input("navy") == (0, 0, 128)

    def test_white(self) -> None:
        assert _parse_color_input("white") == (255, 255, 255)

    def test_black(self) -> None:
        assert _parse_color_input("black") == (0, 0, 0)

    def test_rebeccapurple(self) -> None:
        assert _parse_color_input("rebeccapurple") == (102, 51, 153)

    def test_unknown_name(self) -> None:
        assert _parse_color_input("notacolor") is None

    def test_completely_invalid(self) -> None:
        assert _parse_color_input("xyz123") is None

    def test_empty_string(self) -> None:
        assert _parse_color_input("") is None


# ---------------------------------------------------------------------------
# _rgb_to_hex — юнит-тесты
# ---------------------------------------------------------------------------


class TestRgbToHex:
    """Проверяет конвертацию RGB → HEX."""

    def test_red(self) -> None:
        assert _rgb_to_hex(255, 0, 0) == "#FF0000"

    def test_green(self) -> None:
        assert _rgb_to_hex(0, 255, 0) == "#00FF00"

    def test_blue(self) -> None:
        assert _rgb_to_hex(0, 0, 255) == "#0000FF"

    def test_black(self) -> None:
        assert _rgb_to_hex(0, 0, 0) == "#000000"

    def test_white(self) -> None:
        assert _rgb_to_hex(255, 255, 255) == "#FFFFFF"

    def test_ff5733(self) -> None:
        assert _rgb_to_hex(255, 87, 51) == "#FF5733"

    def test_uppercase(self) -> None:
        """HEX всегда в верхнем регистре."""
        result = _rgb_to_hex(171, 205, 239)
        assert result == result.upper()

    def test_padding_single_digit(self) -> None:
        """Однозначные компоненты паддятся нулём: rgb(1,2,3) → #010203."""
        assert _rgb_to_hex(1, 2, 3) == "#010203"


# ---------------------------------------------------------------------------
# _CSS_NAMED_COLORS — проверка справочника
# ---------------------------------------------------------------------------


class TestCssNamedColors:
    """Проверяет справочник CSS named colors."""

    def test_has_140_entries(self) -> None:
        """Стандарт CSS3 определяет 140 named colors (включая алиасы grey/gray)."""
        assert len(_CSS_NAMED_COLORS) >= 140

    def test_all_values_in_range(self) -> None:
        """Все RGB значения должны быть в диапазоне 0–255."""
        for name, (r, g, b) in _CSS_NAMED_COLORS.items():
            assert 0 <= r <= 255, f"{name}: r={r} out of range"
            assert 0 <= g <= 255, f"{name}: g={g} out of range"
            assert 0 <= b <= 255, f"{name}: b={b} out of range"

    def test_grey_gray_aliases(self) -> None:
        """grey и gray — одинаковые цвета."""
        assert _CSS_NAMED_COLORS["grey"] == _CSS_NAMED_COLORS["gray"]

    def test_cyan_aqua_same(self) -> None:
        """cyan и aqua — один цвет."""
        assert _CSS_NAMED_COLORS["cyan"] == _CSS_NAMED_COLORS["aqua"]

    def test_fuchsia_magenta_same(self) -> None:
        """fuchsia и magenta — один цвет."""
        assert _CSS_NAMED_COLORS["fuchsia"] == _CSS_NAMED_COLORS["magenta"]

    def test_common_colors_present(self) -> None:
        """Ключевые CSS цвета присутствуют."""
        for name in ("red", "green", "blue", "white", "black", "yellow", "orange", "purple"):
            assert name in _CSS_NAMED_COLORS, f"Missing: {name}"


# ---------------------------------------------------------------------------
# handle_color — Telegram handler тесты
# ---------------------------------------------------------------------------


def _make_bot(color_arg: str) -> SimpleNamespace:
    return SimpleNamespace(_get_command_args=lambda msg: color_arg)


def _make_message() -> SimpleNamespace:
    return SimpleNamespace(reply=AsyncMock(), chat=SimpleNamespace(id=123))


@pytest.mark.asyncio
async def test_handle_color_hex_gives_rgb_and_hsl() -> None:
    """!color #FF5733 → показывает RGB и HSL."""
    bot = _make_bot("#FF5733")
    msg = _make_message()
    await handle_color(bot, msg)
    msg.reply.assert_awaited_once()
    text: str = msg.reply.call_args[0][0]
    assert "rgb(255, 87, 51)" in text
    assert "hsl(" in text
    assert "HSL" in text


@pytest.mark.asyncio
async def test_handle_color_hex_no_redundant_hex() -> None:
    """При HEX вводе HEX снова не выводится."""
    bot = _make_bot("#FF0000")
    msg = _make_message()
    await handle_color(bot, msg)
    text: str = msg.reply.call_args[0][0]
    # Ожидаем RGB и HSL, но не повторный HEX
    assert "RGB" in text
    assert "HSL" in text
    # HEX не должен дублироваться в теле (исходный в заголовке — OK)
    assert text.count("HEX") == 0


@pytest.mark.asyncio
async def test_handle_color_rgb_gives_hex_and_hsl() -> None:
    """!color rgb(255,87,51) → показывает HEX и HSL."""
    bot = _make_bot("rgb(255,87,51)")
    msg = _make_message()
    await handle_color(bot, msg)
    text: str = msg.reply.call_args[0][0]
    assert "#FF5733" in text
    assert "hsl(" in text


@pytest.mark.asyncio
async def test_handle_color_named_gives_hex_rgb_hsl() -> None:
    """!color red → показывает HEX, RGB и HSL."""
    bot = _make_bot("red")
    msg = _make_message()
    await handle_color(bot, msg)
    text: str = msg.reply.call_args[0][0]
    assert "#FF0000" in text
    assert "rgb(255, 0, 0)" in text
    assert "hsl(" in text


@pytest.mark.asyncio
async def test_handle_color_named_tomato() -> None:
    """!color tomato → корректные значения."""
    bot = _make_bot("tomato")
    msg = _make_message()
    await handle_color(bot, msg)
    text: str = msg.reply.call_args[0][0]
    assert "#FF6347" in text
    assert "rgb(255, 99, 71)" in text


@pytest.mark.asyncio
async def test_handle_color_hex3_expanded() -> None:
    """!color #F00 → раскрывается до #FF0000."""
    bot = _make_bot("#F00")
    msg = _make_message()
    await handle_color(bot, msg)
    text: str = msg.reply.call_args[0][0]
    assert "rgb(255, 0, 0)" in text


@pytest.mark.asyncio
async def test_handle_color_empty_raises_user_error() -> None:
    """Пустой аргумент → UserInputError с подсказкой."""
    bot = _make_bot("")
    msg = _make_message()
    with pytest.raises(UserInputError) as exc_info:
        await handle_color(bot, msg)
    assert "!color" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_handle_color_invalid_raises_user_error() -> None:
    """Нераспознанный аргумент → UserInputError."""
    bot = _make_bot("notacolor")
    msg = _make_message()
    with pytest.raises(UserInputError) as exc_info:
        await handle_color(bot, msg)
    assert "notacolor" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_handle_color_rgb_out_of_range_raises() -> None:
    """rgb(256, 0, 0) — вне диапазона → UserInputError."""
    bot = _make_bot("rgb(256, 0, 0)")
    msg = _make_message()
    with pytest.raises(UserInputError):
        await handle_color(bot, msg)


@pytest.mark.asyncio
async def test_handle_color_blue() -> None:
    """!color blue → корректный ответ."""
    bot = _make_bot("blue")
    msg = _make_message()
    await handle_color(bot, msg)
    text: str = msg.reply.call_args[0][0]
    assert "#0000FF" in text
    assert "rgb(0, 0, 255)" in text


@pytest.mark.asyncio
async def test_handle_color_case_insensitive_named() -> None:
    """CSS named colors нечувствительны к регистру."""
    bot = _make_bot("RED")
    msg = _make_message()
    await handle_color(bot, msg)
    text: str = msg.reply.call_args[0][0]
    assert "#FF0000" in text


@pytest.mark.asyncio
async def test_handle_color_hsl_values_in_output() -> None:
    """HSL значения выводятся с градусами и процентами."""
    bot = _make_bot("#FF0000")
    msg = _make_message()
    await handle_color(bot, msg)
    text: str = msg.reply.call_args[0][0]
    # red: hsl(0°, 100%, 50%)
    assert "°" in text
    assert "%" in text


@pytest.mark.asyncio
async def test_handle_color_white() -> None:
    """!color white → белый цвет."""
    bot = _make_bot("white")
    msg = _make_message()
    await handle_color(bot, msg)
    text: str = msg.reply.call_args[0][0]
    assert "#FFFFFF" in text
    assert "rgb(255, 255, 255)" in text


@pytest.mark.asyncio
async def test_handle_color_black() -> None:
    """!color black → чёрный цвет."""
    bot = _make_bot("black")
    msg = _make_message()
    await handle_color(bot, msg)
    text: str = msg.reply.call_args[0][0]
    assert "#000000" in text
    assert "rgb(0, 0, 0)" in text


@pytest.mark.asyncio
async def test_handle_color_hex_lowercase_input() -> None:
    """Hex в нижнем регистре тоже работает."""
    bot = _make_bot("#ff5733")
    msg = _make_message()
    await handle_color(bot, msg)
    text: str = msg.reply.call_args[0][0]
    assert "rgb(255, 87, 51)" in text


@pytest.mark.asyncio
async def test_handle_color_rgb_with_spaces() -> None:
    """rgb со пробелами корректно парсится."""
    bot = _make_bot("rgb(255, 87, 51)")
    msg = _make_message()
    await handle_color(bot, msg)
    text: str = msg.reply.call_args[0][0]
    assert "#FF5733" in text
