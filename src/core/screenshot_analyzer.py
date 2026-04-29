# -*- coding: utf-8 -*-
"""
Screenshot Analyzer — расширенный анализ скриншотов (Idea 38).

Зачем:

Стандартный perceptor извлекает только OCR-текст. Этого мало: на скриншоте
часто важно понимать UI-структуру (какая кнопка под курсором, есть ли алерт,
какое приложение, тон сообщения — ошибка/предупреждение/нормально). Этот
модуль добавляет vision-aware анализ поверх OCR через структурированный
LLM-промпт.

Это **pure module** — без wire-up в `userbot_bridge.py` / `perceptor.py`.
Бридж интегрирует его отдельной сессией (backlog).

### Инварианты

- **Fail-open.** Любая ошибка vision-вызова → возвращается ScreenshotAnalysis
  с `ocr_text` (если получили) и пустым списком ui_elements; sentiment=normal.
- **LRU-кэш по SHA256 image_bytes.** До 100 записей; повторный анализ того
  же изображения отдаётся мгновенно.
- **OCR-fallback.** Если vision_callable=None, работает как обёртка над
  ocr_callable (UI-элементы пустые, sentiment эвристикой по ключевым словам).
- **Структурированный JSON-парсинг.** LLM просим вернуть JSON; невалидный
  ответ → fallback в OCR-only без exception.

### Не решает

- Не делает сам vision-вызов: ожидает callable от caller'а
  (типичная сигнатура — `async (prompt, image_bytes) -> str`).
- Не классифицирует приложения по списку — `app_detected` это free-form
  hint от модели (например, "Safari", "Telegram", "Xcode").
- Не извлекает координаты в пикселях; `position_hint` — словесный
  ("top-left", "center", "bottom-right" и т.п.).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal

logger = logging.getLogger(__name__)

# Тип для async callable, принимающего OCR (текст изображения) → str.
OCRCallable = Callable[[bytes], Awaitable[str]]
# Тип для async vision-callable: получает (prompt, image_bytes) → строка-ответ
# (ожидается JSON, но модуль выживает при любом тексте).
VisionCallable = Callable[[str, bytes], Awaitable[str]]

Sentiment = Literal["normal", "error", "warning"]
UIElementType = Literal[
    "button",
    "input",
    "alert",
    "menu",
    "link",
    "checkbox",
    "tab",
    "icon",
    "label",
    "other",
]

_CACHE_MAX = 100

# Эвристики для определения sentiment по OCR-тексту, если vision недоступен.
_ERROR_MARKERS = (
    "error",
    "ошибка",
    "failed",
    "не удалось",
    "exception",
    "traceback",
    "crash",
    "denied",
    "forbidden",
)
_WARNING_MARKERS = (
    "warning",
    "предупреждение",
    "warn",
    "внимание",
    "caution",
    "deprecated",
)


@dataclass(frozen=True)
class UIElement:
    """Описание одного UI-элемента, распознанного на скриншоте."""

    type: UIElementType
    label: str
    position_hint: str = ""


@dataclass(frozen=True)
class ScreenshotAnalysis:
    """Полный результат анализа скриншота."""

    ocr_text: str
    ui_elements: tuple[UIElement, ...] = field(default_factory=tuple)
    app_detected: str | None = None
    error_dialog: bool = False
    sentiment: Sentiment = "normal"

    def to_dict(self) -> dict:
        """Сериализация для логов / archive sink."""
        return {
            "ocr_text": self.ocr_text,
            "ui_elements": [
                {"type": el.type, "label": el.label, "position_hint": el.position_hint}
                for el in self.ui_elements
            ],
            "app_detected": self.app_detected,
            "error_dialog": self.error_dialog,
            "sentiment": self.sentiment,
        }


_VISION_PROMPT = (
    "Проанализируй скриншот и верни СТРОГО валидный JSON без markdown-обёрток "
    "со следующими полями:\n"
    '  "app_detected": строка с названием приложения или null,\n'
    '  "error_dialog": true если виден диалог ошибки/алерт ошибки, иначе false,\n'
    '  "sentiment": одно из ["normal", "error", "warning"],\n'
    '  "ui_elements": массив объектов вида '
    '{"type": "button|input|alert|menu|link|checkbox|tab|icon|label|other", '
    '"label": "видимый текст", "position_hint": "top-left|top|top-right|'
    'left|center|right|bottom-left|bottom|bottom-right"}.\n'
    "Возвращай не более 12 элементов, только реально видимые. "
    "Если изображение нечитаемо, верни {} с пустыми/дефолтными значениями."
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _detect_sentiment_from_text(text: str) -> tuple[Sentiment, bool]:
    """Эвристика sentiment + error_dialog по OCR-тексту."""
    lowered = text.lower()
    for marker in _ERROR_MARKERS:
        if marker in lowered:
            return "error", True
    for marker in _WARNING_MARKERS:
        if marker in lowered:
            return "warning", False
    return "normal", False


def _parse_vision_json(raw: str) -> dict | None:
    """Извлекает JSON из ответа модели (с защитой от ```json блоков)."""
    if not raw:
        return None
    stripped = raw.strip()
    # Снимаем ```json ... ``` если модель обернула.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    # Берём первый сбалансированный {...} блок если есть лишний текст.
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(stripped[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None


def _coerce_ui_elements(raw_list) -> tuple[UIElement, ...]:
    """Безопасно конвертирует список из JSON в кортеж UIElement."""
    if not isinstance(raw_list, list):
        return ()
    valid_types = {
        "button",
        "input",
        "alert",
        "menu",
        "link",
        "checkbox",
        "tab",
        "icon",
        "label",
        "other",
    }
    out: list[UIElement] = []
    for item in raw_list[:12]:
        if not isinstance(item, dict):
            continue
        el_type = str(item.get("type", "other")).lower().strip()
        if el_type not in valid_types:
            el_type = "other"
        label = str(item.get("label", "")).strip()
        position = str(item.get("position_hint", "")).strip()
        if not label and not position:
            continue
        out.append(UIElement(type=el_type, label=label, position_hint=position))  # type: ignore[arg-type]
    return tuple(out)


class ScreenshotAnalyzer:
    """Анализатор скриншотов с vision+OCR pipeline и LRU-кэшем."""

    def __init__(
        self,
        ocr_callable: OCRCallable | None = None,
        vision_callable: VisionCallable | None = None,
        cache_size: int = _CACHE_MAX,
    ) -> None:
        self._ocr = ocr_callable
        self._vision = vision_callable
        self._cache_size = max(1, int(cache_size))
        self._cache: OrderedDict[str, ScreenshotAnalysis] = OrderedDict()

    def cache_size(self) -> int:
        """Текущий размер LRU-кэша (для тестов / диагностики)."""
        return len(self._cache)

    def clear_cache(self) -> None:
        self._cache.clear()

    async def analyze(self, image_bytes: bytes) -> ScreenshotAnalysis:
        """Главная точка входа: vision → OCR → fail-open."""
        if not image_bytes:
            return ScreenshotAnalysis(ocr_text="")

        key = _sha256(image_bytes)
        cached = self._cache.get(key)
        if cached is not None:
            # LRU touch.
            self._cache.move_to_end(key)
            return cached

        # 1. OCR (если есть callable).
        ocr_text = ""
        if self._ocr is not None:
            try:
                raw = await self._ocr(image_bytes)
                ocr_text = (raw or "").strip()
            except Exception as exc:
                logger.warning(
                    "screenshot_analyzer_ocr_failed",
                    extra={
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )

        # 2. Vision (если есть callable) — пытаемся извлечь UI structure.
        analysis: ScreenshotAnalysis | None = None
        if self._vision is not None:
            try:
                raw = await self._vision(_VISION_PROMPT, image_bytes)
                parsed = _parse_vision_json(raw)
                if parsed is not None:
                    analysis = self._build_from_vision(ocr_text, parsed)
            except Exception as exc:
                logger.warning(
                    "screenshot_analyzer_vision_failed",
                    extra={
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )

        # 3. Fallback в OCR-only (эвристика sentiment).
        if analysis is None:
            sentiment, error_dialog = _detect_sentiment_from_text(ocr_text)
            analysis = ScreenshotAnalysis(
                ocr_text=ocr_text,
                ui_elements=(),
                app_detected=None,
                error_dialog=error_dialog,
                sentiment=sentiment,
            )

        self._store(key, analysis)
        return analysis

    def _build_from_vision(self, ocr_text: str, parsed: dict) -> ScreenshotAnalysis:
        """Конструирует ScreenshotAnalysis из распарсенного vision-JSON."""
        sentiment_raw = str(parsed.get("sentiment", "normal")).lower().strip()
        if sentiment_raw not in {"normal", "error", "warning"}:
            sentiment_raw = "normal"
        error_dialog = bool(parsed.get("error_dialog", False))
        # Если vision сказал error — синхронизируем error_dialog как минимум True.
        if sentiment_raw == "error":
            error_dialog = True

        app_detected_raw = parsed.get("app_detected")
        app_detected: str | None = None
        if isinstance(app_detected_raw, str):
            stripped = app_detected_raw.strip()
            app_detected = stripped or None

        ui_elements = _coerce_ui_elements(parsed.get("ui_elements"))

        return ScreenshotAnalysis(
            ocr_text=ocr_text,
            ui_elements=ui_elements,
            app_detected=app_detected,
            error_dialog=error_dialog,
            sentiment=sentiment_raw,  # type: ignore[arg-type]
        )

    def _store(self, key: str, analysis: ScreenshotAnalysis) -> None:
        self._cache[key] = analysis
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)


# Singleton по образу остальных core-модулей. Bridge инжектирует реальные
# ocr/vision callable'ы при wire-up; до тех пор analyzer работает в
# OCR-only / no-op режиме.
screenshot_analyzer = ScreenshotAnalyzer()


def configure_default_callables(
    ocr_callable: OCRCallable | None = None,
    vision_callable: VisionCallable | None = None,
) -> None:
    """Пере-настройка singleton (вызывается из bootstrap при wire-up)."""
    if ocr_callable is not None:
        screenshot_analyzer._ocr = ocr_callable  # noqa: SLF001
    if vision_callable is not None:
        screenshot_analyzer._vision = vision_callable  # noqa: SLF001
