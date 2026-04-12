# -*- coding: utf-8 -*-
"""
Тесты системных команд: !macos, !chatban, !stats, !restart.

Покрываем:
1. handle_macos — справка при вызове без аргументов
2. handle_macos — недоступность автоматизации
3. handle_macos — подкоманда status
4. handle_chatban — status (пустой cache)
5. handle_chatban — status с записями
6. handle_chatban — clear с валидным chat_id
7. handle_chatban — clear без chat_id → UserInputError
8. handle_chatban — неизвестная подкоманда → UserInputError
9. handle_chatban — вызов без аргументов (= status)
10. handle_stats — рендер панели без падений
11. handle_restart — sys.exit(42)
12. handle_stats — контент панели содержит ключевые заголовки
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.handlers.command_handlers as cmd_module
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_chatban, handle_macos, handle_restart, handle_stats


# ─────────────────────────── helpers ────────────────────────────────────────


def _msg(text: str) -> SimpleNamespace:
    """Минимальный stub Message с reply-mock."""
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=12345),
        reply=AsyncMock(),
    )


def _bot() -> SimpleNamespace:
    """Минимальный stub KraabUserbot."""
    return SimpleNamespace()


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
    bot = SimpleNamespace(get_voice_runtime_profile=lambda: None)
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
    bot = SimpleNamespace(get_voice_runtime_profile=lambda: None)
    await handle_stats(bot, message)
    text = message.reply.await_args.args[0]
    # Проверяем ключевые секции панели
    assert "rate limiter" in text.lower() or "Rate" in text
    assert "ban" in text.lower() or "Ban" in text


# ─────────────────────────── handle_restart ─────────────────────────────────


@pytest.mark.asyncio
async def test_restart_exits_with_42() -> None:
    """handle_restart отправляет сообщение и делает sys.exit(42)."""
    message = _msg("!restart")
    with pytest.raises(SystemExit) as exc_info:
        await handle_restart(_bot(), message)
    assert exc_info.value.code == 42
    message.reply.assert_awaited_once()
