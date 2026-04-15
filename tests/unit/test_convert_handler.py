# -*- coding: utf-8 -*-
"""
Тесты !convert — конвертер единиц измерения.

Покрываем:
- чистые функции _do_convert, _normalize_unit, _format_convert_result
- handle_convert: корректные преобразования, формат ответа, ошибки
"""

from __future__ import annotations

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.handlers.command_handlers import (
    _do_convert,
    _normalize_unit,
    _format_convert_result,
    handle_convert,
    _CONVERT_HELP,
)


# ---------------------------------------------------------------------------
# _normalize_unit — нормализация строки единицы
# ---------------------------------------------------------------------------

class TestNormalizeUnit:
    def test_lowercase_passthrough(self):
        assert _normalize_unit("km") == "km"

    def test_uppercase_to_lower(self):
        assert _normalize_unit("KM") == "km"

    def test_alias_miles(self):
        assert _normalize_unit("miles") == "mi"

    def test_alias_celsius(self):
        assert _normalize_unit("celsius") == "c"

    def test_alias_fahrenheit(self):
        assert _normalize_unit("fahrenheit") == "f"

    def test_alias_kelvin(self):
        assert _normalize_unit("kelvin") == "k"

    def test_alias_pounds(self):
        assert _normalize_unit("lbs") == "lb"

    def test_alias_gallons(self):
        assert _normalize_unit("gallons") == "gal"

    def test_alias_liters(self):
        assert _normalize_unit("liters") == "l"

    def test_alias_feet(self):
        assert _normalize_unit("feet") == "ft"

    def test_alias_inches(self):
        assert _normalize_unit("inches") == "in"

    def test_alias_knots(self):
        assert _normalize_unit("knots") == "kn"

    def test_unknown_unit_passthrough(self):
        # Незнакомая единица возвращается как есть (lower)
        assert _normalize_unit("furlongs") == "furlongs"

    def test_degree_symbol_c(self):
        assert _normalize_unit("°c") == "c"

    def test_degree_symbol_f(self):
        assert _normalize_unit("°f") == "f"


# ---------------------------------------------------------------------------
# _format_convert_result — форматирование чисел
# ---------------------------------------------------------------------------

class TestFormatConvertResult:
    def test_integer_result(self):
        assert _format_convert_result(100.0) == "100"

    def test_zero(self):
        assert _format_convert_result(0.0) == "0"

    def test_float_trims_zeros(self):
        # 62.137119... должен форматироваться как 6 значащих цифр без хвостовых нулей
        result = _format_convert_result(62.137119223733395)
        assert "." in result  # это не целое

    def test_small_float(self):
        result = _format_convert_result(0.000123)
        assert result  # не пусто

    def test_large_integer(self):
        assert _format_convert_result(1000000.0) == "1000000"


# ---------------------------------------------------------------------------
# _do_convert — основная логика конвертации
# ---------------------------------------------------------------------------

