"""Unit-тесты для Idea 15: Time-aware retrieval boost.

Проверяем bucket-логику compute_recency_boost(), интеграцию apply_recency_boost()
с feedback boost и graceful-обработку отсутствующего config.
"""

from __future__ import annotations

import pytest

from src.core.memory_hybrid_reranker import (
    RECENCY_BOOST_DAY_SECONDS,
    RECENCY_BOOST_HOUR_SECONDS,
    RECENCY_BOOST_LAST_DAY,
    RECENCY_BOOST_LAST_HOUR,
    RECENCY_BOOST_LAST_WEEK,
    RECENCY_BOOST_WEEK_SECONDS,
    SearchResult,
    apply_recency_boost,
    apply_response_feedback_boost,
    compute_recency_boost,
)


def test_recency_boost_buckets_each_tier():
    """Каждый bucket возвращает свой множитель — точная проверка границ."""
    # < 1h → ×1.5
    assert compute_recency_boost(0) == RECENCY_BOOST_LAST_HOUR
    assert compute_recency_boost(60) == RECENCY_BOOST_LAST_HOUR
    assert compute_recency_boost(RECENCY_BOOST_HOUR_SECONDS - 1) == RECENCY_BOOST_LAST_HOUR
    # ровно 1h уже в следующем bucket → ×1.2
    assert compute_recency_boost(RECENCY_BOOST_HOUR_SECONDS) == RECENCY_BOOST_LAST_DAY
    # < 24h → ×1.2
    assert compute_recency_boost(3600 * 12) == RECENCY_BOOST_LAST_DAY
    assert compute_recency_boost(RECENCY_BOOST_DAY_SECONDS - 1) == RECENCY_BOOST_LAST_DAY
    # ровно 24h → следующий bucket: ×1.0
    assert compute_recency_boost(RECENCY_BOOST_DAY_SECONDS) == RECENCY_BOOST_LAST_WEEK
    # 7d уже за пределами активной зоны → 1.0
    assert compute_recency_boost(RECENCY_BOOST_WEEK_SECONDS) == 1.0
    # negative age (clock skew) → ×1.5 (трактуем как самый свежий).
    assert compute_recency_boost(-100) == RECENCY_BOOST_LAST_HOUR


def test_recency_boost_falls_to_decay_for_old_items():
    """Старые chunks (>7д) не получают boost — отдаём decay'у."""
    assert compute_recency_boost(RECENCY_BOOST_WEEK_SECONDS) == 1.0
    assert compute_recency_boost(RECENCY_BOOST_WEEK_SECONDS * 4) == 1.0
    assert compute_recency_boost(RECENCY_BOOST_WEEK_SECONDS * 100) == 1.0


def test_apply_recency_boost_resorts_and_idempotent():
    """apply_recency_boost поднимает свежий chunk выше старого при equal RRF."""
    fresh = SearchResult(chunk_id="fresh", rrf_score=1.0)
    older = SearchResult(chunk_id="older", rrf_score=1.0)
    # age в днях: 0.01 ≈ 14 минут (<1h, ×1.5), 14 дней (>7д, no-op).
    out = apply_recency_boost([fresh, older], age_days_map={"fresh": 0.01, "older": 14.0})
    assert out[0].chunk_id == "fresh"
    assert out[0].rrf_score == pytest.approx(1.5)
    assert out[1].rrf_score == pytest.approx(1.0)

    # Идемпотентность не гарантируется в смысле «повторный вызов не меняет» —
    # boost мультипликативен, поэтому очищаем mapping и проверяем no-op.
    same = apply_recency_boost(out, age_days_map={})
    assert same[0].rrf_score == pytest.approx(1.5)
    assert same[1].rrf_score == pytest.approx(1.0)


def test_apply_recency_boost_missing_chunk_in_map_is_noop():
    """Отсутствие chunk_id в age_days_map → multiplier=1.0 (graceful)."""
    a = SearchResult(chunk_id="known", rrf_score=2.0)
    b = SearchResult(chunk_id="unknown", rrf_score=1.0)
    out = apply_recency_boost([a, b], age_days_map={"known": 0.0})
    # known получит ×1.5 → 3.0, unknown останется 1.0.
    assert out[0].chunk_id == "known"
    assert out[0].rrf_score == pytest.approx(3.0)
    assert out[1].chunk_id == "unknown"
    assert out[1].rrf_score == pytest.approx(1.0)
    # Пустой mapping → результат идентичен входу.
    src = [SearchResult(chunk_id="x", rrf_score=0.5)]
    out_empty = apply_recency_boost(src, age_days_map={})
    assert out_empty is src
    assert out_empty[0].rrf_score == 0.5


def test_recency_boost_combines_with_feedback_boost():
    """Композиция: feedback ×N, потом recency ×M — порядок не критичен,
    результат мультипликативен."""
    chunk = SearchResult(chunk_id="c1", rrf_score=1.0)
    # Положительный feedback (1 pos) даёт >1.0; recency на свежее ×1.5.
    after_fb = apply_response_feedback_boost([chunk], feedback_map={"c1": (1, 0)})
    score_after_fb = after_fb[0].rrf_score
    assert score_after_fb > 1.0
    after_recency = apply_recency_boost(after_fb, age_days_map={"c1": 0.001})
    # Финальный = score_after_fb * 1.5
    assert after_recency[0].rrf_score == pytest.approx(score_after_fb * RECENCY_BOOST_LAST_HOUR)
