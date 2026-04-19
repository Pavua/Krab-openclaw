# -*- coding: utf-8 -*-
"""
Тесты для !currency — конвертера валют.

Покрывает:
- _parse_currency_args: разбор аргументов (2 и 3 токена, регистронезависимость)
- _fmt_currency: форматирование чисел
- fetch_exchange_rate: HTTP-запрос к open.er-api.com (мок httpx)
- handle_currency: интеграционный тест хендлера (мок бота и сообщения)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import (
    _fmt_currency,
    _parse_currency_args,
    fetch_exchange_rate,
    handle_currency,
)

# ---------------------------------------------------------------------------
# _parse_currency_args — разбор аргументов
# ---------------------------------------------------------------------------


class TestParseCurrencyArgs:
    """Разбор строки аргументов команды !currency."""

    def test_three_tokens(self) -> None:
        """!currency 100 USD EUR → (100.0, 'USD', 'EUR')."""
        amount, frm, to = _parse_currency_args("100 USD EUR")
        assert amount == 100.0
        assert frm == "USD"
        assert to == "EUR"

    def test_two_tokens_no_target(self) -> None:
        """!currency 100 USD → (100.0, 'USD', None)."""
        amount, frm, to = _parse_currency_args("100 USD")
        assert amount == 100.0
        assert frm == "USD"
        assert to is None

    def test_lowercase_currencies_normalized(self) -> None:
        """Коды валют в нижнем регистре нормализуются в UPPER."""
        amount, frm, to = _parse_currency_args("50 usd eur")
        assert frm == "USD"
        assert to == "EUR"

    def test_float_amount(self) -> None:
        """Дробная сумма корректно парсится."""
        amount, frm, to = _parse_currency_args("100.50 GBP EUR")
        assert abs(amount - 100.50) < 1e-10
        assert frm == "GBP"

    def test_comma_decimal_separator(self) -> None:
        """Запятая как разделитель десятичных."""
        amount, frm, _ = _parse_currency_args("1,5 USD")
        assert abs(amount - 1.5) < 1e-10

    def test_zero_amount(self) -> None:
        """Нулевая сумма допустима."""
        amount, frm, to = _parse_currency_args("0 USD EUR")
        assert amount == 0.0

    def test_too_few_tokens_raises(self) -> None:
        """Только один токен → UserInputError."""
        with pytest.raises(UserInputError):
            _parse_currency_args("100")

    def test_too_many_tokens_raises(self) -> None:
        """Четыре токена → UserInputError."""
        with pytest.raises(UserInputError):
            _parse_currency_args("100 USD EUR RUB")

    def test_invalid_amount_raises(self) -> None:
        """Нечисловая сумма → UserInputError."""
        with pytest.raises(UserInputError):
            _parse_currency_args("abc USD EUR")

    def test_negative_amount_raises(self) -> None:
        """Отрицательная сумма → UserInputError."""
        with pytest.raises(UserInputError):
            _parse_currency_args("-100 USD EUR")

    def test_empty_string_raises(self) -> None:
        """Пустая строка → UserInputError."""
        with pytest.raises(UserInputError):
            _parse_currency_args("")


# ---------------------------------------------------------------------------
# _fmt_currency — форматирование числа
# ---------------------------------------------------------------------------


class TestFmtCurrency:
    """Форматирование чисел для вывода конвертера."""

    def test_large_number_with_comma(self) -> None:
        """Числа >= 1000 форматируются с разделителем тысяч."""
        result = _fmt_currency(1234.56)
        assert "1,234.56" in result

    def test_normal_number_no_trailing_zeros(self) -> None:
        """Числа >= 0.01 — без лишних нулей."""
        result = _fmt_currency(0.9235)
        assert result == "0.9235"

    def test_normal_number_trailing_zeros_stripped(self) -> None:
        """Нули в конце обрезаются."""
        result = _fmt_currency(1.5000)
        assert result == "1.5"

    def test_whole_number_no_dot(self) -> None:
        """Целые числа без дробной части отображаются без точки."""
        result = _fmt_currency(1.0)
        assert result == "1"

    def test_very_small_number(self) -> None:
        """Очень маленькие числа (< 0.01) — до 6 знаков."""
        result = _fmt_currency(0.001234)
        assert "0.001234" in result

    def test_zero(self) -> None:
        """Ноль форматируется корректно."""
        result = _fmt_currency(0.0)
        assert result == "0"


# ---------------------------------------------------------------------------
# fetch_exchange_rate — HTTP-запрос к API (мок)
# ---------------------------------------------------------------------------


def _make_api_response(rates: dict, result: str = "success") -> MagicMock:
    """Создаёт мок httpx-ответа с заданными данными."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"result": result, "rates": rates}
    return resp


