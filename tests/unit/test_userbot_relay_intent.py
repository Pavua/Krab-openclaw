# -*- coding: utf-8 -*-
"""
Тесты для relay intent detection в userbot_bridge.
"""

from __future__ import annotations

import pytest

# Импортируем напрямую — константа и метод не зависят от Pyrogram/asyncio
from src.userbot_bridge import _RELAY_INTENT_KEYWORDS
from src.userbot_bridge import KraabUserbot as UserBotBridge

# ── Keyword coverage ─────────────────────────────────────────────────────────


class TestDetectRelayIntentPositives:
    """Все ключевые слова должны триггерить relay intent."""

    @pytest.mark.parametrize("kw", sorted(_RELAY_INTENT_KEYWORDS))
    def test_bare_keyword_triggers(self, kw: str) -> None:
        assert UserBotBridge._detect_relay_intent(kw) is True, f"keyword '{kw}' не задетектирован"

    def test_keyword_embedded_in_sentence(self) -> None:
        assert UserBotBridge._detect_relay_intent("привет, передай ему что я буду в 6") is True

    def test_keyword_with_punctuation(self) -> None:
        assert UserBotBridge._detect_relay_intent("скажи, пожалуйста, что всё ок") is True

    def test_english_relay_phrase(self) -> None:
        assert UserBotBridge._detect_relay_intent("please tell him about the meeting") is True

    def test_case_insensitive(self) -> None:
        assert UserBotBridge._detect_relay_intent("Передай пожалуйста") is True
        assert UserBotBridge._detect_relay_intent("СКАЖИ ЕМУ") is True

    def test_mixed_language_relay(self) -> None:
        assert UserBotBridge._detect_relay_intent("please pass along — скажи что я позвоню") is True


class TestDetectRelayIntentNegatives:
    """Обычные сообщения не должны триггерить relay intent."""

    def test_empty_string(self) -> None:
        assert UserBotBridge._detect_relay_intent("") is False

    def test_none_like_empty(self) -> None:
        # Метод принимает str; None обрабатывается через str(query or "")
        assert UserBotBridge._detect_relay_intent(None) is False  # type: ignore[arg-type]

    def test_plain_question(self) -> None:
        assert UserBotBridge._detect_relay_intent("когда он будет дома?") is False

    def test_greeting(self) -> None:
        assert UserBotBridge._detect_relay_intent("привет, как дела?") is False

    def test_command_like(self) -> None:
        assert UserBotBridge._detect_relay_intent("!help") is False

    def test_unrelated_sentence_with_similar_prefix(self) -> None:
        # "сказал" — прошедшее время, не в списке ключевых слов
        assert UserBotBridge._detect_relay_intent("он сказал что придёт") is False


# ── Frozenset sanity ─────────────────────────────────────────────────────────


def test_relay_keywords_is_frozenset() -> None:
    assert isinstance(_RELAY_INTENT_KEYWORDS, frozenset)


def test_relay_keywords_nonempty() -> None:
    assert len(_RELAY_INTENT_KEYWORDS) >= 8, (
        f"Слишком мало ключевых слов: {len(_RELAY_INTENT_KEYWORDS)}"
    )


def test_relay_keywords_lowercase() -> None:
    for kw in _RELAY_INTENT_KEYWORDS:
        assert kw == kw.lower(), f"Ключевое слово '{kw}' должно быть нижнего регистра"
