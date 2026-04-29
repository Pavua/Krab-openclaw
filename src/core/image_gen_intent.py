"""Детектор намерения сгенерировать изображение (Idea 37).

Чистый эвристический модуль: анализирует входной текст и возвращает
``ImageIntent`` если пользователь явно или неявно просит визуализацию.
Реальный вызов DALL-E / Midjourney оставлен на бэклог (нужен API key
и cost-gate). Тут только обнаружение намерения и подготовка hint'а.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# Поддерживаемые стили
ImageStyle = Literal["photo", "art", "sketch", "cartoon"]
# Поддерживаемые соотношения сторон
ImageAspectRatio = Literal["1:1", "16:9", "9:16"]


@dataclass(frozen=True)
class ImageIntent:
    """Результат детекции намерения на изображение."""

    confidence: float  # 0.0..1.0
    prompt_hint: str  # очищенная подсказка для image-генератора
    style: ImageStyle  # определённый стиль (по умолчанию photo)
    aspect_ratio: ImageAspectRatio  # 1:1 по умолчанию


# --- Триггеры (упорядочены по убыванию веса) ---------------------------------

# Высокая уверенность: явный императив "нарисуй / draw / generate image"
_HIGH_CONFIDENCE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bнарису[йите]+\b", re.IGNORECASE),
    re.compile(r"\bвизуализир(уй|уйте|овать)\b", re.IGNORECASE),
    re.compile(r"\bсгенерируй\s+(картинк|изображен|пикч)", re.IGNORECASE),
    re.compile(r"\bdraw\s+(me\s+)?(a|an|the)?\b", re.IGNORECASE),
    re.compile(
        r"\bgenerate\s+(an?\s+)?([\w-]+\s+)?(image|picture|photo|art|illustration)\b", re.IGNORECASE
    ),
    re.compile(
        r"\bcreate\s+(an?\s+)?([\w-]+\s+)?(image|picture|illustration|art)\b", re.IGNORECASE
    ),
    re.compile(r"\bmake\s+(me\s+)?(an?\s+)?(image|picture)\b", re.IGNORECASE),
    re.compile(r"\b(photo|picture|sketch|drawing|painting)\s+of\b", re.IGNORECASE),
    re.compile(r"\bshow\s+me\s+(an?\s+)?(image|picture|photo)\b", re.IGNORECASE),
    re.compile(r"\bпокажи\s+(мне\s+)?(картинк|изображен|фото)", re.IGNORECASE),
)

# Средняя уверенность: непрямые формулировки
_MEDIUM_CONFIDENCE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bimagine\s+(an?|the)?\b", re.IGNORECASE),
    re.compile(r"\bi\s+want\s+to\s+see\b", re.IGNORECASE),
    re.compile(r"\bcan\s+you\s+(visualize|picture|imagine|show)\b", re.IGNORECASE),
    re.compile(r"\bхочу\s+увидеть\b", re.IGNORECASE),
    re.compile(r"\bпредстав[ьте]+\s+себе\b", re.IGNORECASE),
    re.compile(r"\bкак\s+(бы\s+)?выглядел[а-я]*\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+(would|does)\s+.+\s+look\s+like\b", re.IGNORECASE),
)

# Стиль-маркеры
_STYLE_KEYWORDS: dict[ImageStyle, tuple[str, ...]] = {
    "photo": ("photo", "photograph", "photorealistic", "фото", "фотограф", "реалистич"),
    "sketch": ("sketch", "pencil", "набросок", "скетч", "карандаш"),
    "cartoon": ("cartoon", "anime", "manga", "мультяш", "аниме", "мульт"),
    "art": (
        "painting",
        "art",
        "illustration",
        "digital art",
        "арт",
        "картин",
        "иллюстра",
        "живопис",
    ),
}

# Aspect ratio маркеры
# Для русских корней используем lookahead вместо trailing \b — суффиксы (-ый/-ая) глушат \b
_ASPECT_LANDSCAPE = re.compile(
    r"(?:\b(landscape|widescreen|16:9|wide|panoramic)\b)"
    r"|(?:\b(?:горизонтальн|широкоформатн|панорам)\w*)",
    re.IGNORECASE,
)
_ASPECT_PORTRAIT = re.compile(
    r"(?:\b(portrait|vertical|9:16|tall)\b)"
    r"|(?:\b(?:вертикальн|портретн)\w*)",
    re.IGNORECASE,
)
_ASPECT_SQUARE = re.compile(
    r"(?:\b(square|1:1)\b)|(?:\b(?:квадрат)\w*)",
    re.IGNORECASE,
)

# Минимальная уверенность для срабатывания триггера
DEFAULT_MIN_CONFIDENCE = 0.55


class ImageIntentDetector:
    """Эвристический детектор намерения на генерацию изображения."""

    def __init__(self, min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> None:
        # Порог отсечки — ниже него возвращаем None
        self._min_confidence = max(0.0, min(1.0, min_confidence))

    def detect_intent(self, text: str) -> ImageIntent | None:
        """Возвращает ``ImageIntent`` если уверенность >= порога, иначе None."""
        if not text or not text.strip():
            return None

        normalized = text.strip()

        # Считаем confidence по совпадениям
        confidence = 0.0
        matched_high: list[re.Match[str]] = []
        for pat in _HIGH_CONFIDENCE_PATTERNS:
            m = pat.search(normalized)
            if m is not None:
                matched_high.append(m)
        if matched_high:
            confidence = min(1.0, 0.85 + 0.05 * (len(matched_high) - 1))

        if confidence < 0.85:
            for pat in _MEDIUM_CONFIDENCE_PATTERNS:
                if pat.search(normalized) is not None:
                    confidence = max(confidence, 0.6)
                    break

        if confidence < self._min_confidence:
            return None

        style = self._detect_style(normalized)
        aspect_ratio = self._detect_aspect_ratio(normalized)
        prompt_hint = self._build_prompt_hint(normalized, matched_high)

        return ImageIntent(
            confidence=round(confidence, 3),
            prompt_hint=prompt_hint,
            style=style,
            aspect_ratio=aspect_ratio,
        )

    # --- Внутренние помощники -----------------------------------------------

    @staticmethod
    def _detect_style(text: str) -> ImageStyle:
        # Перебираем в фиксированном порядке: sketch/cartoon/art сильнее, чем photo
        lowered = text.lower()
        for style in ("sketch", "cartoon", "art", "photo"):
            for kw in _STYLE_KEYWORDS[style]:  # type: ignore[index]
                if kw in lowered:
                    return style  # type: ignore[return-value]
        return "photo"

    @staticmethod
    def _detect_aspect_ratio(text: str) -> ImageAspectRatio:
        if _ASPECT_LANDSCAPE.search(text) is not None:
            return "16:9"
        if _ASPECT_PORTRAIT.search(text) is not None:
            return "9:16"
        if _ASPECT_SQUARE.search(text) is not None:
            return "1:1"
        return "1:1"

    @staticmethod
    def _build_prompt_hint(text: str, matched_high: list[re.Match[str]]) -> str:
        # Срезаем сам триггер чтобы получить "чистое" описание
        hint = text
        for m in matched_high:
            hint = hint.replace(m.group(0), " ")
        # Убираем лишние префиксы вроде "пожалуйста", "please"
        hint = re.sub(
            r"\b(пожалуйста|please|можешь|could you|can you)\b", " ", hint, flags=re.IGNORECASE
        )
        # Схлопываем пробелы и пунктуацию по краям
        hint = re.sub(r"\s+", " ", hint).strip(" ,.:;!?-—")
        return hint or text.strip()


# Singleton по принятому в проекте паттерну
image_intent_detector = ImageIntentDetector()


__all__ = [
    "ImageAspectRatio",
    "ImageIntent",
    "ImageIntentDetector",
    "ImageStyle",
    "image_intent_detector",
]
