# -*- coding: utf-8 -*-
"""
Тесты системных команд: !macos, !chatban, !stats, !restart.

Покрываем:
1.  handle_macos — справка при вызове без аргументов
2.  handle_macos — недоступность автоматизации
3.  handle_macos — подкоманда status
4.  handle_chatban — status (пустой cache)
5.  handle_chatban — status с записями
6.  handle_chatban — clear с валидным chat_id
7.  handle_chatban — clear без chat_id → UserInputError
8.  handle_chatban — неизвестная подкоманда → UserInputError
9.  handle_chatban — вызов без аргументов (= status)
10. handle_stats — рендер панели без падений
11. handle_restart — без аргументов → запрос подтверждения (NO sys.exit)
12. handle_stats — контент панели содержит ключевые заголовки
13. handle_restart confirm — launchctl успех → сообщение "Перезапускаю"
14. handle_restart confirm — launchctl ошибка → sys.exit(42)
15. handle_restart confirm — launchctl недоступен → sys.exit(42)
16. handle_restart status — показывает PID и uptime
17. handle_restart status — launchctl недоступен → N/A
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.handlers.command_handlers as cmd_module
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_chatban, handle_macos, handle_restart, handle_stats

# ─────────────────────────── helpers ────────────────────────────────────────


def _msg(text: str, args: str = "") -> SimpleNamespace:
    """Минимальный stub Message с reply-mock."""
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=12345),
        reply=AsyncMock(),
    )


def _bot(cmd_args: str = "", session_start: float | None = None) -> SimpleNamespace:
    """Минимальный stub KraabUserbot."""
    start = session_start if session_start is not None else time.time() - 3661  # 1ч 1м
    return SimpleNamespace(
        _get_command_args=lambda msg: cmd_args,
        _session_start_time=start,
    )


# ─────────────────────────── handle_macos ───────────────────────────────────


@pytest.mark.asyncio
async def test_macos_help_when_no_args(monkeypatch: pytest.MonkeyPatch) -> None:
    """!mac без аргументов → справка с перечнем подкоманд."""
    monkeypatch.setattr(cmd_module.macos_automation, "is_available", lambda: True)
    message = _msg("!mac")
    await handle_macos(_bot(), message)
    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    # Справка содержит типовые подкоманды
    assert "clip" in text
    assert "notify" in text
    assert "status" in text


@pytest.mark.asyncio
async def test_macos_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если macOS automation недоступен — уведомление без краша."""
    monkeypatch.setattr(cmd_module.macos_automation, "is_available", lambda: False)
    message = _msg("!mac status")
    await handle_macos(_bot(), message)
    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    assert "недоступен" in text


@pytest.mark.asyncio
async def test_macos_status_renders(monkeypatch: pytest.MonkeyPatch) -> None:
    """!mac status вызывает automation.status() и рендерит ответ."""
    monkeypatch.setattr(cmd_module.macos_automation, "is_available", lambda: True)
    fake_status = {
        "available": True,
        "frontmost_app": "Finder",
        "frontmost_window": "Desktop",
        "running_apps": ["Finder", "Safari"],
        "clipboard_chars": 5,
        "clipboard_preview": "hello",
        "warnings": [],
        "reminder_lists": [],
        "note_folders": [],
        "calendars": [],
    }
    monkeypatch.setattr(
        cmd_module.macos_automation,
        "status",
        AsyncMock(return_value=fake_status),
    )
    message = _msg("!mac status")
    await handle_macos(_bot(), message)
    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    assert "Finder" in text
    assert "clipboard" in text.lower() or "Clipboard" in text


# ─────────────────────────── handle_chatban ─────────────────────────────────


