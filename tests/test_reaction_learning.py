# -*- coding: utf-8 -*-
"""Тесты реактивного обучения и mood-профиля чатов."""

from __future__ import annotations

from pathlib import Path

from src.core.reaction_learning import ReactionLearningEngine


class _DummyRouter:
    """Минимальный роутер для проверки submit_feedback weak-signal."""

    def __init__(self) -> None:
        self.feedback_calls: list[dict] = []

    def submit_feedback(self, **kwargs):
        self.feedback_calls.append(dict(kwargs))
        return {"ok": True, "score": kwargs.get("score")}


def test_reaction_learning_registers_feedback_and_updates_mood(tmp_path: Path) -> None:
    """Реакция должна попадать в store, mood и weak-feedback в router."""
    store_path = tmp_path / "reaction_feedback.json"
    engine = ReactionLearningEngine(store_path=str(store_path), enabled=True, mood_enabled=True)
    router = _DummyRouter()

    engine.bind_assistant_message(
        chat_id=100,
        message_id=5,
        route={"profile": "chat", "model": "gemini-2.5-flash", "channel": "cloud", "task_type": "chat"},
    )

    result = engine.register_reaction(
        chat_id=100,
        message_id=5,
        actor_id=42,
        emoji="👍",
        action="added",
        router=router,
    )

    assert result["ok"] is True
    assert store_path.exists() is True
    assert len(router.feedback_calls) == 1
    assert int(router.feedback_calls[0]["score"]) >= 3

    mood = engine.get_chat_mood(100)
    assert mood["events"] >= 1
    assert mood["label"] in {"neutral", "positive"}


def test_reaction_learning_deduplicates_same_event(tmp_path: Path) -> None:
    """Повтор одного и того же event_key не должен дублироваться в хранилище."""
    engine = ReactionLearningEngine(store_path=str(tmp_path / "reaction_feedback.json"), enabled=False)

    first = engine.register_reaction(chat_id=1, message_id=2, actor_id=3, emoji="🔥", action="added", router=None)
    second = engine.register_reaction(chat_id=1, message_id=2, actor_id=3, emoji="🔥", action="added", router=None)

    assert first["ok"] is True
    assert second.get("deduplicated") is True
    stats = engine.get_reaction_stats(chat_id=1)
    assert stats["total"] == 1
