# -*- coding: utf-8 -*-
"""
Тесты для commands/scheduler_commands.py (Phase 2 Wave 3 — Session 27).

Цель — покрытие domain extraction:
- Helpers: _parse_duration, _fmt_duration
- Autodel state: get_autodel_delay, _set_autodel_delay
- Cron jobs.json helpers: _cron_format_schedule, _cron_format_last_status
- Re-exports: handlers и state доступны через src.handlers.command_handlers
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.handlers.commands import scheduler_commands as sc

# ---------------------------------------------------------------------------
# _parse_duration / _fmt_duration
# ---------------------------------------------------------------------------


class TestParseDuration:
    def test_seconds(self) -> None:
        assert sc._parse_duration("90s") == 90

    def test_minutes(self) -> None:
        assert sc._parse_duration("5m") == 300

    def test_compound(self) -> None:
        assert sc._parse_duration("1h30m20s") == 5420

    def test_pure_digit_as_seconds(self) -> None:
        assert sc._parse_duration("3600") == 3600

    def test_zero_returns_none(self) -> None:
        assert sc._parse_duration("0s") is None

    def test_invalid(self) -> None:
        assert sc._parse_duration("abc") is None


class TestFmtDuration:
    def test_seconds(self) -> None:
        assert sc._fmt_duration(45) == "45с"

    def test_minutes_seconds(self) -> None:
        assert sc._fmt_duration(90) == "1м 30с"

    def test_hours(self) -> None:
        assert sc._fmt_duration(3600) == "1ч"


# ---------------------------------------------------------------------------
# Autodel helpers
# ---------------------------------------------------------------------------


class TestAutodelHelpers:
    def test_get_autodel_delay_disabled_by_default(self) -> None:
        bot = MagicMock()
        bot._runtime_state = {}
        assert sc.get_autodel_delay(bot, 123) is None

    def test_set_and_get_delay(self) -> None:
        bot = MagicMock()
        bot._runtime_state = {}
        sc._set_autodel_delay(bot, 123, 60.0)
        assert sc.get_autodel_delay(bot, 123) == 60.0

    def test_set_zero_clears(self) -> None:
        bot = MagicMock()
        bot._runtime_state = {}
        sc._set_autodel_delay(bot, 123, 60.0)
        sc._set_autodel_delay(bot, 123, 0)
        assert sc.get_autodel_delay(bot, 123) is None


# ---------------------------------------------------------------------------
# Cron format helpers
# ---------------------------------------------------------------------------


class TestCronFormatHelpers:
    def test_format_schedule_every_hours(self) -> None:
        assert sc._cron_format_schedule({"schedule": {"kind": "every", "everyMs": 7200000}}) == (
            "каждые 2ч"
        )

    def test_format_schedule_every_minutes(self) -> None:
        assert sc._cron_format_schedule({"schedule": {"kind": "every", "everyMs": 300000}}) == (
            "каждые 5м"
        )

    def test_format_schedule_cron(self) -> None:
        assert (
            sc._cron_format_schedule({"schedule": {"kind": "cron", "expr": "0 10 * * *"}})
            == "cron `0 10 * * *`"
        )

    def test_format_last_status_clean(self) -> None:
        assert sc._cron_format_last_status({"state": {"lastStatus": "ok"}}) == "ok"

    def test_format_last_status_with_errors(self) -> None:
        result = sc._cron_format_last_status(
            {"state": {"lastStatus": "fail", "consecutiveErrors": 3}}
        )
        assert "fail" in result
        assert "3" in result


# ---------------------------------------------------------------------------
# handle_cronstatus — quick smoke
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_cronstatus_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    """!cronstatus отдаёт строку scheduler status."""
    monkeypatch.setattr(
        sc.krab_scheduler,
        "get_status",
        lambda: {
            "scheduler_enabled": True,
            "started": True,
            "pending_count": 5,
            "next_due_at": "2026-04-26T10:00:00",
            "storage_path": "/tmp/x",
        },
    )
    bot = MagicMock()
    bot.client = MagicMock()
    bot.client.send_message = AsyncMock()
    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.id = 100  # ЛС → reply прямой
    msg.reply = AsyncMock()

    await sc.handle_cronstatus(bot, msg)
    msg.reply.assert_called_once()
    text = msg.reply.call_args[0][0]
    assert "Scheduler status" in text
    assert "pending" in text


# ---------------------------------------------------------------------------
# Re-exports: проверяем backward compatibility через command_handlers
# ---------------------------------------------------------------------------


class TestReExports:
    """Проверяем что все handlers/state/helpers доступны через command_handlers."""

    def test_state_dicts_are_same_object(self) -> None:
        """_active_timers / _stopwatches — те же объекты в обоих модулях."""
        from src.handlers import command_handlers as ch

        assert ch._active_timers is sc._active_timers
        assert ch._stopwatches is sc._stopwatches

    def test_helpers_re_exported(self) -> None:
        from src.handlers import command_handlers as ch

        assert ch._parse_duration is sc._parse_duration
        assert ch._fmt_duration is sc._fmt_duration
        assert ch.get_autodel_delay is sc.get_autodel_delay
        assert ch.schedule_autodel is sc.schedule_autodel

    def test_handlers_re_exported(self) -> None:
        from src.handlers import command_handlers as ch

        for name in (
            "handle_timer",
            "handle_stopwatch",
            "handle_remind",
            "handle_reminders",
            "handle_rm_remind",
            "handle_schedule",
            "handle_autodel",
            "handle_todo",
            "handle_cron",
            "handle_cronstatus",
        ):
            assert getattr(ch, name) is getattr(sc, name), f"{name} mismatch"

    def test_remind_help_constant(self) -> None:
        from src.handlers import command_handlers as ch

        assert ch._REMIND_HELP is sc._REMIND_HELP
        assert "Напоминания" in sc._REMIND_HELP

    def test_autodel_state_key(self) -> None:
        from src.handlers import command_handlers as ch

        assert ch._AUTODEL_STATE_KEY == sc._AUTODEL_STATE_KEY == "autodel_settings"
