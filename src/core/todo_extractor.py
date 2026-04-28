# -*- coding: utf-8 -*-
"""
TodoExtractor — извлечение TODO-намерений из текста владельца.

Используется для context-обогащения и подсказок: парсит сообщение
и возвращает список потенциальных задач с категориями и confidence.
Сам ничего не создаёт — caller сам решает, показать ли пользователю
или подтвердить через UI-флоу.

Idea 21 (Session 28). Backlog: hook auto-create через confirmation flow.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Literal

from .logger import get_logger

logger = get_logger(__name__)

# Категории TODO
TodoCategory = Literal["task", "shopping", "contact", "idea"]


@dataclass(frozen=True)
class ExtractedTodo:
    """Один кандидат-TODO, извлечённый из текста."""

    action_text: str
    category: TodoCategory
    confidence: float  # 0.0..1.0

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Паттерны
# ---------------------------------------------------------------------------

# Русские триггеры: (regex, category, base_confidence)
# Группа 1 — текст действия (то, что пойдёт в action_text)
_RU_PATTERNS: list[tuple[re.Pattern[str], TodoCategory, float]] = [
    # "надо сделать X", "нужно X", "надо X"
    (
        re.compile(r"\b(?:надо|нужно)(?:\s+(?:бы|будет))?\s+(.+?)(?:[.!?\n]|$)", re.IGNORECASE),
        "task",
        0.75,
    ),
    # "не забыть Y", "не забудь Y"
    (re.compile(r"\bне\s+забы(?:ть|дь|вай)\s+(.+?)(?:[.!?\n]|$)", re.IGNORECASE), "task", 0.8),
    # "купить Z" — shopping
    (re.compile(r"\bкупить\s+(.+?)(?:[.!?\n]|$)", re.IGNORECASE), "shopping", 0.85),
    # "позвонить W", "набрать W", "написать W" — contact
    (
        re.compile(
            r"\b(?:позвонить|набрать|написать|связаться\s+с)\s+(.+?)(?:[.!?\n]|$)", re.IGNORECASE
        ),
        "contact",
        0.8,
    ),
    # "идея: X", "мысль: X" — idea
    (re.compile(r"\b(?:идея|мысль)[:\-—]\s*(.+?)(?:[.!?\n]|$)", re.IGNORECASE), "idea", 0.7),
    # "запомнить X", "сохранить X"
    (re.compile(r"\b(?:запомнить|сохранить)\s+(.+?)(?:[.!?\n]|$)", re.IGNORECASE), "task", 0.65),
]

# Английские триггеры
_EN_PATTERNS: list[tuple[re.Pattern[str], TodoCategory, float]] = [
    # "need to X", "have to X", "must X", "should X"
    (
        re.compile(r"\b(?:need\s+to|have\s+to|must|should)\s+(.+?)(?:[.!?\n]|$)", re.IGNORECASE),
        "task",
        0.7,
    ),
    # "don't forget X", "remember to X"
    (
        re.compile(
            r"\b(?:don'?t\s+forget(?:\s+to)?|remember\s+to)\s+(.+?)(?:[.!?\n]|$)", re.IGNORECASE
        ),
        "task",
        0.8,
    ),
    # "buy X" — shopping
    (re.compile(r"\bbuy\s+(.+?)(?:[.!?\n]|$)", re.IGNORECASE), "shopping", 0.85),
    # "call X", "email X", "text X"
    (
        re.compile(r"\b(?:call|email|text|message)\s+(.+?)(?:[.!?\n]|$)", re.IGNORECASE),
        "contact",
        0.8,
    ),
    # "idea: X"
    (re.compile(r"\bidea[:\-—]\s*(.+?)(?:[.!?\n]|$)", re.IGNORECASE), "idea", 0.7),
    # "TODO X", "todo: X"
    (re.compile(r"\btodo[:\s\-]+(.+?)(?:[.!?\n]|$)", re.IGNORECASE), "task", 0.9),
]

# Минимальная длина action_text — отсекаем мусор вроде "надо это"
_MIN_ACTION_LEN = 3
# Confidence threshold для возврата (всё ниже — игнорируем)
_MIN_CONFIDENCE = 0.5


class TodoExtractor:
    """Парсер TODO-намерений из произвольного текста."""

    def __init__(self, *, min_confidence: float = _MIN_CONFIDENCE) -> None:
        self.min_confidence = min_confidence

    def extract_todos(self, text: str, *, lang: str = "ru") -> list[ExtractedTodo]:
        """Извлекает TODO-кандидаты из текста.

        Args:
            text: исходное сообщение.
            lang: 'ru' / 'en' / 'auto' — какие паттерны применять.

        Returns:
            Список ExtractedTodo (может быть пустым).
        """
        if not text or not text.strip():
            return []

        patterns: list[tuple[re.Pattern[str], TodoCategory, float]] = []
        if lang in ("ru", "auto"):
            patterns.extend(_RU_PATTERNS)
        if lang in ("en", "auto"):
            patterns.extend(_EN_PATTERNS)
        if not patterns:
            # Неизвестный lang — fallback на оба
            patterns = _RU_PATTERNS + _EN_PATTERNS

        results: list[ExtractedTodo] = []
        seen: set[tuple[str, str]] = set()  # (action_text_lower, category) для дедупликации

        for pattern, category, base_conf in patterns:
            for match in pattern.finditer(text):
                action = (match.group(1) or "").strip(" \t\"'«»")
                if len(action) < _MIN_ACTION_LEN:
                    continue
                # Понижаем confidence для очень коротких action
                conf = base_conf
                if len(action) < 6:
                    conf -= 0.15
                if conf < self.min_confidence:
                    continue
                key = (action.lower(), category)
                if key in seen:
                    continue
                seen.add(key)
                results.append(
                    ExtractedTodo(
                        action_text=action,
                        category=category,
                        confidence=round(conf, 2),
                    )
                )

        if results:
            logger.debug(
                "todo_extractor_extracted",
                extra={"count": len(results), "lang": lang},
            )
        return results


# Singleton (по аналогии с другими core-сервисами)
todo_extractor = TodoExtractor()
