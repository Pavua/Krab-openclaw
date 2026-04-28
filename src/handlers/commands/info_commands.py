# -*- coding: utf-8 -*-
"""
info_commands — Phase 2 Wave 19+20 extraction (Session 28).

Информационные/справочные команды (без сложного state):
  - !weather  — погода через wttr.in (LLM fallback).
  - !define   — определение слова через AI.
  - !urban    — Urban Dictionary lookup через AI + web_search.
  - !currency — конвертер валют (open.er-api.com).
  - !convert  — конвертер единиц (чистая математика).
  - !color    — конвертер цветов HEX/RGB/HSL + CSS named.
  - !emoji    — поиск эмодзи по описанию.
  - !news     — новости через AI (web_search).

Все handler-функции и helpers re-exported в ``command_handlers.py`` для
обратной совместимости тестов и external imports.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any

import httpx
from pyrogram.types import Message

from ...config import config
from ...core.exceptions import UserInputError
from ...core.logger import get_logger
from ...openclaw_client import openclaw_client as _openclaw_client_default

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)


def _ch_attr(name: str, default: Any) -> Any:
    """Lazy dual-namespace lookup: command_handlers.<name> сначала
    (для test monkeypatch), fallback к локальному default.

    Tests heavily патчат `command_handlers.openclaw_client`,
    `command_handlers._fetch_wttr`, `command_handlers._split_text_for_telegram`,
    `command_handlers._parse_define_args`, etc. — этот proxy позволяет
    handler-у видеть их.
    """
    try:
        from .. import command_handlers as _ch  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return default
    return getattr(_ch, name, default)


def _split_text_for_telegram_default(text: str, limit: int = 4000) -> list[str]:
    """Fallback splitter — используется если command_handlers недоступен."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    cur = text
    while len(cur) > limit:
        parts.append(cur[:limit])
        cur = cur[limit:]
    if cur:
        parts.append(cur)
    return parts


# ---------------------------------------------------------------------------
# !weather — wttr.in fast path + LLM fallback
# ---------------------------------------------------------------------------

_WTTR_URL = "https://wttr.in/{city}?format=4&lang=ru"
_WTTR_TIMEOUT = 8.0


async def _fetch_wttr(city: str) -> str | None:
    """Получает погоду через wttr.in format=4 (compact one-liner).

    Returns:
        Строка погоды или None при ошибке.
    """
    url = _WTTR_URL.format(city=city.replace(" ", "+"))
    try:
        async with httpx.AsyncClient(timeout=_WTTR_TIMEOUT) as client:
            resp = await client.get(url, follow_redirects=True)
        if resp.status_code == 200:
            text = resp.text.strip()
            if text and len(text) > 5:
                return text
    except (httpx.TimeoutException, httpx.RequestError):
        pass
    return None


async def handle_weather(bot: "KraabUserbot", message: Message) -> None:
    """
    Показывает текущую погоду для города.

    Форматы:
    - !weather          — погода в городе по умолчанию (DEFAULT_WEATHER_CITY)
    - !weather <город>  — погода в указанном городе

    Приоритет: wttr.in (быстро, без API-ключа) → LLM web_search (fallback).
    """
    city = bot._get_command_args(message).strip()
    if not city:
        _cfg = _ch_attr("config", config)
        city = _cfg.DEFAULT_WEATHER_CITY

    msg = await message.reply(f"🌤 Смотрю погоду в **{city}**...")

    # Быстрый путь: wttr.in
    fetch_wttr = _ch_attr("_fetch_wttr", _fetch_wttr)
    wttr_result = await fetch_wttr(city)
    if wttr_result:
        await msg.edit(f"🌤 {wttr_result}")
        return

    # Fallback: LLM + web_search
    session_id = f"weather_{message.chat.id}"
    prompt = (
        f"Какая сейчас погода в {city}? "
        "Дай краткий ответ: температура, облачность, осадки. "
        "Используй актуальные данные из веб-поиска."
    )

    try:
        chunks: list[str] = []
        client = _ch_attr("openclaw_client", _openclaw_client_default)
        async for chunk in client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            disable_tools=False,
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()
        if not result:
            await msg.edit("❌ Не удалось получить данные о погоде.")
            return

        splitter = _ch_attr("_split_text_for_telegram", _split_text_for_telegram_default)
        parts = splitter(result)
        await msg.edit(parts[0])
        for part in parts[1:]:
            await message.reply(part)

    except Exception as exc:  # noqa: BLE001
        logger.error("handle_weather_error", error=str(exc))
        await msg.edit(f"❌ Ошибка получения погоды: {exc}")


# ---------------------------------------------------------------------------
# !define — определение слова через AI
# ---------------------------------------------------------------------------

_DEFINE_DETAILED_KEYWORDS = {"подробно", "detailed", "full", "полно", "полностью", "расширенно"}
_DEFINE_EN_KEYWORDS = {"en", "english", "англ", "английский"}


def _parse_define_args(raw_args: str) -> tuple[str, str, bool]:
    """
    Разбирает аргументы команды !define.

    Возвращает (слово, язык, подробно).
    язык: "ru" | "en"
    подробно: True если запрошено развёрнутое определение.
    """
    parts = raw_args.strip().split()
    if not parts:
        return ("", "ru", False)

    lang = "ru"
    detailed = False
    term_parts: list[str] = []

    for part in parts:
        lower = part.lower()
        if lower in _DEFINE_EN_KEYWORDS:
            lang = "en"
        elif lower in _DEFINE_DETAILED_KEYWORDS:
            detailed = True
        else:
            term_parts.append(part)

    term = " ".join(term_parts).strip()
    return (term, lang, detailed)


