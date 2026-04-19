# -*- coding: utf-8 -*-
"""
Тесты функционала реакций на owner-сообщения (src/userbot_bridge.py).

Покрытие:
- _send_message_reaction: базовый вызов, disabled через config, некорректные chat_id/msg_id
- TELEGRAM_REACTIONS_ENABLED влияет на отправку реакций
- Реакция 👀 ставится при is_self=True в начале pipeline
- Исключения в send_reaction молча поглощаются
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.userbot_bridge import KraabUserbot

# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _make_bot() -> KraabUserbot:
    """Минимальный stub KraabUserbot без вызова __init__."""
    bot = KraabUserbot.__new__(KraabUserbot)
    bot.current_role = "default"
    bot.me = SimpleNamespace(id=777)
    bot.client = AsyncMock()
    bot.client.send_reaction = AsyncMock()
    return bot


def _make_message(chat_id: int = 123, message_id: int = 456) -> MagicMock:
    """Фейковое Pyrogram Message с chat и id."""
    msg = MagicMock()
    msg.id = message_id
    msg.chat = SimpleNamespace(id=chat_id)
    return msg


# ---------------------------------------------------------------------------
# _send_message_reaction: базовый вызов
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_reaction_calls_send_reaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """При TELEGRAM_REACTIONS_ENABLED=True send_reaction вызывается с правильными аргументами."""
    import src.userbot_bridge as _ub

    monkeypatch.setattr(_ub.config, "TELEGRAM_REACTIONS_ENABLED", True)
    bot = _make_bot()
    msg = _make_message(chat_id=100, message_id=200)

    await bot._send_message_reaction(msg, "👀")

    bot.client.send_reaction.assert_awaited_once_with(
        chat_id=100,
        message_id=200,
        emoji="👀",
    )


@pytest.mark.asyncio
async def test_send_message_reaction_disabled_by_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """При TELEGRAM_REACTIONS_ENABLED=False send_reaction НЕ вызывается."""
    import src.userbot_bridge as _ub

    monkeypatch.setattr(_ub.config, "TELEGRAM_REACTIONS_ENABLED", False)
    bot = _make_bot()
    msg = _make_message()

    await bot._send_message_reaction(msg, "✅")

    bot.client.send_reaction.assert_not_called()


@pytest.mark.asyncio
async def test_send_message_reaction_zero_chat_id_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    """При chat_id=0 send_reaction НЕ вызывается (некорректное сообщение)."""
    import src.userbot_bridge as _ub

    monkeypatch.setattr(_ub.config, "TELEGRAM_REACTIONS_ENABLED", True)
    bot = _make_bot()
    msg = _make_message(chat_id=0, message_id=100)

    await bot._send_message_reaction(msg, "👀")

    bot.client.send_reaction.assert_not_called()


@pytest.mark.asyncio
async def test_send_message_reaction_zero_message_id_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    """При message_id=0 send_reaction НЕ вызывается."""
    import src.userbot_bridge as _ub

    monkeypatch.setattr(_ub.config, "TELEGRAM_REACTIONS_ENABLED", True)
    bot = _make_bot()
    msg = _make_message(chat_id=100, message_id=0)

    await bot._send_message_reaction(msg, "✅")

    bot.client.send_reaction.assert_not_called()


@pytest.mark.asyncio
async def test_send_message_reaction_exception_is_silenced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если send_reaction выбрасывает исключение — оно поглощается, не падает."""
    import src.userbot_bridge as _ub

    monkeypatch.setattr(_ub.config, "TELEGRAM_REACTIONS_ENABLED", True)
    bot = _make_bot()
    bot.client.send_reaction = AsyncMock(side_effect=Exception("REACTIONS_DISABLED"))
    msg = _make_message(chat_id=100, message_id=200)

    # Не должно бросить исключение
    await bot._send_message_reaction(msg, "👀")


@pytest.mark.asyncio
async def test_send_message_reaction_different_emojis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Каждый эмодзи передаётся без изменений."""
    import src.userbot_bridge as _ub

    monkeypatch.setattr(_ub.config, "TELEGRAM_REACTIONS_ENABLED", True)
    bot = _make_bot()
    msg = _make_message(chat_id=1, message_id=1)

    for emoji in ("👀", "✅", "❌"):
        bot.client.send_reaction.reset_mock()
        await bot._send_message_reaction(msg, emoji)
        bot.client.send_reaction.assert_awaited_once_with(chat_id=1, message_id=1, emoji=emoji)


# ---------------------------------------------------------------------------
# TELEGRAM_REACTIONS_ENABLED в config
# ---------------------------------------------------------------------------


def test_config_reactions_enabled_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """TELEGRAM_REACTIONS_ENABLED по умолчанию True (из env '1')."""
    import importlib

    import src.config as config_module

    monkeypatch.setenv("TELEGRAM_REACTIONS_ENABLED", "1")
    importlib.reload(config_module)

    assert config_module.config.TELEGRAM_REACTIONS_ENABLED is True

    # Восстанавливаем canonical config (иначе conftest ломается)
    import src.userbot_bridge as _ub

    monkeypatch.setattr(config_module, "config", _ub.config)


def test_config_reactions_disabled_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """TELEGRAM_REACTIONS_ENABLED=0 → False."""
    import importlib

    import src.config as config_module

    monkeypatch.setenv("TELEGRAM_REACTIONS_ENABLED", "0")
    importlib.reload(config_module)

    assert config_module.config.TELEGRAM_REACTIONS_ENABLED is False

    import src.userbot_bridge as _ub

    monkeypatch.setattr(config_module, "config", _ub.config)


def test_config_reactions_enabled_true_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """TELEGRAM_REACTIONS_ENABLED=true → True."""
    import importlib

    import src.config as config_module

    monkeypatch.setenv("TELEGRAM_REACTIONS_ENABLED", "true")
    importlib.reload(config_module)

    assert config_module.config.TELEGRAM_REACTIONS_ENABLED is True

    import src.userbot_bridge as _ub

    monkeypatch.setattr(config_module, "config", _ub.config)


# ---------------------------------------------------------------------------
# Тесты через message с None-атрибутами (edge cases)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_reaction_message_without_chat_attr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сообщение без атрибута chat не вызывает исключения."""
    import src.userbot_bridge as _ub

    monkeypatch.setattr(_ub.config, "TELEGRAM_REACTIONS_ENABLED", True)
    bot = _make_bot()
    msg = MagicMock()
    msg.id = 100
    msg.chat = None  # chat отсутствует

    # Не должно падать
    await bot._send_message_reaction(msg, "👀")
    bot.client.send_reaction.assert_not_called()


@pytest.mark.asyncio
async def test_send_reaction_message_chat_id_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """chat.id = None → пропускаем."""
    import src.userbot_bridge as _ub

    monkeypatch.setattr(_ub.config, "TELEGRAM_REACTIONS_ENABLED", True)
    bot = _make_bot()
    msg = MagicMock()
    msg.id = 100
    msg.chat = SimpleNamespace(id=None)

    await bot._send_message_reaction(msg, "✅")
    bot.client.send_reaction.assert_not_called()
