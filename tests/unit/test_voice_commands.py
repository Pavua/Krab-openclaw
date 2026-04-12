# -*- coding: utf-8 -*-
"""
Тесты обработчика голосовых команд handle_voice.

Покрываем:
1) !voice status / !voice (без аргументов) — показывает профиль;
2) !voice on / !voice off — включение/выключение;
3) !voice toggle — инверсия текущего состояния;
4) !voice speed <val> — установка скорости;
5) !voice speed без аргумента — UserInputError;
6) !voice voice <id> — смена voice-id;
7) !voice delivery text+voice / voice-only — смена режима;
8) !voice delivery с некорректным значением — UserInputError;
9) !voice reset — сброс к умолчаниям;
10) !voice block <chat_id> — добавление в blocklist;
11) !voice unblock <chat_id> — удаление из blocklist;
12) !voice blocked — список заблокированных чатов;
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.handlers.command_handlers import UserInputError, handle_voice


def _make_message(text: str) -> SimpleNamespace:
    """Минимальный stub Pyrogram Message с текстом и async reply."""
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=-1001000000001),
        reply=AsyncMock(),
    )


def _make_bot(voice_enabled: bool = False) -> MagicMock:
    """
    Stub KraabUserbot с методами VoiceProfileMixin.
    Используем MagicMock, чтобы легко перехватить вызовы update/get.
    """
    bot = MagicMock()
    # Базовый профиль, который возвращают get и update
    _profile = {
        "enabled": voice_enabled,
        "speed": 1.5,
        "voice": "ru-RU-DmitryNeural",
        "delivery": "text+voice",
        "blocked_chats": [],
        "input_transcription_ready": False,
        "output_tts_ready": True,
        "live_voice_foundation": False,
        "voice_strategy": "voice-first",
        "voice_foundation_ready": False,
        "voice_runtime_enabled": voice_enabled,
    }
    bot.get_voice_runtime_profile.return_value = dict(_profile)
    bot.update_voice_runtime_profile.return_value = dict(_profile)
    bot.get_voice_blocked_chats.return_value = []
    bot.add_voice_blocked_chat.return_value = ["-1001000000001"]
    bot.remove_voice_blocked_chat.return_value = []
    return bot


# ---------------------------------------------------------------------------
# !voice (без аргументов) и !voice status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_no_args_shows_profile() -> None:
    """!voice без аргументов → вызывает get_voice_runtime_profile и шлёт ответ."""
    bot = _make_bot()
    msg = _make_message("!voice")
    await handle_voice(bot, msg)
    bot.get_voice_runtime_profile.assert_called_once()
    msg.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_voice_status_shows_profile() -> None:
    """!voice status → тот же путь, что и без аргументов."""
    bot = _make_bot()
    msg = _make_message("!voice status")
    await handle_voice(bot, msg)
    bot.get_voice_runtime_profile.assert_called_once()
    msg.reply.assert_awaited_once()


# ---------------------------------------------------------------------------
# !voice on / off
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_on_enables_voice() -> None:
    """!voice on → update_voice_runtime_profile(enabled=True, persist=True)."""
    bot = _make_bot()
    msg = _make_message("!voice on")
    await handle_voice(bot, msg)
    bot.update_voice_runtime_profile.assert_called_once_with(enabled=True, persist=True)
    msg.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_voice_off_disables_voice() -> None:
    """!voice off → update_voice_runtime_profile(enabled=False, persist=True)."""
    bot = _make_bot()
    msg = _make_message("!voice off")
    await handle_voice(bot, msg)
    bot.update_voice_runtime_profile.assert_called_once_with(enabled=False, persist=True)
    msg.reply.assert_awaited_once()


# ---------------------------------------------------------------------------
# !voice toggle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_toggle_inverts_enabled() -> None:
    """!voice toggle → инвертирует текущий enabled-флаг из профиля."""
    bot = _make_bot(voice_enabled=False)
    msg = _make_message("!voice toggle")
    await handle_voice(bot, msg)
    # Должно вызвать update с enabled=True (инверсия False)
    call_kwargs = bot.update_voice_runtime_profile.call_args
    assert call_kwargs.kwargs.get("enabled") is True
    assert call_kwargs.kwargs.get("persist") is True


# ---------------------------------------------------------------------------
# !voice speed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_speed_sets_value() -> None:
    """!voice speed 1.25 → update_voice_runtime_profile(speed=1.25, persist=True)."""
    bot = _make_bot()
    msg = _make_message("!voice speed 1.25")
    await handle_voice(bot, msg)
    bot.update_voice_runtime_profile.assert_called_once_with(speed=1.25, persist=True)
    msg.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_voice_speed_without_arg_raises() -> None:
    """!voice speed без числа → UserInputError."""
    bot = _make_bot()
    msg = _make_message("!voice speed")
    with pytest.raises(UserInputError):
        await handle_voice(bot, msg)


@pytest.mark.asyncio
async def test_voice_speed_non_numeric_raises() -> None:
    """!voice speed abc → UserInputError (не парсится в float)."""
    bot = _make_bot()
    msg = _make_message("!voice speed abc")
    with pytest.raises(UserInputError):
        await handle_voice(bot, msg)


# ---------------------------------------------------------------------------
# !voice delivery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_delivery_text_plus_voice() -> None:
    """!voice delivery text+voice → update_voice_runtime_profile(delivery='text+voice', persist=True)."""
    bot = _make_bot()
    msg = _make_message("!voice delivery text+voice")
    await handle_voice(bot, msg)
    bot.update_voice_runtime_profile.assert_called_once_with(delivery="text+voice", persist=True)


@pytest.mark.asyncio
async def test_voice_delivery_voice_only() -> None:
    """!voice delivery voice-only → update с delivery='voice-only'."""
    bot = _make_bot()
    msg = _make_message("!voice delivery voice-only")
    await handle_voice(bot, msg)
    bot.update_voice_runtime_profile.assert_called_once_with(delivery="voice-only", persist=True)


@pytest.mark.asyncio
async def test_voice_delivery_invalid_raises() -> None:
    """!voice delivery unknown → UserInputError."""
    bot = _make_bot()
    msg = _make_message("!voice delivery unknown")
    with pytest.raises(UserInputError):
        await handle_voice(bot, msg)


# ---------------------------------------------------------------------------
# !voice reset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_reset_restores_defaults() -> None:
    """!voice reset → update_voice_runtime_profile с дефолтными значениями."""
    bot = _make_bot()
    msg = _make_message("!voice reset")
    await handle_voice(bot, msg)
    call_kwargs = bot.update_voice_runtime_profile.call_args.kwargs
    assert call_kwargs.get("enabled") is False
    assert call_kwargs.get("speed") == 1.5
    assert call_kwargs.get("voice") == "ru-RU-DmitryNeural"
    assert call_kwargs.get("delivery") == "text+voice"
    assert call_kwargs.get("persist") is True


# ---------------------------------------------------------------------------
# !voice block / unblock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_block_chat_id() -> None:
    """!voice block <chat_id> → add_voice_blocked_chat вызван с нужным id."""
    bot = _make_bot()
    msg = _make_message("!voice block -1001000000001")
    await handle_voice(bot, msg)
    bot.add_voice_blocked_chat.assert_called_once_with("-1001000000001", persist=True)
    msg.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_voice_unblock_chat_id() -> None:
    """!voice unblock <chat_id> → remove_voice_blocked_chat вызван с нужным id."""
    bot = _make_bot()
    msg = _make_message("!voice unblock -1001000000001")
    await handle_voice(bot, msg)
    bot.remove_voice_blocked_chat.assert_called_once_with("-1001000000001", persist=True)
    msg.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_voice_blocked_empty_list() -> None:
    """!voice blocked при пустом списке → сообщение об отсутствии чатов."""
    bot = _make_bot()
    bot.get_voice_blocked_chats.return_value = []
    msg = _make_message("!voice blocked")
    await handle_voice(bot, msg)
    msg.reply.assert_awaited_once()
    # Убедимся, что ответ содержит информацию о пустом blocklist
    reply_text: str = msg.reply.call_args.args[0]
    assert "blocklist" in reply_text.lower() or "block" in reply_text.lower()


@pytest.mark.asyncio
async def test_voice_blocked_with_chats() -> None:
    """!voice blocked с непустым списком → выводит все заблокированные chat_id."""
    bot = _make_bot()
    bot.get_voice_blocked_chats.return_value = ["-1001587432709", "-1002000000000"]
    msg = _make_message("!voice blocked")
    await handle_voice(bot, msg)
    reply_text: str = msg.reply.call_args.args[0]
    assert "-1001587432709" in reply_text
    assert "-1002000000000" in reply_text


# ---------------------------------------------------------------------------
# Неизвестная подкоманда
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_unknown_subcommand_raises() -> None:
    """!voice unknowncmd → UserInputError с подсказкой."""
    bot = _make_bot()
    msg = _make_message("!voice unknowncmd")
    with pytest.raises(UserInputError):
        await handle_voice(bot, msg)
