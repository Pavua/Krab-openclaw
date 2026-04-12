# -*- coding: utf-8 -*-
"""
Тесты для Telegram-команды !report.

Покрывает:
- !report daily    — дневной отчёт
- !report weekly   — недельный через WeeklyDigest
- !report <тема>   — кастомный через LLM
- owner-only защита
- вспомогательные функции _collect_daily_report_data, _render_daily_report
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.access_control import AccessLevel
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import (
    _collect_daily_report_data,
    _render_daily_report,
    handle_report,
)


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_bot(is_owner: bool = True, cmd_args: str = "") -> MagicMock:
    """Mock KraabUserbot с заданным уровнем доступа."""
    bot = MagicMock()
    level = AccessLevel.OWNER if is_owner else AccessLevel.PARTIAL

    class _FakeProfile:
        def __init__(self):
            self.level = level

    bot._get_access_profile = MagicMock(return_value=_FakeProfile())
    bot._get_command_args = MagicMock(return_value=cmd_args)
    return bot


def _make_message(text: str = "!report daily") -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.from_user = SimpleNamespace(id=100, username="owner")
    msg.chat = SimpleNamespace(id=12345)
    msg.reply = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    return msg


def _make_status_msg() -> MagicMock:
    """Mock для сообщения-плейсхолдера (возвращается reply)."""
    status = MagicMock()
    status.edit = AsyncMock()
    return status


# ---------------------------------------------------------------------------
# _collect_daily_report_data
# ---------------------------------------------------------------------------


class TestCollectDailyReportData:
    def test_returns_all_required_keys(self) -> None:
        """Функция возвращает все ожидаемые ключи."""
        from src.core.cost_analytics import CostAnalytics
        from src.core.inbox_service import InboxService

        fake_ca = CostAnalytics()
        fake_inbox = MagicMock()
        fake_inbox.get_summary.return_value = {"open": 2, "error": 1, "warning": 1}

        fake_sas = MagicMock()
        fake_sas.list_artifacts.return_value = []

        with (
            patch("src.handlers.command_handlers.cost_analytics", fake_ca),
            patch("src.handlers.command_handlers.inbox_service", fake_inbox),
            patch("src.core.swarm_artifact_store.swarm_artifact_store", fake_sas),
        ):
            data = _collect_daily_report_data()

        required_keys = {
            "cost_today_usd", "cost_month_usd", "calls_today", "tokens_today",
            "swarm_rounds_today", "swarm_teams_today", "swarm_duration_today",
            "inbox_open", "inbox_errors", "inbox_warnings",
        }
        assert required_keys.issubset(data.keys())

    def test_cost_today_counts_only_today(self) -> None:
        """cost_today_usd учитывает только вызовы за сегодня."""
        import time

        from src.core.cost_analytics import CallRecord, CostAnalytics

        fake_ca = CostAnalytics()
        # Вызов за сегодня
        today_ts = time.mktime(datetime.date.today().timetuple()) + 3600
        # Вызов за вчера
        yesterday_ts = today_ts - 86400

        fake_ca._calls = [
            CallRecord(model_id="m", input_tokens=100, output_tokens=50, cost_usd=0.01, timestamp=today_ts),
            CallRecord(model_id="m", input_tokens=100, output_tokens=50, cost_usd=0.05, timestamp=yesterday_ts),
        ]

        fake_inbox = MagicMock()
        fake_inbox.get_summary.return_value = {"open": 0, "error": 0, "warning": 0}
        fake_sas = MagicMock()
        fake_sas.list_artifacts.return_value = []

        with (
            patch("src.handlers.command_handlers.cost_analytics", fake_ca),
            patch("src.handlers.command_handlers.inbox_service", fake_inbox),
        ):
            data = _collect_daily_report_data()

        # Только один вызов за сегодня
        assert data["calls_today"] == 1
        assert abs(data["cost_today_usd"] - 0.01) < 1e-6

    def test_swarm_rounds_filtered_by_today(self) -> None:
        """swarm_rounds_today считает только сегодняшние артефакты."""
        today_str = datetime.date.today().isoformat()
        yesterday_str = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

        fake_sas = MagicMock()
        fake_sas.list_artifacts.return_value = [
            {"timestamp_iso": f"{today_str}T10:00:00", "team": "coders", "duration_sec": 120},
            {"timestamp_iso": f"{today_str}T12:00:00", "team": "traders", "duration_sec": 60},
            {"timestamp_iso": f"{yesterday_str}T09:00:00", "team": "analysts", "duration_sec": 80},
        ]

        fake_ca = MagicMock()
        fake_ca._calls = []
        fake_ca.get_monthly_cost_usd.return_value = 0.0
        fake_inbox = MagicMock()
        fake_inbox.get_summary.return_value = {"open": 0, "error": 0, "warning": 0}

        # Патчим модуль swarm_artifact_store внутри command_handlers (локальный import)
        with (
            patch("src.handlers.command_handlers.cost_analytics", fake_ca),
            patch("src.handlers.command_handlers.inbox_service", fake_inbox),
            patch("src.core.swarm_artifact_store.SwarmArtifactStore.list_artifacts", return_value=fake_sas.list_artifacts.return_value),
        ):
            data = _collect_daily_report_data()

        assert data["swarm_rounds_today"] == 2
        assert set(data["swarm_teams_today"]) == {"coders", "traders"}
        assert data["swarm_duration_today"] == 180

    def test_graceful_on_cost_error(self) -> None:
        """При ошибке cost_analytics возвращает нули."""
        fake_ca = MagicMock()
        fake_ca._calls = None  # вызовет TypeError при итерации

        fake_inbox = MagicMock()
        fake_inbox.get_summary.return_value = {"open": 0, "error": 0, "warning": 0}
        fake_sas = MagicMock()
        fake_sas.list_artifacts.return_value = []

        with (
            patch("src.handlers.command_handlers.cost_analytics", fake_ca),
            patch("src.handlers.command_handlers.inbox_service", fake_inbox),
        ):
            data = _collect_daily_report_data()

        assert data["cost_today_usd"] == 0.0
        assert data["calls_today"] == 0

    def test_graceful_on_inbox_error(self) -> None:
        """При ошибке inbox_service возвращает нули для inbox-полей."""
        from src.core.cost_analytics import CostAnalytics
        fake_ca = CostAnalytics()

        fake_inbox = MagicMock()
        fake_inbox.get_summary.side_effect = RuntimeError("db error")
        fake_sas = MagicMock()
        fake_sas.list_artifacts.return_value = []

        with (
            patch("src.handlers.command_handlers.cost_analytics", fake_ca),
            patch("src.handlers.command_handlers.inbox_service", fake_inbox),
        ):
            data = _collect_daily_report_data()

        assert data["inbox_open"] == 0
        assert data["inbox_errors"] == 0


# ---------------------------------------------------------------------------
# _render_daily_report
# ---------------------------------------------------------------------------


class TestRenderDailyReport:
    def _sample_data(self) -> dict:
        return {
            "cost_today_usd": 0.0123,
            "cost_month_usd": 1.5,
            "calls_today": 42,
            "tokens_today": 10000,
            "swarm_rounds_today": 3,
            "swarm_teams_today": ["coders", "traders"],
            "swarm_duration_today": 300,
            "inbox_open": 5,
            "inbox_errors": 2,
            "inbox_warnings": 3,
        }

    def test_contains_today_date(self) -> None:
        """Отчёт содержит сегодняшнюю дату."""
        data = self._sample_data()
        result = _render_daily_report(data)
        assert datetime.date.today().isoformat() in result

    def test_contains_cost(self) -> None:
        """Отчёт содержит стоимость."""
        data = self._sample_data()
        result = _render_daily_report(data)
        assert "0.0123" in result
        assert "1.5" in result

    def test_contains_swarm_teams(self) -> None:
        """Отчёт содержит команды свёрма."""
        data = self._sample_data()
        result = _render_daily_report(data)
        assert "coders" in result
        assert "traders" in result

    def test_contains_inbox_errors(self) -> None:
        """Отчёт содержит информацию об ошибках inbox."""
        data = self._sample_data()
        result = _render_daily_report(data)
        assert "2" in result  # errors count
        assert "3" in result  # warnings count

    def test_no_teams_section_when_empty(self) -> None:
        """При пустом списке команд строка команд не добавляется."""
        data = self._sample_data()
        data["swarm_teams_today"] = []
        data["swarm_duration_today"] = 0
        result = _render_daily_report(data)
        assert "Команды:" not in result
        assert "Суммарное время" not in result

    def test_is_markdown(self) -> None:
        """Отчёт содержит markdown-разметку."""
        data = self._sample_data()
        result = _render_daily_report(data)
        assert "**" in result


# ---------------------------------------------------------------------------
# handle_report — общие проверки
# ---------------------------------------------------------------------------


class TestHandleReportAccess:
    @pytest.mark.asyncio
    async def test_non_owner_raises(self) -> None:
        """Не-владелец получает UserInputError."""
        bot = _make_bot(is_owner=False, cmd_args="daily")
        msg = _make_message("!report daily")

        with pytest.raises(UserInputError) as exc_info:
            await handle_report(bot, msg)
        assert "владельцу" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_no_args_raises_with_help(self) -> None:
        """Без аргументов выбрасывается UserInputError с подсказкой."""
        bot = _make_bot(is_owner=True, cmd_args="")
        msg = _make_message("!report")

        with pytest.raises(UserInputError) as exc_info:
            await handle_report(bot, msg)

        assert "daily" in str(exc_info.value.user_message).lower()
        assert "weekly" in str(exc_info.value.user_message).lower()

    @pytest.mark.asyncio
    async def test_help_raises_with_help(self) -> None:
        """!report help выбрасывает UserInputError с инструкцией."""
        bot = _make_bot(is_owner=True, cmd_args="help")
        msg = _make_message("!report help")

        with pytest.raises(UserInputError):
            await handle_report(bot, msg)


# ---------------------------------------------------------------------------
# handle_report daily
# ---------------------------------------------------------------------------


class TestHandleReportDaily:
    @pytest.mark.asyncio
    async def test_daily_sends_report(self) -> None:
        """!report daily отправляет дневной отчёт."""
        bot = _make_bot(is_owner=True, cmd_args="daily")
        msg = _make_message("!report daily")
        status_msg = _make_status_msg()
        msg.reply.return_value = status_msg

        fake_data = {
            "cost_today_usd": 0.01,
            "cost_month_usd": 0.5,
            "calls_today": 5,
            "tokens_today": 2000,
            "swarm_rounds_today": 1,
            "swarm_teams_today": ["coders"],
            "swarm_duration_today": 60,
            "inbox_open": 0,
            "inbox_errors": 0,
            "inbox_warnings": 0,
        }

        with patch("src.handlers.command_handlers._collect_daily_report_data", return_value=fake_data):
            await handle_report(bot, msg)

        status_msg.edit.assert_called_once()
        call_arg = status_msg.edit.call_args[0][0]
        assert "Daily Report" in call_arg

    @pytest.mark.asyncio
    async def test_daily_russian_aliases(self) -> None:
        """!report день и !report дневной тоже работают."""
        for alias in ("день", "дневной"):
            bot = _make_bot(is_owner=True, cmd_args=alias)
            msg = _make_message(f"!report {alias}")
            status_msg = _make_status_msg()
            msg.reply.return_value = status_msg

            fake_data = {
                "cost_today_usd": 0.0,
                "cost_month_usd": 0.0,
                "calls_today": 0,
                "tokens_today": 0,
                "swarm_rounds_today": 0,
                "swarm_teams_today": [],
                "swarm_duration_today": 0,
                "inbox_open": 0,
                "inbox_errors": 0,
                "inbox_warnings": 0,
            }

            with patch("src.handlers.command_handlers._collect_daily_report_data", return_value=fake_data):
                await handle_report(bot, msg)

            status_msg.edit.assert_called_once()

    @pytest.mark.asyncio
    async def test_daily_collect_error_returns_error_msg(self) -> None:
        """При ошибке сбора данных выводит сообщение об ошибке."""
        bot = _make_bot(is_owner=True, cmd_args="daily")
        msg = _make_message("!report daily")
        status_msg = _make_status_msg()
        msg.reply.return_value = status_msg

        with patch(
            "src.handlers.command_handlers._collect_daily_report_data",
            side_effect=RuntimeError("boom"),
        ):
            await handle_report(bot, msg)

        call_arg = status_msg.edit.call_args[0][0]
        assert "❌" in call_arg


# ---------------------------------------------------------------------------
# handle_report weekly
# ---------------------------------------------------------------------------


class TestHandleReportWeekly:
    @pytest.mark.asyncio
    async def test_weekly_success(self) -> None:
        """!report weekly выводит недельный отчёт."""
        bot = _make_bot(is_owner=True, cmd_args="weekly")
        msg = _make_message("!report weekly")
        status_msg = _make_status_msg()
        msg.reply.return_value = status_msg

        fake_result = {
            "ok": True,
            "total_rounds": 10,
            "cost_week_usd": 0.25,
            "attention_count": 3,
            "calls_count": 50,
            "total_tokens": 100000,
        }

        with patch("src.handlers.command_handlers.weekly_digest") as mock_wd:
            mock_wd.generate_digest = AsyncMock(return_value=fake_result)
            await handle_report(bot, msg)

        status_msg.edit.assert_called_once()
        call_arg = status_msg.edit.call_args[0][0]
        assert "Weekly Report" in call_arg
        assert "10" in call_arg  # rounds
        assert "0.2500" in call_arg  # cost

    @pytest.mark.asyncio
    async def test_weekly_russian_aliases(self) -> None:
        """!report неделя и !report недельный тоже работают."""
        for alias in ("неделя", "недельный"):
            bot = _make_bot(is_owner=True, cmd_args=alias)
            msg = _make_message(f"!report {alias}")
            status_msg = _make_status_msg()
            msg.reply.return_value = status_msg

            fake_result = {
                "ok": True,
                "total_rounds": 5,
                "cost_week_usd": 0.1,
                "attention_count": 0,
                "calls_count": 20,
                "total_tokens": 50000,
            }

            with patch("src.handlers.command_handlers.weekly_digest") as mock_wd:
                mock_wd.generate_digest = AsyncMock(return_value=fake_result)
                await handle_report(bot, msg)

            status_msg.edit.assert_called_once()

    @pytest.mark.asyncio
    async def test_weekly_digest_error(self) -> None:
        """При ошибке generate_digest выводит сообщение об ошибке."""
        bot = _make_bot(is_owner=True, cmd_args="weekly")
        msg = _make_message("!report weekly")
        status_msg = _make_status_msg()
        msg.reply.return_value = status_msg

        with patch("src.handlers.command_handlers.weekly_digest") as mock_wd:
            mock_wd.generate_digest = AsyncMock(side_effect=RuntimeError("network error"))
            await handle_report(bot, msg)

        call_arg = status_msg.edit.call_args[0][0]
        assert "❌" in call_arg

    @pytest.mark.asyncio
    async def test_weekly_digest_not_ok(self) -> None:
        """Если result['ok'] == False — выводит ошибку."""
        bot = _make_bot(is_owner=True, cmd_args="weekly")
        msg = _make_message("!report weekly")
        status_msg = _make_status_msg()
        msg.reply.return_value = status_msg

        fake_result = {"ok": False, "error": "no data"}

        with patch("src.handlers.command_handlers.weekly_digest") as mock_wd:
            mock_wd.generate_digest = AsyncMock(return_value=fake_result)
            await handle_report(bot, msg)

        call_arg = status_msg.edit.call_args[0][0]
        assert "❌" in call_arg
        assert "no data" in call_arg


# ---------------------------------------------------------------------------
# handle_report <тема> — кастомный LLM-отчёт
# ---------------------------------------------------------------------------


class TestHandleReportCustom:
    @pytest.mark.asyncio
    async def test_custom_topic_streams_response(self) -> None:
        """Кастомный отчёт потоково стримит ответ LLM."""
        topic = "расходы за апрель"
        bot = _make_bot(is_owner=True, cmd_args=topic)
        msg = _make_message(f"!report {topic}")
        status_msg = _make_status_msg()
        msg.reply.return_value = status_msg

        fake_data = {
            "cost_today_usd": 0.0,
            "cost_month_usd": 0.0,
            "calls_today": 0,
            "tokens_today": 0,
            "swarm_rounds_today": 0,
            "swarm_teams_today": [],
            "swarm_duration_today": 0,
            "inbox_open": 0,
            "inbox_errors": 0,
            "inbox_warnings": 0,
        }

        async def _fake_stream(**kwargs):
            yield "Анализ "
            yield "расходов "
            yield "завершён."

        with (
            patch("src.handlers.command_handlers._collect_daily_report_data", return_value=fake_data),
            patch("src.handlers.command_handlers.openclaw_client") as mock_oc,
        ):
            mock_oc.send_message_stream = _fake_stream
            await handle_report(bot, msg)

        # Хотя бы одно edit было вызвано
        assert status_msg.edit.call_count >= 1
        # Последний edit содержит тему
        final_call = status_msg.edit.call_args_list[-1][0][0]
        assert topic in final_call

    @pytest.mark.asyncio
    async def test_custom_topic_llm_error(self) -> None:
        """При ошибке LLM стриминга выводит сообщение об ошибке."""
        bot = _make_bot(is_owner=True, cmd_args="экономика")
        msg = _make_message("!report экономика")
        status_msg = _make_status_msg()
        msg.reply.return_value = status_msg

        fake_data = {
            "cost_today_usd": 0.0,
            "cost_month_usd": 0.0,
            "calls_today": 0,
            "tokens_today": 0,
            "swarm_rounds_today": 0,
            "swarm_teams_today": [],
            "swarm_duration_today": 0,
            "inbox_open": 0,
            "inbox_errors": 0,
            "inbox_warnings": 0,
        }

        async def _failing_stream(**kwargs):
            raise RuntimeError("LLM unavailable")
            yield  # делает функцию async generator

        with (
            patch("src.handlers.command_handlers._collect_daily_report_data", return_value=fake_data),
            patch("src.handlers.command_handlers.openclaw_client") as mock_oc,
        ):
            mock_oc.send_message_stream = _failing_stream
            await handle_report(bot, msg)

        # Должен показать ошибку
        error_calls = [
            c for c in status_msg.edit.call_args_list
            if "❌" in str(c)
        ]
        assert len(error_calls) >= 1

    @pytest.mark.asyncio
    async def test_custom_topic_header_in_response(self) -> None:
        """Заголовок отчёта содержит тему."""
        topic = "производительность свёрма"
        bot = _make_bot(is_owner=True, cmd_args=topic)
        msg = _make_message(f"!report {topic}")
        status_msg = _make_status_msg()
        msg.reply.return_value = status_msg

        fake_data = {
            "cost_today_usd": 0.0,
            "cost_month_usd": 0.0,
            "calls_today": 0,
            "tokens_today": 0,
            "swarm_rounds_today": 0,
            "swarm_teams_today": [],
            "swarm_duration_today": 0,
            "inbox_open": 0,
            "inbox_errors": 0,
            "inbox_warnings": 0,
        }

        async def _simple_stream(**kwargs):
            yield "Краткий отчёт готов."

        with (
            patch("src.handlers.command_handlers._collect_daily_report_data", return_value=fake_data),
            patch("src.handlers.command_handlers.openclaw_client") as mock_oc,
        ):
            mock_oc.send_message_stream = _simple_stream
            await handle_report(bot, msg)

        all_edits = [c[0][0] for c in status_msg.edit.call_args_list]
        assert any("Отчёт" in e and topic in e for e in all_edits)

    @pytest.mark.asyncio
    async def test_custom_context_block_graceful_on_error(self) -> None:
        """Если _collect_daily_report_data падает — LLM всё равно вызывается (context_block пустой)."""
        bot = _make_bot(is_owner=True, cmd_args="тест")
        msg = _make_message("!report тест")
        status_msg = _make_status_msg()
        msg.reply.return_value = status_msg

        async def _simple_stream(**kwargs):
            yield "OK"

        with (
            patch(
                "src.handlers.command_handlers._collect_daily_report_data",
                side_effect=RuntimeError("fail"),
            ),
            patch("src.handlers.command_handlers.openclaw_client") as mock_oc,
        ):
            mock_oc.send_message_stream = _simple_stream
            await handle_report(bot, msg)

        # Должен завершиться без исключения и вызвать edit
        assert status_msg.edit.call_count >= 1
