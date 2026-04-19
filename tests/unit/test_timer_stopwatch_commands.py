# -*- coding: utf-8 -*-
"""
Тесты команд !timer и !stopwatch.

Проверяем:
- _parse_duration: парсинг форматов времени
- _fmt_duration: форматирование секунд
- handle_timer: новый таймер, list, cancel, невалидный ввод
- handle_stopwatch: start, stop, lap, status, двойной старт
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.handlers.command_handlers import (
    _fmt_duration,
    _parse_duration,
)

# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------


def _make_message(text: str = "", chat_id: int = 42) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.reply = AsyncMock()
    return msg


def _make_bot(command_args: str = "") -> MagicMock:
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value=command_args)
    bot.client = MagicMock()
    bot.client.send_message = AsyncMock()
    return bot


# ---------------------------------------------------------------------------
# _parse_duration
# ---------------------------------------------------------------------------


class TestParseDuration:
    """Тесты парсинга длительности."""

    def test_секунды(self) -> None:
        assert _parse_duration("90s") == 90

    def test_минуты(self) -> None:
        assert _parse_duration("5m") == 300

    def test_часы(self) -> None:
        assert _parse_duration("1h") == 3600

    def test_составной_формат_1h30m(self) -> None:
        assert _parse_duration("1h30m") == 5400

    def test_составной_формат_1h30m20s(self) -> None:
        assert _parse_duration("1h30m20s") == 5420

    def test_чистое_число_как_секунды(self) -> None:
        assert _parse_duration("3600") == 3600

    def test_ноль_возвращает_None(self) -> None:
        assert _parse_duration("0s") is None

    def test_пустая_строка_возвращает_None(self) -> None:
        assert _parse_duration("") is None

    def test_невалидная_строка_возвращает_None(self) -> None:
        assert _parse_duration("abc") is None

    def test_только_минуты_30m(self) -> None:
        assert _parse_duration("30m") == 1800


# ---------------------------------------------------------------------------
# _fmt_duration
# ---------------------------------------------------------------------------


class TestFmtDuration:
    """Тесты форматирования длительности."""

    def test_секунды(self) -> None:
        assert _fmt_duration(45) == "45с"

    def test_минуты_и_секунды(self) -> None:
        assert _fmt_duration(90) == "1м 30с"

    def test_часы_минуты_секунды(self) -> None:
        assert _fmt_duration(3661) == "1ч 1м 1с"

    def test_ноль(self) -> None:
        assert _fmt_duration(0) == "0с"

    def test_ровно_минута(self) -> None:
        assert _fmt_duration(60) == "1м"

    def test_ровно_час(self) -> None:
        assert _fmt_duration(3600) == "1ч"


# ---------------------------------------------------------------------------
# handle_timer
# ---------------------------------------------------------------------------


class TestHandleTimer:
    """Тесты управления таймерами."""

    @pytest.mark.asyncio
    async def test_новый_таймер_5m(self) -> None:
        """!timer 5m должен создать таймер и ответить."""
        import src.handlers.command_handlers as hm

        bot = _make_bot("5m")
        msg = _make_message("!timer 5m")

        # Сбрасываем глобальное состояние
        hm._active_timers.clear()
        hm._timer_counter = 0

        with patch("asyncio.create_task") as mock_create_task:
            mock_task = MagicMock()
            mock_create_task.return_value = mock_task

            from src.handlers.command_handlers import handle_timer

            await handle_timer(bot, msg)

        msg.reply.assert_called_once()
        reply_text = msg.reply.call_args[0][0]
        assert "Таймер" in reply_text
        assert "#1" in reply_text
        assert "5м" in reply_text
        assert 1 in hm._active_timers
        hm._active_timers.clear()

    @pytest.mark.asyncio
    async def test_новый_таймер_с_меткой(self) -> None:
        """!timer 10m Обед должен сохранить метку."""
        import src.handlers.command_handlers as hm

        bot = _make_bot("10m Обед")
        msg = _make_message("!timer 10m Обед")
        hm._active_timers.clear()
        hm._timer_counter = 0

        with patch("asyncio.create_task") as mock_create_task:
            mock_create_task.return_value = MagicMock()
            from src.handlers.command_handlers import handle_timer

            await handle_timer(bot, msg)

        assert hm._active_timers[1]["label"] == "Обед"
        reply_text = msg.reply.call_args[0][0]
        assert "Обед" in reply_text
        hm._active_timers.clear()

    @pytest.mark.asyncio
    async def test_list_пустой(self) -> None:
        """!timer list без таймеров — ответить что нет активных."""
        import src.handlers.command_handlers as hm

        bot = _make_bot("list")
        msg = _make_message("!timer list")
        hm._active_timers.clear()

        from src.handlers.command_handlers import handle_timer

        await handle_timer(bot, msg)

        reply_text = msg.reply.call_args[0][0]
        assert "Нет" in reply_text

    @pytest.mark.asyncio
    async def test_list_с_таймером(self) -> None:
        """!timer list показывает активные таймеры."""
        import time

        import src.handlers.command_handlers as hm

        bot = _make_bot("list")
        msg = _make_message("!timer list")
        hm._active_timers.clear()

        mock_task = MagicMock()
        hm._active_timers[99] = {
            "task": mock_task,
            "label": "Тест",
            "ends_at": time.monotonic() + 300,
            "chat_id": 42,
        }

        from src.handlers.command_handlers import handle_timer

        await handle_timer(bot, msg)

        reply_text = msg.reply.call_args[0][0]
        assert "#99" in reply_text
        assert "Тест" in reply_text
        hm._active_timers.clear()

    @pytest.mark.asyncio
    async def test_cancel_по_id(self) -> None:
        """!timer cancel 5 отменяет таймер по ID."""
        import src.handlers.command_handlers as hm

        bot = _make_bot("cancel 5")
        msg = _make_message("!timer cancel 5")
        hm._active_timers.clear()

        mock_task = MagicMock()
        hm._active_timers[5] = {
            "task": mock_task,
            "label": "",
            "ends_at": 0,
            "chat_id": 42,
        }

        from src.handlers.command_handlers import handle_timer

        await handle_timer(bot, msg)

        mock_task.cancel.assert_called_once()
        assert 5 not in hm._active_timers
        reply_text = msg.reply.call_args[0][0]
        assert "отменён" in reply_text

    @pytest.mark.asyncio
    async def test_cancel_несуществующий_id(self) -> None:
        """!timer cancel 999 — ошибка если не найден."""
        import src.handlers.command_handlers as hm

        bot = _make_bot("cancel 999")
        msg = _make_message("!timer cancel 999")
        hm._active_timers.clear()

        from src.handlers.command_handlers import handle_timer

        await handle_timer(bot, msg)

        reply_text = msg.reply.call_args[0][0]
        assert "не найден" in reply_text

    @pytest.mark.asyncio
    async def test_cancel_все(self) -> None:
        """!timer cancel без ID отменяет все таймеры."""
        import src.handlers.command_handlers as hm

        bot = _make_bot("cancel")
        msg = _make_message("!timer cancel")
        hm._active_timers.clear()

        for i in (1, 2, 3):
            hm._active_timers[i] = {
                "task": MagicMock(),
                "label": "",
                "ends_at": 0,
                "chat_id": 42,
            }

        from src.handlers.command_handlers import handle_timer

        await handle_timer(bot, msg)

        assert len(hm._active_timers) == 0
        reply_text = msg.reply.call_args[0][0]
        assert "3" in reply_text

    @pytest.mark.asyncio
    async def test_невалидное_время(self) -> None:
        """!timer xyz — ошибка парсинга."""
        import src.handlers.command_handlers as hm

        bot = _make_bot("xyz")
        msg = _make_message("!timer xyz")
        hm._active_timers.clear()

        from src.handlers.command_handlers import handle_timer

        await handle_timer(bot, msg)

        reply_text = msg.reply.call_args[0][0]
        assert "❌" in reply_text

    @pytest.mark.asyncio
    async def test_пустые_аргументы_показывает_помощь(self) -> None:
        """!timer без аргументов — показать справку."""

        bot = _make_bot("")
        msg = _make_message("!timer")

        from src.handlers.command_handlers import handle_timer

        await handle_timer(bot, msg)

        reply_text = msg.reply.call_args[0][0]
        assert "list" in reply_text


# ---------------------------------------------------------------------------
# handle_stopwatch
# ---------------------------------------------------------------------------


class TestHandleStopwatch:
    """Тесты управления секундомером."""

    def _clear(self) -> None:
        import src.handlers.command_handlers as hm

        hm._stopwatches.clear()

    @pytest.mark.asyncio
    async def test_start(self) -> None:
        """!stopwatch start — запускает секундомер."""
        import src.handlers.command_handlers as hm

        self._clear()

        bot = _make_bot("start")
        msg = _make_message("!stopwatch start", chat_id=10)

        from src.handlers.command_handlers import handle_stopwatch

        await handle_stopwatch(bot, msg)

        assert 10 in hm._stopwatches
        reply_text = msg.reply.call_args[0][0]
        assert "запущен" in reply_text
        self._clear()

    @pytest.mark.asyncio
    async def test_start_дважды(self) -> None:
        """!stopwatch start когда уже запущен — предупреждение."""
        import time

        import src.handlers.command_handlers as hm

        self._clear()

        hm._stopwatches[10] = {"started_at": time.monotonic() - 5, "laps": []}
        bot = _make_bot("start")
        msg = _make_message("!stopwatch start", chat_id=10)

        from src.handlers.command_handlers import handle_stopwatch

        await handle_stopwatch(bot, msg)

        reply_text = msg.reply.call_args[0][0]
        assert "уже запущен" in reply_text
        self._clear()

    @pytest.mark.asyncio
    async def test_stop_без_старта(self) -> None:
        """!stopwatch stop без активного секундомера — ошибка."""
        self._clear()

        bot = _make_bot("stop")
        msg = _make_message("!stopwatch stop", chat_id=10)

        from src.handlers.command_handlers import handle_stopwatch

        await handle_stopwatch(bot, msg)

        reply_text = msg.reply.call_args[0][0]
        assert "не запущен" in reply_text

    @pytest.mark.asyncio
    async def test_stop_после_старта(self) -> None:
        """!stopwatch stop — показывает итоговое время."""
        import time

        import src.handlers.command_handlers as hm

        self._clear()

        hm._stopwatches[10] = {"started_at": time.monotonic() - 10, "laps": []}
        bot = _make_bot("stop")
        msg = _make_message("!stopwatch stop", chat_id=10)

        from src.handlers.command_handlers import handle_stopwatch

        await handle_stopwatch(bot, msg)

        assert 10 not in hm._stopwatches
        reply_text = msg.reply.call_args[0][0]
        assert "Итого" in reply_text

    @pytest.mark.asyncio
    async def test_stop_с_кругами(self) -> None:
        """!stopwatch stop показывает круги."""
        import time

        import src.handlers.command_handlers as hm

        self._clear()

        start = time.monotonic() - 20
        hm._stopwatches[10] = {
            "started_at": start,
            "laps": [start + 5, start + 12],
        }
        bot = _make_bot("stop")
        msg = _make_message("!stopwatch stop", chat_id=10)

        from src.handlers.command_handlers import handle_stopwatch

        await handle_stopwatch(bot, msg)

        reply_text = msg.reply.call_args[0][0]
        assert "Круг 1" in reply_text
        assert "Круг 2" in reply_text

    @pytest.mark.asyncio
    async def test_lap_первый(self) -> None:
        """!stopwatch lap — первый круг без delta."""
        import time

        import src.handlers.command_handlers as hm

        self._clear()

        hm._stopwatches[10] = {"started_at": time.monotonic() - 8, "laps": []}
        bot = _make_bot("lap")
        msg = _make_message("!stopwatch lap", chat_id=10)

        from src.handlers.command_handlers import handle_stopwatch

        await handle_stopwatch(bot, msg)

        assert len(hm._stopwatches[10]["laps"]) == 1
        reply_text = msg.reply.call_args[0][0]
        assert "Круг 1" in reply_text
        self._clear()

    @pytest.mark.asyncio
    async def test_lap_второй(self) -> None:
        """!stopwatch lap второй раз — показывает split с прошлого круга."""
        import time

        import src.handlers.command_handlers as hm

        self._clear()

        start = time.monotonic() - 15
        hm._stopwatches[10] = {"started_at": start, "laps": [start + 7]}
        bot = _make_bot("lap")
        msg = _make_message("!stopwatch lap", chat_id=10)

        from src.handlers.command_handlers import handle_stopwatch

        await handle_stopwatch(bot, msg)

        reply_text = msg.reply.call_args[0][0]
        assert "Круг 2" in reply_text
        assert "с прошлого" in reply_text
        self._clear()

    @pytest.mark.asyncio
    async def test_status_не_запущен(self) -> None:
        """!stopwatch без аргументов когда не запущен — ошибка."""
        self._clear()

        bot = _make_bot("")
        msg = _make_message("!stopwatch", chat_id=10)

        from src.handlers.command_handlers import handle_stopwatch

        await handle_stopwatch(bot, msg)

        reply_text = msg.reply.call_args[0][0]
        assert "не запущен" in reply_text

    @pytest.mark.asyncio
    async def test_status_запущен(self) -> None:
        """!stopwatch без аргументов показывает текущее время."""
        import time

        import src.handlers.command_handlers as hm

        self._clear()

        hm._stopwatches[10] = {"started_at": time.monotonic() - 30, "laps": []}
        bot = _make_bot("")
        msg = _make_message("!stopwatch", chat_id=10)

        from src.handlers.command_handlers import handle_stopwatch

        await handle_stopwatch(bot, msg)

        reply_text = msg.reply.call_args[0][0]
        assert "Текущее время" in reply_text
        self._clear()

    @pytest.mark.asyncio
    async def test_lap_без_старта(self) -> None:
        """!stopwatch lap без активного секундомера — ошибка."""
        self._clear()

        bot = _make_bot("lap")
        msg = _make_message("!stopwatch lap", chat_id=10)

        from src.handlers.command_handlers import handle_stopwatch

        await handle_stopwatch(bot, msg)

        reply_text = msg.reply.call_args[0][0]
        assert "не запущен" in reply_text

    @pytest.mark.asyncio
    async def test_неизвестный_аргумент_показывает_помощь(self) -> None:
        """!stopwatch reset — неизвестная команда, показать справку."""
        self._clear()

        bot = _make_bot("reset")
        msg = _make_message("!stopwatch reset", chat_id=10)

        from src.handlers.command_handlers import handle_stopwatch

        await handle_stopwatch(bot, msg)

        reply_text = msg.reply.call_args[0][0]
        assert "start" in reply_text