class TestDoConvert:

    # Длина
    def test_km_to_mi(self):
        result = _do_convert(100, "km", "mi")
        assert abs(result - 62.1371) < 0.01

    def test_mi_to_km(self):
        result = _do_convert(1, "mi", "km")
        assert abs(result - 1.609344) < 0.001

    def test_m_to_ft(self):
        result = _do_convert(1, "m", "ft")
        assert abs(result - 3.28084) < 0.0001

    def test_ft_to_m(self):
        result = _do_convert(1, "ft", "m")
        assert abs(result - 0.3048) < 0.0001

    def test_cm_to_in(self):
        result = _do_convert(1, "cm", "in")
        assert abs(result - 0.393701) < 0.0001

    def test_in_to_cm(self):
        result = _do_convert(1, "in", "cm")
        assert abs(result - 2.54) < 0.001

    def test_km_to_m(self):
        result = _do_convert(5, "km", "m")
        assert abs(result - 5000) < 0.001

    def test_m_to_km(self):
        result = _do_convert(1000, "m", "km")
        assert abs(result - 1.0) < 0.0001

    def test_mm_to_cm(self):
        result = _do_convert(10, "mm", "cm")
        assert abs(result - 1.0) < 0.0001

    def test_yd_to_m(self):
        result = _do_convert(1, "yd", "m")
        assert abs(result - 0.9144) < 0.0001

    # Масса
    def test_kg_to_lb(self):
        result = _do_convert(5, "kg", "lb")
        assert abs(result - 11.0231) < 0.01

    def test_lb_to_kg(self):
        result = _do_convert(1, "lb", "kg")
        assert abs(result - 0.453592) < 0.0001

    def test_g_to_kg(self):
        result = _do_convert(1000, "g", "kg")
        assert abs(result - 1.0) < 0.0001

    def test_kg_to_g(self):
        result = _do_convert(1, "kg", "g")
        assert abs(result - 1000) < 0.001

    def test_oz_to_g(self):
        result = _do_convert(1, "oz", "g")
        assert abs(result - 28.35) < 0.1

    def test_lb_to_oz(self):
        result = _do_convert(1, "lb", "oz")
        assert abs(result - 16.0) < 0.1

    # Объём
    def test_l_to_gal(self):
        result = _do_convert(3.78541, "l", "gal")
        assert abs(result - 1.0) < 0.001

    def test_gal_to_l(self):
        result = _do_convert(1, "gal", "l")
        assert abs(result - 3.78541) < 0.001

    def test_ml_to_l(self):
        result = _do_convert(1000, "ml", "l")
        assert abs(result - 1.0) < 0.0001

    def test_l_to_ml(self):
        result = _do_convert(1, "l", "ml")
        assert abs(result - 1000) < 0.001

    def test_pt_to_l(self):
        result = _do_convert(1, "pt", "l")
        assert abs(result - 0.473176) < 0.001

    # Скорость
    def test_kmh_to_mph(self):
        result = _do_convert(100, "kmh", "mph")
        assert abs(result - 62.137) < 0.1

    def test_mph_to_kmh(self):
        result = _do_convert(60, "mph", "kmh")
        assert abs(result - 96.5606) < 0.1

    def test_ms_to_kmh(self):
        result = _do_convert(1, "ms", "kmh")
        assert abs(result - 3.6) < 0.01

    def test_kmh_to_ms(self):
        result = _do_convert(36, "kmh", "ms")
        assert abs(result - 10.0) < 0.01

    def test_kn_to_kmh(self):
        result = _do_convert(1, "kn", "kmh")
        assert abs(result - 1.852) < 0.01

    # Температура
    def test_c_to_f_zero(self):
        result = _do_convert(0, "C", "F")
        assert abs(result - 32.0) < 0.01

    def test_c_to_f_boiling(self):
        result = _do_convert(100, "C", "F")
        assert abs(result - 212.0) < 0.01

    def test_f_to_c_freezing(self):
        result = _do_convert(32, "F", "C")
        assert abs(result - 0.0) < 0.01

    def test_f_to_c_body_temp(self):
        result = _do_convert(72, "F", "C")
        assert abs(result - 22.2222) < 0.01

    def test_c_to_k(self):
        result = _do_convert(0, "C", "K")
        assert abs(result - 273.15) < 0.01

    def test_k_to_c(self):
        result = _do_convert(273.15, "K", "C")
        assert abs(result - 0.0) < 0.01

    def test_f_to_k(self):
        result = _do_convert(32, "F", "K")
        assert abs(result - 273.15) < 0.01

    def test_k_to_f(self):
        result = _do_convert(373.15, "K", "F")
        assert abs(result - 212.0) < 0.01

    def test_same_unit_identity(self):
        """Конвертация в ту же единицу должна вернуть исходное значение."""
        assert abs(_do_convert(42, "km", "km") - 42.0) < 0.0001
        assert abs(_do_convert(100, "kg", "kg") - 100.0) < 0.0001
        assert abs(_do_convert(37, "C", "C") - 37.0) < 0.0001

    def test_alias_input(self):
        """Алиасы должны нормально работать."""
        result = _do_convert(1, "miles", "km")
        assert abs(result - 1.609344) < 0.001

    def test_alias_pounds(self):
        result = _do_convert(1, "pounds", "kg")
        assert abs(result - 0.453592) < 0.0001

    def test_alias_gallons(self):
        result = _do_convert(1, "gallon", "l")
        assert abs(result - 3.78541) < 0.001

    # Ошибки
    def test_unknown_src_unit(self):
        with pytest.raises(ValueError, match="Неизвестная единица"):
            _do_convert(1, "furlongs", "km")

    def test_unknown_dst_unit(self):
        with pytest.raises(ValueError, match="Неизвестная единица"):
            _do_convert(1, "km", "furlongs")

    def test_incompatible_units_length_mass(self):
        with pytest.raises(ValueError, match="Несовместимые"):
            _do_convert(1, "km", "kg")

    def test_incompatible_units_mass_volume(self):
        with pytest.raises(ValueError, match="Несовместимые"):
            _do_convert(1, "kg", "l")

    def test_incompatible_temp_and_length(self):
        with pytest.raises(ValueError, match="температуру"):
            _do_convert(100, "C", "km")

    def test_incompatible_length_and_temp(self):
        with pytest.raises(ValueError, match="температуру"):
            _do_convert(100, "km", "C")

    def test_negative_values(self):
        """Отрицательные значения должны конвертироваться корректно."""
        result = _do_convert(-10, "C", "F")
        assert abs(result - 14.0) < 0.01

    def test_zero_km(self):
        assert abs(_do_convert(0, "km", "mi") - 0.0) < 0.0001

    def test_float_value(self):
        result = _do_convert(1.5, "kg", "lb")
        assert abs(result - 3.30693) < 0.01


# ---------------------------------------------------------------------------
# handle_convert — handler (через AsyncMock)
# ---------------------------------------------------------------------------

