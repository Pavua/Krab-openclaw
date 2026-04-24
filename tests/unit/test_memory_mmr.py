"""Тесты MMR diversity re-ranking (P2 carry-over).

Проверяем:
    1. Порядок меняется — near-duplicates отодвигаются.
    2. disabled-flag → поведение стабильно / не применяется.
    3. Fallback без embeddings — Jaccard работает на чистых текстах.
    4. Edge-cases — пустой вход, один документ.
"""

from __future__ import annotations

import os

import pytest

from src.core.memory_mmr import (
    mmr_is_enabled,
    mmr_lambda,
    mmr_rerank,
    mmr_rerank_texts,
)


def test_mmr_disabled_flag(monkeypatch):
    """При KRAB_RAG_MMR_ENABLED=0 — mmr_is_enabled() возвращает False."""
    monkeypatch.setenv("KRAB_RAG_MMR_ENABLED", "0")
    assert mmr_is_enabled() is False

    monkeypatch.setenv("KRAB_RAG_MMR_ENABLED", "1")
    assert mmr_is_enabled() is True

    # Default — on.
    monkeypatch.delenv("KRAB_RAG_MMR_ENABLED", raising=False)
    assert mmr_is_enabled() is True


def test_mmr_lambda_clamp(monkeypatch):
    """λ читается из env и клэмпится в [0, 1]."""
    monkeypatch.setenv("KRAB_RAG_MMR_LAMBDA", "0.5")
    assert mmr_lambda() == 0.5
    monkeypatch.setenv("KRAB_RAG_MMR_LAMBDA", "1.5")
    assert mmr_lambda() == 1.0
    monkeypatch.setenv("KRAB_RAG_MMR_LAMBDA", "-0.3")
    assert mmr_lambda() == 0.0
    monkeypatch.setenv("KRAB_RAG_MMR_LAMBDA", "not-a-number")
    assert mmr_lambda() == 0.7  # default


def test_mmr_rerank_empty_and_single():
    """Edge cases: пустой список и один документ."""
    assert mmr_rerank(None, [], [], top_k=5) == []
    assert mmr_rerank(None, [[1.0]], ["a"], rrf_scores=[0.9], top_k=5) == ["a"]
    # top_k=0 → пусто
    assert mmr_rerank(None, [[1.0]], ["a"], rrf_scores=[0.9], top_k=0) == []


def test_mmr_rerank_with_embeddings_picks_diverse():
    """Два почти идентичных вектора → MMR выбирает один, затем разнообразный."""
    query = [1.0, 1.0, 0.0]
    # a, a_dup почти идентичны между собой; b отличается по третьему измерению.
    docs = [
        [1.0, 1.0, 0.0],  # a — max relevance
        [0.99, 1.01, 0.0],  # a_dup — почти дубль a
        [0.7, 0.7, 0.5],  # b — чуть менее relevance, но diverse vs a
    ]
    ids = ["a", "a_dup", "b"]
    # λ=0.2 — diversity сильно доминирует.
    ordered = mmr_rerank(query, docs, ids, top_k=2, lambda_=0.2)
    # Первым — наиболее релевантный (a).
    assert ordered[0] == "a"
    # Вторым — НЕ a_dup (diversity penalty), а b.
    assert ordered[1] == "b"


def test_mmr_rerank_texts_jaccard_fallback():
    """Без embeddings используется Jaccard по токенам."""
    ids = ["c1", "c2", "c3"]
    texts = [
        "установить krab на macos",
        "установить krab на macos сегодня",  # почти дубль c1
        "настройка voice gateway отдельно",  # уникальный
    ]
    rrf = [1.0, 0.95, 0.5]
    ordered = mmr_rerank_texts(
        query="",
        doc_ids=ids,
        doc_texts=texts,
        rrf_scores=rrf,
        top_k=2,
        lambda_=0.4,  # больший вес diversity
    )
    # Первым остаётся c1 (top relevance).
    assert ordered[0] == "c1"
    # Вторым — c3 (разнообразный), а не c2 (дубль).
    assert ordered[1] == "c3"


def test_mmr_rerank_texts_respects_top_k():
    """Возвращает не больше top_k, даже если кандидатов больше."""
    ids = [f"id_{i}" for i in range(10)]
    texts = [f"text {i}" for i in range(10)]
    rrf = [1.0 - i * 0.1 for i in range(10)]
    ordered = mmr_rerank_texts(
        query="",
        doc_ids=ids,
        doc_texts=texts,
        rrf_scores=rrf,
        top_k=3,
    )
    assert len(ordered) == 3
    # Первым — лучший по relevance.
    assert ordered[0] == "id_0"


def test_mmr_rerank_fallback_to_rrf_when_no_vectors():
    """Если doc_vecs=[None, None], relevance берётся из rrf_scores."""
    ids = ["x", "y"]
    ordered = mmr_rerank(
        query_vec=None,
        doc_vecs=[None, None],
        doc_ids=ids,
        rrf_scores=[0.3, 0.9],
        top_k=2,
    )
    # Более релевантный y идёт первым.
    assert ordered[0] == "y"
    assert ordered[1] == "x"


@pytest.fixture(autouse=True)
def _reset_env():
    """Не даём env от одного теста течь в другой."""
    keys = ["KRAB_RAG_MMR_ENABLED", "KRAB_RAG_MMR_LAMBDA"]
    saved = {k: os.environ.get(k) for k in keys}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
