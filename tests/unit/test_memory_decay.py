"""Unit-тесты для Feature D: Memory Decay (memory_hybrid_reranker)."""

from __future__ import annotations

import math

import pytest

from src.core.memory_hybrid_reranker import (
    DECAY_FLOOR,
    RECENT_CONFIRM_BOOST,
    SearchResult,
    apply_decay,
    compute_decay_multiplier,
    compute_recent_confirm_boost,
)


def test_decay_multiplier_zero_age_is_unity():
    """Свежий chunk (age=0) не должен терять вес."""
    assert compute_decay_multiplier(0) == 1.0
    assert compute_decay_multiplier(-5) == 1.0  # negative — clamp до 0


def test_decay_multiplier_monotonic_decrease():
    """С ростом возраста множитель монотонно падает (до floor)."""
    ages = [1, 7, 30, 180, 365, 1000]
    values = [compute_decay_multiplier(a) for a in ages]
    for i in range(len(values) - 1):
        assert values[i] >= values[i + 1] - 1e-9, f"non-monotonic at {ages[i]}->{ages[i+1]}"
    # Floor enforced для очень старых.
    assert compute_decay_multiplier(10_000) == DECAY_FLOOR
    # Точная сверка формулы для известного дня (например, 30):
    expected_30 = max(DECAY_FLOOR, 1.0 - 0.05 * math.log2(1 + 30))
    assert compute_decay_multiplier(30) == pytest.approx(expected_30)


def test_recent_confirm_boost_window():
    """Boost ×1.2 только если confirm моложе RECENT_CONFIRM_WINDOW_DAYS."""
    assert compute_recent_confirm_boost(None) == 1.0
    assert compute_recent_confirm_boost(0) == RECENT_CONFIRM_BOOST
    assert compute_recent_confirm_boost(7) == RECENT_CONFIRM_BOOST
    assert compute_recent_confirm_boost(7.001) == 1.0
    assert compute_recent_confirm_boost(30) == 1.0
    assert compute_recent_confirm_boost(-1) == 1.0


def test_apply_decay_resorts_results():
    """apply_decay пересортирует — старый чанк опускается ниже свежего при equal RRF."""
    a = SearchResult(chunk_id="old", rrf_score=1.0)
    b = SearchResult(chunk_id="fresh", rrf_score=1.0)
    out = apply_decay([a, b], age_map={"old": 365, "fresh": 0})
    # fresh должен оказаться первым.
    assert out[0].chunk_id == "fresh"
    assert out[1].chunk_id == "old"
    assert out[1].rrf_score < 1.0
    assert out[0].rrf_score == 1.0


def test_apply_decay_with_recent_confirm_outranks_old():
    """Старый, но недавно подтверждённый чанк может обогнать просто старый."""
    confirmed = SearchResult(chunk_id="old_confirmed", rrf_score=1.0)
    old = SearchResult(chunk_id="old_plain", rrf_score=1.0)
    out = apply_decay(
        [confirmed, old],
        age_map={"old_confirmed": 100, "old_plain": 100},
        confirm_age_map={"old_confirmed": 1.0},  # подтверждён вчера
    )
    assert out[0].chunk_id == "old_confirmed"
    # boost ×1.2 поверх decay должен поднять выше plain old.
    assert out[0].rrf_score > out[1].rrf_score


def test_apply_decay_empty_maps_no_op():
    """Без age_map/confirm_map — список не меняется."""
    a = SearchResult(chunk_id="a", rrf_score=0.5)
    b = SearchResult(chunk_id="b", rrf_score=0.3)
    out = apply_decay([a, b], age_map={})
    assert [r.chunk_id for r in out] == ["a", "b"]
    assert a.rrf_score == 0.5
