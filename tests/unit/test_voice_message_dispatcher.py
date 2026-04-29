# -*- coding: utf-8 -*-
"""Тесты VoiceMessageDispatcher (Idea 1)."""

from __future__ import annotations

import pytest

from src.core.audio_summarizer import AudioSummary
from src.core.voice_message_dispatcher import (
    DEFAULT_MAX_FULL_CHARS,
    VoiceMessageDispatcher,
    get_dispatcher,
    reset_dispatcher,
)


@pytest.fixture()
def dispatcher() -> VoiceMessageDispatcher:
    return VoiceMessageDispatcher()


@pytest.fixture()
def summary() -> AudioSummary:
    return AudioSummary(
        bullets=["Первый пункт", "Второй пункт", "Третий пункт"],
        topic="Тестовая тема",
        sentiment="neutral",
        length_chars=1800,
    )


def test_decide_short_returns_full(dispatcher: VoiceMessageDispatcher) -> None:
    decision = dispatcher.decide_format("привет, как дела?", duration_sec=10)
    assert decision.kind == "full"
    assert decision.reason == "short"


def test_decide_medium_returns_both(dispatcher: VoiceMessageDispatcher) -> None:
    text = "слово " * 100  # ~600 chars
    decision = dispatcher.decide_format(text, duration_sec=60)
    assert decision.kind == "both"
    assert decision.reason == "medium"


def test_decide_long_by_chars_returns_summary(
    dispatcher: VoiceMessageDispatcher,
) -> None:
    text = "x" * 2000
    decision = dispatcher.decide_format(text, duration_sec=20)
    assert decision.kind == "summary"
    assert decision.reason == "long_by_chars"


def test_decide_long_by_duration_returns_summary(
    dispatcher: VoiceMessageDispatcher,
) -> None:
    text = "короткий текст " * 5
    decision = dispatcher.decide_format(text, duration_sec=180)
    assert decision.kind == "summary"
    assert decision.reason == "long_by_duration"


def test_decide_no_duration_uses_chars_only(
    dispatcher: VoiceMessageDispatcher,
) -> None:
    # Нет duration: короткий по chars → full
    assert dispatcher.decide_format("hello", duration_sec=None).kind == "full"
    # Нет duration: medium по chars → both
    assert (
        dispatcher.decide_format("x" * 800, duration_sec=None).kind == "both"
    )


def test_format_both_with_summary_truncates_full(
    dispatcher: VoiceMessageDispatcher, summary: AudioSummary
) -> None:
    long_text = "слово " * 200  # > DEFAULT_MAX_FULL_CHARS
    out = dispatcher.format_response(
        long_text,
        summary=summary,
        format_kind="both",
        max_full_chars=DEFAULT_MAX_FULL_CHARS,
    )
    # Полный текст должен быть обрезан с "…"
    assert "…" in out
    # И ниже — summary с topic+bullets
    assert "Тестовая тема" in out
    assert "Первый пункт" in out
    # Проверяем cap по длине full-фрагмента
    full_part = out.split("\n\n", 1)[0]
    # +1 на «…», +символы префикса "🎙 "
    assert len(full_part) <= DEFAULT_MAX_FULL_CHARS + 10


def test_format_summary_only_renders_topic_and_bullets(
    dispatcher: VoiceMessageDispatcher, summary: AudioSummary
) -> None:
    out = dispatcher.format_response(
        "x" * 2000, summary=summary, format_kind="summary"
    )
    assert "Тестовая тема" in out
    assert "• Первый пункт" in out
    assert "• Третий пункт" in out
    # Full transcript не должен быть включён
    assert "x" * 50 not in out


def test_format_summary_fail_open_to_full_when_no_summary(
    dispatcher: VoiceMessageDispatcher,
) -> None:
    out = dispatcher.format_response(
        "fallback text", summary=None, format_kind="summary"
    )
    assert "fallback text" in out


def test_format_full_no_truncation(dispatcher: VoiceMessageDispatcher) -> None:
    text = "короткое сообщение"
    out = dispatcher.format_response(text, format_kind="full")
    assert text in out
    assert "…" not in out


def test_singleton_reset() -> None:
    a = get_dispatcher()
    b = get_dispatcher()
    assert a is b
    reset_dispatcher()
    c = get_dispatcher()
    assert c is not a
    reset_dispatcher()
