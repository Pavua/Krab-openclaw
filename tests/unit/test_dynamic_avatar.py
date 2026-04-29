# -*- coding: utf-8 -*-
"""Тесты для AvatarSuggestionEngine (Idea 34)."""

from __future__ import annotations

from src.core.dynamic_avatar import (
    AvatarSuggestion,
    AvatarSuggestionEngine,
    avatar_suggestion_engine,
)


def test_mood_only_returns_mood_avatar() -> None:
    # Только mood — выбираем emoji из mood-маппинга
    engine = AvatarSuggestionEngine()
    result = engine.current_avatar(owner_mood="playful")
    assert isinstance(result, AvatarSuggestion)
    assert result.emoji == "😄"
    assert "mood=playful" in result.reason
    assert result.priority == 30


def test_time_only_returns_time_avatar() -> None:
    # Только время суток — emoji времени
    engine = AvatarSuggestionEngine()
    result = engine.current_avatar(time_of_day="morning")
    assert result.emoji == "☕"
    assert "time=morning" in result.reason
    assert result.priority == 10


def test_combined_signals_mood_wins_over_time_and_topic() -> None:
    # Все три сигнала — mood выигрывает (priority 30 > 20 > 10)
    engine = AvatarSuggestionEngine()
    result = engine.current_avatar(
        owner_mood="business",
        time_of_day="evening",
        activity_topic="coding",
    )
    assert result.emoji == "👔"
    assert result.reason == "mood=business"
    assert result.priority == 30


def test_topic_overrides_time_when_no_mood() -> None:
    # Topic (20) приоритетнее time (10), при отсутствии mood
    engine = AvatarSuggestionEngine()
    result = engine.current_avatar(time_of_day="evening", activity_topic="music")
    assert result.emoji == "🎵"
    assert result.reason == "topic=music"
    assert result.priority == 20


def test_default_fallback_when_no_signals() -> None:
    # Нет ни одного валидного сигнала → дефолтный 🦀
    engine = AvatarSuggestionEngine()
    result = engine.current_avatar()
    assert result.emoji == "🦀"
    assert result.priority == 0
    assert "default" in result.reason

    # Неизвестные ярлыки тоже падают в default
    result_unknown = engine.current_avatar(
        owner_mood="ecstatic", time_of_day="dawn", activity_topic="philosophy"
    )
    assert result_unknown.emoji == "🦀"
    assert result_unknown.priority == 0


def test_singleton_exposed() -> None:
    # Module-level singleton присутствует и работает
    result = avatar_suggestion_engine.current_avatar(activity_topic="food")
    assert result.emoji == "🍕"
    assert "food" in result.reason


def test_known_lists_nonempty() -> None:
    # Helper-методы возвращают непустые списки известных ярлыков
    engine = AvatarSuggestionEngine()
    assert "playful" in engine.known_moods()
    assert "coding" in engine.known_topics()
    assert "morning" in engine.known_times()


def test_case_insensitive_inputs() -> None:
    # Caller может передать ярлык в любом регистре
    engine = AvatarSuggestionEngine()
    result = engine.current_avatar(owner_mood="ANNOYED")
    assert result.emoji == "😤"
