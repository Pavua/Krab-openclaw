"""Тесты эвристического детектора намерения на генерацию изображения."""

from __future__ import annotations

import pytest

from src.core.image_gen_intent import (
    ImageIntent,
    ImageIntentDetector,
    image_intent_detector,
)


@pytest.fixture()
def detector() -> ImageIntentDetector:
    return ImageIntentDetector()


def test_explicit_draw_intent_high_confidence(detector: ImageIntentDetector) -> None:
    """Явный императив 'нарисуй / draw' даёт высокую уверенность."""
    intent = detector.detect_intent("нарисуй кота в шляпе")
    assert isinstance(intent, ImageIntent)
    assert intent.confidence >= 0.85
    assert "кота" in intent.prompt_hint
    assert intent.style == "photo"

    intent_en = detector.detect_intent("draw me a cat wearing a hat")
    assert intent_en is not None
    assert intent_en.confidence >= 0.85
    assert "cat" in intent_en.prompt_hint.lower()


def test_implicit_visualization_medium_confidence(detector: ImageIntentDetector) -> None:
    """Непрямые формулировки 'imagine / хочу увидеть' — средняя уверенность."""
    intent = detector.detect_intent("imagine a futuristic city at night")
    assert intent is not None
    assert 0.55 <= intent.confidence < 0.85

    intent_ru = detector.detect_intent("хочу увидеть как выглядел бы дракон в стиле киберпанк")
    assert intent_ru is not None
    assert 0.55 <= intent_ru.confidence < 0.85


def test_style_detection_sketch_cartoon_art(detector: ImageIntentDetector) -> None:
    """Стиль определяется по ключевым словам."""
    sketch = detector.detect_intent("draw a sketch of a horse")
    assert sketch is not None and sketch.style == "sketch"

    cartoon = detector.detect_intent("нарисуй мультяшного робота")
    assert cartoon is not None and cartoon.style == "cartoon"

    art = detector.detect_intent("generate digital art of a dragon")
    assert art is not None and art.style == "art"

    photo = detector.detect_intent("photo of a sunset over the ocean")
    assert photo is not None and photo.style == "photo"


def test_aspect_ratio_detection(detector: ImageIntentDetector) -> None:
    """Определение aspect ratio по landscape/portrait/square маркерам."""
    landscape = detector.detect_intent("draw a landscape photo of mountains")
    assert landscape is not None and landscape.aspect_ratio == "16:9"

    portrait = detector.detect_intent("нарисуй вертикальный портрет девушки")
    assert portrait is not None and portrait.aspect_ratio == "9:16"

    square = detector.detect_intent("сгенерируй картинку квадрат с логотипом")
    assert square is not None and square.aspect_ratio == "1:1"

    # Без маркера — дефолт 1:1
    default = detector.detect_intent("нарисуй яблоко")
    assert default is not None and default.aspect_ratio == "1:1"


def test_low_confidence_rejection(detector: ImageIntentDetector) -> None:
    """Текст без триггеров возвращает None."""
    assert detector.detect_intent("привет, как дела?") is None
    assert detector.detect_intent("расскажи про погоду в Мадриде") is None
    assert detector.detect_intent("") is None
    assert detector.detect_intent("   ") is None
    # Слово 'image' само по себе без явного триггера не должно срабатывать
    assert detector.detect_intent("this image is broken, fix it") is None


def test_multilanguage_and_singleton() -> None:
    """Singleton доступен и поддерживает оба языка одновременно."""
    ru = image_intent_detector.detect_intent("визуализируй закат на пляже")
    en = image_intent_detector.detect_intent("show me a picture of the Eiffel tower")
    assert ru is not None and ru.confidence >= 0.85
    assert en is not None and en.confidence >= 0.85
    assert "закат" in ru.prompt_hint
    assert "Eiffel" in en.prompt_hint

    # Кастомный порог: высокая отсечка глушит medium-confidence
    strict = ImageIntentDetector(min_confidence=0.9)
    assert strict.detect_intent("imagine a calm forest") is None
    # Множественные high-confidence триггеры дают confidence >= 0.9
    assert strict.detect_intent("draw a photo of a cat, please show me a picture") is not None
