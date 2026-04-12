# -*- coding: utf-8 -*-
"""
Тесты команды !dice (handle_dice) из src/handlers/command_handlers.py.

Покрытие:
- !dice (без аргументов) → 🎲 кубик
- !dice dice → 🎲 кубик (явный alias)
- !dice dart → 🎯 дартс
- !dice darts → 🎯 дартс (alias)
- !dice ball → ⚽ футбол
- !dice football → ⚽ футбол (alias)
- !dice soccer → ⚽ футбол (alias)
- !dice basket → 🏀 баскетбол
- !dice basketball → 🏀 баскетбол (alias)
- !dice bowl → 🎳 боулинг
- !dice bowling → 🎳 боулинг (alias)
- !dice slot → 🎰 слот-машина
- !dice slots → 🎰 слот-машина (alias)
- !dice casino → 🎰 слот-машина (alias)
- Неизвестный alias → UserInputError с help-текстом
- Удаление исходного сообщения после успеха
- Ошибка удаления поглощается — dice всё равно отправляется
- send_dice вызывается с правильным chat_id
- Аргументы нечувствительны к регистру
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_dice


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _make_bot(command_args: str = "") -> MagicMock:
    """Мок KraabUserbot с send_dice на клиенте."""
    bot = MagicMock()
    bot.client = MagicMock()
    bot.client.send_dice = AsyncMock()
    bot._get_command_args = MagicMock(return_value=command_args)
    return bot


def _make_message(chat_id: int = 100) -> MagicMock:
    msg = MagicMock()
    msg.chat = SimpleNamespace(id=chat_id)
    msg.reply = AsyncMock()
    msg.delete = AsyncMock()
    return msg


# ===========================================================================
# Базовые типы dice
# ===========================================================================


@pytest.mark.asyncio
async def test_dice_default_no_args() -> None:
    """!dice без аргументов → 🎲."""
    bot = _make_bot("")
    message = _make_message(chat_id=555)

    await handle_dice(bot, message)

    bot.client.send_dice.assert_awaited_once_with(chat_id=555, emoji="🎲")


@pytest.mark.asyncio
async def test_dice_explicit_dice_alias() -> None:
    """!dice dice → 🎲."""
    bot = _make_bot("dice")
    message = _make_message()

    await handle_dice(bot, message)

    call_kwargs = bot.client.send_dice.call_args.kwargs
    assert call_kwargs["emoji"] == "🎲"


@pytest.mark.asyncio
async def test_dice_dart() -> None:
    """!dice dart → 🎯."""
    bot = _make_bot("dart")
    message = _make_message(chat_id=10)

    await handle_dice(bot, message)

    bot.client.send_dice.assert_awaited_once_with(chat_id=10, emoji="🎯")


@pytest.mark.asyncio
async def test_dice_darts_alias() -> None:
    """!dice darts → 🎯 (alias)."""
    bot = _make_bot("darts")
    message = _make_message()

    await handle_dice(bot, message)

    assert bot.client.send_dice.call_args.kwargs["emoji"] == "🎯"


@pytest.mark.asyncio
async def test_dice_ball() -> None:
    """!dice ball → ⚽."""
    bot = _make_bot("ball")
    message = _make_message(chat_id=20)

    await handle_dice(bot, message)

    bot.client.send_dice.assert_awaited_once_with(chat_id=20, emoji="⚽")


@pytest.mark.asyncio
async def test_dice_football_alias() -> None:
    """!dice football → ⚽ (alias)."""
    bot = _make_bot("football")
    message = _make_message()

    await handle_dice(bot, message)

    assert bot.client.send_dice.call_args.kwargs["emoji"] == "⚽"


@pytest.mark.asyncio
async def test_dice_soccer_alias() -> None:
    """!dice soccer → ⚽ (alias)."""
    bot = _make_bot("soccer")
    message = _make_message()

    await handle_dice(bot, message)

    assert bot.client.send_dice.call_args.kwargs["emoji"] == "⚽"


@pytest.mark.asyncio
async def test_dice_basket() -> None:
    """!dice basket → 🏀."""
    bot = _make_bot("basket")
    message = _make_message(chat_id=30)

    await handle_dice(bot, message)

    bot.client.send_dice.assert_awaited_once_with(chat_id=30, emoji="🏀")


@pytest.mark.asyncio
async def test_dice_basketball_alias() -> None:
    """!dice basketball → 🏀 (alias)."""
    bot = _make_bot("basketball")
    message = _make_message()

    await handle_dice(bot, message)

    assert bot.client.send_dice.call_args.kwargs["emoji"] == "🏀"


@pytest.mark.asyncio
async def test_dice_bowl() -> None:
    """!dice bowl → 🎳."""
    bot = _make_bot("bowl")
    message = _make_message(chat_id=40)

    await handle_dice(bot, message)

    bot.client.send_dice.assert_awaited_once_with(chat_id=40, emoji="🎳")


@pytest.mark.asyncio
async def test_dice_bowling_alias() -> None:
    """!dice bowling → 🎳 (alias)."""
    bot = _make_bot("bowling")
    message = _make_message()

    await handle_dice(bot, message)

    assert bot.client.send_dice.call_args.kwargs["emoji"] == "🎳"


@pytest.mark.asyncio
async def test_dice_slot() -> None:
    """!dice slot → 🎰."""
    bot = _make_bot("slot")
    message = _make_message(chat_id=50)

    await handle_dice(bot, message)

    bot.client.send_dice.assert_awaited_once_with(chat_id=50, emoji="🎰")


@pytest.mark.asyncio
async def test_dice_slots_alias() -> None:
    """!dice slots → 🎰 (alias)."""
    bot = _make_bot("slots")
    message = _make_message()

    await handle_dice(bot, message)

    assert bot.client.send_dice.call_args.kwargs["emoji"] == "🎰"


@pytest.mark.asyncio
async def test_dice_casino_alias() -> None:
    """!dice casino → 🎰 (alias)."""
    bot = _make_bot("casino")
    message = _make_message()

    await handle_dice(bot, message)

    assert bot.client.send_dice.call_args.kwargs["emoji"] == "🎰"


# ===========================================================================
# Ошибки валидации
# ===========================================================================


@pytest.mark.asyncio
async def test_dice_unknown_type_raises() -> None:
    """Неизвестный alias → UserInputError."""
    bot = _make_bot("cards")
    message = _make_message()

    with pytest.raises(UserInputError):
        await handle_dice(bot, message)

    bot.client.send_dice.assert_not_awaited()


@pytest.mark.asyncio
async def test_dice_unknown_raises_help_text() -> None:
    """UserInputError содержит список типов."""
    bot = _make_bot("unknown_type")
    message = _make_message()

    with pytest.raises(UserInputError) as exc_info:
        await handle_dice(bot, message)

    msg = exc_info.value.user_message
    assert "dart" in msg.lower()
    assert "slot" in msg.lower()


@pytest.mark.asyncio
async def test_dice_help_keyword_raises() -> None:
    """Аргумент 'help' → UserInputError (не является alias)."""
    bot = _make_bot("help")
    message = _make_message()

    with pytest.raises(UserInputError):
        await handle_dice(bot, message)

    bot.client.send_dice.assert_not_awaited()


# ===========================================================================
# Поведение после отправки
# ===========================================================================


@pytest.mark.asyncio
async def test_dice_deletes_command_message() -> None:
    """После успешной отправки исходная команда удаляется."""
    bot = _make_bot("dart")
    message = _make_message()

    await handle_dice(bot, message)

    message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_dice_delete_failure_silenced() -> None:
    """Ошибка при удалении поглощается — dice всё равно отправляется."""
    bot = _make_bot("slot")
    message = _make_message()
    message.delete = AsyncMock(side_effect=Exception("NO_PERMISSION"))

    await handle_dice(bot, message)

    bot.client.send_dice.assert_awaited_once()


@pytest.mark.asyncio
async def test_dice_correct_chat_id_passed() -> None:
    """chat_id из message.chat.id передаётся в send_dice."""
    bot = _make_bot("bowl")
    message = _make_message(chat_id=999)

    await handle_dice(bot, message)

    assert bot.client.send_dice.call_args.kwargs["chat_id"] == 999


# ===========================================================================
# Нечувствительность к регистру
# ===========================================================================


@pytest.mark.asyncio
async def test_dice_args_case_insensitive_upper() -> None:
    """Аргумент в верхнем регистре DART → 🎯."""
    bot = _make_bot("DART")
    message = _make_message()

    await handle_dice(bot, message)

    assert bot.client.send_dice.call_args.kwargs["emoji"] == "🎯"


@pytest.mark.asyncio
async def test_dice_args_case_insensitive_mixed() -> None:
    """Аргумент в смешанном регистре Slot → 🎰."""
    bot = _make_bot("Slot")
    message = _make_message()

    await handle_dice(bot, message)

    assert bot.client.send_dice.call_args.kwargs["emoji"] == "🎰"


@pytest.mark.asyncio
async def test_dice_args_with_whitespace() -> None:
    """Пробелы вокруг аргумента срезаются: '  basket  ' → 🏀."""
    bot = _make_bot("  basket  ")
    message = _make_message()

    await handle_dice(bot, message)

    assert bot.client.send_dice.call_args.kwargs["emoji"] == "🏀"


# ===========================================================================
# Точность вызова send_dice
# ===========================================================================


@pytest.mark.asyncio
async def test_dice_send_dice_called_exactly_once() -> None:
    """send_dice вызывается ровно один раз."""
    bot = _make_bot("ball")
    message = _make_message()

    await handle_dice(bot, message)

    assert bot.client.send_dice.await_count == 1


@pytest.mark.asyncio
async def test_dice_send_dice_not_called_on_error() -> None:
    """При ошибке валидации send_dice не вызывается."""
    bot = _make_bot("nonexistent")
    message = _make_message()

    with pytest.raises(UserInputError):
        await handle_dice(bot, message)

    assert bot.client.send_dice.await_count == 0