def _build_define_prompt(term: str, lang: str, detailed: bool) -> str:
    """Формирует промпт для запроса определения."""
    if lang == "en":
        if detailed:
            return (
                f"Give a detailed definition of the term or word: «{term}». "
                "Include etymology if relevant, main meanings, examples of use, "
                "and related concepts. Answer in English."
            )
        return (
            f"Give a brief definition of the term or word: «{term}» in 2-3 sentences. "
            "Be precise and clear. Answer in English."
        )
    if detailed:
        return (
            f"Дай развёрнутое определение термина или слова: «{term}». "
            "Включи этимологию если уместно, основные значения, примеры использования "
            "и связанные понятия. Отвечай на русском."
        )
    return (
        f"Дай краткое определение термина или слова: «{term}» в 2-3 предложениях. "
        "Будь точен и лаконичен. Отвечай на русском."
    )


async def handle_define(bot: "KraabUserbot", message: Message) -> None:
    """!define <слово> [en] [подробно] — определение слова/термина через AI."""
    raw_args = bot._get_command_args(message).strip()

    if not raw_args and message.reply_to_message:
        raw_args = (message.reply_to_message.text or "").strip()

    if not raw_args:
        raise UserInputError(
            user_message=(
                "📖 **!define — определение слова/термина**\n\n"
                "`!define <слово>` — краткое определение (рус.)\n"
                "`!define <слово> en` — краткое определение (англ.)\n"
                "`!define <слово> подробно` — развёрнутое определение\n\n"
                "_Пример: `!define энтропия` или `!define recursion en`_"
            )
        )

    parser = _ch_attr("_parse_define_args", _parse_define_args)
    term, lang, detailed = parser(raw_args)

    if not term:
        raise UserInputError(user_message="❓ Укажи слово или термин: `!define <слово>`")

    lang_label = " (EN)" if lang == "en" else ""
    status_msg = await message.reply(f"📖 Определяю «{term}»{lang_label}...")

    builder = _ch_attr("_build_define_prompt", _build_define_prompt)
    prompt = builder(term, lang, detailed)
    session_id = f"define_{message.chat.id}"
    max_tokens = 800 if detailed else 350

    try:
        chunks: list[str] = []
        client = _ch_attr("openclaw_client", _openclaw_client_default)
        async for chunk in client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            disable_tools=True,
            max_output_tokens=max_tokens,
        ):
            chunks.append(chunk)

        definition = "".join(chunks).strip()
        if not definition:
            raise ValueError("пустой ответ от модели")

        header = f"📖 **{term}**{lang_label}"
        if detailed:
            header += " _(подробно)_"
        response_text = f"{header}\n\n{definition}"

        if len(response_text) > 4000:
            response_text = response_text[:3950] + "..."

        await status_msg.edit(response_text)

    except Exception as exc:  # noqa: BLE001
        logger.warning("handle_define_failed", term=term, error=str(exc))
        await status_msg.edit(f"❌ Не удалось получить определение «{term}»: {exc}")


# ---------------------------------------------------------------------------
# !currency — конвертер валют
# ---------------------------------------------------------------------------

_CURRENCY_DEFAULT_TARGET: str = os.getenv("CURRENCY_DEFAULT_TARGET", "EUR").upper()
_CURRENCY_API_URL = "https://open.er-api.com/v6/latest/{base}"
_CURRENCY_HTTP_TIMEOUT = 10.0


def _parse_currency_args(raw: str) -> tuple[float, str, str | None]:
    """Разбирает аргументы команды !currency."""
    parts = raw.strip().split()
    if len(parts) < 2 or len(parts) > 3:
        raise UserInputError(
            user_message=(
                "💱 **Конвертер валют**\n\n"
                "Использование:\n"
                "`!currency <сумма> <FROM> [TO]`\n\n"
                "Примеры:\n"
                "`!currency 100 USD EUR` → 100 USD в EUR\n"
                f"`!currency 100 USD` → 100 USD в {_CURRENCY_DEFAULT_TARGET} (дефолт)\n\n"
                "_Курсы: open.er-api.com (обновляются ежечасно)_"
            )
        )
    try:
        amount = float(parts[0].replace(",", "."))
    except ValueError:
        raise UserInputError(user_message=f"❌ Неверная сумма: `{parts[0]}`")

    if amount < 0:
        raise UserInputError(user_message="❌ Сумма не может быть отрицательной.")

    from_currency = parts[1].upper()
    to_currency = parts[2].upper() if len(parts) == 3 else None
    return amount, from_currency, to_currency


async def fetch_exchange_rate(from_currency: str, to_currency: str) -> float:
    """Получает курс from_currency → to_currency через open.er-api.com."""
    url = _CURRENCY_API_URL.format(base=from_currency)
    async with httpx.AsyncClient(timeout=_CURRENCY_HTTP_TIMEOUT) as client:
        try:
            resp = await client.get(url)
        except httpx.TimeoutException:
            raise UserInputError(user_message="❌ API курсов валют не отвечает (таймаут).")
        except httpx.RequestError as exc:
            raise UserInputError(user_message=f"❌ Ошибка соединения с API: {exc}")

    if resp.status_code != 200:
        raise UserInputError(
            user_message=f"❌ API вернул статус {resp.status_code}. Попробуй позже."
        )

    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        raise UserInputError(user_message="❌ Не удалось разобрать ответ API.")

    if data.get("result") != "success":
        error_type = data.get("error-type", "unknown")
        if error_type == "unsupported-code":
            raise UserInputError(
                user_message=f"❌ Неизвестная валюта: `{from_currency}`. Проверь код (ISO 4217)."
            )
        raise UserInputError(user_message=f"❌ API ошибка: `{error_type}`")

    rates = data.get("rates", {})
    if to_currency not in rates:
        raise UserInputError(
            user_message=f"❌ Неизвестная целевая валюта: `{to_currency}`. Проверь код (ISO 4217)."
        )

    return float(rates[to_currency])


