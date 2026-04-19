# -*- coding: utf-8 -*-
"""
Тесты для !time — мировые часы и конвертация времени.
Покрываем: _time_lookup_tz, _time_format_dt, handle_time (все 3 режима).
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import (
    _TIME_CITY_MAP,
    _TIME_DEFAULT_CITIES,
    _time_format_dt,
    _time_lookup_tz,
    handle_time,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bot(args: str) -> SimpleNamespace:
    """Минимальный mock бота с _get_command_args."""
    return SimpleNamespace(_get_command_args=lambda msg: args)


def _make_message() -> SimpleNamespace:
    """Минимальный mock Message с reply."""
    return SimpleNamespace(reply=AsyncMock(), chat=SimpleNamespace(id=999))


# ---------------------------------------------------------------------------
# _time_lookup_tz — маппинг городов
# ---------------------------------------------------------------------------


class TestTimeLookupTz:
    """Тесты функции поиска timezone по имени города."""

    def test_madrid(self) -> None:
        assert _time_lookup_tz("madrid") == "Europe/Madrid"

    def test_moscow_lower(self) -> None:
        assert _time_lookup_tz("moscow") == "Europe/Moscow"

    def test_moscow_upper(self) -> None:
        assert _time_lookup_tz("Moscow") == "Europe/Moscow"

    def test_nyc(self) -> None:
        assert _time_lookup_tz("nyc") == "America/New_York"

    def test_new_york_with_space(self) -> None:
        assert _time_lookup_tz("new york") == "America/New_York"

    def test_tokyo(self) -> None:
        assert _time_lookup_tz("tokyo") == "Asia/Tokyo"

    def test_london(self) -> None:
        assert _time_lookup_tz("london") == "Europe/London"

    def test_dubai(self) -> None:
        assert _time_lookup_tz("dubai") == "Asia/Dubai"

    def test_barcelona(self) -> None:
        # Barcelona → Europe/Madrid (одна зона с Испанией)
        assert _time_lookup_tz("barcelona") == "Europe/Madrid"

    def test_russian_москва(self) -> None:
        assert _time_lookup_tz("москва") == "Europe/Moscow"

    def test_russian_токио(self) -> None:
        assert _time_lookup_tz("токио") == "Asia/Tokyo"

    def test_iana_direct(self) -> None:
        """Прямая IANA-строка должна быть принята."""
        assert _time_lookup_tz("Europe/Berlin") == "Europe/Berlin"

    def test_unknown_city_returns_none(self) -> None:
        assert _time_lookup_tz("atlantis") is None

    def test_empty_string_returns_none(self) -> None:
        assert _time_lookup_tz("") is None

    def test_invalid_iana_returns_none(self) -> None:
        assert _time_lookup_tz("Not/ATimezone") is None

    def test_case_insensitive(self) -> None:
        assert _time_lookup_tz("MADRID") == "Europe/Madrid"
        assert _time_lookup_tz("Madrid") == "Europe/Madrid"

    def test_singapore(self) -> None:
        assert _time_lookup_tz("singapore") == "Asia/Singapore"

    def test_sydney(self) -> None:
        assert _time_lookup_tz("sydney") == "Australia/Sydney"

    def test_mumbai_maps_to_kolkata(self) -> None:
        assert _time_lookup_tz("mumbai") == "Asia/Kolkata"

    def test_all_map_entries_valid(self) -> None:
        """Каждая timezone в маппинге должна быть валидной IANA-зоной."""
        for city, tz in _TIME_CITY_MAP.items():
            try:
                ZoneInfo(tz)
            except Exception as exc:
                pytest.fail(f"Невалидная timezone '{tz}' для города '{city}': {exc}")

    def test_default_cities_all_valid(self) -> None:
        """Все timezone в _TIME_DEFAULT_CITIES должны быть валидными."""
        for city_name, tz in _TIME_DEFAULT_CITIES:
            try:
                ZoneInfo(tz)
            except Exception as exc:
                pytest.fail(f"Невалидная timezone '{tz}' для '{city_name}': {exc}")


# ---------------------------------------------------------------------------
# _time_format_dt — форматирование datetime
# ---------------------------------------------------------------------------


class TestTimeFormatDt:
    """Тесты форматирования datetime в читаемую строку."""

    def test_format_basic(self) -> None:
        dt = datetime.datetime(2026, 4, 12, 15, 30, 0, tzinfo=ZoneInfo("UTC"))
        result = _time_format_dt(dt)
        assert "15:30" in result
        assert "Apr" in result
        assert "12" in result

    def test_format_midnight(self) -> None:
        dt = datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=ZoneInfo("UTC"))
        result = _time_format_dt(dt)
        assert "00:00" in result
        assert "Jan" in result

    def test_format_contains_weekday(self) -> None:
        # 2026-04-12 — воскресенье
        dt = datetime.datetime(2026, 4, 12, 10, 0, 0, tzinfo=ZoneInfo("UTC"))
        result = _time_format_dt(dt)
        assert "Sun" in result

    def test_format_returns_string(self) -> None:
        dt = datetime.datetime.now(ZoneInfo("UTC"))
        assert isinstance(_time_format_dt(dt), str)


# ---------------------------------------------------------------------------
# handle_time — без аргументов (мировые часы)
# ---------------------------------------------------------------------------


class TestHandleTimeNoArgs:
    """!time (без аргументов) → 4 города."""

    @pytest.mark.asyncio
    async def test_no_args_replies(self) -> None:
        bot = _make_bot("")
        msg = _make_message()
        await handle_time(bot, msg)
        msg.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_args_contains_madrid(self) -> None:
        bot = _make_bot("")
        msg = _make_message()
        await handle_time(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "Madrid" in text

    @pytest.mark.asyncio
    async def test_no_args_contains_moscow(self) -> None:
        bot = _make_bot("")
        msg = _make_message()
        await handle_time(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "Moscow" in text

    @pytest.mark.asyncio
    async def test_no_args_contains_new_york(self) -> None:
        bot = _make_bot("")
        msg = _make_message()
        await handle_time(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "New York" in text

    @pytest.mark.asyncio
    async def test_no_args_contains_tokyo(self) -> None:
        bot = _make_bot("")
        msg = _make_message()
        await handle_time(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "Tokyo" in text

    @pytest.mark.asyncio
    async def test_no_args_contains_utc_offset(self) -> None:
        bot = _make_bot("")
        msg = _make_message()
        await handle_time(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "UTC" in text

    @pytest.mark.asyncio
    async def test_no_args_contains_world_time_header(self) -> None:
        bot = _make_bot("")
        msg = _make_message()
        await handle_time(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "Мировое время" in text


# ---------------------------------------------------------------------------
# handle_time — конкретный город
# ---------------------------------------------------------------------------


class TestHandleTimeCity:
    """!time <город> → время в одном городе."""

    @pytest.mark.asyncio
    async def test_madrid_replies(self) -> None:
        bot = _make_bot("Madrid")
        msg = _make_message()
        await handle_time(bot, msg)
        msg.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_madrid_contains_timezone(self) -> None:
        bot = _make_bot("Madrid")
        msg = _make_message()
        await handle_time(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "Europe/Madrid" in text

    @pytest.mark.asyncio
    async def test_moscow_contains_timezone(self) -> None:
        bot = _make_bot("Moscow")
        msg = _make_message()
        await handle_time(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "Europe/Moscow" in text

    @pytest.mark.asyncio
    async def test_tokyo_contains_timezone(self) -> None:
        bot = _make_bot("Tokyo")
        msg = _make_message()
        await handle_time(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "Asia/Tokyo" in text

    @pytest.mark.asyncio
    async def test_nyc_alias(self) -> None:
        bot = _make_bot("nyc")
        msg = _make_message()
        await handle_time(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "America/New_York" in text

    @pytest.mark.asyncio
    async def test_iana_direct(self) -> None:
        """Прямая IANA-строка работает как город."""
        bot = _make_bot("Europe/Berlin")
        msg = _make_message()
        await handle_time(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "Europe/Berlin" in text

    @pytest.mark.asyncio
    async def test_russian_москва(self) -> None:
        bot = _make_bot("москва")
        msg = _make_message()
        await handle_time(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "Europe/Moscow" in text

    @pytest.mark.asyncio
    async def test_unknown_city_raises(self) -> None:
        bot = _make_bot("atlantis")
        msg = _make_message()
        with pytest.raises(UserInputError) as exc_info:
            await handle_time(bot, msg)
        assert "не найден" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_city_response_contains_time(self) -> None:
        bot = _make_bot("Dubai")
        msg = _make_message()
        await handle_time(bot, msg)
        text: str = msg.reply.call_args[0][0]
        # Должен быть формат HH:MM (backtick)
        import re

        assert re.search(r"`\d{2}:\d{2}", text)

    @pytest.mark.asyncio
    async def test_partial_match_lon(self) -> None:
        """Частичное совпадение: 'lon' → london."""
        bot = _make_bot("lon")
        msg = _make_message()
        await handle_time(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "Europe/London" in text


# ---------------------------------------------------------------------------
# handle_time convert — конвертация времени
# ---------------------------------------------------------------------------


class TestHandleTimeConvert:
    """!time convert HH:MM <из> <в> — конвертация между зонами."""

    @pytest.mark.asyncio
    async def test_convert_madrid_moscow(self) -> None:
        bot = _make_bot("convert 15:00 Madrid Moscow")
        msg = _make_message()
        await handle_time(bot, msg)
        msg.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_convert_contains_from_and_to(self) -> None:
        bot = _make_bot("convert 15:00 Madrid Moscow")
        msg = _make_message()
        await handle_time(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "Europe/Madrid" in text
        assert "Europe/Moscow" in text

    @pytest.mark.asyncio
    async def test_convert_contains_arrow(self) -> None:
        bot = _make_bot("convert 15:00 Madrid Moscow")
        msg = _make_message()
        await handle_time(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "→" in text

    @pytest.mark.asyncio
    async def test_convert_correct_offset(self) -> None:
        """Madrid (UTC+2 летом) → Moscow (UTC+3): разница +1ч."""
        # Фиксируем дату летнюю, когда Мадрид UTC+2, Москва UTC+3
        fixed_date = datetime.date(2026, 7, 15)
        with patch("datetime.date") as mock_date:
            mock_date.today.return_value = fixed_date
            mock_date.side_effect = lambda *a, **kw: datetime.date(*a, **kw)
            bot = _make_bot("convert 14:00 Madrid Moscow")
            msg = _make_message()
            await handle_time(bot, msg)
        text: str = msg.reply.call_args[0][0]
        # 14:00 Мадрид (UTC+2) = 15:00 Москва (UTC+3)
        assert "15:00" in text

    @pytest.mark.asyncio
    async def test_convert_madrid_to_tokyo(self) -> None:
        bot = _make_bot("convert 10:00 Madrid Tokyo")
        msg = _make_message()
        await handle_time(bot, msg)
        msg.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_convert_invalid_time_format(self) -> None:
        bot = _make_bot("convert 25:00 Madrid Moscow")
        msg = _make_message()
        with pytest.raises(UserInputError) as exc_info:
            await handle_time(bot, msg)
        assert "Некорректное время" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_convert_unknown_from_city(self) -> None:
        bot = _make_bot("convert 15:00 Atlantis Moscow")
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_time(bot, msg)

    @pytest.mark.asyncio
    async def test_convert_unknown_to_city(self) -> None:
        bot = _make_bot("convert 15:00 Madrid Lemuria")
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_time(bot, msg)

    @pytest.mark.asyncio
    async def test_convert_missing_args(self) -> None:
        bot = _make_bot("convert")
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_time(bot, msg)

    @pytest.mark.asyncio
    async def test_convert_nyc_to_tokyo(self) -> None:
        bot = _make_bot("convert 09:00 nyc Tokyo")
        msg = _make_message()
        await handle_time(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "Asia/Tokyo" in text

    @pytest.mark.asyncio
    async def test_convert_case_insensitive(self) -> None:
        bot = _make_bot("CONVERT 12:00 MADRID MOSCOW")
        msg = _make_message()
        await handle_time(bot, msg)
        msg.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_convert_minutes_59(self) -> None:
        bot = _make_bot("convert 23:59 Madrid Moscow")
        msg = _make_message()
        await handle_time(bot, msg)
        msg.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_convert_midnight(self) -> None:
        bot = _make_bot("convert 00:00 London Tokyo")
        msg = _make_message()
        await handle_time(bot, msg)
        msg.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_convert_contains_source_time(self) -> None:
        bot = _make_bot("convert 17:30 Madrid Moscow")
        msg = _make_message()
        await handle_time(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "17:30" in text

    @pytest.mark.asyncio
    async def test_convert_result_is_valid_time(self) -> None:
        """Результирующее время в ответе должно быть валидным HH:MM."""
        import re

        bot = _make_bot("convert 10:00 Madrid Tokyo")
        msg = _make_message()
        await handle_time(bot, msg)
        text: str = msg.reply.call_args[0][0]
        # Ищем все HH:MM в тексте
        times = re.findall(r"\d{2}:\d{2}", text)
        assert len(times) >= 2  # минимум исходное и результирующее
        for t in times:
            hh, mm = map(int, t.split(":"))
            assert 0 <= hh <= 23
            assert 0 <= mm <= 59
