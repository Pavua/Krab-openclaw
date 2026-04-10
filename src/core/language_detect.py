# -*- coding: utf-8 -*-
"""
language_detect.py — определение языка текста и резолв пары для переводчика.

Используется в Translator MVP для определения языка транскрипта
и выбора направления перевода на основе profile.language_pair.
"""

from __future__ import annotations

from langdetect import DetectorFactory, detect

# Фиксируем seed для детерминизма
DetectorFactory.seed = 0

# Минимальная длина текста для надёжной детекции
_MIN_TEXT_LEN = 5


def detect_language(text: str) -> str:
    """
    Определяет язык текста, возвращает ISO 639-1 код.

    Возвращает пустую строку если текст слишком короткий или детекция не удалась.
    """
    if not text or len(text.strip()) < _MIN_TEXT_LEN:
        return ""
    try:
        return detect(text.strip())
    except Exception:
        return ""


def resolve_translation_pair(
    detected_lang: str,
    profile_pair: str,
) -> tuple[str, str]:
    """
    Резолвит (src_lang, tgt_lang) на основе определённого языка и profile pair.

    Profile pair формат: "es-ru" → если detected=es, target=ru; если detected=ru, target=es.
    Если язык не в паре — переводим на второй язык пары (обычно ru).
    """
    parts = profile_pair.split("-", 1)
    if len(parts) != 2:
        return detected_lang, "ru"

    lang_a, lang_b = parts
    if detected_lang == lang_a:
        return lang_a, lang_b
    elif detected_lang == lang_b:
        return lang_b, lang_a
    else:
        # Язык не в паре — переводим на второй язык (целевой)
        return detected_lang, lang_b