def _fmt_currency(val: float) -> str:
    """Форматирует число для вывода в !currency."""
    if val >= 1000:
        return f"{val:,.2f}"
    if val >= 0.01:
        return f"{val:.4f}".rstrip("0").rstrip(".")
    return f"{val:.6f}".rstrip("0").rstrip(".")


async def handle_currency(bot: "KraabUserbot", message: Message) -> None:
    """!currency <сумма> <FROM> [TO] — конвертер валют."""
    raw_args = bot._get_command_args(message).strip()
    if not raw_args:
        raise UserInputError(
            user_message=(
                "💱 **Конвертер валют**\n\n"
                "Использование:\n"
                "`!currency <сумма> <FROM> [TO]`\n\n"
                "Примеры:\n"
                "`!currency 100 USD EUR` → 100 USD в EUR\n"
                f"`!currency 100 USD` → 100 USD в {_CURRENCY_DEFAULT_TARGET} (дефолт)\n\n"
                "_Курсы: open.er-api.com (обновляются ежечасно)_"
            )
        )

    parser = _ch_attr("_parse_currency_args", _parse_currency_args)
    amount, from_currency, to_currency_raw = parser(raw_args)
    default_target = _ch_attr("_CURRENCY_DEFAULT_TARGET", _CURRENCY_DEFAULT_TARGET)
    to_currency = to_currency_raw or default_target

    fmt = _ch_attr("_fmt_currency", _fmt_currency)
    if from_currency == to_currency:
        formatted_amount = fmt(amount)
        await message.reply(
            f"💱 {formatted_amount} {from_currency} = {formatted_amount} {to_currency} (курс: 1)"
        )
        return

    fetcher = _ch_attr("fetch_exchange_rate", fetch_exchange_rate)
    rate = await fetcher(from_currency, to_currency)
    converted = amount * rate

    amount_str = fmt(amount)
    converted_str = fmt(converted)
    rate_str = fmt(rate)

    await message.reply(
        f"💱 {amount_str} {from_currency} = {converted_str} {to_currency} (курс: {rate_str})"
    )


# ---------------------------------------------------------------------------
# !urban — Urban Dictionary lookup
# ---------------------------------------------------------------------------


async def handle_urban(bot: "KraabUserbot", message: Message) -> None:
    """!urban <слово> — определение слова из Urban Dictionary через AI."""
    word = bot._get_command_args(message).strip()

    if not word and message.reply_to_message:
        word = (message.reply_to_message.text or "").strip()

    if not word:
        raise UserInputError(
            user_message=(
                "📖 **!urban — Urban Dictionary lookup**\n\n"
                "`!urban <слово>` — поиск сленгового определения\n\n"
                "_Пример: `!urban yeet` или `!urban ghosting`_"
            )
        )

    status_msg = await message.reply(f"📖 Ищу «{word}» на Urban Dictionary...")
    session_id = f"urban_{message.chat.id}"

    prompt = (
        f"Найди определение слова '{word}' на Urban Dictionary. "
        "Используй web_search чтобы найти актуальное определение. "
        "Покажи в ответе: определение, пример использования, автор. "
        "Если слово не найдено — скажи об этом честно."
    )

    try:
        chunks: list[str] = []
        client = _ch_attr("openclaw_client", _openclaw_client_default)
        async for chunk in client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            disable_tools=False,
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()

        if not result:
            await status_msg.edit(f"❌ Не удалось получить определение «{word}».")
            return

        header = f"📖 **Urban Dictionary: {word}**\n\n"
        full_text = header + result

        splitter = _ch_attr("_split_text_for_telegram", _split_text_for_telegram_default)
        parts = splitter(full_text)
        total = len(parts)

        first = parts[0]
        if total > 1:
            first += f"\n\n_(часть 1/{total})_"
        await status_msg.edit(first)

        for i, part in enumerate(parts[1:], start=2):
            suffix = f"\n\n_(часть {i}/{total})_" if total > 2 else ""
            await message.reply(part + suffix)

    except Exception as exc:  # noqa: BLE001
        logger.error("handle_urban_error", word=word, error=str(exc))
        await status_msg.edit(f"❌ Ошибка поиска Urban Dictionary: {exc}")


# ---------------------------------------------------------------------------
# !convert — конвертер единиц
# ---------------------------------------------------------------------------

_CONVERT_UNITS: dict[str, tuple[float, str]] = {
    # Длина → метры
    "km": (1000.0, "m"),
    "m": (1.0, "m"),
    "cm": (0.01, "m"),
    "mm": (0.001, "m"),
    "mi": (1609.344, "m"),
    "ft": (0.3048, "m"),
    "in": (0.0254, "m"),
    "yd": (0.9144, "m"),
    # Масса → килограммы
    "kg": (1.0, "kg"),
    "g": (0.001, "kg"),
    "lb": (0.453592, "kg"),
    "oz": (0.028350, "kg"),
    # Объём → литры
    "l": (1.0, "l"),
    "ml": (0.001, "l"),
    "gal": (3.78541, "l"),
    "pt": (0.473176, "l"),
    # Скорость — база м/с
    "kmh": (1.0 / 3.6, "speed"),
    "mph": (0.44704, "speed"),
    "ms": (1.0, "speed"),
    "kn": (0.514444, "speed"),
}

_CONVERT_ALIASES: dict[str, str] = {
    "kilometer": "km",
    "kilometers": "km",
    "kilometre": "km",
    "kilometres": "km",
    "meter": "m",
    "meters": "m",
    "metre": "m",
    "metres": "m",
    "centimeter": "cm",
    "centimeters": "cm",
    "millimeter": "mm",
    "millimeters": "mm",
    "mile": "mi",
    "miles": "mi",
    "foot": "ft",
    "feet": "ft",
    "inch": "in",
    "inches": "in",
    "yard": "yd",
    "yards": "yd",
    "kilogram": "kg",
    "kilograms": "kg",
    "gram": "g",
    "grams": "g",
    "pound": "lb",
    "pounds": "lb",
    "lbs": "lb",
    "ounce": "oz",
    "ounces": "oz",
    "liter": "l",
    "liters": "l",
    "litre": "l",
    "litres": "l",
    "milliliter": "ml",
    "milliliters": "ml",
    "gallon": "gal",
    "gallons": "gal",
    "pint": "pt",
    "pints": "pt",
    "km/h": "kmh",
    "m/s": "ms",
    "knot": "kn",
    "knots": "kn",
    # Температура
    "c": "c",
    "celsius": "c",
    "f": "f",
    "fahrenheit": "f",
    "k": "k",
    "kelvin": "k",
    "°c": "c",
    "°f": "f",
}

