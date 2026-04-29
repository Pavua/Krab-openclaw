# -*- coding: utf-8 -*-
"""
Regression tests для Phase 2 Wave 16 (state_commands).

Проверяет, что после извлечения handle_clear/forget/reset/model/web/macos/browser
в src/handlers/commands/state_commands.py все символы остаются доступны через
src/handlers/command_handlers.py namespace и dual-namespace patching работает.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.handlers import command_handlers as ch
from src.handlers.commands import state_commands


def test_state_commands_reexport_present():
    """Все extracted символы доступны через command_handlers."""
    for name in (
        "handle_clear",
        "handle_forget",
        "handle_reset",
        "handle_model",
        "handle_web",
        "handle_macos",
        "handle_browser",
        "_format_model_info",
        "_format_size_gb",
        "_split_text_for_telegram",
    ):
        assert hasattr(ch, name), f"missing re-export: {name}"
        assert getattr(ch, name) is getattr(state_commands, name), (
            f"namespace divergence: {name}"
        )


def test_format_size_gb_behavior():
    """Сохраняем точную семантику pre-extraction (n/a для <=0)."""
    assert ch._format_size_gb(0) == "n/a"
    assert ch._format_size_gb(-1) == "n/a"
    assert ch._format_size_gb(None) == "n/a"  # type: ignore[arg-type]
    assert ch._format_size_gb(2.5) == "2.50 GB"
    assert ch._format_size_gb("bad") == "n/a"  # type: ignore[arg-type]


def test_split_text_for_telegram_short_text():
    """Короткий текст возвращается как один chunk."""
    text = "hello\nworld"
    chunks = ch._split_text_for_telegram(text, limit=100)
    assert chunks == ["hello\nworld"]


def test_split_text_for_telegram_long_text():
    """Длинный текст разбивается с сохранением границ строк."""
    long_text = "\n".join(f"line{i}" for i in range(50))
    chunks = ch._split_text_for_telegram(long_text, limit=30)
    assert len(chunks) >= 2
    assert all(len(c) <= 30 for c in chunks)


@pytest.mark.asyncio
async def test_handle_forget_owner_only_blocks_non_owner():
    """!forget блокируется для не-owner."""
    from src.core.exceptions import UserInputError

    bot = MagicMock()
    profile = MagicMock()

    # Эмулируем не-owner profile.
    class _Lvl:
        name = "USER"

    profile.level = _Lvl()
    bot._get_access_profile.return_value = profile

    msg = MagicMock()
    msg.from_user = MagicMock()
    msg.chat.id = 42

    with pytest.raises(UserInputError):
        await ch.handle_forget(bot, msg)


@pytest.mark.asyncio
async def test_handle_clear_default_clears_session(monkeypatch):
    """!clear без аргументов вызывает openclaw_client.clear_session(chat_id)."""
    fake_client = MagicMock()
    fake_client.clear_session = MagicMock()
    monkeypatch.setattr(ch, "openclaw_client", fake_client)

    bot = MagicMock()
    bot.me.id = 100
    msg = MagicMock()
    msg.text = "!clear"
    msg.chat.id = 555
    msg.from_user.id = 999  # не bot.me — должна быть reply, не edit
    msg.reply = AsyncMock()
    msg.edit = AsyncMock()

    await ch.handle_clear(bot, msg)

    fake_client.clear_session.assert_called_once_with("555")
    msg.reply.assert_called_once()
    msg.edit.assert_not_called()


@pytest.mark.asyncio
async def test_handle_clear_cache_subcommand(monkeypatch):
    """!clear cache очищает history_cache + search_cache."""
    fake_history = MagicMock()
    fake_history.clear_all.return_value = 7
    fake_search = MagicMock()
    fake_search.clear_all.return_value = 3
    monkeypatch.setattr(ch, "history_cache", fake_history)
    monkeypatch.setattr(ch, "search_cache", fake_search)

    bot = MagicMock()
    bot.me.id = 100
    msg = MagicMock()
    msg.text = "!clear cache"
    msg.chat.id = 1
    msg.from_user.id = 2
    msg.reply = AsyncMock()
    msg.edit = AsyncMock()

    await ch.handle_clear(bot, msg)

    fake_history.clear_all.assert_called_once()
    fake_search.clear_all.assert_called_once()
    body = msg.reply.call_args[0][0]
    assert "history_cache" in body
    assert "7" in body and "3" in body


@pytest.mark.asyncio
async def test_handle_model_no_args_shows_status(monkeypatch):
    """!model без аргументов показывает текущее состояние."""
    fake_config = MagicMock()
    fake_config.FORCE_CLOUD = False
    fake_config.MODEL = "google/gemini-3-pro-preview"
    fake_config.LM_STUDIO_URL = "http://localhost:1234"
    monkeypatch.setattr(ch, "config", fake_config)

    fake_mm = MagicMock()
    fake_mm._current_model = "google/gemini-3-pro-preview"
    monkeypatch.setattr(ch, "model_manager", fake_mm)

    bot = MagicMock()
    msg = MagicMock()
    msg.text = "!model"
    msg.reply = AsyncMock()

    await ch.handle_model(bot, msg)

    msg.reply.assert_called_once()
    body = msg.reply.call_args[0][0]
    assert "Маршрутизация" in body
    assert "google/gemini-3-pro-preview" in body
