# -*- coding: utf-8 -*-
"""
Модуль реактивного обучения Krab.

Назначение:
1. Собирать реакции на ответы Краба и хранить их как слабый сигнал качества.
2. Формировать "профиль настроения" чата (rolling mood), который можно учитывать в ответах.
3. Передавать weak-signal в роутер моделей без замены ручного feedback.

Связи:
- Используется в `src/handlers/ai.py` (привязка ответов + обработка raw reaction updates).
- Используется в `src/handlers/commands.py` и `src/modules/web_app.py` для отчетов/управления.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.get_logger("ReactionLearning")


@dataclass
class BoundRoute:
    """Связка telegram message -> маршрут модели для weak-feedback."""

    chat_id: int
    message_id: int
    profile: str
    model: str
    channel: str
    task_type: str
    ts: float


class ReactionLearningEngine:
    """Движок обработки реакций и chat mood."""

    def __init__(
        self,
        *,
        store_path: str = "artifacts/reaction_feedback.json",
        enabled: bool = True,
        weight: float = 0.35,
        mood_enabled: bool = True,
        auto_reactions_enabled: bool = True,
        auto_reaction_rate_seconds: int = 6,
        mood_window: int = 120,
    ):
        self.store_path = Path(store_path)
        self.enabled = bool(enabled)
        self.weight = float(weight) if float(weight) > 0 else 0.35
        self.mood_enabled = bool(mood_enabled)
        self.auto_reactions_enabled = bool(auto_reactions_enabled)
        self.auto_reaction_rate_seconds = max(1, int(auto_reaction_rate_seconds))
        self.mood_window = max(20, int(mood_window))

        self._bound_routes: dict[str, BoundRoute] = {}
        self._last_auto_reaction_ts: dict[int, float] = {}
        self._state = self._load_state()

        # Базовая карта "эмодзи -> тональность". Диапазон [-1..1].
        self._emoji_sentiment: dict[str, float] = {
            "👍": 0.8,
            "🔥": 0.9,
            "❤️": 0.9,
            "💯": 0.9,
            "👏": 0.8,
            "✅": 0.7,
            "😀": 0.6,
            "🙂": 0.4,
            "🤔": 0.0,
            "😐": -0.1,
            "👎": -0.9,
            "😡": -0.9,
            "🤬": -1.0,
            "💩": -0.9,
            "❌": -0.8,
            "😢": -0.6,
        }

    def _default_state(self) -> dict[str, Any]:
        return {
            "events": [],
            "chat_mood": {},
            "updated_at": "",
        }

    def _load_state(self) -> dict[str, Any]:
        try:
            if not self.store_path.exists():
                return self._default_state()
            with self.store_path.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
            if not isinstance(data, dict):
                return self._default_state()
            if not isinstance(data.get("events"), list):
                data["events"] = []
            if not isinstance(data.get("chat_mood"), dict):
                data["chat_mood"] = {}
            return data
        except Exception as exc:
            logger.warning("Не удалось загрузить reaction store", error=str(exc))
            return self._default_state()

    def _save_state(self) -> None:
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            self._state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            with self.store_path.open("w", encoding="utf-8") as fp:
                json.dump(self._state, fp, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("Не удалось сохранить reaction store", error=str(exc))

    @staticmethod
    def _binding_key(chat_id: int, message_id: int) -> str:
        return f"{chat_id}:{message_id}"

    def bind_assistant_message(self, *, chat_id: int, message_id: int, route: dict[str, Any]) -> None:
        """
        Привязывает отправленный ответ Краба к маршруту модели.
        Это позволяет интерпретировать реакции как weak-feedback.
        """
        if not isinstance(route, dict) or not route:
            return
        binding = BoundRoute(
            chat_id=int(chat_id),
            message_id=int(message_id),
            profile=str(route.get("profile", "chat") or "chat"),
            model=str(route.get("model", "unknown") or "unknown"),
            channel=str(route.get("channel", "local") or "local"),
            task_type=str(route.get("task_type", "chat") or "chat"),
            ts=time.time(),
        )
        self._bound_routes[self._binding_key(chat_id, message_id)] = binding

    def _sentiment(self, emoji: str) -> float:
        if not emoji:
            return 0.0
        return float(self._emoji_sentiment.get(str(emoji), 0.0))

    def _sentiment_to_feedback_score(self, sentiment: float) -> int:
        """
        Переводит тональность реакции в мягкий feedback score (1..5).
        Чем меньше weight, тем ближе к нейтральной 3.
        """
        raw = 3.0 + (float(sentiment) * 2.0 * float(self.weight))
        if raw < 1.0:
            raw = 1.0
        if raw > 5.0:
            raw = 5.0
        return int(round(raw))

    def _update_mood(self, chat_id: int, sentiment: float, emoji: str) -> None:
        chat_key = str(chat_id)
        mood = self._state.setdefault("chat_mood", {}).setdefault(
            chat_key,
            {"samples": [], "label": "neutral", "avg": 0.0, "events": 0, "top_emojis": {}},
        )
        if not isinstance(mood.get("samples"), list):
            mood["samples"] = []
        mood["samples"].append(float(sentiment))
        if len(mood["samples"]) > self.mood_window:
            mood["samples"] = mood["samples"][-self.mood_window :]

        avg = 0.0
        if mood["samples"]:
            avg = sum(mood["samples"]) / len(mood["samples"])
        mood["avg"] = round(avg, 4)
        mood["events"] = int(mood.get("events", 0)) + 1
        if avg > 0.25:
            mood["label"] = "positive"
        elif avg < -0.25:
            mood["label"] = "negative"
        else:
            mood["label"] = "neutral"

        top = mood.setdefault("top_emojis", {})
        top[emoji] = int(top.get(emoji, 0)) + 1

    def register_reaction(
        self,
        *,
        chat_id: int,
        message_id: int,
        actor_id: int,
        emoji: str,
        action: str = "added",
        router=None,
    ) -> dict[str, Any]:
        """
        Регистрирует событие реакции и, если возможно, отправляет weak-feedback в роутер.
        """
        normalized_emoji = str(emoji or "").strip()
        if not normalized_emoji:
            return {"ok": False, "reason": "emoji_required"}

        sentiment = self._sentiment(normalized_emoji)
        event_key = f"{chat_id}:{message_id}:{actor_id}:{normalized_emoji}:{action}"
        events = self._state.setdefault("events", [])
        if not isinstance(events, list):
            events = []
            self._state["events"] = events

        if any(str(item.get("event_key", "")) == event_key for item in events[-800:]):
            return {"ok": True, "deduplicated": True}

        event_payload = {
            "event_key": event_key,
            "chat_id": int(chat_id),
            "message_id": int(message_id),
            "actor_id": int(actor_id),
            "emoji": normalized_emoji,
            "action": str(action or "added"),
            "sentiment": sentiment,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        events.append(event_payload)
        if len(events) > 3000:
            del events[: len(events) - 3000]

        if self.mood_enabled:
            self._update_mood(chat_id, sentiment, normalized_emoji)

        feedback_result = None
        if self.enabled and router is not None:
            binding = self._bound_routes.get(self._binding_key(chat_id, message_id))
            if binding and hasattr(router, "submit_feedback"):
                try:
                    score = self._sentiment_to_feedback_score(sentiment)
                    feedback_result = router.submit_feedback(
                        score=score,
                        profile=binding.profile,
                        model_name=binding.model,
                        channel=binding.channel,
                        note=f"weak_reaction_signal:{normalized_emoji}:{action}",
                    )
                except Exception as exc:
                    logger.debug("Weak reaction feedback skipped", error=str(exc))

        self._save_state()
        return {"ok": True, "event": event_payload, "feedback": feedback_result}

    def get_reaction_stats(self, chat_id: Optional[int] = None) -> dict[str, Any]:
        """Возвращает сводку по реакциям (общую или по чату)."""
        events = self._state.get("events", [])
        if not isinstance(events, list):
            events = []

        selected = events
        if chat_id is not None:
            selected = [ev for ev in events if int(ev.get("chat_id", 0)) == int(chat_id)]

        by_emoji: dict[str, int] = {}
        pos = 0
        neg = 0
        neu = 0
        for ev in selected:
            emoji = str(ev.get("emoji", ""))
            by_emoji[emoji] = int(by_emoji.get(emoji, 0)) + 1
            sentiment = float(ev.get("sentiment", 0.0))
            if sentiment > 0.2:
                pos += 1
            elif sentiment < -0.2:
                neg += 1
            else:
                neu += 1

        top_emojis = sorted(by_emoji.items(), key=lambda item: item[1], reverse=True)[:8]
        return {
            "total": len(selected),
            "positive": pos,
            "negative": neg,
            "neutral": neu,
            "top_emojis": [{"emoji": k, "count": v} for k, v in top_emojis],
            "updated_at": self._state.get("updated_at", ""),
        }

    def get_chat_mood(self, chat_id: int) -> dict[str, Any]:
        """Возвращает профиль настроения чата."""
        payload = self._state.get("chat_mood", {}).get(str(chat_id), {})
        if not isinstance(payload, dict) or not payload:
            return {
                "chat_id": int(chat_id),
                "label": "neutral",
                "avg": 0.0,
                "events": 0,
                "top_emojis": [],
            }
        top = payload.get("top_emojis", {})
        top_pairs = []
        if isinstance(top, dict):
            top_pairs = sorted(top.items(), key=lambda item: item[1], reverse=True)[:6]
        return {
            "chat_id": int(chat_id),
            "label": str(payload.get("label", "neutral")),
            "avg": float(payload.get("avg", 0.0)),
            "events": int(payload.get("events", 0)),
            "top_emojis": [{"emoji": k, "count": v} for k, v in top_pairs],
        }

    def reset_chat_mood(self, chat_id: int) -> dict[str, Any]:
        """Сбрасывает профиль настроения конкретного чата."""
        mood = self._state.setdefault("chat_mood", {})
        removed = bool(mood.pop(str(chat_id), None))
        self._save_state()
        return {"ok": True, "chat_id": int(chat_id), "removed": removed}

    def build_mood_context_line(self, chat_id: int) -> str:
        """
        Возвращает короткую строку контекста по настроению чата для prompt.
        """
        if not self.mood_enabled:
            return ""
        mood = self.get_chat_mood(chat_id)
        if int(mood.get("events", 0)) < 3:
            return ""
        return (
            f"[CHAT MOOD]: tone={mood.get('label', 'neutral')}, "
            f"avg={mood.get('avg', 0.0)}, events={mood.get('events', 0)}"
        )

    def can_send_auto_reaction(self, chat_id: int) -> bool:
        """Rate-limit для авто-реакций Краба."""
        if not self.auto_reactions_enabled:
            return False
        now = time.time()
        prev = float(self._last_auto_reaction_ts.get(int(chat_id), 0.0))
        if (now - prev) < float(self.auto_reaction_rate_seconds):
            return False
        self._last_auto_reaction_ts[int(chat_id)] = now
        return True

    def choose_auto_reaction(self, response_text: str, chat_id: int) -> str:
        """
        Подбирает emoji для авто-реакции по ответу и текущему mood чата.
        """
        text = str(response_text or "").lower()
        if any(word in text for word in ("ошибка", "не удалось", "fallback", "⚠️", "❌")):
            return "👀"
        mood = self.get_chat_mood(chat_id)
        label = str(mood.get("label", "neutral"))
        if label == "negative":
            return "🤝"
        if label == "positive":
            return "🔥"
        return "✅"

    def set_enabled(self, value: bool) -> None:
        self.enabled = bool(value)

    def set_auto_reactions_enabled(self, value: bool) -> None:
        self.auto_reactions_enabled = bool(value)