def _make_bot(args: str):
    """Вспомогательная фабрика: простой bot-объект."""
    bot = SimpleNamespace()
    bot._get_command_args = lambda msg: args
    return bot


def _make_message():
    msg = SimpleNamespace()
    msg.reply = AsyncMock()
    return msg


@pytest.mark.asyncio
async def test_handle_convert_km_to_mi():
    """!convert 100 km mi → 62.1371 mi."""
    bot = _make_bot("100 km mi")
    msg = _make_message()
    await handle_convert(bot, msg)
    msg.reply.assert_awaited_once()
    text = msg.reply.call_args[0][0]
    assert "km" in text.lower() or "mi" in text.lower()
    assert "62.1" in text


@pytest.mark.asyncio
async def test_handle_convert_f_to_c():
    """!convert 72 F C → 22.2°C."""
    bot = _make_bot("72 F C")
    msg = _make_message()
    await handle_convert(bot, msg)
    text = msg.reply.call_args[0][0]
    assert "°C" in text
    assert "22.2" in text


@pytest.mark.asyncio
async def test_handle_convert_kg_to_lb():
    """!convert 5 kg lb → 11.02 lb."""
    bot = _make_bot("5 kg lb")
    msg = _make_message()
    await handle_convert(bot, msg)
    text = msg.reply.call_args[0][0]
    assert "11.0" in text


@pytest.mark.asyncio
async def test_handle_convert_no_args_shows_help():
    """!convert без аргументов → выводит справку."""
    bot = _make_bot("")
    msg = _make_message()
    await handle_convert(bot, msg)
    text = msg.reply.call_args[0][0]
    assert "convert" in text.lower()
    assert "Примеры" in text or "convert" in text


@pytest.mark.asyncio
async def test_handle_convert_wrong_arg_count():
    """!convert 100 km → ошибка формата."""
    from src.core.exceptions import UserInputError
    bot = _make_bot("100 km")
    msg = _make_message()
    with pytest.raises(UserInputError):
        await handle_convert(bot, msg)


@pytest.mark.asyncio
async def test_handle_convert_bad_number():
    """!convert abc km mi → ошибка числа (UserInputError с user_message)."""
    from src.core.exceptions import UserInputError
    bot = _make_bot("abc km mi")
    msg = _make_message()
    with pytest.raises(UserInputError) as exc_info:
        await handle_convert(bot, msg)
    # user_message хранится в атрибуте, не в str(exc)
    assert "разобрать число" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_handle_convert_incompatible_units():
    """!convert 5 km kg → ошибка несовместимых единиц."""
    from src.core.exceptions import UserInputError
    bot = _make_bot("5 km kg")
    msg = _make_message()
    with pytest.raises(UserInputError) as exc_info:
        await handle_convert(bot, msg)
    assert exc_info.value.user_message  # непустое сообщение об ошибке


@pytest.mark.asyncio
async def test_handle_convert_unknown_unit():
    """!convert 5 furlongs km → ошибка неизвестной единицы."""
    from src.core.exceptions import UserInputError
    bot = _make_bot("5 furlongs km")
    msg = _make_message()
    with pytest.raises(UserInputError) as exc_info:
        await handle_convert(bot, msg)
    assert "Неизвестная единица" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_handle_convert_comma_decimal():
    """!convert 1,5 kg lb — запятая как разделитель."""
    bot = _make_bot("1,5 kg lb")
    msg = _make_message()
    await handle_convert(bot, msg)
    text = msg.reply.call_args[0][0]
    assert "lb" in text.lower() or "lb" in text


@pytest.mark.asyncio
async def test_handle_convert_c_to_k():
    """!convert 0 C K → 273.15 K."""
    bot = _make_bot("0 C K")
    msg = _make_message()
    await handle_convert(bot, msg)
    text = msg.reply.call_args[0][0]
    assert "273.15" in text or "273" in text


@pytest.mark.asyncio
async def test_handle_convert_l_to_gal():
    """!convert 3.785 L gal → ~1 gal."""
    bot = _make_bot("3.785 l gal")
    msg = _make_message()
    await handle_convert(bot, msg)
    text = msg.reply.call_args[0][0]
    assert "gal" in text.lower()
    # 3.785 / 3.78541 ≈ 0.999...
    assert "0.999" in text or "1" in text


@pytest.mark.asyncio
async def test_handle_convert_kmh_to_mph():
    """!convert 100 kmh mph → ~62.1 mph."""
    bot = _make_bot("100 kmh mph")
    msg = _make_message()
    await handle_convert(bot, msg)
    text = msg.reply.call_args[0][0]
    assert "62." in text


@pytest.mark.asyncio
async def test_handle_convert_result_format_reply():
    """Ответ содержит исходное значение и единицу назначения."""
    bot = _make_bot("10 m ft")
    msg = _make_message()
    await handle_convert(bot, msg)
    text = msg.reply.call_args[0][0]
    # Должно быть: 10 m = X ft
    assert "10" in text
    assert "ft" in text
