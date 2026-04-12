# -*- coding: utf-8 -*-
"""
Тесты _handle_message_reaction_updated (src/userbot_bridge.py).

Покрытие:
- Добавленная реакция записывается в ReactionEngine
- Удалённая реакция не записывается как feedback
- Без изменений (old == new) → ничего не записывается
- Некорректные данные (None chat) не вызывают исключений
- Feedback логируется корректно
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.userbot_bridge import KraabUserbot


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _make_bot() -> KraabUserbot:
    bot = KraabUserbot.__new__(KraabUserbot)
    bot.me = SimpleNamespace(id=777)
    bot.client = AsyncMock()
    return bot


def _make_reaction(
    *,
    chat_id: int = 100,
    message_id: int = 200,
    user_id: int = 42,
    new_emojis: list[str] | None = None,
    old_emojis: list[str] | None = None,
) -> MagicMock:
    """Создаёт stub MessageReactionUpdated."""
    def _make_reaction_obj(emoji: str) -> SimpleNamespace:
        return SimpleNamespace(emoji=emoji, emoticon=None)

    update = MagicMock()
    update.id = message_id
    update.chat = SimpleNamespace(id=chat_id)
    update.from_user = SimpleNamespace(id=user_id)
    update.new_reaction = [_make_reaction_obj(e) for e in (new_emojis or [])]
    update.old_reaction = [_make_reaction_obj(e) for e in (old_emojis or [])]
    return update


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reaction_updated_records_added_emoji() -> None:
    """Новая реакция (не было в old) записывается в ReactionEngine."""
    from src.core.reaction_engine import ReactionEngine

    bot = _make_bot()
    mock_engine = ReactionEngine()

    with patch("src.userbot_bridge.KraabUserbot._handle_message_reaction_updated",
               wraps=bot._handle_message_reaction_updated):
        with patch("src.core.reaction_engine.reaction_engine", mock_engine):
            update = _make_reaction(new_emojis=["👍"], old_emojis=[])
            await bot._handle_message_reaction_updated(update)

    stats = mock_engine.get_reaction_stats(chat_id=100)
    assert stats["total"] == 1
    assert stats["positive"] == 1


@pytest.mark.asyncio
async def test_reaction_updated_no_change_not_recorded() -> None:
    """Если old == new → ничего не записывается."""
    from src.core.reaction_engine import ReactionEngine

    bot = _make_bot()
    mock_engine = ReactionEngine()

    with patch("src.core.reaction_engine.reaction_engine", mock_engine):
        update = _make_reaction(new_emojis=["👍"], old_emojis=["👍"])
        await bot._handle_message_reaction_updated(update)

    stats = mock_engine.get_reaction_stats(chat_id=100)
    assert stats["total"] == 0


@pytest.mark.asyncio
async def test_reaction_updated_negative_emoji_recorded() -> None:
    """👎 записывается как negative feedback."""
    from src.core.reaction_engine import ReactionEngine

    bot = _make_bot()
    mock_engine = ReactionEngine()

    with patch("src.core.reaction_engine.reaction_engine", mock_engine):
        update = _make_reaction(new_emojis=["👎"], old_emojis=[])
        await bot._handle_message_reaction_updated(update)

    stats = mock_engine.get_reaction_stats(chat_id=100)
    assert stats["negative"] == 1


@pytest.mark.asyncio
async def test_reaction_updated_empty_new_and_old_skipped() -> None:
    """Пустые new и old → ничего не записывается."""
    from src.core.reaction_engine import ReactionEngine

    bot = _make_bot()
    mock_engine = ReactionEngine()

    with patch("src.core.reaction_engine.reaction_engine", mock_engine):
        update = _make_reaction(new_emojis=[], old_emojis=[])
        await bot._handle_message_reaction_updated(update)

    stats = mock_engine.get_reaction_stats(chat_id=100)
    assert stats["total"] == 0


@pytest.mark.asyncio
async def test_reaction_updated_none_chat_no_exception() -> None:
    """Если chat=None → без исключений."""
    bot = _make_bot()
    update = MagicMock()
    update.id = 100
    update.chat = None  # нет чата
    update.from_user = SimpleNamespace(id=42)
    update.new_reaction = []
    update.old_reaction = []

    # Не должно бросить исключение
    await bot._handle_message_reaction_updated(update)


@pytest.mark.asyncio
async def test_reaction_updated_none_from_user_no_exception() -> None:
    """Если from_user=None (анонимный) → без исключений."""
    from src.core.reaction_engine import ReactionEngine

    bot = _make_bot()
    mock_engine = ReactionEngine()
    update = _make_reaction(new_emojis=["❤️"], old_emojis=[])
    update.from_user = None  # анонимный реагент

    with patch("src.core.reaction_engine.reaction_engine", mock_engine):
        await bot._handle_message_reaction_updated(update)

    # Всё равно должно записаться (user_id=None)
    stats = mock_engine.get_reaction_stats(chat_id=100)
    assert stats["total"] == 1


@pytest.mark.asyncio
async def test_reaction_updated_multiple_new_emojis() -> None:
    """Несколько новых реакций сразу → все записываются."""
    from src.core.reaction_engine import ReactionEngine

    bot = _make_bot()
    mock_engine = ReactionEngine()

    with patch("src.core.reaction_engine.reaction_engine", mock_engine):
        update = _make_reaction(new_emojis=["👍", "❤️", "🔥"], old_emojis=[])
        await bot._handle_message_reaction_updated(update)

    stats = mock_engine.get_reaction_stats(chat_id=100)
    assert stats["total"] == 3
    assert stats["positive"] == 3


@pytest.mark.asyncio
async def test_reaction_updated_engine_error_no_exception() -> None:
    """Если ReactionEngine.record_reaction падает — основной handler не падает."""
    from src.core.reaction_engine import ReactionEngine

    bot = _make_bot()
    broken_engine = MagicMock()
    broken_engine.record_reaction = MagicMock(side_effect=RuntimeError("broken"))

    with patch("src.core.reaction_engine.reaction_engine", broken_engine):
        update = _make_reaction(new_emojis=["👍"], old_emojis=[])
        # Не должно бросить исключение
        await bot._handle_message_reaction_updated(update)


@pytest.mark.asyncio
async def test_reaction_updated_partial_overlap() -> None:
    """old=[👍,❤️], new=[👍,👎] → добавлена 👎, 👍 осталась (не добавляется снова)."""
    from src.core.reaction_engine import ReactionEngine

    bot = _make_bot()
    mock_engine = ReactionEngine()

    with patch("src.core.reaction_engine.reaction_engine", mock_engine):
        update = _make_reaction(
            new_emojis=["👍", "👎"],
            old_emojis=["👍", "❤️"],
        )
        await bot._handle_message_reaction_updated(update)

    stats = mock_engine.get_reaction_stats(chat_id=100)
    # Только 👎 новая (не было в old)
    assert stats["total"] == 1
    assert stats["negative"] == 1
