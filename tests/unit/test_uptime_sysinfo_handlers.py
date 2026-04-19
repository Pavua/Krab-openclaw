# -*- coding: utf-8 -*-
"""
Тесты для !sysinfo и !uptime — команды системной информации.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.handlers.command_handlers import (
    _format_uptime_str,
    handle_sysinfo,
    handle_uptime,
)

# ---------------------------------------------------------------------------
# _format_uptime_str — unit-тесты форматирования
# ---------------------------------------------------------------------------


class TestFormatUptimeStr:
    """Проверяем форматирование секунд в строку uptime."""

    def test_only_minutes(self) -> None:
        assert _format_uptime_str(300) == "5м"

    def test_hours_and_minutes(self) -> None:
        assert _format_uptime_str(3600 + 15 * 60) == "1ч 15м"

    def test_days_hours_minutes(self) -> None:
        elapsed = 2 * 86400 + 3 * 3600 + 45 * 60
        assert _format_uptime_str(elapsed) == "2д 3ч 45м"

    def test_zero(self) -> None:
        assert _format_uptime_str(0) == "0м"

    def test_exactly_one_hour(self) -> None:
        assert _format_uptime_str(3600) == "1ч 0м"

    def test_exactly_one_day(self) -> None:
        # При точно 1 дне нет часов — формат "1д 0м"
        assert _format_uptime_str(86400) == "1д 0м"

    def test_float_input(self) -> None:
        # Должен корректно принимать float
        result = _format_uptime_str(125.9)
        assert "2м" in result

    def test_large_value(self) -> None:
        # 30 дней
        result = _format_uptime_str(30 * 86400)
        assert "30д" in result


# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------


def _make_bot(start_offset: float = 100.0) -> MagicMock:
    """Создаёт мок-бота с _session_start_time."""
    bot = MagicMock()
    bot._session_start_time = time.time() - start_offset
    return bot


def _make_message() -> AsyncMock:
    msg = AsyncMock()
    msg.reply = AsyncMock()
    return msg


# ---------------------------------------------------------------------------
# handle_sysinfo — интеграционные тесты
# ---------------------------------------------------------------------------


class TestHandleSysinfo:
    """Тесты для !sysinfo."""

    @pytest.mark.asyncio
    async def test_reply_called(self) -> None:
        """handle_sysinfo всегда вызывает message.reply."""
        bot = _make_bot()
        msg = _make_message()
        await handle_sysinfo(bot, msg)
        msg.reply.assert_called_once()

    @pytest.mark.asyncio
    async def test_reply_contains_system_info_header(self) -> None:
        """Ответ содержит заголовок System Info."""
        bot = _make_bot()
        msg = _make_message()
        await handle_sysinfo(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "System Info" in text

    @pytest.mark.asyncio
    async def test_reply_contains_macos_line(self) -> None:
        """Ответ содержит строку macOS."""
        bot = _make_bot()
        msg = _make_message()
        await handle_sysinfo(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "macOS" in text or "OS:" in text

    @pytest.mark.asyncio
    async def test_reply_contains_ram_line(self) -> None:
        """Ответ содержит строку RAM."""
        bot = _make_bot()
        msg = _make_message()
        await handle_sysinfo(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "RAM" in text

    @pytest.mark.asyncio
    async def test_reply_contains_disk_line(self) -> None:
        """Ответ содержит строку Disk."""
        bot = _make_bot()
        msg = _make_message()
        await handle_sysinfo(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "Disk" in text

    @pytest.mark.asyncio
    async def test_reply_contains_python_line(self) -> None:
        """Ответ содержит строку Python."""
        bot = _make_bot()
        msg = _make_message()
        await handle_sysinfo(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "Python" in text

    @pytest.mark.asyncio
    async def test_reply_contains_krab_pid(self) -> None:
        """Ответ содержит строку Krab с PID."""
        bot = _make_bot()
        msg = _make_message()
        await handle_sysinfo(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "PID" in text

    @pytest.mark.asyncio
    async def test_krab_uptime_in_reply(self) -> None:
        """Uptime Краба включает единицы времени (м/ч/д)."""
        bot = _make_bot(start_offset=3700.0)  # ~1ч 1м
        msg = _make_message()
        await handle_sysinfo(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "Krab" in text and ("ч" in text or "м" in text)

    @pytest.mark.asyncio
    async def test_survives_missing_start_time(self) -> None:
        """Если у бота нет _session_start_time — не падает."""
        bot = MagicMock(spec=[])  # нет атрибутов
        msg = _make_message()
        await handle_sysinfo(bot, msg)  # должно не бросить исключение
        msg.reply.assert_called_once()

    @pytest.mark.asyncio
    async def test_psutil_failure_graceful(self) -> None:
        """Если psutil падает — ответ всё равно отправляется."""
        bot = _make_bot()
        msg = _make_message()
        with patch("psutil.virtual_memory", side_effect=RuntimeError("psutil error")):
            with patch("psutil.disk_usage", side_effect=RuntimeError("disk error")):
                await handle_sysinfo(bot, msg)
        msg.reply.assert_called_once()
        text: str = msg.reply.call_args[0][0]
        assert "System Info" in text

    @pytest.mark.asyncio
    async def test_network_failure_graceful(self) -> None:
        """Если сеть недоступна — не падает, пишет N/A."""
        import socket

        bot = _make_bot()
        msg = _make_message()
        with patch.object(socket.socket, "connect", side_effect=OSError("no route")):
            await handle_sysinfo(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "Network: N/A" in text

    @pytest.mark.asyncio
    async def test_separator_present(self) -> None:
        """Ответ содержит разделитель."""
        bot = _make_bot()
        msg = _make_message()
        await handle_sysinfo(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "─" in text


# ---------------------------------------------------------------------------
# handle_uptime — тесты
# ---------------------------------------------------------------------------


class TestHandleUptime:
    """Тесты для !uptime."""

    @pytest.mark.asyncio
    async def test_reply_called(self) -> None:
        """handle_uptime всегда вызывает message.reply."""
        bot = _make_bot()
        msg = _make_message()
        await handle_uptime(bot, msg)
        msg.reply.assert_called_once()

    @pytest.mark.asyncio
    async def test_header_present(self) -> None:
        """Ответ содержит заголовок Uptime."""
        bot = _make_bot()
        msg = _make_message()
        await handle_uptime(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "Uptime" in text

    @pytest.mark.asyncio
    async def test_macos_line_present(self) -> None:
        """Ответ содержит строку macOS."""
        bot = _make_bot()
        msg = _make_message()
        await handle_uptime(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "macOS" in text

    @pytest.mark.asyncio
    async def test_krab_line_present(self) -> None:
        """Ответ содержит строку Краб."""
        bot = _make_bot()
        msg = _make_message()
        await handle_uptime(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "Краб" in text

    @pytest.mark.asyncio
    async def test_openclaw_line_present(self) -> None:
        """Ответ содержит строку OpenClaw."""
        bot = _make_bot()
        msg = _make_message()
        await handle_uptime(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "OpenClaw" in text

    @pytest.mark.asyncio
    async def test_sysctl_failure_graceful(self) -> None:
        """Если sysctl недоступен — не падает, пишет N/A."""
        bot = _make_bot()
        msg = _make_message()
        with patch("subprocess.run", side_effect=FileNotFoundError("no sysctl")):
            await handle_uptime(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "macOS: N/A" in text

    @pytest.mark.asyncio
    async def test_openclaw_online_with_uptime(self) -> None:
        """Если OpenClaw отвечает с полем uptime — отображаем его."""
        bot = _make_bot()
        msg = _make_message()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"uptime": 7200}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await handle_uptime(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "OpenClaw" in text
        assert "2ч" in text  # 7200 сек = 2ч

    @pytest.mark.asyncio
    async def test_openclaw_online_no_uptime_field(self) -> None:
        """Если OpenClaw онлайн но без поля uptime — показываем Online."""
        bot = _make_bot()
        msg = _make_message()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await handle_uptime(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Online" in text

    @pytest.mark.asyncio
    async def test_openclaw_offline(self) -> None:
        """Если OpenClaw недоступен — показываем offline."""
        bot = _make_bot()
        msg = _make_message()

        with patch("httpx.AsyncClient", side_effect=Exception("connection refused")):
            await handle_uptime(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Offline" in text or "Недоступен" in text

    @pytest.mark.asyncio
    async def test_krab_uptime_formatted(self) -> None:
        """Uptime Краба корректно форматируется."""
        bot = _make_bot(start_offset=5400.0)  # 1ч 30м
        msg = _make_message()
        await handle_uptime(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "1ч" in text and "30м" in text

    @pytest.mark.asyncio
    async def test_missing_start_time_graceful(self) -> None:
        """Если у бота нет _session_start_time — Краб: N/A."""
        bot = MagicMock(spec=[])
        msg = _make_message()
        await handle_uptime(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "Краб: N/A" in text

    @pytest.mark.asyncio
    async def test_separator_present(self) -> None:
        """Ответ содержит разделитель."""
        bot = _make_bot()
        msg = _make_message()
        await handle_uptime(bot, msg)
        text: str = msg.reply.call_args[0][0]
        assert "─" in text

    @pytest.mark.asyncio
    async def test_sysctl_parses_boot_time(self) -> None:
        """sysctl kern.boottime корректно парсится."""
        bot = _make_bot()
        msg = _make_message()

        # Эмулируем ответ sysctl с boot time 1 час назад
        import time as _t

        boot_ts = int(_t.time()) - 3600
        fake_output = f"{{ sec = {boot_ts}, usec = 0 }} Sun Apr  6 12:00:00 2025"

        mock_result = MagicMock()
        mock_result.stdout = fake_output

        with patch("subprocess.run", return_value=mock_result):
            await handle_uptime(bot, msg)

        text: str = msg.reply.call_args[0][0]
        # Должно быть что-то вроде "1ч 0м" или "0ч ..." — главное не N/A
        assert "macOS: N/A" not in text
        assert "macOS:" in text
