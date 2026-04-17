# -*- coding: utf-8 -*-
"""
Юнит-тесты для src.core.cron_spec_parser.parse_cron_expression.

Покрытие:
  - Daily RU/EN ("каждый день в HH:MM", "every day at HH:MM", "ежедневно", "daily")
  - Every N hours ("каждые 2 часа", "every 4 hours")
  - Every N minutes ("каждые 15 минут", "every 30 minutes")
  - Weekly RU (понедельник..воскресенье в разных падежах)
  - Weekly EN (monday..sunday)
  - Прямой cron ("0 10 * * *", "*/5 * * * *", "0 8-18 * * 1-5")
  - Invalid: пустая строка, мусор, неверное время
"""

from __future__ import annotations

from src.core.cron_spec_parser import parse_cron_expression

# --------------------------------------------------------------------------
# Daily
# --------------------------------------------------------------------------


def test_parse_every_day_ru() -> None:
    assert parse_cron_expression("каждый день в 10:00") == "0 10 * * *"


def test_parse_every_day_ru_with_mins() -> None:
    assert parse_cron_expression("каждый день в 09:30") == "30 9 * * *"


def test_parse_every_day_ru_dot_separator() -> None:
    # "в 10.00" — редкая форма, но поддерживаем : и .
    assert parse_cron_expression("каждый день в 10.00") == "0 10 * * *"


def test_parse_every_day_en() -> None:
    assert parse_cron_expression("every day at 14:30") == "30 14 * * *"


def test_parse_each_day_en() -> None:
    assert parse_cron_expression("each day at 06:00") == "0 6 * * *"


def test_parse_daily_en() -> None:
    assert parse_cron_expression("daily 23:59") == "59 23 * * *"


def test_parse_ezhednevno_ru() -> None:
    assert parse_cron_expression("ежедневно 07:15") == "15 7 * * *"


# --------------------------------------------------------------------------
# Every N hours
# --------------------------------------------------------------------------


def test_parse_every_2_hours_ru() -> None:
    assert parse_cron_expression("каждые 2 часа") == "0 */2 * * *"


def test_parse_every_4_hours_en() -> None:
    assert parse_cron_expression("every 4 hours") == "0 */4 * * *"


def test_parse_every_hour_singular_en() -> None:
    assert parse_cron_expression("every 1 hour") == "0 */1 * * *"


def test_parse_every_6_hours_ru_alt() -> None:
    # "6 часов" — plural
    assert parse_cron_expression("каждые 6 часов") == "0 */6 * * *"


# --------------------------------------------------------------------------
# Every N minutes
# --------------------------------------------------------------------------


def test_parse_every_15_minutes_ru() -> None:
    assert parse_cron_expression("каждые 15 минут") == "*/15 * * * *"


def test_parse_every_30_minutes_en() -> None:
    assert parse_cron_expression("every 30 minutes") == "*/30 * * * *"


# --------------------------------------------------------------------------
# Weekly RU
# --------------------------------------------------------------------------


def test_parse_weekly_ru_monday() -> None:
    assert parse_cron_expression("каждый понедельник в 10:00") == "0 10 * * 1"


def test_parse_weekly_ru_friday_accusative() -> None:
    # "каждую пятницу" — winitelny case
    assert parse_cron_expression("каждую пятницу в 18:30") == "30 18 * * 5"


def test_parse_weekly_ru_sunday() -> None:
    assert (
        parse_cron_expression("каждое воскресенье в 12:00") == "0 12 * * 0"
        or parse_cron_expression("каждый воскресенье в 12:00") == "0 12 * * 0"
    )


def test_parse_weekly_ru_saturday() -> None:
    assert parse_cron_expression("каждую субботу в 09:00") == "0 9 * * 6"


# --------------------------------------------------------------------------
# Weekly EN
# --------------------------------------------------------------------------


def test_parse_weekly_en_monday() -> None:
    assert parse_cron_expression("every monday at 09:00") == "0 9 * * 1"


def test_parse_weekly_en_friday() -> None:
    assert parse_cron_expression("every friday at 17:30") == "30 17 * * 5"


def test_parse_weekly_en_sunday() -> None:
    assert parse_cron_expression("every sunday at 08:15") == "15 8 * * 0"


# --------------------------------------------------------------------------
# Прямой cron (passthrough)
# --------------------------------------------------------------------------


def test_parse_direct_cron_simple() -> None:
    assert parse_cron_expression("0 10 * * *") == "0 10 * * *"


def test_parse_direct_cron_star_slash() -> None:
    assert parse_cron_expression("*/5 * * * *") == "*/5 * * * *"


def test_parse_direct_cron_with_range() -> None:
    assert parse_cron_expression("0 8-18 * * 1-5") == "0 8-18 * * 1-5"


def test_parse_direct_cron_with_list() -> None:
    assert parse_cron_expression("0 9,12,18 * * *") == "0 9,12,18 * * *"


# --------------------------------------------------------------------------
# Invalid / None
# --------------------------------------------------------------------------


def test_parse_empty_string() -> None:
    assert parse_cron_expression("") is None


def test_parse_whitespace_only() -> None:
    assert parse_cron_expression("   ") is None


def test_parse_non_string() -> None:
    assert parse_cron_expression(None) is None  # type: ignore[arg-type]


def test_parse_garbage_text() -> None:
    assert parse_cron_expression("какая-то хуйня") is None


def test_parse_garbage_english() -> None:
    assert parse_cron_expression("sometimes maybe never") is None


def test_parse_invalid_time_out_of_range() -> None:
    # 25:00 — неверный час
    assert parse_cron_expression("каждый день в 25:00") is None


def test_parse_invalid_minutes_out_of_range() -> None:
    assert parse_cron_expression("каждый день в 10:99") is None


def test_parse_invalid_cron_wrong_fields() -> None:
    # 4 поля вместо 5
    assert parse_cron_expression("0 10 * *") is None


def test_parse_invalid_cron_letters() -> None:
    assert parse_cron_expression("a b c d e") is None


def test_parse_every_zero_hours() -> None:
    assert parse_cron_expression("каждые 0 часов") is None


def test_parse_every_24_hours() -> None:
    # 24 часа — превышает допустимый диапазон для */N
    assert parse_cron_expression("каждые 24 часа") is None


def test_parse_weekly_ru_missing_time() -> None:
    # день указан, но время отсутствует → None
    assert parse_cron_expression("каждый понедельник") is None


def test_parse_daily_without_time() -> None:
    # "каждый день" без указания времени → None
    assert parse_cron_expression("каждый день") is None
