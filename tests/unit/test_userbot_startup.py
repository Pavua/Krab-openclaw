"""
Тесты старта Telegram userbot в non-interactive окружении.

Зачем:
- гарантировать, что отсутствие интерактивного ввода (TTY) не валит весь runtime;
- закрепить controlled degraded-mode (`login_required`) вместо фатального EOF.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import src.userbot_bridge as userbot_bridge_module
from src.userbot_bridge import KraabUserbot


def _build_bot_stub() -> KraabUserbot:
    """Собирает упрощенный экземпляр бота без реального Pyrogram подключения."""
    bot = KraabUserbot.__new__(KraabUserbot)
    bot.client = SimpleNamespace(is_connected=False)
    bot.me = None
    bot.current_role = "default"
    bot.voice_mode = False
    bot.maintenance_task = None
    bot._telegram_watchdog_task = None
    bot._session_recovery_lock = asyncio.Lock()
    bot._client_lifecycle_lock = asyncio.Lock()
    bot._session_workdir = Path(".")
    bot._disclosure_sent_for_chat_ids = set()
    bot._startup_state = "initializing"
    bot._startup_error_code = ""
    bot._startup_error = ""
    return bot


@pytest.mark.asyncio
async def test_start_non_interactive_missing_session_goes_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Если session отсутствует и нет TTY — старт не должен падать.
    Ожидаем controlled-state: `login_required`.
    """
    bot = _build_bot_stub()
    bot._restore_primary_session_from_legacy = Mock(return_value=False)
    bot._primary_session_snapshot = Mock(return_value={"exists": False, "has_user_binding": False})
    bot._cleanup_telegram_session_locks = Mock(return_value=[])
    bot._start_client_serialized = AsyncMock()
    bot._safe_stop_client = AsyncMock()
    bot._purge_telegram_session_files = Mock(return_value=[])
    bot._recreate_client = Mock()
    bot._ensure_maintenance_started = Mock()

    class _FakeStdin:
        @staticmethod
        def isatty() -> bool:
            return False

    monkeypatch.setattr(userbot_bridge_module.sys, "stdin", _FakeStdin())

    await bot.start()

    assert bot.get_runtime_state()["startup_state"] == "login_required"
    assert bot.get_runtime_state()["startup_error_code"] == "telegram_session_login_required"
    bot._start_client_serialized.assert_not_called()
    bot._purge_telegram_session_files.assert_not_called()
    bot._ensure_maintenance_started.assert_called_once()


@pytest.mark.asyncio
async def test_start_non_interactive_eof_prompt_goes_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Если Pyrogram всё же запрашивает интерактивный ввод и ловим EOF,
    runtime не падает, а уходит в controlled-state.
    """
    bot = _build_bot_stub()
    bot._restore_primary_session_from_legacy = Mock(return_value=False)
    bot._primary_session_snapshot = Mock(return_value={"exists": True, "has_user_binding": True})
    bot._cleanup_telegram_session_locks = Mock(return_value=[])
    bot._start_client_serialized = AsyncMock(side_effect=EOFError("EOF when reading a line"))
    bot._safe_stop_client = AsyncMock()
    bot._purge_telegram_session_files = Mock(return_value=[])
    bot._recreate_client = Mock()
    bot._ensure_maintenance_started = Mock()

    class _FakeStdin:
        @staticmethod
        def isatty() -> bool:
            return False

    monkeypatch.setattr(userbot_bridge_module.sys, "stdin", _FakeStdin())

    await bot.start()

    state = bot.get_runtime_state()
    assert state["startup_state"] == "login_required"
    assert state["startup_error_code"] == "telegram_session_login_required"
    assert "EOF" in state["startup_error"]
    bot._safe_stop_client.assert_awaited_once()
    bot._purge_telegram_session_files.assert_not_called()
    bot._ensure_maintenance_started.assert_called_once()


@pytest.mark.asyncio
async def test_start_interactive_invalid_session_requires_manual_relogin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    В интерактивном терминале обычный старт не должен залипать на `Enter phone number...`
    если сессия неавторизована. Ожидаем controlled-state и ручной relogin через отдельный скрипт.
    """
    bot = _build_bot_stub()
    bot._restore_primary_session_from_legacy = Mock(return_value=False)
    bot._primary_session_snapshot = Mock(return_value={"exists": True, "has_user_binding": False})
    bot._cleanup_telegram_session_locks = Mock(return_value=[])
    bot._start_client_serialized = AsyncMock()
    bot._safe_stop_client = AsyncMock()
    bot._purge_telegram_session_files = Mock(return_value=[])
    bot._recreate_client = Mock()
    bot._ensure_maintenance_started = Mock()

    class _FakeStdin:
        @staticmethod
        def isatty() -> bool:
            return True

    monkeypatch.setattr(userbot_bridge_module.sys, "stdin", _FakeStdin())
    monkeypatch.setattr(
        userbot_bridge_module.config,
        "TELEGRAM_ALLOW_INTERACTIVE_LOGIN",
        False,
        raising=False,
    )

    await bot.start()

    state = bot.get_runtime_state()
    assert state["startup_state"] == "login_required"
    assert state["startup_error_code"] == "telegram_session_login_required"
    bot._start_client_serialized.assert_not_called()
    bot._purge_telegram_session_files.assert_not_called()
    bot._ensure_maintenance_started.assert_called_once()
