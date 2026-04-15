# -*- coding: utf-8 -*-
"""
ReactionEngine — движок для сбора и анализа реакций на сообщения Краба.

Хранит feedback: когда пользователи ставят 👍/👎/❤️ на ответы Краба,
это логируется и накапливается как сигнал качества.

Используется:
  - в /api/reactions/stats — статистика реакций по чатам
  - в /api/reactions/mood — sentiment snapshot чата
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

from .logger import get_logger

logger = get_logger(__name__)

# Реакции, которые считаются положительным feedback
POSITIVE_REACTIONS = {"👍", "❤️", "🔥", "🎉", "🏆", "💯", "⚡", "🤩", "😍", "✅"}
# Реакции, которые считаются отрицательным feedback
NEGATIVE_REACTIONS = {"👎", "💩", "🤮", "😡", "🤬", "❌"}
# Нейтральные / информационные
NEUTRAL_REACTIONS = {"👀", "🤔", "😐", "🤷"}


@dataclass
class ReactionEvent:
    """Одно событие реакции от пользователя."""
    chat_id: int
    message_id: int
    user_id: Optional[int]
    emoji: str
    is_positive: bool
    is_negative: bool
    timestamp: float = field(default_factory=time.time)


class ChatReactionStats:
    """Статистика реакций для одного чата (rolling window)."""

    MAX_EVENTS = 100  # максимум событий в памяти на чат

    def __init__(self) -> None:
        self.events: Deque[ReactionEvent] = deque(maxlen=self.MAX_EVENTS)
        self.emoji_counts: Dict[str, int] = defaultdict(int)
        self.positive_count: int = 0
        self.negative_count: int = 0
        self.total_count: int = 0

    def add(self, event: ReactionEvent) -> None:
        """Добавляет событие реакции в статистику."""
        self.events.append(event)
        self.emoji_counts[event.emoji] += 1
        self.total_count += 1
        if event.is_positive:
            self.positive_count += 1
        elif event.is_negative:
            self.negative_count += 1

    def get_mood(self) -> str:
        """
        Возвращает текущий sentiment чата на основе реакций.

        Возможные значения: 'positive', 'negative', 'neutral', 'unknown'.
        """
        if self.total_count == 0:
            return "unknown"
        if self.positive_count > self.negative_count * 2:
            return "positive"
        if self.negative_count > self.positive_count * 2:
            return "negative"
        return "neutral"

    def to_dict(self) -> dict:
        """Сериализует статистику в словарь для API."""
        return {
            "total": self.total_count,
            "positive": self.positive_count,
            "negative": self.negative_count,
            "emoji_counts": dict(self.emoji_counts),
            "mood": self.get_mood(),
            "recent_events": [
                {
                    "chat_id": e.chat_id,
                    "message_id": e.message_id,
                    "emoji": e.emoji,
                    "timestamp": e.timestamp,
                }
                for e in list(self.events)[-10:]  # последние 10
            ],
        }


class ReactionEngine:
    """
    Движок для отслеживания реакций на сообщения Краба.

    Принимает события реакций и хранит статистику по чатам.
    Используется как feedback-сигнал качества ответов.
    """

    def __init__(self) -> None:
        # chat_id -> ChatReactionStats
        self._stats: Dict[int, ChatReactionStats] = defaultdict(ChatReactionStats)

    def record_reaction(
        self,
        *,
        chat_id: int,
        message_id: int,
        user_id: Optional[int],
        new_emojis: List[str],
        old_emojis: List[str],
    ) -> None:
        """
        Записывает изменение реакции пользователя.

        new_emojis — реакции которые появились (добавлены).
        old_emojis — реакции которые исчезли (убраны).
        """
        # Добавленные реакции
        for emoji in new_emojis:
            if emoji not in old_emojis:
                event = ReactionEvent(
                    chat_id=chat_id,
                    message_id=message_id,
                    user_id=user_id,
                    emoji=emoji,
                    is_positive=emoji in POSITIVE_REACTIONS,
                    is_negative=emoji in NEGATIVE_REACTIONS,
                )
                self._stats[chat_id].add(event)
                sentiment = (
                    "positive" if event.is_positive
                    else "negative" if event.is_negative
                    else "neutral"
                )
                logger.info(
                    "reaction_feedback_received",
                    chat_id=chat_id,
                    message_id=message_id,
                    emoji=emoji,
                    sentiment=sentiment,
                    user_id=user_id,
                )

    def get_reaction_stats(self, *, chat_id: Optional[int] = None) -> dict:
        """
        Возвращает статистику реакций.

        Если chat_id задан — только для этого чата.
        Иначе — агрегированная статистика по всем чатам.
        """
        if chat_id is not None:
            stats = self._stats.get(chat_id)
            if not stats:
                return {"total": 0, "positive": 0, "negative": 0, "mood": "unknown"}
            return stats.to_dict()

        # Агрегат по всем чатам
        total = positive = negative = 0
        emoji_counts: Dict[str, int] = defaultdict(int)
        for stats in self._stats.values():
            total += stats.total_count
            positive += stats.positive_count
            negative += stats.negative_count
            for emoji, count in stats.emoji_counts.items():
                emoji_counts[emoji] += count

        mood = "unknown"
        if total > 0:
            if positive > negative * 2:
                mood = "positive"
            elif negative > positive * 2:
                mood = "negative"
            else:
                mood = "neutral"

        return {
            "total": total,
            "positive": positive,
            "negative": negative,
            "emoji_counts": dict(emoji_counts),
            "mood": mood,
            "chats_tracked": len(self._stats),
        }

    def get_chat_mood(self, chat_id: int) -> str:
        """Возвращает mood для конкретного чата."""
        stats = self._stats.get(chat_id)
        if not stats:
            return "unknown"
        return stats.get_mood()


# Глобальный синглтон — инициализируется в bootstrap/runtime.py
reaction_engine = ReactionEngine()
