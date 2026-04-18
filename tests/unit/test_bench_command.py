# -*- coding: utf-8 -*-
"""
Тесты для команды !bench (бенчмарк производительности).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.access_control import AccessLevel
from src.handlers.command_handlers import handle_bench


@pytest.mark.asyncio
async def test_handle_bench_owner_only():
    """Только владелец может запускать бенчмарки."""
    bot = MagicMock()
    message = MagicMock()
    message.reply = AsyncMock()

    # Non-owner user
    access_profile = MagicMock()
    access_profile.level = AccessLevel.GUEST
    bot._get_access_profile.return_value = access_profile
    bot._get_command_args.return_value = "fast"

    await handle_bench(bot, message)

    message.reply.assert_called_once()
    call_args = message.reply.call_args[0]
    assert "⛔" in call_args[0]
    assert "владельца" in call_args[0]


@pytest.mark.asyncio
async def test_handle_bench_calls_subprocess():
    """Бенчмарк запускает subprocess с правильными параметрами."""
    bot = MagicMock()
    message = MagicMock()
    message.reply = AsyncMock()

    # Owner user
    access_profile = MagicMock()
    access_profile.level = AccessLevel.OWNER
    bot._get_access_profile.return_value = access_profile
    bot._get_command_args.return_value = "fast"

    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.stdout = "benchmark output\nFinal: 100 ops/sec"
        mock_run.return_value = mock_result

        with patch("src.core.command_registry.bump_command"):
            await handle_bench(bot, message)

    # Проверяем, что subprocess был вызван
    mock_run.assert_called_once()
    call_args = mock_run.call_args
    assert "--iterations" in call_args[0][0]
    assert "20" in call_args[0][0]  # fast = 20 итераций


@pytest.mark.asyncio
async def test_handle_bench_truncates_long_output():
    """Длинный вывод обрезается до 1500 символов."""
    bot = MagicMock()
    message = MagicMock()
    message.reply = AsyncMock()

    # Owner user
    access_profile = MagicMock()
    access_profile.level = AccessLevel.OWNER
    bot._get_access_profile.return_value = access_profile
    bot._get_command_args.return_value = "full"

    # Генерируем длинный вывод
    long_output = "x" * 5000

    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.stdout = long_output
        mock_run.return_value = mock_result

        with patch("src.core.command_registry.bump_command"):
            await handle_bench(bot, message)

    # Проверяем, что результат обрезан
    reply_call = message.reply.call_args_list[-1]
    reply_text = reply_call[0][0]
    # Вывод должен содержать 1500 последних символов
    assert len(reply_text) < len(long_output) + 100  # +100 для маркеров


@pytest.mark.asyncio
async def test_handle_bench_default_fast_preset():
    """При отсутствии аргумента используется preset 'fast'."""
    bot = MagicMock()
    message = MagicMock()
    message.reply = AsyncMock()

    # Owner user
    access_profile = MagicMock()
    access_profile.level = AccessLevel.OWNER
    bot._get_access_profile.return_value = access_profile
    bot._get_command_args.return_value = ""  # Без аргументов

    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.stdout = "ok"
        mock_run.return_value = mock_result

        with patch("src.core.command_registry.bump_command"):
            await handle_bench(bot, message)

    # Проверяем, что используется 'fast' (20 итераций)
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert "20" in call_args