@pytest.mark.asyncio
async def test_chatban_status_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """!chatban status при пустом cache → сообщение без записей."""
    monkeypatch.setattr(cmd_module.chat_ban_cache, "list_entries", lambda: [])
    message = _msg("!chatban status")
    await handle_chatban(_bot(), message)
    message.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_chatban_status_with_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    """!chatban status показывает записи из cache."""
    entries = [
        {"chat_id": "-100111", "last_error_code": "UserBannedInChannel"},
        {"chat_id": "-100222", "last_error_code": "ChatWriteForbidden"},
    ]
    monkeypatch.setattr(cmd_module.chat_ban_cache, "list_entries", lambda: entries)
    message = _msg("!chatban")
    await handle_chatban(_bot(), message)
    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    # Хотя бы один chat_id должен присутствовать в ответе
    assert "-100111" in text or "-100222" in text or "2" in text


@pytest.mark.asyncio
async def test_chatban_clear_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    """!chatban clear <chat_id> для существующей записи → подтверждение."""
    monkeypatch.setattr(cmd_module.chat_ban_cache, "clear", lambda cid: True)
    message = _msg("!chatban clear -100111")
    await handle_chatban(_bot(), message)
    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    assert "100111" in text or "Убрал" in text


@pytest.mark.asyncio
async def test_chatban_clear_missing_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """!chatban clear <chat_id> если записи нет → info-ответ без ошибки."""
    monkeypatch.setattr(cmd_module.chat_ban_cache, "clear", lambda cid: False)
    message = _msg("!chatban clear -100999")
    await handle_chatban(_bot(), message)
    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    assert "не был" in text or "снят" in text or "cache" in text.lower()


@pytest.mark.asyncio
async def test_chatban_clear_no_id_raises() -> None:
    """!chatban clear без chat_id → UserInputError."""
    message = _msg("!chatban clear")
    with pytest.raises(UserInputError):
        await handle_chatban(_bot(), message)


@pytest.mark.asyncio
async def test_chatban_unknown_subcommand_raises() -> None:
    """!chatban bogus → UserInputError."""
    message = _msg("!chatban bogus")
    with pytest.raises(UserInputError):
        await handle_chatban(_bot(), message)


@pytest.mark.asyncio
async def test_chatban_no_args_defaults_to_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """!chatban без аргументов → то же, что status."""
    monkeypatch.setattr(cmd_module.chat_ban_cache, "list_entries", lambda: [])
    message = _msg("!chatban")
    await handle_chatban(_bot(), message)
    message.reply.assert_awaited_once()


# ─────────────────────────── handle_stats ───────────────────────────────────


@pytest.mark.asyncio
async def test_stats_renders_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    """handle_stats рендерит панель и отвечает сообщением."""
    # Подменяем синглтоны, чтобы не зависеть от runtime-состояния
    fake_rl_stats = {
        "max_per_sec": 5,
        "window_sec": 1.0,
        "current_in_window": 0,
        "total_acquired": 10,
        "total_waited": 2,
        "total_wait_sec": 0.05,
    }
    from src.core import telegram_rate_limiter as rl_mod

    monkeypatch.setattr(rl_mod.telegram_rate_limiter, "stats", lambda: fake_rl_stats)

    message = _msg("!stats")
    bot = SimpleNamespace(
        get_voice_runtime_profile=lambda: None,
        _get_command_args=lambda _msg: "",
    )
    await handle_stats(bot, message)
    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    assert "Stats" in text or "stats" in text.lower() or "Krab" in text


@pytest.mark.asyncio
async def test_stats_panel_contains_key_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    """Панель stats содержит секции rate limiter и chat ban cache."""
    from src.core import telegram_rate_limiter as rl_mod

    monkeypatch.setattr(
        rl_mod.telegram_rate_limiter,
        "stats",
        lambda: {
            "max_per_sec": 3,
            "window_sec": 1.0,
            "current_in_window": 1,
            "total_acquired": 5,
            "total_waited": 0,
            "total_wait_sec": 0.0,
        },
    )
    monkeypatch.setattr(cmd_module.chat_ban_cache, "list_entries", lambda: [])

    message = _msg("!stats")
    bot = SimpleNamespace(
        get_voice_runtime_profile=lambda: None,
        _get_command_args=lambda _msg: "",
    )
    await handle_stats(bot, message)
    text = message.reply.await_args.args[0]
    # Проверяем ключевые секции панели
    assert "rate limiter" in text.lower() or "Rate" in text
    assert "ban" in text.lower() or "Ban" in text