_TEMP_UNITS = {"c", "f", "k"}


def _normalize_unit(raw: str) -> str:
    """Нормализует строку единицы: lower + алиасы → канонический ключ."""
    key = raw.lower().strip()
    return _CONVERT_ALIASES.get(key, key)


def _convert_temperature(value: float, src: str, dst: str) -> float:
    """Конвертация температуры между C / F / K."""
    if src == "c":
        celsius = value
    elif src == "f":
        celsius = (value - 32) * 5.0 / 9.0
    elif src == "k":
        celsius = value - 273.15
    else:
        raise ValueError(f"Неизвестная единица температуры: {src}")
    if dst == "c":
        return celsius
    if dst == "f":
        return celsius * 9.0 / 5.0 + 32
    if dst == "k":
        return celsius + 273.15
    raise ValueError(f"Неизвестная единица температуры: {dst}")


def _do_convert(value: float, src: str, dst: str) -> float:
    """Конвертирует value из src в dst."""
    src_n = _normalize_unit(src)
    dst_n = _normalize_unit(dst)

    if src_n in _TEMP_UNITS or dst_n in _TEMP_UNITS:
        if src_n not in _TEMP_UNITS or dst_n not in _TEMP_UNITS:
            raise ValueError("Нельзя конвертировать температуру и другие единицы вместе.")
        return _convert_temperature(value, src_n, dst_n)

    if src_n not in _CONVERT_UNITS:
        raise ValueError(f"Неизвестная единица: `{src}`")
    if dst_n not in _CONVERT_UNITS:
        raise ValueError(f"Неизвестная единица: `{dst}`")

    src_factor, src_base = _CONVERT_UNITS[src_n]
    dst_factor, dst_base = _CONVERT_UNITS[dst_n]

    if src_base != dst_base:
        raise ValueError(f"Несовместимые единицы: `{src}` ({src_base}) и `{dst}` ({dst_base})")

    return value * src_factor / dst_factor


def _format_convert_result(result: float) -> str:
    """Форматирует число: до 6 значащих цифр, без лишних нулей."""
    if result == int(result) and abs(result) < 1e12:
        return str(int(result))
    return f"{result:.6g}"


_CONVERT_HELP = (
    "**Использование:** `!convert <число> <из> <в>`\n\n"
    "**Примеры:**\n"
    "`!convert 100 km mi` → 62.14 mi\n"
    "`!convert 72 F C` → 22.22 °C\n"
    "`!convert 5 kg lb` → 11.02 lb\n"
    "`!convert 3.5 L gal` → 0.924 gal\n\n"
    "**Поддерживаемые единицы:**\n"
    "Длина: `km m cm mm mi ft in yd`\n"
    "Масса: `kg g lb oz`\n"
    "Объём: `L mL gal pt`\n"
    "Скорость: `kmh mph m/s kn`\n"
    "Температура: `C F K`"
)


async def handle_convert(bot: "KraabUserbot", message: Message) -> None:
    """Конвертер единиц измерения без внешних API (!convert)."""
    raw_args = bot._get_command_args(message).strip()

    if not raw_args:
        await message.reply(_CONVERT_HELP)
        return

    parts = raw_args.split()
    if len(parts) != 3:
        raise UserInputError(
            user_message=("❌ Формат: `!convert <число> <из> <в>`\nНапример: `!convert 100 km mi`")
        )

    value_str, src_raw, dst_raw = parts

    try:
        value = float(value_str.replace(",", "."))
    except ValueError:
        raise UserInputError(user_message=f"❌ Не могу разобрать число: `{value_str}`")

    converter = _ch_attr("_do_convert", _do_convert)
    try:
        result = converter(value, src_raw, dst_raw)
    except ValueError as exc:
        raise UserInputError(user_message=f"❌ {exc}")

    normalize = _ch_attr("_normalize_unit", _normalize_unit)
    dst_n = normalize(dst_raw)
    if dst_n == "c":
        unit_symbol = "°C"
    elif dst_n == "f":
        unit_symbol = "°F"
    elif dst_n == "k":
        unit_symbol = "K"
    else:
        unit_symbol = dst_raw

    formatter = _ch_attr("_format_convert_result", _format_convert_result)
    result_str = formatter(result)
    src_display = src_raw.upper() if normalize(src_raw) in _TEMP_UNITS else src_raw
    value_display = formatter(value)

    await message.reply(f"🔢 **{value_display} {src_display}** = **{result_str} {unit_symbol}**")


# ---------------------------------------------------------------------------
# !color — конвертер цветов
# ---------------------------------------------------------------------------

