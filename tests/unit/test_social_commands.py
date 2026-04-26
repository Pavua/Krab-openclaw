# -*- coding: utf-8 -*-
"""
Тесты для commands/social_commands.py (Phase 2 Wave 6, Session 27).

Покрытие:
- handle_react: успешная реакция и удаление команды;
- handle_react: отсутствие emoji → UserInputError;
- handle_react: реакции отключены конфигом → reply warning;
- handle_dice: дефолтный 🎲 кубик;
- handle_dice: alias dart → 🎯;
- handle_dice: неизвестный alias → UserInputError;
- handle_sticker (list пустой/непустой), save без reply, send by name;
- handle_alias: list / set / del / неизвестная подкоманда;
- TestReExports: API сохранён через src.handlers.command_handlers.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.exceptions import UserInputError
from src.handlers.commands import social_commands as sc

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message(text: str = "", reply: object | None = None) -> SimpleNamespace:
    """Stub Pyrogram Message c text/reply/chat и async-методами."""
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=-1001000000001),
        from_user=SimpleNamespace(id=42),
        reply_to_message=reply,
        reply=AsyncMock(),
        delete=AsyncMock(),
        edit=AsyncMock(),
    )


def _make_bot(args: str = "") -> MagicMock:
    """Stub KraabUserbot с минимальным API для social_commands."""
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value=args)
    bot.me = SimpleNamespace(id=99)  # bot id
    bot.client = MagicMock()
    bot.client.send_reaction = AsyncMock()
    bot.client.send_dice = AsyncMock()
    bot.client.send_poll = AsyncMock()
    bot.client.send_sticker = AsyncMock()
    return bot


# ---------------------------------------------------------------------------
# !react
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_react_success_deletes_command() -> None:
    bot = _make_bot(args="👍")
    target = SimpleNamespace(chat=SimpleNamespace(id=-100777), id=555)
    msg = _make_message("!react 👍", reply=target)
    with patch.object(sc, "config", SimpleNamespace(TELEGRAM_REACTIONS_ENABLED=True)):
        await sc.handle_react(bot, msg)
    bot.client.send_reaction.assert_awaited_once()
    msg.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_react_no_args_raises_user_error() -> None:
    bot = _make_bot(args="")
    msg = _make_message("!react")
    with patch.object(sc, "config", SimpleNamespace(TELEGRAM_REACTIONS_ENABLED=True)):
        with pytest.raises(UserInputError):
            await sc.handle_react(bot, msg)


@pytest.mark.asyncio
async def test_react_disabled_in_config_replies_warning() -> None:
    bot = _make_bot(args="🔥")
    msg = _make_message("!react 🔥")
    with patch.object(sc, "config", SimpleNamespace(TELEGRAM_REACTIONS_ENABLED=False)):
        await sc.handle_react(bot, msg)
    msg.reply.assert_awaited_once()
    bot.client.send_reaction.assert_not_called()


# ---------------------------------------------------------------------------
# !dice
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dice_default_sends_cube() -> None:
    bot = _make_bot(args="")
    msg = _make_message("!dice")
    await sc.handle_dice(bot, msg)
    bot.client.send_dice.assert_awaited_once()
    kwargs = bot.client.send_dice.call_args.kwargs
    assert kwargs["emoji"] == "🎲"


@pytest.mark.asyncio
async def test_dice_alias_dart_sends_dart() -> None:
    bot = _make_bot(args="dart")
    msg = _make_message("!dice dart")
    await sc.handle_dice(bot, msg)
    kwargs = bot.client.send_dice.call_args.kwargs
    assert kwargs["emoji"] == "🎯"


@pytest.mark.asyncio
async def test_dice_unknown_alias_raises() -> None:
    bot = _make_bot(args="garbage")
    msg = _make_message("!dice garbage")
    with pytest.raises(UserInputError):
        await sc.handle_dice(bot, msg)


# ---------------------------------------------------------------------------
# !sticker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sticker_list_empty(tmp_path) -> None:
    bot = _make_bot(args="")
    msg = _make_message("!sticker")
    fake_file = tmp_path / "saved_stickers.json"
    with patch.object(sc, "_STICKERS_FILE", fake_file):
        await sc.handle_sticker(bot, msg)
    msg.reply.assert_awaited_once()
    body = msg.reply.await_args.args[0]
    assert "Нет сохранённых" in body or "сохранить" in body.lower() or "📭" in body


@pytest.mark.asyncio
async def test_sticker_save_without_reply_raises() -> None:
    bot = _make_bot(args="save myname")
    msg = _make_message("!sticker save myname")
    with pytest.raises(UserInputError):
        await sc.handle_sticker(bot, msg)


@pytest.mark.asyncio
async def test_sticker_send_by_name(tmp_path) -> None:
    bot = _make_bot(args="cat")
    msg = _make_message("!sticker cat")
    fake_file = tmp_path / "saved_stickers.json"
    fake_file.write_text('{"cat": "FILE_ID_123"}', encoding="utf-8")
    with patch.object(sc, "_STICKERS_FILE", fake_file):
        await sc.handle_sticker(bot, msg)
    bot.client.send_sticker.assert_awaited_once()
    args = bot.client.send_sticker.call_args.args
    assert args[1] == "FILE_ID_123"


# ---------------------------------------------------------------------------
# !alias
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alias_no_subcmd_shows_help() -> None:
    bot = _make_bot()
    msg = _make_message("!alias")
    await sc.handle_alias(bot, msg)
    msg.reply.assert_awaited_once()
    body = msg.reply.await_args.args[0]
    assert "Алиасы" in body


@pytest.mark.asyncio
async def test_alias_list_calls_format_list() -> None:
    bot = _make_bot()
    msg = _make_message("!alias list")
    fake_service = MagicMock()
    fake_service.format_list = MagicMock(return_value="Список: пусто")
    with patch.object(sc, "alias_service", fake_service):
        await sc.handle_alias(bot, msg)
    fake_service.format_list.assert_called_once()
    msg.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_alias_set_persists_via_service() -> None:
    bot = _make_bot()
    msg = _make_message("!alias set t !translate")
    fake_service = MagicMock()
    fake_service.add = MagicMock(return_value=(True, "ok"))
    with patch.object(sc, "alias_service", fake_service):
        await sc.handle_alias(bot, msg)
    fake_service.add.assert_called_once_with("t", "!translate")


@pytest.mark.asyncio
async def test_alias_unknown_subcmd_raises() -> None:
    bot = _make_bot()
    msg = _make_message("!alias frobnicate stuff")
    with pytest.raises(UserInputError):
        await sc.handle_alias(bot, msg)


# ---------------------------------------------------------------------------
# Re-exports preserve API
# ---------------------------------------------------------------------------


class TestReExports:
    """Re-exports через src.handlers.command_handlers — preserve API."""

    def test_handlers_re_exported(self) -> None:
        from src.handlers import command_handlers as ch

        assert ch.handle_pin is sc.handle_pin
        assert ch.handle_unpin is sc.handle_unpin
        assert ch.handle_del is sc.handle_del
        assert ch.handle_purge is sc.handle_purge
        assert ch.handle_react is sc.handle_react
        assert ch.handle_poll is sc.handle_poll
        assert ch.handle_quiz is sc.handle_quiz
        assert ch.handle_dice is sc.handle_dice
        assert ch.handle_sticker is sc.handle_sticker
        assert ch.handle_alias is sc.handle_alias

    def test_helpers_and_state_re_exported(self) -> None:
        from src.handlers import command_handlers as ch

        assert ch._STICKERS_FILE is sc._STICKERS_FILE
        assert ch._load_stickers is sc._load_stickers
        assert ch._save_stickers is sc._save_stickers
