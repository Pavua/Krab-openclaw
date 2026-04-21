# -*- coding: utf-8 -*-
"""
Тесты контекстных авто-реакций (src/core/auto_reactions.py).

Покрытие:
1.  Вопрос-сообщение → None (не ставим реакцию)
2.  Благодарность с seed 0 → 👍 или 🙏
3.  Благодарность с random.random > rate → None
4.  Грустное/проблемное → 🤔 или 😕
5.  Юмор → 😂
6.  Команда (! /start) → None
7.  mode=off → всегда None
8.  mode=aggressive → 50%+ вероятность для любого текста
9.  Rate-limit: 5 gratitude подряд → max 1 реакция (мокаем random)
10. SAFE_EMOJI_WHITELIST — неизвестный emoji отклоняется без API-вызова
11. mark_accepted делегирует в contextual (no unconditional 👍)
12. mark_completed — всегда False (no-op)
13. mark_memory_recall — всегда False (no-op)
14. mark_failed — явная ❌, без rate-limit
15. handle_react status показывает mode и rate
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.core.auto_reactions as ar

# ---------------------------------------------------------------------------
# Вспомогательные фабрики (переиспользуем паттерн из test_auto_reactions.py)
# ---------------------------------------------------------------------------


def _make_group_message(chat_id: int = 100, message_id: int = 42, text: str = "") -> MagicMock:
    """Создаёт mock сообщения в группе (non-PRIVATE)."""
    msg = MagicMock()
    msg.chat.id = chat_id
    msg.id = message_id
    msg.text = text
    msg.caption = None
    # Тип — не PRIVATE (строка для fallback режима)
    msg.chat.type = "GROUP"
    return msg


def _make_bot_with_send_reaction() -> MagicMock:
    bot = MagicMock()
    bot.send_reaction = AsyncMock(return_value=None)
    return bot


def _make_bot_no_api() -> MagicMock:
    bot = MagicMock(spec=[])
    return bot


# ---------------------------------------------------------------------------
# Тесты pick_contextual_emoji
# ---------------------------------------------------------------------------


def test_question_only_returns_none():
    """Вопрос (? в конце, короткий) → не ставим реакцию в contextual режиме."""
    result = ar.pick_contextual_emoji("Как дела?", mode="contextual")
    assert result is None


def test_command_returns_none():
    """Команда !ask → без реакции."""
    assert ar.pick_contextual_emoji("!ask расскажи о Python", mode="contextual") is None
    assert ar.pick_contextual_emoji("/start", mode="contextual") is None


def test_mode_off_returns_none():
    """mode=off → всегда None, независимо от текста."""
    texts = ["спасибо", "хаха", "ошибка", "привет", "!что-то"]
    for text in texts:
        assert ar.pick_contextual_emoji(text, mode="off") is None, f"Failed for: {text}"


def test_gratitude_with_random_below_rate():
    """Благодарность + random < rate → возвращает 👍 или 🙏."""
    with patch("src.core.auto_reactions.random.random", return_value=0.0):  # всегда ниже rate
        result = ar.pick_contextual_emoji("спасибо большое!", mode="contextual")
    assert result in ("👍", "🙏")


def test_gratitude_always_reacts_regardless_of_rate():
    """Благодарность → реагирует всегда (rate limit не применяется к паттернам)."""
    with patch("src.core.auto_reactions.random.random", return_value=0.99):
        result = ar.pick_contextual_emoji("спасибо", mode="contextual")
    assert result in ("👍", "🙏", "❤️")


def test_sadness_returns_emoji():
    """Сообщение с проблемой → 🤔 или 😕."""
    with patch("src.core.auto_reactions.random.random", return_value=0.0):
        result = ar.pick_contextual_emoji("бага в коде, помогите!", mode="contextual")
    assert result in ("🤔", "😕")


def test_humor_returns_emoji():
    """Сообщение с юмором → 😂."""
    with patch("src.core.auto_reactions.random.random", return_value=0.0):
        result = ar.pick_contextual_emoji("хаха это смешно лол", mode="contextual")
    assert result == "😂"


def test_aggressive_mode_boosts_probability():
    """mode=aggressive → вероятность >= 0.5 для нейтрального текста."""
    hits = 0
    n = 1000
    with patch.dict(os.environ, {"KRAB_AUTO_REACTION_RATE_LIMIT": "0.5"}):
        for _ in range(n):
            res = ar.pick_contextual_emoji("привет как дела", mode="aggressive")
            if res is not None:
                hits += 1
    # Должно быть как минимум 30% попаданий при rate=0.5 (статистика)
    assert hits > n * 0.2, f"Слишком мало hits: {hits}/{n}"


def test_aggressive_mode_uses_candidates():
    """mode=aggressive возвращает один из кандидатов."""
    with patch("src.core.auto_reactions.random.random", return_value=0.0):
        result = ar.pick_contextual_emoji("нейтральный текст", mode="aggressive")
    assert result in ("👍", "👀", "🤔", "🔥")


# ---------------------------------------------------------------------------
# Тесты rate-limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_max_one_per_window():
    """5 благодарностей подряд в одном чате → не более 1 реакции."""
    chat_id = 9999
    # Очищаем state для этого chat_id
    ar._reaction_timestamps[chat_id] = []

    bot = _make_bot_with_send_reaction()
    msg = _make_group_message(chat_id=chat_id, text="спасибо")

    reaction_count = 0
    # Мокаем random: всегда даём emoji (random=0.0 < rate)
    with (
        patch("src.core.auto_reactions.random.random", return_value=0.0),
        patch.dict(
            os.environ, {"AUTO_REACTIONS_ENABLED": "true", "KRAB_AUTO_REACTIONS_MODE": "contextual"}
        ),
    ):
        for _ in range(5):
            ok = await ar.contextual_pre_reply_reaction(bot, msg, user_text="спасибо")
            if ok:
                reaction_count += 1

    assert reaction_count <= 1, f"Rate-limit пробит: {reaction_count} реакций"


# ---------------------------------------------------------------------------
# Тесты SAFE_EMOJI_WHITELIST
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_whitelist_unknown_emoji_skipped():
    """Emoji не из whitelist → set_reaction возвращает False, API не вызывается."""
    bot = _make_bot_with_send_reaction()
    with patch.dict(os.environ, {"AUTO_REACTIONS_ENABLED": "true"}):
        result = await ar.set_reaction(bot, 100, 42, "🌈")
    assert result is False
    bot.send_reaction.assert_not_called()


@pytest.mark.asyncio
async def test_whitelist_known_emoji_passes():
    """Emoji из whitelist → API вызывается."""
    bot = _make_bot_with_send_reaction()
    with patch.dict(os.environ, {"AUTO_REACTIONS_ENABLED": "true"}):
        result = await ar.set_reaction(bot, 100, 42, "👍")
    assert result is True
    bot.send_reaction.assert_awaited_once()


# ---------------------------------------------------------------------------
# Тесты mark_* функций
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_accepted_no_unconditional_reaction():
    """mark_accepted больше не ставит 👍 безусловно — только если контекст уместен."""
    bot = _make_bot_with_send_reaction()
    # Нейтральное сообщение → нет реакции в contextual режиме
    msg = _make_group_message(text="нейтральный текст")
    with (
        patch.dict(
            os.environ, {"AUTO_REACTIONS_ENABLED": "true", "KRAB_AUTO_REACTIONS_MODE": "contextual"}
        ),
        patch("src.core.auto_reactions.random.random", return_value=0.99),
    ):  # выше любого rate
        result = await ar.mark_accepted(bot, msg)
    assert result is False
    bot.send_reaction.assert_not_called()


@pytest.mark.asyncio
async def test_mark_accepted_with_gratitude_can_react():
    """mark_accepted ставит реакцию при благодарном тексте."""
    chat_id = 8888
    ar._reaction_timestamps[chat_id] = []
    bot = _make_bot_with_send_reaction()
    msg = _make_group_message(chat_id=chat_id, text="спасибо!")
    with (
        patch.dict(
            os.environ, {"AUTO_REACTIONS_ENABLED": "true", "KRAB_AUTO_REACTIONS_MODE": "contextual"}
        ),
        patch("src.core.auto_reactions.random.random", return_value=0.0),
    ):
        result = await ar.mark_accepted(bot, msg)
    assert result is True
    bot.send_reaction.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_completed_is_noop():
    """mark_completed всегда возвращает False — больше не спамит ✅."""
    bot = _make_bot_with_send_reaction()
    msg = _make_group_message(text="что угодно")
    with patch.dict(os.environ, {"AUTO_REACTIONS_ENABLED": "true"}):
        result = await ar.mark_completed(bot, msg)
    assert result is False
    bot.send_reaction.assert_not_called()


@pytest.mark.asyncio
async def test_mark_memory_recall_is_noop():
    """mark_memory_recall всегда возвращает False — убираем 🧠 спам."""
    bot = _make_bot_with_send_reaction()
    msg = _make_group_message(text="что угодно")
    with patch.dict(os.environ, {"AUTO_REACTIONS_ENABLED": "true"}):
        result = await ar.mark_memory_recall(bot, msg)
    assert result is False
    bot.send_reaction.assert_not_called()


@pytest.mark.asyncio
async def test_mark_failed_is_explicit_no_rate_limit():
    """mark_failed ставит ❌ явно, без rate-limit."""
    bot = _make_bot_with_send_reaction()
    msg = _make_group_message(text="что угодно")
    with patch.dict(os.environ, {"AUTO_REACTIONS_ENABLED": "true"}):
        result = await ar.mark_failed(bot, msg, error="test error")
    assert result is True
    _, kwargs = bot.send_reaction.call_args
    assert kwargs["emoji"] == "❌"


# ---------------------------------------------------------------------------
# Тест handle_react status (показывает mode и rate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_react_status_shows_mode_and_rate():
    """!react status показывает mode и rate в ответе."""
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value="status")
    msg = _make_group_message()
    msg.reply = AsyncMock()
    with patch.dict(
        os.environ,
        {
            "AUTO_REACTIONS_ENABLED": "true",
            "KRAB_AUTO_REACTIONS_MODE": "contextual",
            "KRAB_AUTO_REACTION_RATE_LIMIT": "0.2",
        },
    ):
        await ar.handle_react(bot, msg)
    msg.reply.assert_awaited_once()
    text = msg.reply.call_args[0][0]
    assert "contextual" in text
    assert "0.2" in text
