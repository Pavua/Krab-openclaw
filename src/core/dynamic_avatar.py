# -*- coding: utf-8 -*-
"""
Dynamic Avatar Suggestions — подбор emoji/аватара под текущий контекст (Idea 34).

Зачем это существует:

Краб живёт в нескольких чатах с разной атмосферой и ведёт активность разной
тональности (кодим, слушаем музыку, обсуждаем еду). Статичная аватарка не
отражает текущее настроение, а руками менять её — лишний труд. Этот модуль
рекомендует emoji/описание аватара исходя из трёх независимых сигналов:
mood оператора, время суток, тематика активности.

### Сигналы и приоритеты
- explicit owner_mood (annoyed/playful/focused/business) — приоритет 30
- activity_topic (coding/music/food/...) — приоритет 20
- time_of_day (morning/afternoon/evening/night) — приоритет 10
- default fallback (нейтральный 🦀) — приоритет 0

Когда переданы несколько сигналов — выигрывает highest priority. При равенстве
выбирается первый по порядку iteration (mood > topic > time).

### Что НЕ делает
- Не лезет в Telegram API. Реальное обновление аватарки через
  `pyrogram.Client.set_profile_photo(...)` — задача отдельного wire-up в
  userbot_bridge (см. backlog).
- Не классифицирует mood автоматически — caller передаёт уже определённый
  ярлык. Mood detection (по реакциям/тону переписки) — отдельная зона.
- Не персистит state — pure function-style engine, пересчитывает каждый раз.
- Не wired в активный pipeline. Pure модуль, готов к интеграции.

### Инварианты
- Возвращает всегда AvatarSuggestion (никогда None) — fallback гарантирован.
- Все маппинги read-only после init; engine безопасен для конкурентного чтения.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class AvatarSuggestion:
    """Рекомендация аватара для текущего контекста."""

    emoji: str
    description: str
    reason: str
    priority: int


# Приоритеты сигналов (higher = wins)
_PRIORITY_MOOD: Final[int] = 30
_PRIORITY_TOPIC: Final[int] = 20
_PRIORITY_TIME: Final[int] = 10
_PRIORITY_DEFAULT: Final[int] = 0


# Маппинг настроения оператора → emoji + описание
_MOOD_MAP: Final[dict[str, tuple[str, str]]] = {
    "annoyed": ("😤", "раздражённый, не до шуток"),
    "playful": ("😄", "игривое настроение"),
    "focused": ("🤓", "сосредоточен на задаче"),
    "business": ("👔", "деловой режим"),
    "tired": ("😴", "устал, малая активность"),
    "happy": ("😊", "хорошее настроение"),
}


# Маппинг времени суток → emoji + описание
_TIME_MAP: Final[dict[str, tuple[str, str]]] = {
    "morning": ("☕", "утренний кофе"),
    "afternoon": ("🌞", "полдень"),
    "evening": ("🌙", "вечер"),
    "night": ("🌌", "ночь"),
}


# Маппинг тематики активности → emoji + описание
_TOPIC_MAP: Final[dict[str, tuple[str, str]]] = {
    "coding": ("💻", "кодим"),
    "music": ("🎵", "музыка"),
    "food": ("🍕", "про еду"),
    "gaming": ("🎮", "гейминг"),
    "reading": ("📚", "чтение"),
    "sport": ("🏃", "спорт"),
    "travel": ("✈️", "путешествие"),
    "movie": ("🎬", "кино"),
}


_DEFAULT_AVATAR: Final[AvatarSuggestion] = AvatarSuggestion(
    emoji="🦀",
    description="нейтральный Краб",
    reason="default fallback (нет сигналов)",
    priority=_PRIORITY_DEFAULT,
)


class AvatarSuggestionEngine:
    """Подбирает аватар на основе текущих сигналов контекста."""

    def __init__(self) -> None:
        # Снимаем копии маппингов — на случай будущих per-instance overrides
        self._mood_map = dict(_MOOD_MAP)
        self._time_map = dict(_TIME_MAP)
        self._topic_map = dict(_TOPIC_MAP)

    def current_avatar(
        self,
        *,
        owner_mood: str | None = None,
        time_of_day: str | None = None,
        activity_topic: str | None = None,
    ) -> AvatarSuggestion:
        """Возвращает avatar suggestion с наивысшим приоритетом среди сигналов."""
        candidates: list[AvatarSuggestion] = []

        if owner_mood:
            entry = self._mood_map.get(owner_mood.lower())
            if entry is not None:
                emoji, desc = entry
                candidates.append(
                    AvatarSuggestion(
                        emoji=emoji,
                        description=desc,
                        reason=f"mood={owner_mood.lower()}",
                        priority=_PRIORITY_MOOD,
                    )
                )

        if activity_topic:
            entry = self._topic_map.get(activity_topic.lower())
            if entry is not None:
                emoji, desc = entry
                candidates.append(
                    AvatarSuggestion(
                        emoji=emoji,
                        description=desc,
                        reason=f"topic={activity_topic.lower()}",
                        priority=_PRIORITY_TOPIC,
                    )
                )

        if time_of_day:
            entry = self._time_map.get(time_of_day.lower())
            if entry is not None:
                emoji, desc = entry
                candidates.append(
                    AvatarSuggestion(
                        emoji=emoji,
                        description=desc,
                        reason=f"time={time_of_day.lower()}",
                        priority=_PRIORITY_TIME,
                    )
                )

        if not candidates:
            return _DEFAULT_AVATAR

        # Стабильная сортировка: первый среди равных по приоритету сохранит порядок
        candidates.sort(key=lambda s: s.priority, reverse=True)
        return candidates[0]

    def known_moods(self) -> list[str]:
        """Список известных mood-ярлыков (для подсказок caller'у)."""
        return list(self._mood_map.keys())

    def known_topics(self) -> list[str]:
        """Список известных topic-ярлыков."""
        return list(self._topic_map.keys())

    def known_times(self) -> list[str]:
        """Список известных time-of-day ярлыков."""
        return list(self._time_map.keys())


# Singleton для шеринга между caller'ами (см. паттерн chat_ban_cache и т.п.)
avatar_suggestion_engine = AvatarSuggestionEngine()


__all__ = [
    "AvatarSuggestion",
    "AvatarSuggestionEngine",
    "avatar_suggestion_engine",
]
