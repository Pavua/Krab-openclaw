# -*- coding: utf-8 -*-
"""
Тесты для ReactionEngine (src/core/reaction_engine.py).

Покрытие:
- record_reaction: базовый вызов, positive/negative/neutral классификация
- get_reaction_stats: per-chat и агрегат
- get_chat_mood: positive/negative/neutral/unknown
- ChatReactionStats: rolling window, emoji_counts
- POSITIVE_REACTIONS / NEGATIVE_REACTIONS константы
"""

from __future__ import annotations

from src.core.reaction_engine import (
    NEGATIVE_REACTIONS,
    NEUTRAL_REACTIONS,
    POSITIVE_REACTIONS,
    ChatReactionStats,
    ReactionEngine,
    ReactionEvent,
)

# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _make_engine() -> ReactionEngine:
    return ReactionEngine()


def _record(engine: ReactionEngine, emoji: str, chat_id: int = 1, msg_id: int = 100) -> None:
    engine.record_reaction(
        chat_id=chat_id,
        message_id=msg_id,
        user_id=42,
        new_emojis=[emoji],
        old_emojis=[],
    )


# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------


def test_positive_reactions_contains_thumbsup() -> None:
    assert "👍" in POSITIVE_REACTIONS


def test_positive_reactions_contains_heart() -> None:
    assert "❤️" in POSITIVE_REACTIONS


def test_negative_reactions_contains_thumbsdown() -> None:
    assert "👎" in NEGATIVE_REACTIONS


def test_neutral_reactions_contains_eyes() -> None:
    assert "👀" in NEUTRAL_REACTIONS


def test_reactions_sets_are_disjoint() -> None:
    """Реакции не должны пересекаться между категориями."""
    assert not POSITIVE_REACTIONS & NEGATIVE_REACTIONS
    assert not POSITIVE_REACTIONS & NEUTRAL_REACTIONS
    assert not NEGATIVE_REACTIONS & NEUTRAL_REACTIONS


# ---------------------------------------------------------------------------
# ReactionEvent
# ---------------------------------------------------------------------------


def test_reaction_event_positive() -> None:
    event = ReactionEvent(
        chat_id=1,
        message_id=1,
        user_id=1,
        emoji="👍",
        is_positive=True,
        is_negative=False,
    )
    assert event.is_positive
    assert not event.is_negative


def test_reaction_event_negative() -> None:
    event = ReactionEvent(
        chat_id=1,
        message_id=1,
        user_id=1,
        emoji="👎",
        is_positive=False,
        is_negative=True,
    )
    assert event.is_negative
    assert not event.is_positive


def test_reaction_event_has_timestamp() -> None:
    import time

    before = time.time()
    event = ReactionEvent(
        chat_id=1,
        message_id=1,
        user_id=1,
        emoji="👍",
        is_positive=True,
        is_negative=False,
    )
    after = time.time()
    assert before <= event.timestamp <= after


# ---------------------------------------------------------------------------
# ChatReactionStats
# ---------------------------------------------------------------------------


def test_chat_reaction_stats_empty() -> None:
    stats = ChatReactionStats()
    assert stats.total_count == 0
    assert stats.positive_count == 0
    assert stats.negative_count == 0
    assert stats.get_mood() == "unknown"


def test_chat_reaction_stats_add_positive() -> None:
    stats = ChatReactionStats()
    event = ReactionEvent(
        chat_id=1,
        message_id=1,
        user_id=1,
        emoji="👍",
        is_positive=True,
        is_negative=False,
    )
    stats.add(event)
    assert stats.total_count == 1
    assert stats.positive_count == 1
    assert stats.negative_count == 0
    assert stats.emoji_counts["👍"] == 1


def test_chat_reaction_stats_add_negative() -> None:
    stats = ChatReactionStats()
    event = ReactionEvent(
        chat_id=1,
        message_id=1,
        user_id=1,
        emoji="👎",
        is_positive=False,
        is_negative=True,
    )
    stats.add(event)
    assert stats.negative_count == 1
    assert stats.positive_count == 0


