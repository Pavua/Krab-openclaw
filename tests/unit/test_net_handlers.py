# -*- coding: utf-8 -*-
"""
Тесты для сетевых утилит: !ip, !dns, !ping.
"""

from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import (
    _get_local_ip,
    _get_public_ip,
    handle_dns,
    handle_ip,
    handle_ping,
)


# ---------------------------------------------------------------------------
# Вспомогательные утилиты
# ---------------------------------------------------------------------------


def _make_bot(args: str = "") -> MagicMock:
    """Создать mock-бот с _get_command_args."""
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value=args)
    return bot


def _make_message() -> AsyncMock:
    """Создать mock-сообщение с async reply."""
    msg = MagicMock()
    msg.reply = AsyncMock()
    return msg


# ---------------------------------------------------------------------------
# _get_local_ip — unit
# ---------------------------------------------------------------------------


class TestGetLocalIp:
    """Тесты вспомогательной функции _get_local_ip."""

    def test_возвращает_строку(self) -> None:
        """Функция должна вернуть строку."""
        result = _get_local_ip()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_fallback_при_ошибке(self) -> None:
        """При ошибке сети — возвращает 'н/д'."""
        with patch("socket.socket") as mock_socket_cls:
            mock_socket_cls.return_value.__enter__.return_value.connect.side_effect = OSError
            result = _get_local_ip()
        assert result == "н/д"

    def test_типичный_ip_формат(self) -> None:
        """Локальный IP — 4 октета, разделённых точками."""
        with patch("socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_sock.getsockname.return_value = ("192.168.1.100", 0)
            mock_socket_cls.return_value.__enter__.return_value = mock_sock
            result = _get_local_ip()
        assert result == "192.168.1.100"


# ---------------------------------------------------------------------------
# _get_public_ip — unit
# ---------------------------------------------------------------------------


class TestGetPublicIp:
    """Тесты получения публичного IP."""

    @pytest.mark.asyncio
    async def test_возвращает_ip_из_json(self) -> None:
        """Функция читает поле 'ip' из JSON-ответа."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={"ip": "85.123.45.67"})

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("src.handlers.command_handlers.httpx.AsyncClient", return_value=mock_client):
            result = await _get_public_ip()

        assert result == "85.123.45.67"

    @pytest.mark.asyncio
    async def test_пробрасывает_исключение_при_ошибке_сети(self) -> None:
        """При сетевой ошибке — бросает исключение."""
        import httpx

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("timeout"))

        with patch("src.handlers.command_handlers.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(Exception):
                await _get_public_ip()


# ---------------------------------------------------------------------------
# handle_ip
# ---------------------------------------------------------------------------


class TestHandleIp:
    """Тесты хендлера !ip."""

    @pytest.mark.asyncio
    async def test_без_аргументов_показывает_public_и_local(self) -> None:
        """!ip — показывает оба адреса."""
        bot = _make_bot("")
        msg = _make_message()

        with (
            patch("src.handlers.command_handlers._get_local_ip", return_value="192.168.1.1"),
            patch(
                "src.handlers.command_handlers._get_public_ip",
                new=AsyncMock(return_value="85.1.2.3"),
            ),
        ):
            await handle_ip(bot, msg)

        msg.reply.assert_awaited_once()
        reply_text: str = msg.reply.call_args[0][0]
        assert "Public" in reply_text
        assert "85.1.2.3" in reply_text
        assert "Local" in reply_text
        assert "192.168.1.1" in reply_text

    @pytest.mark.asyncio
    async def test_local_показывает_только_локальный(self) -> None:
        """!ip local — только локальный IP, без HTTP."""
        bot = _make_bot("local")
        msg = _make_message()

        with patch("src.handlers.command_handlers._get_local_ip", return_value="10.0.0.5"):
            await handle_ip(bot, msg)

        msg.reply.assert_awaited_once()
        reply_text: str = msg.reply.call_args[0][0]
        assert "Local" in reply_text
        assert "10.0.0.5" in reply_text
        assert "Public" not in reply_text

    @pytest.mark.asyncio
    async def test_local_не_делает_http_запрос(self) -> None:
        """!ip local — _get_public_ip не вызывается."""
        bot = _make_bot("local")
        msg = _make_message()

        mock_public = AsyncMock(return_value="1.2.3.4")
        with (
            patch("src.handlers.command_handlers._get_local_ip", return_value="127.0.0.1"),
            patch("src.handlers.command_handlers._get_public_ip", new=mock_public),
        ):
            await handle_ip(bot, msg)

        mock_public.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ошибка_public_ip_бросает_user_input_error(self) -> None:
        """При недоступности ipify — UserInputError с понятным сообщением."""
        import httpx

        bot = _make_bot("")
        msg = _make_message()

        with (
            patch("src.handlers.command_handlers._get_local_ip", return_value="10.0.0.1"),
            patch(
                "src.handlers.command_handlers._get_public_ip",
                new=AsyncMock(side_effect=httpx.ConnectError("err")),
            ),
        ):
            with pytest.raises(UserInputError) as exc_info:
                await handle_ip(bot, msg)

        assert "публичный IP" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_формат_ответа_содержит_заголовок(self) -> None:
        """Ответ содержит заголовок '🌐 **IP Info**'."""
        bot = _make_bot("local")
        msg = _make_message()

        with patch("src.handlers.command_handlers._get_local_ip", return_value="1.1.1.1"):
            await handle_ip(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "IP Info" in reply_text

    @pytest.mark.asyncio
    async def test_формат_содержит_разделитель(self) -> None:
        """Ответ содержит разделитель '─────'."""
        bot = _make_bot("local")
        msg = _make_message()

        with patch("src.handlers.command_handlers._get_local_ip", return_value="1.1.1.1"):
            await handle_ip(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "─────" in reply_text


# ---------------------------------------------------------------------------
# handle_dns
# ---------------------------------------------------------------------------


class TestHandleDns:
    """Тесты хендлера !dns."""

    @pytest.mark.asyncio
    async def test_без_аргументов_бросает_ошибку(self) -> None:
        """!dns без домена — UserInputError."""
        bot = _make_bot("")
        msg = _make_message()

        with pytest.raises(UserInputError) as exc_info:
            await handle_dns(bot, msg)

        assert "домен" in exc_info.value.user_message.lower() or "dns" in exc_info.value.user_message.lower()

    @pytest.mark.asyncio
    async def test_возвращает_a_записи(self) -> None:
        """Успешный lookup — A-запись присутствует в ответе."""
        bot = _make_bot("example.com")
        msg = _make_message()

        fake_a = [(socket.AF_INET, None, None, None, ("93.184.216.34", 0))]

        with (
            patch(
                "asyncio.get_event_loop",
                return_value=MagicMock(
                    run_in_executor=AsyncMock(return_value=fake_a)
                ),
            ),
            patch(
                "asyncio.create_subprocess_exec",
                new=AsyncMock(
                    return_value=MagicMock(
                        communicate=AsyncMock(return_value=(b"", b"")),
                        returncode=0,
                    )
                ),
            ),
        ):
            await handle_dns(bot, msg)

        msg.reply.assert_awaited_once()
        reply_text: str = msg.reply.call_args[0][0]
        assert "93.184.216.34" in reply_text

    @pytest.mark.asyncio
    async def test_заголовок_содержит_домен(self) -> None:
        """В заголовке ответа — переданный домен."""
        bot = _make_bot("google.com")
        msg = _make_message()

        fake_a = [(socket.AF_INET, None, None, None, ("8.8.8.8", 0))]

        with (
            patch(
                "asyncio.get_event_loop",
                return_value=MagicMock(
                    run_in_executor=AsyncMock(return_value=fake_a)
                ),
            ),
            patch(
                "asyncio.create_subprocess_exec",
                new=AsyncMock(
                    return_value=MagicMock(
                        communicate=AsyncMock(return_value=(b"", b"")),
                        returncode=0,
                    )
                ),
            ),
        ):
            await handle_dns(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "google.com" in reply_text

    @pytest.mark.asyncio
    async def test_gaierror_показывает_нд(self) -> None:
        """При socket.gaierror для A-записей — показывает 'н/д'."""
        bot = _make_bot("nonexistent.invalid")
        msg = _make_message()

        with (
            patch(
                "asyncio.get_event_loop",
                return_value=MagicMock(
                    run_in_executor=AsyncMock(side_effect=socket.gaierror("no such host"))
                ),
            ),
            patch(
                "asyncio.create_subprocess_exec",
                new=AsyncMock(
                    return_value=MagicMock(
                        communicate=AsyncMock(return_value=(b"", b"")),
                        returncode=1,
                    )
                ),
            ),
        ):
            await handle_dns(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "н/д" in reply_text


# ---------------------------------------------------------------------------
# handle_ping
# ---------------------------------------------------------------------------


class TestHandlePing:
    """Тесты хендлера !ping."""

    @pytest.mark.asyncio
    async def test_без_аргументов_бросает_ошибку(self) -> None:
        """!ping без хоста — UserInputError."""
        bot = _make_bot("")
        msg = _make_message()

        with pytest.raises(UserInputError) as exc_info:
            await handle_ping(bot, msg)

        assert "хост" in exc_info.value.user_message.lower() or "ping" in exc_info.value.user_message.lower()

    @pytest.mark.asyncio
    async def test_успешный_ping_с_latency(self) -> None:
        """Ping успешен, latency парсится из вывода."""
        bot = _make_bot("8.8.8.8")
        msg = _make_message()

        ping_output = (
            "PING 8.8.8.8 (8.8.8.8): 56 data bytes\n"
            "64 bytes from 8.8.8.8: icmp_seq=0 ttl=115 time=12.3 ms\n"
            "\n"
            "--- 8.8.8.8 ping statistics ---\n"
        ).encode()

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(ping_output, b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
            await handle_ping(bot, msg)

        msg.reply.assert_awaited_once()
        reply_text: str = msg.reply.call_args[0][0]
        assert "12.3" in reply_text
        assert "доступен" in reply_text

    @pytest.mark.asyncio
    async def test_хост_недоступен(self) -> None:
        """Ping вернул ненулевой код — 'недоступен' в ответе."""
        bot = _make_bot("192.0.2.1")
        msg = _make_message()

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"Request timeout", b""))
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
            await handle_ping(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "недоступен" in reply_text

    @pytest.mark.asyncio
    async def test_timeout_бросает_user_input_error(self) -> None:
        """asyncio.TimeoutError → UserInputError с 'timeout'."""
        bot = _make_bot("slow.host")
        msg = _make_message()

        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=asyncio.TimeoutError),
        ):
            with pytest.raises(UserInputError) as exc_info:
                await handle_ping(bot, msg)

        assert "timeout" in exc_info.value.user_message.lower()

    @pytest.mark.asyncio
    async def test_ответ_содержит_имя_хоста(self) -> None:
        """Имя хоста всегда отображается в ответе."""
        bot = _make_bot("example.com")
        msg = _make_message()

        ping_output = b"64 bytes from 1.2.3.4: icmp_seq=0 ttl=55 time=5.0 ms\n"
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(ping_output, b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
            await handle_ping(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "example.com" in reply_text

    @pytest.mark.asyncio
    async def test_успешный_ping_без_latency_в_выводе(self) -> None:
        """Ping успешен, но latency не распарсилась — статус ✅ без числа."""
        bot = _make_bot("localhost")
        msg = _make_message()

        ping_output = b"PING localhost: 56 data bytes\n"
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(ping_output, b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
            await handle_ping(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "доступен" in reply_text

    @pytest.mark.asyncio
    async def test_общая_ошибка_subprocess_бросает_user_input_error(self) -> None:
        """Любая Exception при запуске ping → UserInputError."""
        bot = _make_bot("bad-host")
        msg = _make_message()

        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=OSError("no such file")),
        ):
            with pytest.raises(UserInputError) as exc_info:
                await handle_ping(bot, msg)

        assert "ping" in exc_info.value.user_message.lower()