_CSS_NAMED_COLORS: dict[str, tuple[int, int, int]] = {
    "aliceblue": (240, 248, 255),
    "antiquewhite": (250, 235, 215),
    "aqua": (0, 255, 255),
    "aquamarine": (127, 255, 212),
    "azure": (240, 255, 255),
    "beige": (245, 245, 220),
    "bisque": (255, 228, 196),
    "black": (0, 0, 0),
    "blanchedalmond": (255, 235, 205),
    "blue": (0, 0, 255),
    "blueviolet": (138, 43, 226),
    "brown": (165, 42, 42),
    "burlywood": (222, 184, 135),
    "cadetblue": (95, 158, 160),
    "chartreuse": (127, 255, 0),
    "chocolate": (210, 105, 30),
    "coral": (255, 127, 80),
    "cornflowerblue": (100, 149, 237),
    "cornsilk": (255, 248, 220),
    "crimson": (220, 20, 60),
    "cyan": (0, 255, 255),
    "darkblue": (0, 0, 139),
    "darkcyan": (0, 139, 139),
    "darkgoldenrod": (184, 134, 11),
    "darkgray": (169, 169, 169),
    "darkgreen": (0, 100, 0),
    "darkgrey": (169, 169, 169),
    "darkkhaki": (189, 183, 107),
    "darkmagenta": (139, 0, 139),
    "darkolivegreen": (85, 107, 47),
    "darkorange": (255, 140, 0),
    "darkorchid": (153, 50, 204),
    "darkred": (139, 0, 0),
    "darksalmon": (233, 150, 122),
    "darkseagreen": (143, 188, 143),
    "darkslateblue": (72, 61, 139),
    "darkslategray": (47, 79, 79),
    "darkslategrey": (47, 79, 79),
    "darkturquoise": (0, 206, 209),
    "darkviolet": (148, 0, 211),
    "deeppink": (255, 20, 147),
    "deepskyblue": (0, 191, 255),
    "dimgray": (105, 105, 105),
    "dimgrey": (105, 105, 105),
    "dodgerblue": (30, 144, 255),
    "firebrick": (178, 34, 34),
    "floralwhite": (255, 250, 240),
    "forestgreen": (34, 139, 34),
    "fuchsia": (255, 0, 255),
    "gainsboro": (220, 220, 220),
    "ghostwhite": (248, 248, 255),
    "gold": (255, 215, 0),
    "goldenrod": (218, 165, 32),
    "gray": (128, 128, 128),
    "green": (0, 128, 0),
    "greenyellow": (173, 255, 47),
    "grey": (128, 128, 128),
    "honeydew": (240, 255, 240),
    "hotpink": (255, 105, 180),
    "indianred": (205, 92, 92),
    "indigo": (75, 0, 130),
    "ivory": (255, 255, 240),
    "khaki": (240, 230, 140),
    "lavender": (230, 230, 250),
    "lavenderblush": (255, 240, 245),
    "lawngreen": (124, 252, 0),
    "lemonchiffon": (255, 250, 205),
    "lightblue": (173, 216, 230),
    "lightcoral": (240, 128, 128),
    "lightcyan": (224, 255, 255),
    "lightgoldenrodyellow": (250, 250, 210),
    "lightgray": (211, 211, 211),
    "lightgreen": (144, 238, 144),
    "lightgrey": (211, 211, 211),
    "lightpink": (255, 182, 193),
    "lightsalmon": (255, 160, 122),
    "lightseagreen": (32, 178, 170),
    "lightskyblue": (135, 206, 250),
    "lightslategray": (119, 136, 153),
    "lightslategrey": (119, 136, 153),
    "lightsteelblue": (176, 196, 222),
    "lightyellow": (255, 255, 224),
    "lime": (0, 255, 0),
    "limegreen": (50, 205, 50),
    "linen": (250, 240, 230),
    "magenta": (255, 0, 255),
    "maroon": (128, 0, 0),
    "mediumaquamarine": (102, 205, 170),
    "mediumblue": (0, 0, 205),
    "mediumorchid": (186, 85, 211),
    "mediumpurple": (147, 112, 219),
    "mediumseagreen": (60, 179, 113),
    "mediumslateblue": (123, 104, 238),
    "mediumspringgreen": (0, 250, 154),
    "mediumturquoise": (72, 209, 204),
    "mediumvioletred": (199, 21, 133),
    "midnightblue": (25, 25, 112),
    "mintcream": (245, 255, 250),
    "mistyrose": (255, 228, 225),
    "moccasin": (255, 228, 181),
    "navajowhite": (255, 222, 173),
    "navy": (0, 0, 128),
    "oldlace": (253, 245, 230),
    "olive": (128, 128, 0),
    "olivedrab": (107, 142, 35),
    "orange": (255, 165, 0),
    "orangered": (255, 69, 0),
    "orchid": (218, 112, 214),
    "palegoldenrod": (238, 232, 170),
    "palegreen": (152, 251, 152),
    "paleturquoise": (175, 238, 238),
    "palevioletred": (219, 112, 147),
    "papayawhip": (255, 239, 213),
    "peachpuff": (255, 218, 185),
    "peru": (205, 133, 63),
    "pink": (255, 192, 203),
    "plum": (221, 160, 221),
    "powderblue": (176, 224, 230),
    "purple": (128, 0, 128),
    "rebeccapurple": (102, 51, 153),
    "red": (255, 0, 0),
    "rosybrown": (188, 143, 143),
    "royalblue": (65, 105, 225),
    "saddlebrown": (139, 69, 19),
    "salmon": (250, 128, 114),
    "sandybrown": (244, 164, 96),
    "seagreen": (46, 139, 87),
    "seashell": (255, 245, 238),
    "sienna": (160, 82, 45),
    "silver": (192, 192, 192),
    "skyblue": (135, 206, 235),
    "slateblue": (106, 90, 205),
    "slategray": (112, 128, 144),
    "slategrey": (112, 128, 144),
    "snow": (255, 250, 250),
    "springgreen": (0, 255, 127),
    "steelblue": (70, 130, 180),
    "tan": (210, 180, 140),
    "teal": (0, 128, 128),
    "thistle": (216, 191, 216),
    "tomato": (255, 99, 71),
    "turquoise": (64, 224, 208),
    "violet": (238, 130, 238),
    "wheat": (245, 222, 179),
    "white": (255, 255, 255),
    "whitesmoke": (245, 245, 245),
    "yellow": (255, 255, 0),
    "yellowgreen": (154, 205, 50),
}


