# -*- coding: utf-8 -*-
"""Тесты Feature F — Owner Mood Detection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.core.owner_mood import (
    MIN_CONFIDENCE,
    OwnerMoodTracker,
    analyze_recent_messages,
    format_mood_suffix,
)

# ---------------------------------------------------------------------------
# 1. Keyword detection — annoyed / playful / business / focused / relaxed
# ---------------------------------------------------------------------------


class TestKeywordDetection:
    def test_annoyed_via_mat_and_caps(self) -> None:
        msgs = [
            "БЛЯТЬ ну сколько можно!!!",
            "ЭТО ВООБЩЕ НЕ РАБОТАЕТ, ХУЙНЯ КАКАЯ-ТО",
            "ёбаный стыд",
        ]
        snap = analyze_recent_messages(msgs, owner_id="42")
        assert snap["mood"] == "annoyed"
        assert snap["confidence"] >= MIN_CONFIDENCE
        assert any("mat" in e for e in snap["evidence"])

    def test_playful_via_emoji_and_lol(self) -> None:
        msgs = [
            "ахаха топ 😂😂",
            "лол это прям кек 🤣",
            "ору 🐸 хах",
        ]
        snap = analyze_recent_messages(msgs, owner_id="42")
        assert snap["mood"] == "playful"
        assert snap["confidence"] >= MIN_CONFIDENCE

    def test_business_via_long_structured(self) -> None:
        long_block = (
            "Нужно подготовить план миграции БД с детальной разбивкой по этапам, "
            "включая откат, тестирование и финальную валидацию данных. " * 4
        )
        msgs = [
            long_block,
            "Структура задач:\n- этап 1\n- этап 2\n- этап 3\n- этап 4",
            "Дополнительно: подготовь отчёт о рисках и зависимостях между сервисами. " * 3,
        ]
        snap = analyze_recent_messages(msgs, owner_id="42")
        assert snap["mood"] == "business"
        assert snap["confidence"] >= MIN_CONFIDENCE

    def test_focused_via_urgency(self) -> None:
        msgs = [
            "срочно нужно починить деплой!",
            "горит дедлайн? сделай сейчас!",
            "быстрее, не работает прод",
        ]
        snap = analyze_recent_messages(msgs, owner_id="42")
        assert snap["mood"] == "focused"
        assert snap["confidence"] >= MIN_CONFIDENCE

    def test_relaxed_short_positive(self) -> None:
        msgs = [
            "спс, круто 🙂",
            "норм, ок",
            "класс, спасибо",
        ]
        snap = analyze_recent_messages(msgs, owner_id="42")
        assert snap["mood"] == "relaxed"
        assert snap["confidence"] >= MIN_CONFIDENCE


# ---------------------------------------------------------------------------
# 2. Sliding window
# ---------------------------------------------------------------------------


class TestSlidingWindow:
    def test_only_last_n_used(self) -> None:
        # Старые «мат»-сообщения должны быть отрезаны окном WINDOW_SIZE=10.
        old = ["БЛЯТЬ всё плохо!!!" for _ in range(20)]
        recent = ["спс норм 🙂", "класс ок"] * 5
        snap = analyze_recent_messages(old + recent, owner_id="42")
        # Окно последних 10 — должно быть relaxed, не annoyed.
        assert snap["mood"] != "annoyed"

    def test_too_few_messages_neutral(self) -> None:
        snap = analyze_recent_messages(["БЛЯТЬ"], owner_id="42")
        assert snap["mood"] == "neutral"
        assert snap["confidence"] == 0.0


# ---------------------------------------------------------------------------
# 3. Cache TTL
# ---------------------------------------------------------------------------


class TestCacheTTL:
    def test_ttl_expiry(self) -> None:
        clock = [datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)]
        tracker = OwnerMoodTracker(
            capacity=10,
            ttl_minutes=30,
            now_fn=lambda: clock[0],
        )
        tracker.store("chat1", "owner1", {"mood": "annoyed", "confidence": 0.8, "evidence": []})
        assert tracker.get("chat1", "owner1") is not None

        # Через 31 минуту — устарело.
        clock[0] = clock[0] + timedelta(minutes=31)
        assert tracker.get("chat1", "owner1") is None

    def test_lru_eviction(self) -> None:
        tracker = OwnerMoodTracker(capacity=3, ttl_minutes=60)
        for i in range(5):
            tracker.store(f"c{i}", "owner1", {"mood": "neutral", "confidence": 0.1, "evidence": []})
        assert tracker.size() == 3
        # Первые два — выселены.
        assert tracker.get("c0", "owner1") is None
        assert tracker.get("c1", "owner1") is None
        assert tracker.get("c4", "owner1") is not None

    def test_get_returns_copy(self) -> None:
        tracker = OwnerMoodTracker()
        tracker.store("c1", "o1", {"mood": "playful", "confidence": 0.7, "evidence": ["x"]})
        snap = tracker.get("c1", "o1")
        assert snap is not None
        snap["mood"] = "MUTATED"
        # Внутреннее состояние не изменилось.
        snap2 = tracker.get("c1", "o1")
        assert snap2 is not None
        assert snap2["mood"] == "playful"


# ---------------------------------------------------------------------------
# 4. Suffix formatting
# ---------------------------------------------------------------------------


class TestSuffixFormatting:
    def test_annoyed_suffix_text(self) -> None:
        tracker = OwnerMoodTracker()
        tracker.store("c1", "o1", {"mood": "annoyed", "confidence": 0.8, "evidence": []})
        suffix = format_mood_suffix("c1", "o1", tracker=tracker, enabled=True)
        assert "раздражён" in suffix.lower() or "раздражен" in suffix.lower()
        assert "кратко" in suffix.lower()

    def test_playful_suffix(self) -> None:
        tracker = OwnerMoodTracker()
        tracker.store("c1", "o1", {"mood": "playful", "confidence": 0.7, "evidence": []})
        suffix = format_mood_suffix("c1", "o1", tracker=tracker, enabled=True)
        assert "игривый" in suffix.lower()

    def test_business_suffix(self) -> None:
        tracker = OwnerMoodTracker()
        tracker.store("c1", "o1", {"mood": "business", "confidence": 0.9, "evidence": []})
        suffix = format_mood_suffix("c1", "o1", tracker=tracker, enabled=True)
        assert "деловой" in suffix.lower() or "формальный" in suffix.lower()

    def test_focused_suffix(self) -> None:
        tracker = OwnerMoodTracker()
        tracker.store("c1", "o1", {"mood": "focused", "confidence": 0.6, "evidence": []})
        suffix = format_mood_suffix("c1", "o1", tracker=tracker, enabled=True)
        assert "сосредоточ" in suffix.lower() or "торопится" in suffix.lower()

    def test_disabled_returns_empty(self) -> None:
        tracker = OwnerMoodTracker()
        tracker.store("c1", "o1", {"mood": "annoyed", "confidence": 0.9, "evidence": []})
        assert format_mood_suffix("c1", "o1", tracker=tracker, enabled=False) == ""

    def test_no_owner_returns_empty(self) -> None:
        tracker = OwnerMoodTracker()
        assert format_mood_suffix("c1", "", tracker=tracker, enabled=True) == ""
        assert format_mood_suffix("", "o1", tracker=tracker, enabled=True) == ""


# ---------------------------------------------------------------------------
# 5. Unknown / neutral graceful
# ---------------------------------------------------------------------------


class TestUnknownGraceful:
    def test_unknown_mood_returns_empty(self) -> None:
        tracker = OwnerMoodTracker()
        tracker.store("c1", "o1", {"mood": "weirdo", "confidence": 0.99, "evidence": []})
        assert format_mood_suffix("c1", "o1", tracker=tracker, enabled=True) == ""

    def test_neutral_mood_returns_empty(self) -> None:
        tracker = OwnerMoodTracker()
        tracker.store("c1", "o1", {"mood": "neutral", "confidence": 0.5, "evidence": []})
        assert format_mood_suffix("c1", "o1", tracker=tracker, enabled=True) == ""

    def test_low_confidence_returns_empty(self) -> None:
        tracker = OwnerMoodTracker()
        tracker.store("c1", "o1", {"mood": "annoyed", "confidence": 0.05, "evidence": []})
        assert format_mood_suffix("c1", "o1", tracker=tracker, enabled=True) == ""

    def test_empty_messages_neutral(self) -> None:
        snap = analyze_recent_messages([], owner_id="42")
        assert snap["mood"] == "neutral"
        assert snap["confidence"] == 0.0
        assert snap["evidence"] == []

    def test_update_from_messages_round_trip(self) -> None:
        tracker = OwnerMoodTracker()
        snap = tracker.update_from_messages(
            "c1",
            "o1",
            ["спс круто 🙂", "норм ок", "класс спасибо"],
        )
        assert snap["mood"] in {"relaxed", "playful", "neutral"}
        # Сохранилось в кэше.
        assert tracker.get("c1", "o1") is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
