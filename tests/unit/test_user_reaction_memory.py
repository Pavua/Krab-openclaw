# -*- coding: utf-8 -*-
"""Тесты UserReactionStore (Feature B — per-user reaction memory)."""

from __future__ import annotations

import json
import threading

import pytest

from src.core.user_reaction_memory import (
    UserReactionRecord,
    UserReactionStore,
)


@pytest.fixture()
def store(tmp_path):
    path = tmp_path / "user_reaction_memory.json"
    return UserReactionStore(path=path)


def test_positive_bumps_threshold_down(store):
    """3+ positive reactions → modifier -0.15 (легче триггернуть)."""
    user_id = "111"
    for _ in range(3):
        store.record_positive(user_id)
    modifier = store.get_threshold_modifier(user_id)
    assert modifier == pytest.approx(-0.15)
    record = store.get_record(user_id)
    assert record.classify() == "positive"
    assert record.positive_count == 3
    assert record.negative_count == 0


def test_negative_bumps_threshold_up(store):
    """3+ negative без positive → modifier +0.2 (труднее триггернуть)."""
    user_id = "222"
    for _ in range(3):
        store.record_negative(user_id)
    modifier = store.get_threshold_modifier(user_id)
    assert modifier == pytest.approx(0.2)
    record = store.get_record(user_id)
    assert record.classify() == "negative"
    assert record.negative_count == 3


def test_neutral_returns_zero_modifier(store):
    """Neutral count и mixed signals → 0.0."""
    user_id = "333"
    # Только neutral
    for _ in range(10):
        store.record_neutral(user_id)
    assert store.get_threshold_modifier(user_id) == 0.0
    # Mixed: 2 positive + 2 negative — пока ниже порогов
    user_id2 = "444"
    store.record_positive(user_id2)
    store.record_positive(user_id2)
    store.record_negative(user_id2)
    store.record_negative(user_id2)
    assert store.get_threshold_modifier(user_id2) == 0.0
    # Negative с одним positive — больше не "negative" (positive_count != 0)
    user_id3 = "555"
    for _ in range(5):
        store.record_negative(user_id3)
    store.record_positive(user_id3)
    # 5 negative + 1 positive: positive_count != 0 → не negative;
    # positive_count < 3 → не positive → neutral
    assert store.get_threshold_modifier(user_id3) == 0.0


def test_missing_user_defaults_to_zero(store):
    """Неизвестный user_id и None → modifier 0.0, get_record даёт пустую запись."""
    assert store.get_threshold_modifier("9999999") == 0.0
    assert store.get_threshold_modifier(None) == 0.0
    record = store.get_record("9999999")
    assert isinstance(record, UserReactionRecord)
    assert record.user_id == "9999999"
    assert record.positive_count == 0
    assert record.negative_count == 0
    assert record.neutral_count == 0


def test_persistence_across_reload(tmp_path):
    """После записи → новый instance того же path читает данные."""
    path = tmp_path / "ur.json"
    store1 = UserReactionStore(path=path)
    store1.record_positive("777")
    store1.record_positive("777")
    store1.record_positive("777")
    store1.record_negative("888")

    # Sanity: на диске JSON правильной формы
    raw = json.loads(path.read_text())
    assert "users" in raw
    assert "777" in raw["users"]
    assert raw["users"]["777"]["positive_count"] == 3

    # Reload через новый инстанс
    store2 = UserReactionStore(path=path)
    rec_pos = store2.get_record("777")
    assert rec_pos.positive_count == 3
    assert store2.get_threshold_modifier("777") == pytest.approx(-0.15)
    rec_neg = store2.get_record("888")
    assert rec_neg.negative_count == 1


def test_atomic_write_concurrent_increments(tmp_path):
    """Конкурентные increments не теряют данные (RLock + atomic replace)."""
    path = tmp_path / "ur_concurrent.json"
    store = UserReactionStore(path=path)
    user_id = "concurrent_user"

    n_threads = 8
    increments_per_thread = 25

    def worker():
        for _ in range(increments_per_thread):
            store.record_positive(user_id)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    record = store.get_record(user_id)
    assert record.positive_count == n_threads * increments_per_thread

    # Файл валиден
    raw = json.loads(path.read_text())
    assert raw["users"][user_id]["positive_count"] == n_threads * increments_per_thread

    # И новый инстанс читает корректно
    store2 = UserReactionStore(path=path)
    assert store2.get_record(user_id).positive_count == n_threads * increments_per_thread