def _rgb_to_hsl(r: int, g: int, b: int) -> tuple[int, int, int]:
    """Конвертирует RGB (0–255) в HSL (H: 0–360°, S: 0–100%, L: 0–100%)."""
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    cmax = max(rf, gf, bf)
    cmin = min(rf, gf, bf)
    delta = cmax - cmin

    l_val = (cmax + cmin) / 2.0

    if delta == 0.0:
        s_val = 0.0
    else:
        s_val = delta / (1.0 - abs(2.0 * l_val - 1.0))

    if delta == 0.0:
        h_val = 0.0
    elif cmax == rf:
        h_val = 60.0 * (((gf - bf) / delta) % 6)
    elif cmax == gf:
        h_val = 60.0 * (((bf - rf) / delta) + 2.0)
    else:
        h_val = 60.0 * (((rf - gf) / delta) + 4.0)

    return round(h_val), round(s_val * 100), round(l_val * 100)


def _parse_color_input(raw: str) -> tuple[int, int, int] | None:
    """Разбирает строку с цветом и возвращает (R, G, B) или None."""
    s = raw.strip()

    hex_match = re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", s)
    if hex_match:
        h = hex_match.group(1)
        if len(h) == 3:
            h = h[0] * 2 + h[1] * 2 + h[2] * 2
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    rgb_match = re.fullmatch(
        r"rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)", s, re.IGNORECASE
    )
    if rgb_match:
        r_val = int(rgb_match.group(1))
        g_val = int(rgb_match.group(2))
        b_val = int(rgb_match.group(3))
        if 0 <= r_val <= 255 and 0 <= g_val <= 255 and 0 <= b_val <= 255:
            return r_val, g_val, b_val
        return None

    name = s.lower().replace("-", "").replace(" ", "")
    if name in _CSS_NAMED_COLORS:
        return _CSS_NAMED_COLORS[name]

    return None


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    """Конвертирует RGB в HEX строку вида #RRGGBB (верхний регистр)."""
    return f"#{r:02X}{g:02X}{b:02X}"


async def handle_color(bot: "KraabUserbot", message: Message) -> None:
    """!color <цвет> — конвертер цветов между форматами HEX, RGB и HSL."""
    raw = bot._get_command_args(message).strip()

    if not raw:
        raise UserInputError(
            user_message=(
                "🎨 **!color — конвертер цветов**\n\n"
                "Форматы ввода:\n"
                "`!color #FF5733`        → RGB + HSL\n"
                "`!color rgb(255,87,51)` → HEX + HSL\n"
                "`!color red`            → HEX + RGB + HSL\n\n"
                "Поддерживаются: HEX (#RRGGBB, #RGB), rgb(...), CSS named colors"
            )
        )

    parser = _ch_attr("_parse_color_input", _parse_color_input)
    parsed = parser(raw)
    if parsed is None:
        raise UserInputError(
            user_message=(
                f"❌ Не удалось распознать цвет: `{raw}`\n\n"
                "Допустимые форматы: `#FF5733`, `#F57`, `rgb(255,87,51)`, `red`"
            )
        )

    r, g, b = parsed
    hex_fn = _ch_attr("_rgb_to_hex", _rgb_to_hex)
    hsl_fn = _ch_attr("_rgb_to_hsl", _rgb_to_hsl)
    hex_val = hex_fn(r, g, b)
    h_deg, s_pct, l_pct = hsl_fn(r, g, b)

    s_lower = raw.strip().lower()
    is_hex = s_lower.startswith("#")
    is_rgb_fmt = s_lower.startswith("rgb(")
    is_named = not is_hex and not is_rgb_fmt

    lines: list[str] = [f"🎨 Цвет: `{raw}`\n"]

    if is_hex or is_named:
        lines.append(f"RGB: `rgb({r}, {g}, {b})`")
    if is_rgb_fmt or is_named:
        lines.append(f"HEX: `{hex_val}`")
    lines.append(f"HSL: `hsl({h_deg}°, {s_pct}%, {l_pct}%)`")

    await message.reply("\n".join(lines))


# ---------------------------------------------------------------------------
# !emoji — поиск эмодзи по описанию
# ---------------------------------------------------------------------------