class TestFetchExchangeRate:
    """Тесты HTTP-запроса к open.er-api.com."""

    @pytest.mark.asyncio
    async def test_successful_rate_fetch(self) -> None:
        """Успешный запрос возвращает правильный курс."""
        mock_resp = _make_api_response({"EUR": 0.9235})

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with patch("src.handlers.command_handlers.httpx.AsyncClient", return_value=mock_cm):
            rate = await fetch_exchange_rate("USD", "EUR")

        assert abs(rate - 0.9235) < 1e-10

    @pytest.mark.asyncio
    async def test_unknown_from_currency_raises(self) -> None:
        """Неизвестная исходная валюта → UserInputError с сообщением об unsupported-code."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": "error", "error-type": "unsupported-code"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with patch("src.handlers.command_handlers.httpx.AsyncClient", return_value=mock_cm):
            with pytest.raises(UserInputError) as exc_info:
                await fetch_exchange_rate("XYZ", "EUR")
        assert "XYZ" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_unknown_to_currency_raises(self) -> None:
        """Неизвестная целевая валюта (не в rates) → UserInputError."""
        mock_resp = _make_api_response({"EUR": 0.9235})

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with patch("src.handlers.command_handlers.httpx.AsyncClient", return_value=mock_cm):
            with pytest.raises(UserInputError) as exc_info:
                await fetch_exchange_rate("USD", "XYZ")
        assert "XYZ" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_api_http_error_raises(self) -> None:
        """HTTP статус != 200 → UserInputError."""
        mock_resp = MagicMock()
        mock_resp.status_code = 503

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with patch("src.handlers.command_handlers.httpx.AsyncClient", return_value=mock_cm):
            with pytest.raises(UserInputError) as exc_info:
                await fetch_exchange_rate("USD", "EUR")
        assert "503" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_timeout_raises(self) -> None:
        """Таймаут httpx → UserInputError с сообщением о таймауте."""
        import httpx

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with patch("src.handlers.command_handlers.httpx.AsyncClient", return_value=mock_cm):
            with pytest.raises(UserInputError) as exc_info:
                await fetch_exchange_rate("USD", "EUR")
        assert "таймаут" in exc_info.value.user_message.lower()

    @pytest.mark.asyncio
    async def test_request_error_raises(self) -> None:
        """Сетевая ошибка httpx → UserInputError."""
        import httpx

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.RequestError("connection refused", request=MagicMock())
        )
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with patch("src.handlers.command_handlers.httpx.AsyncClient", return_value=mock_cm):
            with pytest.raises(UserInputError) as exc_info:
                await fetch_exchange_rate("USD", "EUR")
        assert "соединения" in exc_info.value.user_message.lower()

    @pytest.mark.asyncio
    async def test_invalid_json_raises(self) -> None:
        """Невалидный JSON от API → UserInputError."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("invalid json")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with patch("src.handlers.command_handlers.httpx.AsyncClient", return_value=mock_cm):
            with pytest.raises(UserInputError) as exc_info:
                await fetch_exchange_rate("USD", "EUR")
        assert "разобрать" in exc_info.value.user_message.lower()


# ---------------------------------------------------------------------------
# handle_currency — интеграционные тесты хендлера
# ---------------------------------------------------------------------------


def _make_bot(args: str) -> SimpleNamespace:
    """Создаёт мок-объект бота с заданными аргументами команды."""
    return SimpleNamespace(_get_command_args=lambda msg: args)


def _make_message() -> SimpleNamespace:
    """Создаёт мок-объект Telegram-сообщения."""
    return SimpleNamespace(reply=AsyncMock(), chat=SimpleNamespace(id=123))


def _mock_api(rate: float):
    """Context manager: мокирует fetch_exchange_rate с заданным курсом."""
    return patch(
        "src.handlers.command_handlers.fetch_exchange_rate",
        new=AsyncMock(return_value=rate),
    )


