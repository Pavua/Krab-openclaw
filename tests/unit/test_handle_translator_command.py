# -*- coding: utf-8 -*-
"""
Тесты handle_translator — регрессия бага "Неизвестная подкоманда translator".

Баг: при multi-word prefix (Краб, @краб, /краб) message.text.split() давало
args[1]='translator' вместо 'status', и команда падала в fallback-ошибку.
Фикс: переход на bot._get_command_args() который корректно убирает command-word.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.handlers.command_handlers import handle_translator


def _make_bot(message_text: str) -> MagicMock:
    """Создаёт mock-бота с корректным _get_command_args."""
    bot = MagicMock()
    bot.get_translator_runtime_profile.return_value = {
        "language_pair": "ru-es",
        "enabled": True,
        "voice_foundation_ready": True,
        "voice_runtime_enabled": False,
    }
    bot.get_translator_session_state.return_value = {}

    def _get_command_args(msg) -> str:
        """Имитирует KraabUserbot._get_command_args: убирает первое слово."""
        text = str(msg.text or "")
        parts = text.split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""

    bot._get_command_args.side_effect = _get_command_args
    return bot


def _make_message(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=-100123456),
        reply=AsyncMock(),
        edit=AsyncMock(),
    )


# ---------------------------------------------------------------------------
# Регрессия: !translator status должен показывать профиль, не ошибку
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_translator_status_bang_prefix() -> None:
    """`!translator status` — показывает профиль."""
    bot = _make_bot("!translator status")
    msg = _make_message("!translator status")
    msg.command = ["translator", "status"]
    await handle_translator(bot, msg)
    msg.reply.assert_awaited_once()
    rendered = msg.reply.await_args.args[0]
    assert "❌" not in rendered, f"Ожидали профиль, получили ошибку: {rendered!r}"


@pytest.mark.asyncio
async def test_translator_status_krab_prefix() -> None:
    """`Краб translator status` — тот же результат, несмотря на multi-word prefix.

    Эмулируем Pyrogram: при "Краб translator status" message.command=["translator","status"].
    """
    bot = _make_bot("Краб translator status")
    msg = _make_message("Краб translator status")
    # Pyrogram заполняет message.command независимо от prefix
    msg.command = ["translator", "status"]
    await handle_translator(bot, msg)
    msg.reply.assert_awaited_once()
    rendered = msg.reply.await_args.args[0]
    assert "❌ Неизвестная подкоманда" not in rendered, (
        f"Регрессия: multi-word prefix вызвал ошибку: {rendered!r}"
    )


@pytest.mark.asyncio
async def test_translator_no_args_shows_profile() -> None:
    """`!translator` без аргументов — тоже показывает профиль."""
    bot = _make_bot("!translator")
    msg = _make_message("!translator")
    msg.command = ["translator"]
    await handle_translator(bot, msg)
    msg.reply.assert_awaited_once()
    rendered = msg.reply.await_args.args[0]
    assert "❌" not in rendered, f"Без аргументов ожидали профиль: {rendered!r}"


@pytest.mark.asyncio
async def test_translator_show_alias() -> None:
    """`!translator show` — псевдоним status."""
    bot = _make_bot("!translator show")
    msg = _make_message("!translator show")
    msg.command = ["translator", "show"]
    await handle_translator(bot, msg)
    msg.reply.assert_awaited_once()
    rendered = msg.reply.await_args.args[0]
    assert "❌" not in rendered, f"show alias вернул ошибку: {rendered!r}"


@pytest.mark.asyncio
async def test_translator_unknown_sub_raises_error() -> None:
    """`!translator unknowncmd` — честная ошибка 'Неизвестная подкоманда'."""
    from src.core.exceptions import UserInputError

    bot = _make_bot("!translator unknowncmd")
    msg = _make_message("!translator unknowncmd")
    msg.command = ["translator", "unknowncmd"]
    with pytest.raises(UserInputError) as exc_info:
        await handle_translator(bot, msg)
    assert "Неизвестная подкоманда" in str(exc_info.value.user_message)