def test_chat_reaction_stats_mood_positive() -> None:
    stats = ChatReactionStats()
    # 3 положительных, 1 отрицательный → positive mood
    for emoji in ["👍", "👍", "👍"]:
        stats.add(
            ReactionEvent(
                chat_id=1,
                message_id=1,
                user_id=1,
                emoji=emoji,
                is_positive=True,
                is_negative=False,
            )
        )
    stats.add(
        ReactionEvent(
            chat_id=1,
            message_id=1,
            user_id=1,
            emoji="👎",
            is_positive=False,
            is_negative=True,
        )
    )
    assert stats.get_mood() == "positive"


def test_chat_reaction_stats_mood_negative() -> None:
    stats = ChatReactionStats()
    for _ in range(3):
        stats.add(
            ReactionEvent(
                chat_id=1,
                message_id=1,
                user_id=1,
                emoji="👎",
                is_positive=False,
                is_negative=True,
            )
        )
    assert stats.get_mood() == "negative"


def test_chat_reaction_stats_mood_neutral() -> None:
    stats = ChatReactionStats()
    stats.add(
        ReactionEvent(
            chat_id=1,
            message_id=1,
            user_id=1,
            emoji="👍",
            is_positive=True,
            is_negative=False,
        )
    )
    stats.add(
        ReactionEvent(
            chat_id=1,
            message_id=1,
            user_id=1,
            emoji="👎",
            is_positive=False,
            is_negative=True,
        )
    )
    assert stats.get_mood() == "neutral"


def test_chat_reaction_stats_to_dict_structure() -> None:
    stats = ChatReactionStats()
    stats.add(
        ReactionEvent(
            chat_id=1,
            message_id=10,
            user_id=5,
            emoji="❤️",
            is_positive=True,
            is_negative=False,
        )
    )
    d = stats.to_dict()
    assert "total" in d
    assert "positive" in d
    assert "negative" in d
    assert "mood" in d
    assert "emoji_counts" in d
    assert "recent_events" in d
    assert d["total"] == 1
    assert d["emoji_counts"]["❤️"] == 1


def test_chat_reaction_stats_maxlen() -> None:
    """rolling window не превышает MAX_EVENTS."""
    stats = ChatReactionStats()
    for i in range(ChatReactionStats.MAX_EVENTS + 10):
        stats.add(
            ReactionEvent(
                chat_id=1,
                message_id=i,
                user_id=1,
                emoji="👍",
                is_positive=True,
                is_negative=False,
            )
        )
    assert len(stats.events) == ChatReactionStats.MAX_EVENTS


# ---------------------------------------------------------------------------
# ReactionEngine.record_reaction
# ---------------------------------------------------------------------------


def test_engine_record_positive_reaction() -> None:
    engine = _make_engine()
    engine.record_reaction(
        chat_id=1,
        message_id=100,
        user_id=42,
        new_emojis=["👍"],
        old_emojis=[],
    )
    stats = engine.get_reaction_stats(chat_id=1)
    assert stats["total"] == 1
    assert stats["positive"] == 1
    assert stats["negative"] == 0


def test_engine_record_negative_reaction() -> None:
    engine = _make_engine()
    engine.record_reaction(
        chat_id=1,
        message_id=100,
        user_id=42,
        new_emojis=["👎"],
        old_emojis=[],
    )
    stats = engine.get_reaction_stats(chat_id=1)
    assert stats["negative"] == 1


