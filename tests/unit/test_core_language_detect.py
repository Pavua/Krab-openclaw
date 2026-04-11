# -*- coding: utf-8 -*-
"""
Тесты для src/core/language_detect.py — определение языка и резолв пары.
"""

from __future__ import annotations

import pytest

try:
    from src.core.language_detect import detect_language, resolve_translation_pair
except ImportError:
    pytest.skip("langdetect не установлен", allow_module_level=True)

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

    def test_fr_unknown_in_pair(self) -> None:
        # fr не входит в пару de-ru — переводим на второй (ru)
        src, tgt = resolve_translation_pair("fr", "de-ru")
        assert src == "fr"
        assert tgt == "ru"

    def test_empty_detected_empty_pair(self) -> None:
        # пустой detected + пустая строка пары → fallback на ru
        src, tgt = resolve_translation_pair("", "")
        assert src == ""
        assert tgt == "ru"


# ------------------------------------------------------------------
# detect_language — дополнительные языки и длинный текст
# ------------------------------------------------------------------


class TestDetectLanguageExtra:
    def test_french(self) -> None:
        result = detect_language("Bonjour, comment allez-vous? Je m'appelle Pierre")
        assert result == "fr"

    def test_german(self) -> None:
        result = detect_language("Guten Tag, wie geht es Ihnen? Ich heiße Hans")
        assert result == "de"

    def test_long_text_russian(self) -> None:
        # Длинный текст — детекция должна быть уверенной
        long_ru = (
            "Это очень длинный текст на русском языке, который содержит много слов "
            "и предложений. Детектор языка должен без труда определить, что это "
            "именно русский язык, а не какой-то другой. Проверяем устойчивость к "
            "объёму: алгоритм не должен давать ложных срабатываний на длинных текстах."
        )
        assert detect_language(long_ru) == "ru"
