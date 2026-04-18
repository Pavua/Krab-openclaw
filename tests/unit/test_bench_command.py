# -*- coding: utf-8 -*-
"""
Тесты для команды !bench (бенчмарк производительности).
"""

from __future__ import annotations

import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.access_control import AccessLevel
from src.handlers.command_handlers import handle_bench


@pytest.mark.asyncio
async def test_handle_bench_owner_only():
    """Только владелец может запускать бенчмарки — non-owner получает отказ."""
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
    call_text = message.reply.call_args[0][0]
    assert "⛔" in call_text
    assert "владельца" in call_text


@pytest.mark.asyncio
async def test_handle_bench_unknown_preset_defaults_to_fast():
    """Неизвестный preset сбрасывается на 'fast' с 20 итерациями."""
    bot = MagicMock()
    message = MagicMock()
    message.reply = AsyncMock()

    access_profile = MagicMock()
    access_profile.level = AccessLevel.OWNER
    bot._get_access_profile.return_value = access_profile
    bot._get_command_args.return_value = "unknown_preset"

    with patch("subprocess.run") as mock_run, patch("src.core.command_registry.bump_command"):
        mock_result = MagicMock()
        mock_result.stdout = "ok"
        mock_run.return_value = mock_result

        await handle_bench(bot, message)

    # Должен быть вызван subprocess с 20 итерациями (fast)
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert "20" in call_args


@pytest.mark.asyncio
async def test_handle_bench_calls_subprocess():
    """Бенчмарк запускает subprocess с правильными параметрами."""
    bot = MagicMock()
    message = MagicMock()
    message.reply = AsyncMock()

    access_profile = MagicMock()
    access_profile.level = AccessLevel.OWNER
    bot._get_access_profile.return_value = access_profile
    bot._get_command_args.return_value = "fast"

    with patch("subprocess.run") as mock_run, patch("src.core.command_registry.bump_command"):
        mock_result = MagicMock()
        mock_result.stdout = "benchmark output\nFinal: 100 ops/sec"
        mock_run.return_value = mock_result

        await handle_bench(bot, message)

    # subprocess вызван, аргументы содержат итерации
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert "--iterations" in call_args
    assert "20" in call_args  # fast = 20 итераций


@pytest.mark.asyncio
async def test_handle_bench_timeout_replies_correctly():
    """При TimeoutExpired — правильный ответ через message.reply без AttributeError."""
    bot = MagicMock()
    message = MagicMock()
    message.reply = AsyncMock()

    access_profile = MagicMock()
    access_profile.level = AccessLevel.OWNER
    bot._get_access_profile.return_value = access_profile
    bot._get_command_args.return_value = "fast"

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="bench", timeout=120)), \
         patch("src.core.command_registry.bump_command"):
        await handle_bench(bot, message)

    # reply вызывался дважды: статус + timeout
    assert message.reply.call_count == 2
    last_text = message.reply.call_args_list[-1][0][0]
    assert "timed out" in last_text or "Benchmark timed" in last_text


@pytest.mark.asyncio
async def test_handle_bench_exception_replies_correctly():
    """При произвольной Exception — правильный ответ через message.reply без AttributeError."""
    bot = MagicMock()
    message = MagicMock()
    message.reply = AsyncMock()

    access_profile = MagicMock()
    access_profile.level = AccessLevel.OWNER
    bot._get_access_profile.return_value = access_profile
    bot._get_command_args.return_value = "fast"

    with patch("subprocess.run", side_effect=RuntimeError("disk full")), \
         patch("src.core.command_registry.bump_command"):
        await handle_bench(bot, message)

    # reply вызывался дважды: статус + error
    assert message.reply.call_count == 2
    last_text = message.reply.call_args_list[-1][0][0]
    assert "Benchmark failed" in last_text or "disk full" in last_text
