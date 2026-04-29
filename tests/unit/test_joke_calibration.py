# -*- coding: utf-8 -*-
"""Тесты для src/core/joke_calibration.py (Idea 33)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.core.joke_calibration import JokeCalibrationStore


@pytest.fixture
def tmp_store(tmp_path: Path) -> JokeCalibrationStore:
    fixed_now = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)
    return JokeCalibrationStore(
        storage_path=tmp_path / "joke_calibration.json",
        now_fn=lambda: fixed_now,
    )


def test_record_joke_increments_counters(tmp_store: JokeCalibrationStore) -> None:
    """record_joke корректно учитывает позитивный и негативный исход."""
    tmp_store.record_joke(123, "punчик про коня", "positive")
    tmp_store.record_joke(123, "не зашёл каламбур", "negative")
    tmp_store.record_joke(123, "молчание", "neutral")

    stats = tmp_store.get_stats(123)
    assert stats is not None
    assert stats["positive"] == 1
    assert stats["negative"] == 1
    assert stats["neutral"] == 1
    assert stats["total"] == 3


def test_chat_humor_score_pos_neg_ratio(tmp_store: JokeCalibrationStore) -> None:
    """score = positive / (positive + negative); neutral не влияет."""
    for _ in range(3):
        tmp_store.record_joke(42, "ok", "positive")
    tmp_store.record_joke(42, "miss", "negative")
    # neutral не должен сдвинуть score
    for _ in range(10):
        tmp_store.record_joke(42, "no react", "neutral")

    score = tmp_store.chat_humor_score(42)
    assert score == pytest.approx(0.75)
    # below threshold = 0.8 → False, above 0.5 → True
    assert tmp_store.should_attempt_humor(42, threshold=0.5) is True
    assert tmp_store.should_attempt_humor(42, threshold=0.8) is False


def test_threshold_below_min_samples_returns_neutral(tmp_store: JokeCalibrationStore) -> None:
    """Меньше _MIN_SAMPLES_FOR_SCORE активных сигналов → нейтральный prior 0.5."""
    tmp_store.record_joke(7, "sole joke", "negative")
    # Активных всего 1 — недостаточно для значимого score.
    assert tmp_store.chat_humor_score(7) == 0.5
    assert tmp_store.should_attempt_humor(7) is True  # 0.5 >= 0.5


def test_persist_and_reload_round_trip(tmp_path: Path) -> None:
    """Запись на диск + повторная загрузка сохраняет счётчики и историю."""
    storage = tmp_path / "joke_calibration.json"
    fixed_now = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)
    store1 = JokeCalibrationStore(storage_path=storage, now_fn=lambda: fixed_now)
    store1.record_joke(-100, "joke A", "positive")
    store1.record_joke(-100, "joke B", "positive")
    store1.record_joke(-100, "joke C", "negative")

    # Файл должен реально существовать и быть валидным JSON
    assert storage.exists()
    raw = json.loads(storage.read_text(encoding="utf-8"))
    assert "-100" in raw
    assert raw["-100"]["positive"] == 2
    assert raw["-100"]["negative"] == 1

    # Новый стор с тем же путём — должен подхватить состояние
    store2 = JokeCalibrationStore(storage_path=storage, now_fn=lambda: fixed_now)
    stats = store2.get_stats(-100)
    assert stats is not None
    assert stats["positive"] == 2
    assert stats["negative"] == 1
    assert stats["score"] == pytest.approx(2 / 3)


def test_multi_chat_isolation(tmp_store: JokeCalibrationStore) -> None:
    """Записи по разным чатам не пересекаются."""
    for _ in range(5):
        tmp_store.record_joke(111, "smart chat joke", "positive")
    for _ in range(5):
        tmp_store.record_joke(222, "grumpy chat joke", "negative")

    assert tmp_store.chat_humor_score(111) == 1.0
    assert tmp_store.chat_humor_score(222) == 0.0
    assert tmp_store.should_attempt_humor(111) is True
    assert tmp_store.should_attempt_humor(222) is False

    advice_good = tmp_store.format_humor_advice_for_prompt(111)
    advice_bad = tmp_store.format_humor_advice_for_prompt(222)
    assert "score=1.00" in advice_good
    assert "смелее" in advice_good
    assert "score=0.00" in advice_bad
    assert "не шутить" in advice_bad

    # Чат без записей — пустая строка (caller сам решит)
    assert tmp_store.format_humor_advice_for_prompt(999) == ""

    # list_chats содержит оба
    chats = {c["chat_id"] for c in tmp_store.list_chats()}
    assert chats == {"111", "222"}
