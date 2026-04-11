# -*- coding: utf-8 -*-
"""
Тесты для src/core/language_detect.py — определение языка и резолв пары.
"""

from __future__ import annotations

from src.core.language_detect import detect_language, resolve_translation_pair

# ------------------------------------------------------------------
# detect_language
# ------------------------------------------------------------------


class TestDetectLanguage:
    def test_spanish(self) -> None:
        assert detect_language("Hola, cómo estás? Me llamo Pablo") == "es"

    def test_russian(self) -> None:
        assert detect_language("Привет, как дела? Меня зовут Паша") == "ru"

    def test_english(self) -> None:
        assert detect_language("Hello, how are you? My name is Paul") == "en"

    def test_empty_string(self) -> None:
        assert detect_language("") == ""

    def test_too_short(self) -> None:
        assert detect_language("Hi") == ""

    def test_none_safe(self) -> None:
        assert detect_language(None) == ""  # type: ignore[arg-type]

    def test_whitespace_only(self) -> None:
        assert detect_language("    ") == ""

    def test_mixed_but_dominant_spanish(self) -> None:
        result = detect_language("Buenos días señor, necesito ayuda por favor")
        assert result == "es"


# ------------------------------------------------------------------
# resolve_translation_pair
# ------------------------------------------------------------------


class TestResolveTranslationPair:
    def test_detected_matches_first(self) -> None:
        src, tgt = resolve_translation_pair("es", "es-ru")
        assert src == "es"
        assert tgt == "ru"

    def test_detected_matches_second(self) -> None:
        src, tgt = resolve_translation_pair("ru", "es-ru")
        assert src == "ru"
        assert tgt == "es"

    def test_detected_not_in_pair(self) -> None:
        # Неизвестный язык — переводим на второй (целевой)
        src, tgt = resolve_translation_pair("fr", "es-ru")
        assert src == "fr"
        assert tgt == "ru"

    def test_en_ru_pair(self) -> None:
        src, tgt = resolve_translation_pair("en", "en-ru")
        assert src == "en"
        assert tgt == "ru"

    def test_invalid_pair_format(self) -> None:
        src, tgt = resolve_translation_pair("es", "invalid")
        assert src == "es"
        assert tgt == "ru"  # fallback

    def test_empty_detected(self) -> None:
        src, tgt = resolve_translation_pair("", "es-ru")
        assert src == ""
        assert tgt == "ru"

    def test_auto_detect_pair_spanish(self) -> None:
        # auto-detect: испанский → русский
        src, tgt = resolve_translation_pair("es", "auto-detect")
        assert src == "es"
        assert tgt == "ru"

    def test_auto_detect_pair_russian(self) -> None:
        # auto-detect: русский → английский
        src, tgt = resolve_translation_pair("ru", "auto-detect")
        assert src == "ru"
        assert tgt == "en"

    def test_auto_detect_pair_english(self) -> None:
        # auto-detect: английский → русский
        src, tgt = resolve_translation_pair("en", "auto-detect")
        assert src == "en"
        assert tgt == "ru"