_EMOJI_DB: dict[str, list[str]] = {
    # Огонь / энергия
    "fire": ["🔥"],
    "flame": ["🔥"],
    "hot": ["🔥", "🌡️"],
    # Сердце / любовь
    "heart": ["❤️", "💜", "💙", "💚", "🖤", "🤍", "🧡", "💛", "💗", "💓", "💞", "💕", "💔", "❣️"],
    "love": ["❤️", "💕", "😍", "😘", "🥰"],
    "kiss": ["😘", "💋", "😗"],
    "hug": ["🤗"],
    # Кошки
    "cat": ["🐱", "🐈", "😺", "😸", "😹", "😻", "😼", "😽", "🙀", "😿", "😾"],
    "kitten": ["🐱", "😺"],
    "meow": ["🐱", "😺"],
    # Собаки
    "dog": ["🐶", "🐕", "🦮", "🐩"],
    "puppy": ["🐶"],
    "woof": ["🐶"],
    # Смех / радость
    "laugh": ["😂", "🤣", "😄", "😁"],
    "happy": ["😊", "😄", "😁", "🙂", "😀"],
    "joy": ["😂", "🥳"],
    "smile": ["😊", "🙂", "😀", "😁"],
    "lol": ["😂", "🤣"],
    "haha": ["😂", "🤣"],
    # Грусть / слёзы
    "sad": ["😢", "😭", "😔", "😞"],
    "cry": ["😢", "😭"],
    "tear": ["😢", "😭"],
    # Злость
    "angry": ["😠", "😡", "🤬"],
    "rage": ["😡", "🤬"],
    "mad": ["😠", "😡"],
    # Удивление
    "surprise": ["😮", "😲", "🤯"],
    "shock": ["😱", "😨", "🤯"],
    "wow": ["😮", "🤩", "😲"],
    "mind": ["🤯"],
    # Испуг
    "fear": ["😨", "😰", "😱"],
    "scared": ["😨", "😱"],
    # Сон
    "sleep": ["😴", "💤"],
    "tired": ["😴", "🥱"],
    "yawn": ["🥱"],
    # Еда
    "pizza": ["🍕"],
    "burger": ["🍔"],
    "taco": ["🌮"],
    "sushi": ["🍣"],
    "ramen": ["🍜"],
    "cake": ["🎂", "🍰"],
    "coffee": ["☕", "🍵"],
    "tea": ["🍵", "🫖"],
    "beer": ["🍺", "🍻"],
    "wine": ["🍷"],
    "cocktail": ["🍹", "🍸"],
    "cookie": ["🍪"],
    "bread": ["🍞"],
    "apple": ["🍎"],
    "banana": ["🍌"],
    "strawberry": ["🍓"],
    "watermelon": ["🍉"],
    "grapes": ["🍇"],
    "mango": ["🥭"],
    "avocado": ["🥑"],
    "salad": ["🥗"],
    "chicken": ["🍗"],
    "steak": ["🥩"],
    "egg": ["🥚"],
    "icecream": ["🍦", "🍧", "🍨"],
    "chocolate": ["🍫"],
    "candy": ["🍬", "🍭"],
    "donut": ["🍩"],
    # Природа / животные
    "sun": ["☀️", "🌞"],
    "moon": ["🌙", "🌕", "🌝"],
    "star": ["⭐", "🌟", "✨", "💫"],
    "cloud": ["☁️", "⛅"],
    "rain": ["🌧️", "🌂"],
    "snow": ["❄️", "☃️", "🌨️"],
    "thunder": ["⛈️", "🌩️"],
    "rainbow": ["🌈"],
    "flower": ["🌸", "🌺", "🌼", "🌻", "🌹", "💐"],
    "rose": ["🌹"],
    "leaf": ["🍀", "🍃", "🌿"],
    "tree": ["🌳", "🌲", "🎄"],
    "mountain": ["⛰️", "🏔️"],
    "ocean": ["🌊", "🏖️"],
    "water": ["💧", "🌊"],
    "earth": ["🌍", "🌎", "🌏"],
    "fish": ["🐟", "🐠", "🐡"],
    "bird": ["🐦", "🦜", "🦅", "🦆"],
    "butterfly": ["🦋"],
    "bee": ["🐝"],
    "snake": ["🐍"],
    "frog": ["🐸"],
    "rabbit": ["🐰", "🐇"],
    "bear": ["🐻"],
    "panda": ["🐼"],
    "fox": ["🦊"],
    "wolf": ["🐺"],
    "lion": ["🦁"],
    "tiger": ["🐯"],
    "horse": ["🐴", "🦄"],
    "unicorn": ["🦄"],
    "monkey": ["🐵", "🙈", "🙉", "🙊"],
    "pig": ["🐷", "🐖"],
    "cow": ["🐮", "🐄"],
    "elephant": ["🐘"],
    "dolphin": ["🐬"],
    "shark": ["🦈"],
    "turtle": ["🐢"],
    "crab": ["🦀"],
    "lobster": ["🦞"],
    "octopus": ["🐙"],
    # Жесты / реакции
    "ok": ["👌", "✅"],
    "yes": ["✅", "👍"],
    "no": ["❌", "👎"],
    "thumbsup": ["👍"],
    "thumbsdown": ["👎"],
    "clap": ["👏"],
    "wave": ["👋"],
    "point": ["👉", "👆", "👇", "👈"],
    "muscle": ["💪"],
    "fist": ["✊", "👊"],
    "peace": ["✌️", "☮️"],
    "pray": ["🙏"],
    "eyes": ["👀"],
    "think": ["🤔"],
    "shrug": ["🤷"],
    "facepalm": ["🤦"],
    "celebrate": ["🎉", "🥳", "🎊"],
    "party": ["🎉", "🎊", "🥳"],
    # Техника
    "phone": ["📱", "☎️"],
    "computer": ["💻", "🖥️"],
    "camera": ["📷", "📸"],
    "music": ["🎵", "🎶"],
    "headphones": ["🎧"],
    "rocket": ["🚀"],
    "robot": ["🤖"],
    "alien": ["👽"],
    "ghost": ["👻"],
    "skull": ["💀"],
    "bomb": ["💣"],
    "lightning": ["⚡"],
    "magnet": ["🧲"],
    "lock": ["🔒"],
    "key": ["🔑", "🗝️"],
    "money": ["💰", "💵", "💸"],
    "gem": ["💎"],
    "crown": ["👑"],
    "trophy": ["🏆"],
    "medal": ["🥇", "🥈", "🥉"],
    "sword": ["⚔️"],
    "shield": ["🛡️"],
    "magic": ["✨", "🪄"],
    "book": ["📚", "📖"],
    "pencil": ["✏️", "📝"],
    "clock": ["🕐", "⏰", "⏱️"],
    "calendar": ["📅", "📆"],
    "mail": ["📧", "✉️"],
    "bell": ["🔔"],
    "flag": ["🚩", "🏁"],
    "search": ["🔍", "🔎"],
    "bulb": ["💡"],
    "warning": ["⚠️"],
    "forbidden": ["🚫"],
    "check": ["✅", "☑️"],
    "cross": ["❌"],
    "plus": ["➕"],
    "minus": ["➖"],
    "infinity": ["♾️"],
    # Транспорт
    "car": ["🚗", "🚙"],
    "bus": ["🚌"],
    "plane": ["✈️"],
    "ship": ["🚢"],
    "bike": ["🚲", "🛵"],
    "train": ["🚂", "🚆"],
    # Разное
    "poop": ["💩"],
    "nerd": ["🤓"],
    "cool": ["😎"],
    "sick": ["🤒", "🤧"],
    "mask": ["😷"],
    "zombie": ["🧟"],
    "vampire": ["🧛"],
    "mermaid": ["🧜"],
    "fairy": ["🧚"],
    "angel": ["😇"],
    "devil": ["😈"],
    "clown": ["🤡"],
    "santa": ["🎅"],
    "snowman": ["☃️"],
    "christmas": ["🎄", "🎅", "🎁"],
    "gift": ["🎁"],
    "balloon": ["🎈"],
    "confetti": ["🎊", "🎉"],
    "sparkles": ["✨"],
    "diamond": ["💎"],
}