# ─────────────────────────── handle_restart ─────────────────────────────────


@pytest.mark.asyncio
async def test_restart_no_args_asks_for_confirmation() -> None:
    """!restart без аргументов — отправляет запрос подтверждения, НЕ выходит."""
    message = _msg("!restart")
    bot = _bot(cmd_args="")
    await handle_restart(bot, message)
    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    assert "confirm" in text.lower()
    assert "restart" in text.lower()


@pytest.mark.asyncio
async def test_restart_confirm_launchctl_success() -> None:
    """!restart confirm — launchctl возвращает 0, сообщение "Перезапускаю", нет sys.exit."""
    message = _msg("!restart confirm")
    bot = _bot(cmd_args="confirm")

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = ""
    mock_proc.stderr = ""

    with (
        patch("src.core.subprocess_env.clean_subprocess_env", return_value={}),
        patch("src.handlers.command_handlers.subprocess.run", return_value=mock_proc) as mock_run,
    ):
        await handle_restart(bot, message)

    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    assert "Перезапускаю" in text

    # Проверяем, что вызывался launchctl kickstart -k
    call_args = mock_run.call_args_list[-1]
    cmd = call_args.args[0]
    assert "kickstart" in cmd
    assert "-k" in cmd
    assert "ai.krab.core" in " ".join(cmd)


