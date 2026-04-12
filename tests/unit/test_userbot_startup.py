"""
Тесты старта Telegram userbot в non-interactive окружении.

Зачем:
- гарантировать, что отсутствие интерактивного ввода (TTY) не валит весь runtime;
- закрепить controlled degraded-mode (`login_required`) вместо фатального EOF.
"""

from __future__ import annotations

import asyncio
import sqlite3
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
    bot._telegram_restart_lock = asyncio.Lock()
    bot._telegram_probe_failures = 0
    bot._session_workdir = Path(".")
    bot._disclosure_sent_for_chat_ids = set()
    bot._startup_state = "initializing"
    bot._startup_error_code = ""
    bot._startup_error = ""
    return bot


@pytest.mark.asyncio
async def test_start_non_interactive_missing_session_goes_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
async def test_start_non_interactive_eof_prompt_goes_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


@pytest.mark.asyncio
async def test_stop_awaits_background_tasks_and_clears_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    `stop()` должен не только отменять watchdog/proactive tasks, но и дожидаться их завершения.
    Это защищает restart от гонки со старым Pyrogram probe.
    """
    bot = _build_bot_stub()
    bot._auto_export_handoff_snapshot = AsyncMock()
    bot._safe_stop_client = AsyncMock()

    async def _background_loop() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise

    bot._telegram_watchdog_task = asyncio.create_task(_background_loop())
    bot._proactive_watch_task = asyncio.create_task(_background_loop())

    fake_scheduler = SimpleNamespace(is_started=False, stop=Mock())
    monkeypatch.setattr(userbot_bridge_module, "krab_scheduler", fake_scheduler)
    monkeypatch.setattr(userbot_bridge_module.model_manager, "close", AsyncMock())
    monkeypatch.setattr(userbot_bridge_module, "close_search", AsyncMock())

    await bot.stop()

    assert bot._telegram_watchdog_task is None
    assert bot._proactive_watch_task is None
    bot._safe_stop_client.assert_awaited_once()
    assert bot.get_runtime_state()["startup_state"] == "stopped"


def test_mark_transport_degraded_sets_truthful_runtime_state() -> None:
    """
    Broken Telegram transport не должен оставаться в ложном `running`.
    """
    bot = _build_bot_stub()
    bot._startup_state = "running"

    bot._mark_transport_degraded(reason="probe_failed", error="Connection lost")

    state = bot.get_runtime_state()
    assert state["startup_state"] == "degraded"
    assert state["startup_error_code"] == "telegram_transport_degraded"
    assert "Connection lost" in state["startup_error"]


@pytest.mark.asyncio
async def test_restart_recreates_client_between_stop_and_start() -> None:
    """
    Runtime restart должен поднимать новый Client, чтобы не тащить хвосты старого Pyrogram transport.
    """
    bot = _build_bot_stub()
    bot.stop = AsyncMock()
    bot.start = AsyncMock()
    bot._recreate_client = Mock()

    await bot.restart(reason="test_restart")

    bot.stop.assert_awaited_once()
    bot._recreate_client.assert_called_once()
    bot.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_client_restart_tasks_cancels_only_current_session_tasks() -> None:
    """
    Перед stop() нужно гасить висячие pyrogram restart-task'и именно текущей session.
    """
    bot = _build_bot_stub()

    class _DummySession:
        async def restart(self) -> None:
            await asyncio.sleep(60)

    owned_session = _DummySession()
    foreign_session = _DummySession()
    bot.client = SimpleNamespace(is_connected=True, session=owned_session)

    owned_task = asyncio.create_task(owned_session.restart())
    foreign_task = asyncio.create_task(foreign_session.restart())

    await asyncio.sleep(0)
    await bot._cancel_client_restart_tasks()

    assert owned_task.cancelled() is True
    assert foreign_task.cancelled() is False
    foreign_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await foreign_task


@pytest.mark.asyncio
async def test_arm_client_session_shutdown_guard_suppresses_controlled_restart() -> None:
    """
    Во время controlled shutdown внутренний Session.restart() не должен перезапускать transport.
    """
    bot = _build_bot_stub()
    restart_called = False

    class _DummySession:
        async def restart(self) -> None:
            nonlocal restart_called
            restart_called = True

    session = _DummySession()
    bot.client = SimpleNamespace(is_connected=True, session=session)

    bot._arm_client_session_shutdown_guard()
    result = await session.restart()

    assert result is None
    assert restart_called is False
    assert getattr(session, "_krab_shutdown_requested", False) is True
    assert getattr(session, "_krab_restart_guard_installed", False) is True


@pytest.mark.asyncio
async def test_arm_client_session_shutdown_guard_ignores_closed_database_error() -> None:
    """
    Поздний internal restart после shutdown не должен шуметь на `closed database`.
    """
    bot = _build_bot_stub()

    class _DummySession:
        async def restart(self) -> None:
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")

    session = _DummySession()
    bot.client = SimpleNamespace(is_connected=True, session=session)

    bot._arm_client_session_shutdown_guard()
    result = await session.restart()

    assert result is None


def test_is_sqlite_io_error_matches_closed_database_programming_error() -> None:
    """
    `closed database` считаем non-fatal shutdown-шумом sqlite storage.
    """
    bot = _build_bot_stub()

    assert (
        bot._is_sqlite_io_error(sqlite3.ProgrammingError("Cannot operate on a closed database."))
        is True
    )