def test_engine_record_only_added_reactions() -> None:
    """Только новые реакции (не в old) записываются."""
    engine = _make_engine()
    # "👍" уже было в old → не добавляется
    engine.record_reaction(
        chat_id=1,
        message_id=100,
        user_id=42,
        new_emojis=["👍", "❤️"],
        old_emojis=["👍"],  # 👍 было, ❤️ новое
    )
    stats = engine.get_reaction_stats(chat_id=1)
    assert stats["total"] == 1  # только ❤️ добавлена
    assert stats["emoji_counts"].get("❤️") == 1
    assert stats["emoji_counts"].get("👍") is None


def test_engine_record_reaction_no_change() -> None:
    """Если new_emojis == old_emojis → ничего не записывается."""
    engine = _make_engine()
    engine.record_reaction(
        chat_id=1,
        message_id=100,
        user_id=42,
        new_emojis=["👍"],
        old_emojis=["👍"],
    )
    stats = engine.get_reaction_stats(chat_id=1)
    assert stats["total"] == 0


def test_engine_multiple_chats() -> None:
    engine = _make_engine()
    _record(engine, "👍", chat_id=1)
    _record(engine, "👍", chat_id=2)
    _record(engine, "👎", chat_id=2)

    stats1 = engine.get_reaction_stats(chat_id=1)
    assert stats1["total"] == 1

    stats2 = engine.get_reaction_stats(chat_id=2)
    assert stats2["total"] == 2


def test_engine_aggregate_stats() -> None:
    engine = _make_engine()
    _record(engine, "👍", chat_id=1)
    _record(engine, "❤️", chat_id=2)
    _record(engine, "👎", chat_id=3)

    agg = engine.get_reaction_stats()  # без chat_id
    assert agg["total"] == 3
    assert agg["positive"] == 2
    assert agg["negative"] == 1
    assert agg["chats_tracked"] == 3


def test_engine_get_reaction_stats_unknown_chat() -> None:
    engine = _make_engine()
    stats = engine.get_reaction_stats(chat_id=99999)
    assert stats["total"] == 0
    assert stats["mood"] == "unknown"


def test_engine_get_chat_mood_unknown() -> None:
    engine = _make_engine()
    assert engine.get_chat_mood(999) == "unknown"


def test_engine_get_chat_mood_positive() -> None:
    engine = _make_engine()
    for _ in range(5):
        _record(engine, "👍", chat_id=10)
    assert engine.get_chat_mood(10) == "positive"


def test_engine_get_chat_mood_negative() -> None:
    engine = _make_engine()
    for _ in range(5):
        _record(engine, "👎", chat_id=20)
    assert engine.get_chat_mood(20) == "negative"


def test_engine_aggregate_mood_positive() -> None:
    engine = _make_engine()
    for i in range(10):
        _record(engine, "👍", chat_id=i)
    agg = engine.get_reaction_stats()
    assert agg["mood"] == "positive"


def test_engine_aggregate_mood_empty() -> None:
    engine = _make_engine()
    agg = engine.get_reaction_stats()
    assert agg["mood"] == "unknown"


def test_engine_neutral_emoji_not_counted_as_positive_or_negative() -> None:
    engine = _make_engine()
    _record(engine, "🤔", chat_id=1)
    stats = engine.get_reaction_stats(chat_id=1)
    assert stats["total"] == 1
    assert stats["positive"] == 0
    assert stats["negative"] == 0


def test_engine_emoji_counts_accumulated() -> None:
    engine = _make_engine()
    for _ in range(3):
        _record(engine, "👍", chat_id=1)
    for _ in range(2):
        _record(engine, "❤️", chat_id=1)

    stats = engine.get_reaction_stats(chat_id=1)
    assert stats["emoji_counts"]["👍"] == 3
    assert stats["emoji_counts"]["❤️"] == 2


# ---------------------------------------------------------------------------
# Тест глобального синглтона
# ---------------------------------------------------------------------------


def test_global_reaction_engine_singleton() -> None:
    """Глобальный синглтон импортируется без ошибок."""
    from src.core.reaction_engine import reaction_engine as global_engine

    assert isinstance(global_engine, ReactionEngine)