class TestHandleCurrencyIntegration:
    """Интеграционные тесты handle_currency."""

    @pytest.mark.asyncio
    async def test_basic_usd_to_eur(self) -> None:
        """!currency 100 USD EUR → корректный ответ с эмодзи 💱."""
        bot = _make_bot("100 USD EUR")
        msg = _make_message()
        with _mock_api(0.9235):
            await handle_currency(bot, msg)
        reply_text: str = msg.reply.call_args[0][0]
        assert reply_text.startswith("💱")
        assert "USD" in reply_text
        assert "EUR" in reply_text

    @pytest.mark.asyncio
    async def test_default_target_currency(self) -> None:
        """!currency 100 USD (без TO) → использует дефолтную целевую валюту."""
        bot = _make_bot("100 USD")
        msg = _make_message()
        with _mock_api(0.9235):
            await handle_currency(bot, msg)
        reply_text: str = msg.reply.call_args[0][0]
        assert "USD" in reply_text
        # Дефолтная цель — EUR (или CURRENCY_DEFAULT_TARGET)
        assert msg.reply.await_count == 1

    @pytest.mark.asyncio
    async def test_lowercase_args_normalized(self) -> None:
        """!currency 100 usd eur → коды нормализуются в UPPER."""
        bot = _make_bot("100 usd eur")
        msg = _make_message()
        with _mock_api(0.9235):
            await handle_currency(bot, msg)
        reply_text: str = msg.reply.call_args[0][0]
        assert "USD" in reply_text
        assert "EUR" in reply_text

    @pytest.mark.asyncio
    async def test_same_currency_no_api_call(self) -> None:
        """!currency 100 EUR EUR → тривиальный случай, курс = 1, API не вызывается."""
        bot = _make_bot("100 EUR EUR")
        msg = _make_message()
        with patch(
            "src.handlers.command_handlers.fetch_exchange_rate",
            new=AsyncMock(side_effect=AssertionError("API не должен вызываться")),
        ):
            await handle_currency(bot, msg)
        reply_text: str = msg.reply.call_args[0][0]
        assert "курс: 1" in reply_text

    @pytest.mark.asyncio
    async def test_result_format(self) -> None:
        """Формат ответа: '💱 100 USD = 92.35 EUR (курс: 0.9235)'."""
        bot = _make_bot("100 USD EUR")
        msg = _make_message()
        with _mock_api(0.9235):
            await handle_currency(bot, msg)
        reply_text: str = msg.reply.call_args[0][0]
        # Проверяем структуру: содержит = и (курс:
        assert " = " in reply_text
        assert "(курс:" in reply_text

    @pytest.mark.asyncio
    async def test_empty_args_raises(self) -> None:
        """Пустые аргументы → UserInputError с подсказкой."""
        bot = _make_bot("")
        msg = _make_message()
        with pytest.raises(UserInputError) as exc_info:
            await handle_currency(bot, msg)
        assert "!currency" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_invalid_amount_raises(self) -> None:
        """Нечисловая сумма → UserInputError."""
        bot = _make_bot("abc USD EUR")
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_currency(bot, msg)

    @pytest.mark.asyncio
    async def test_api_error_propagated(self) -> None:
        """Ошибка API пробрасывается как UserInputError."""
        bot = _make_bot("100 XYZ EUR")
        msg = _make_message()
        with patch(
            "src.handlers.command_handlers.fetch_exchange_rate",
            new=AsyncMock(side_effect=UserInputError(user_message="❌ Неизвестная валюта: `XYZ`")),
        ):
            with pytest.raises(UserInputError) as exc_info:
                await handle_currency(bot, msg)
        assert "XYZ" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_large_amount(self) -> None:
        """Большая сумма (>= 1000) — разделитель тысяч в ответе."""
        bot = _make_bot("10000 USD EUR")
        msg = _make_message()
        with _mock_api(0.9235):
            await handle_currency(bot, msg)
        reply_text: str = msg.reply.call_args[0][0]
        # Форматирование крупных сумм с запятой
        assert "10,000.00" in reply_text or "10000" in reply_text

    @pytest.mark.asyncio
    async def test_rub_conversion(self) -> None:
        """!currency 1500 RUB EUR → корректная конвертация с маленьким курсом."""
        bot = _make_bot("1500 RUB EUR")
        msg = _make_message()
        with _mock_api(0.0101):
            await handle_currency(bot, msg)
        reply_text: str = msg.reply.call_args[0][0]
        assert "RUB" in reply_text
        assert "EUR" in reply_text
