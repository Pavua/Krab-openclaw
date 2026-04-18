"""Тесты adaptive re-ranking (Memory Layer Phase 3 foundation)."""
from __future__ import annotations

import time

from src.core.memory_adaptive_rerank import (
    ScoredChunk,
    apply_mmr,
    apply_temporal_decay,
    apply_trust_weights,
    rerank_adaptive,
)


def _mk(cid: str, score: float, text: str, **meta) -> ScoredChunk:
    return ScoredChunk(chunk_id=cid, score=score, text=text, metadata=meta)


def test_mmr_diversity_zero_is_identity():
    chunks = [_mk("a", 0.9, "alpha beta"), _mk("b", 0.8, "alpha beta"), _mk("c", 0.7, "gamma")]
    out = apply_mmr(chunks, diversity=0)
    assert [c.chunk_id for c in out] == ["a", "b", "c"]


def test_mmr_high_diversity_prefers_different():
    # При высоком diversity после дубликата должен идти непохожий текст
    chunks = [
        _mk("a", 0.9, "alpha beta gamma"),
        _mk("b", 0.85, "alpha beta gamma"),  # почти дубликат
        _mk("c", 0.7, "delta epsilon zeta"),  # непохоже
    ]
    out = apply_mmr(chunks, diversity=0.9)
    # Первый — a (highest score), второй должен быть c (непохожий), а не b
    assert out[0].chunk_id == "a"
    assert out[1].chunk_id == "c"
    assert out[2].chunk_id == "b"


def test_mmr_empty_input():
    assert apply_mmr([], diversity=0.5) == []


def test_temporal_decay_recent_first():
    now = 1_700_000_000.0
    day = 86400.0
    chunks = [
        _mk("old", 1.0, "x", timestamp=now - 60 * day),
        _mk("new", 0.6, "x", timestamp=now - 1 * day),
    ]
    out = apply_temporal_decay(chunks, half_life_days=30.0, now_ts=now)
    # Старый за 60 дней: 1.0 * 0.25 = 0.25; новый: 0.6 * ~0.977 ~ 0.586
    assert out[0].chunk_id == "new"
    assert out[0].score > out[1].score


def test_temporal_decay_missing_timestamp_uses_now():
    now = 1_700_000_000.0
    chunks = [_mk("a", 1.0, "x")]
    out = apply_temporal_decay(chunks, half_life_days=30.0, now_ts=now)
    # Без timestamp age=0, decay=1.0
    assert out[0].score == 1.0


def test_trust_weights_boost_confirmed():
    chunks = [
        _mk("plain", 1.0, "x", source_trust="default"),
        _mk("trusted", 0.9, "x", source_trust="confirmed"),
    ]
    out = apply_trust_weights(chunks)
    # 0.9 * 1.3 = 1.17 > 1.0
    assert out[0].chunk_id == "trusted"


def test_trust_weights_custom_map():
    chunks = [_mk("a", 1.0, "x", source_trust="tier1")]
    out = apply_trust_weights(chunks, trust_map={"tier1": 2.0})
    assert out[0].score == 2.0


def test_rerank_adaptive_empty_input():
    assert rerank_adaptive([], query="foo", strategy="mmr+temporal") == []


def test_rerank_adaptive_pipeline_runs():
    now = time.time()
    chunks = [
        {"id": "a", "score": 0.9, "text": "alpha beta", "metadata": {"timestamp": now}},
        {"id": "b", "score": 0.85, "text": "alpha beta", "metadata": {"timestamp": now}},
        {"id": "c", "score": 0.7, "text": "gamma delta", "metadata": {"timestamp": now}},
    ]
    out = rerank_adaptive(chunks, query="alpha", strategy="mmr+temporal+trust", diversity=0.8)
    assert len(out) == 3
    assert {c["id"] for c in out} == {"a", "b", "c"}


def test_rerank_adaptive_unknown_stage_ignored():
    chunks = [{"id": "a", "score": 1.0, "text": "x", "metadata": {}}]
    out = rerank_adaptive(chunks, query="q", strategy="bogus+mmr", diversity=0.5)
    assert len(out) == 1
    assert out[0]["id"] == "a"


def test_rerank_adaptive_pipeline_ordering_matters():
    # Если trust идёт после temporal, порядок результата может отличаться
    now = 1_700_000_000.0
    chunks = [
        {"id": "a", "score": 1.0, "text": "x", "metadata": {"timestamp": now, "source_trust": "default"}},
        {"id": "b", "score": 0.5, "text": "x", "metadata": {"timestamp": now, "source_trust": "confirmed"}},
    ]
    out_trust_first = rerank_adaptive(chunks, query="q", strategy="trust", now_ts=now)
    # b: 0.5 * 1.3 = 0.65; a: 1.0 — порядок: a first
    assert out_trust_first[0]["id"] == "a"


def test_rerank_adaptive_single_stage_mmr():
    chunks = [
        {"id": "a", "score": 0.9, "text": "alpha", "metadata": {}},
        {"id": "b", "score": 0.5, "text": "beta", "metadata": {}},
    ]
    out = rerank_adaptive(chunks, query="q", strategy="mmr", diversity=0.3)
    assert [c["id"] for c in out] == ["a", "b"]