@pytest.mark.asyncio
async def test_restart_confirm_launchctl_nonzero_falls_back_to_exit() -> None:
    """!restart confirm — launchctl вернул ненулевой код → sys.exit(42)."""
    message = _msg("!restart confirm")
    bot = _bot(cmd_args="confirm")

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = ""
    mock_proc.stderr = "error"

    with (
        patch("src.core.subprocess_env.clean_subprocess_env", return_value={}),
        patch("src.handlers.command_handlers.subprocess.run", return_value=mock_proc),
        pytest.raises(SystemExit) as exc_info,
    ):
        await handle_restart(bot, message)

    assert exc_info.value.code == 42
    message.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_restart_confirm_launchctl_not_found_falls_back_to_exit() -> None:
    """!restart confirm — launchctl не найден (FileNotFoundError) → sys.exit(42)."""
    message = _msg("!restart confirm")
    bot = _bot(cmd_args="confirm")

    with (
        patch("src.core.subprocess_env.clean_subprocess_env", return_value={}),
        patch(
            "src.handlers.command_handlers.subprocess.run",
            side_effect=FileNotFoundError("launchctl not found"),
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        await handle_restart(bot, message)

    assert exc_info.value.code == 42
    message.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_restart_status_shows_pid_and_uptime() -> None:
    """!restart status — сообщение содержит PID и uptime."""
    message = _msg("!restart status")
    bot = _bot(cmd_args="status", session_start=time.time() - 3661)

    # Симулируем launchctl print с PID в выводе
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "pid = 12345\nstate = running"

    with (
        patch("src.core.subprocess_env.clean_subprocess_env", return_value={}),
        patch("src.handlers.command_handlers.subprocess.run", return_value=mock_proc),
    ):
        await handle_restart(bot, message)

    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    assert "PID" in text
    assert "Uptime" in text
    assert "ai.krab.core" in text
    assert "confirm" in text.lower()


@pytest.mark.asyncio
async def test_restart_status_launchctl_unavailable() -> None:
    """!restart status — launchctl недоступен → launchd_status = N/A."""
    message = _msg("!restart status")
    bot = _bot(cmd_args="status")

    with (
        patch("src.core.subprocess_env.clean_subprocess_env", return_value={}),
        patch(
            "src.handlers.command_handlers.subprocess.run",
            side_effect=Exception("unavailable"),
        ),
    ):
        await handle_restart(bot, message)

    text = message.reply.await_args.args[0]
    assert "N/A" in text


@pytest.mark.asyncio
async def test_restart_status_launchctl_stopped() -> None:
    """!restart status — launchctl print без 'pid' → Stopped."""
    message = _msg("!restart status")
    bot = _bot(cmd_args="status")

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "state = waiting"

    with (
        patch("src.core.subprocess_env.clean_subprocess_env", return_value={}),
        patch("src.handlers.command_handlers.subprocess.run", return_value=mock_proc),
    ):
        await handle_restart(bot, message)

    text = message.reply.await_args.args[0]
    assert "Stopped" in text


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 Wave 10 (Session 27): system_commands re-exports
# ─────────────────────────────────────────────────────────────────────────────


class TestPhase2Wave10ReExports:
    """Гарантирует, что после извлечения system_commands.py все символы доступны
    через src.handlers.command_handlers (тесты, _AgentRoomRouterAdapter,
    external code импортируют через старый namespace)."""

    def test_handlers_re_exported(self):
        """Handlers видны через command_handlers и идентичны в обоих модулях."""
        from src.handlers import command_handlers as _ch
        from src.handlers.commands import system_commands as _sc

        for name in (
            "handle_status",
            "handle_sysinfo",
            "handle_uptime",
            "handle_panel",
            "handle_version",
            "handle_restart",
            "handle_diagnose",
            "handle_debug",
            "handle_health",
            "handle_stats",
            "handle_ip",
            "handle_dns",
            "handle_ping",
            "handle_log",
            "handle_diag",
        ):
            assert hasattr(_ch, name), f"command_handlers missing {name}"
            assert hasattr(_sc, name), f"system_commands missing {name}"
            # Re-export проксирует тот же объект
            assert getattr(_ch, name) is getattr(_sc, name)

    def test_helpers_re_exported(self):
        """Private helpers тоже видны через command_handlers (используются в тестах)."""
        from src.handlers import command_handlers as _ch
        from src.handlers.commands import system_commands as _sc

        for name in (
            "_format_uptime_str",
            "_render_stats_panel",
            "_format_ecosystem_report",
            "_handle_stats_ecosystem",
            "_health_deep_report",
            "_get_local_ip",
            "_get_public_ip",
            "_read_log_tail_subprocess",
            "_KRAB_LOG_PATH",
            "_LOG_MAX_INLINE_SIZE",
            "_LOG_TEXT_MAX_LINES",
            "_diag_panel_base",
            "_diag_fetch_json",
            "_diag_fmt_section_infra",
            "_diag_fmt_section_model",
            "_diag_fmt_section_traffic",
            "_diag_fmt_section_memory",
            "_diag_fmt_section_errors",
            "_diag_fmt_section_inbox",
            "_diag_fmt_section_cron",
            "_diag_fmt_section_phase2",
            "_diag_fmt_section_sentry",
            "_diag_fmt_section_security",
            "_diag_fetch_sentry",
            "_diag_collect_security",
        ):
            assert hasattr(_ch, name), f"command_handlers missing {name}"
            assert hasattr(_sc, name), f"system_commands missing {name}"
            assert getattr(_ch, name) is getattr(_sc, name)

    def test_landmines_remain_in_command_handlers(self):
        """``_swarm_status_deep_report`` и ``_split_text_for_telegram`` НЕ переехали:
        тесты test_swarm_status_deep патчат через namespace command_handlers, а
        _split_text_for_telegram используется множеством handlers вне system."""
        from src.handlers import command_handlers as _ch

        assert hasattr(_ch, "_swarm_status_deep_report")
        assert hasattr(_ch, "_split_text_for_telegram")
        # Они должны быть определены ИМЕННО в command_handlers (не re-export):
        assert _ch._swarm_status_deep_report.__module__.endswith("command_handlers")
        assert _ch._split_text_for_telegram.__module__.endswith("command_handlers")

    def test_format_uptime_str_works(self):
        """Sanity check: _format_uptime_str форматирует секунды в "Nд Mч Kм"."""
        from src.handlers.command_handlers import _format_uptime_str

        assert _format_uptime_str(0) == "0м"
        assert _format_uptime_str(3661) == "1ч 1м"
        assert _format_uptime_str(90061) == "1д 1ч 1м"