def _emoji_search(query: str) -> list[str]:
    """Ищет эмодзи по ключевому слову. Возвращает дедуплицированный список."""
    q = query.strip().lower()
    seen: set[str] = set()
    results: list[str] = []

    if q in _EMOJI_DB:
        for em in _EMOJI_DB[q]:
            if em not in seen:
                seen.add(em)
                results.append(em)

    for key, emojis in _EMOJI_DB.items():
        if key == q:
            continue
        if q in key or key in q:
            for em in emojis:
                if em not in seen:
                    seen.add(em)
                    results.append(em)

    return results


async def handle_emoji(bot: "KraabUserbot", message: Message) -> None:
    """Поиск эмодзи по текстовому описанию."""
    raw = bot._get_command_args(message).strip()

    if not raw:
        await message.reply(
            "😊 **!emoji** — поиск эмодзи по описанию\n\n"
            "`!emoji <слово>` — первое совпадение\n"
            "`!emoji search <слово>` — все варианты\n\n"
            "_Примеры: `!emoji fire`, `!emoji heart`, `!emoji search cat`_"
        )
        return

    parts = raw.split(maxsplit=1)
    show_all = parts[0].lower() == "search"
    if show_all:
        query = parts[1].strip() if len(parts) > 1 else ""
    else:
        query = raw

    if not query:
        await message.reply("🔍 Укажи слово для поиска: `!emoji search <слово>`")
        return

    searcher = _ch_attr("_emoji_search", _emoji_search)
    matches = searcher(query)

    if not matches:
        await message.reply(
            f"🤷 Эмодзи для «{query}» не найдены.\n"
            "_Попробуй синоним на английском: fire, heart, cat, smile..._"
        )
        return

    if show_all:
        line = " ".join(matches)
        await message.reply(f"🔍 `{query}` → {line}")
    else:
        preview = " ".join(matches[:5])
        if len(matches) > 5:
            suffix = f" _( +{len(matches) - 5} ещё — `!emoji search {query}`)_"
        else:
            suffix = ""
        await message.reply(f"{preview}{suffix}")


# ---------------------------------------------------------------------------
# !news — новости через AI
# ---------------------------------------------------------------------------

_NEWS_LANG_MAP: dict[str, str] = {
    "ru": "на русском языке",
    "рус": "на русском языке",
    "rus": "на русском языке",
    "en": "на английском языке",
    "eng": "на английском языке",
}

_NEWS_KNOWN_TOPICS: frozenset[str] = frozenset(
    {
        "crypto",
        "крипто",
        "криптовалюта",
        "ai",
        "ии",
        "ml",
        "tech",
        "технологии",
        "технология",
        "finance",
        "финансы",
        "финансовые",
        "science",
        "наука",
        "politics",
        "политика",
        "business",
        "бизнес",
        "sports",
        "спорт",
        "gaming",
        "игры",
        "space",
        "космос",
        "health",
        "здоровье",
        "world",
        "мир",
        "russia",
        "россия",
        "usa",
        "сша",
    }
)


async def handle_news(bot: "KraabUserbot", message: Message) -> None:
    """Быстрые новости через AI (web_search)."""
    raw = bot._get_command_args(message).strip()

    lang_suffix = ""
    topic = "мировые события"

    lang_map = _ch_attr("_NEWS_LANG_MAP", _NEWS_LANG_MAP)
    if raw:
        first_word = raw.split()[0].lower()
        if first_word in lang_map:
            lang_suffix = f" {lang_map[first_word]}"
            rest = raw[len(first_word) :].strip()
            if rest:
                topic = rest
        else:
            topic = raw

    prompt = (
        f"Дай топ-5 главных новостей за сегодня по теме: {topic}. "
        f"Кратко, с источниками{lang_suffix}. "
        "Формат каждой новости: порядковый номер, заголовок, одно-два предложения сути, источник/URL."
    )

    session_id = f"news_{message.chat.id}"

    display_topic = topic if topic != "мировые события" else "топ новостей"
    msg = await message.reply(f"📰 **Краб читает новости:** `{display_topic}`...")

    try:
        chunks: list[str] = []
        client = _ch_attr("openclaw_client", _openclaw_client_default)
        async for chunk in client.send_message_stream(
            message=prompt,
            chat_id=session_id,
            disable_tools=False,
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()

        if not result:
            await msg.edit("❌ Не удалось получить новости. Попробуй позже.")
            return

        header = f"📰 **{display_topic.capitalize()}**\n\n"
        full_text = header + result

        splitter = _ch_attr("_split_text_for_telegram", _split_text_for_telegram_default)
        parts = splitter(full_text)
        total = len(parts)

        first = parts[0]
        if total > 1:
            first += f"\n\n_(часть 1/{total})_"
        await msg.edit(first)

        for i, part in enumerate(parts[1:], start=2):
            suffix = f"\n\n_(часть {i}/{total})_" if total > 2 else ""
            await message.reply(part + suffix)

    except Exception as exc:  # noqa: BLE001
        logger.error("handle_news_error", topic=topic, error=str(exc))
        await msg.edit(f"❌ Ошибка получения новостей: {exc}")
