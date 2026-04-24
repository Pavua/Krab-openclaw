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


def test_materialize_cosine_path_when_model_available(monkeypatch):
    """HybridRetriever._materialize_results использует cosine MMR при наличии модели."""
    from datetime import datetime, timezone

    from src.core.memory_retrieval import HybridRetriever, SearchResult

    r = HybridRetriever.__new__(HybridRetriever)  # без __init__, чтобы не трогать БД
    r._rrf_k = 60
    r._last_query = "что ставили на macos"
    r._now = lambda: datetime.now(timezone.utc)

    # Stub model: возвращает предсказуемые векторы (query ~ doc_0).
    class _FakeModel:
        def encode(self, texts):
            out = []
            for t in texts:
                if "macos" in t:
                    out.append(_np([1.0, 0.0, 0.0]))
                elif "дубль" in t or "macos1" in t:
                    out.append(_np([0.99, 0.0, 0.0]))
                else:
                    out.append(_np([0.0, 1.0, 0.0]))
            return out

    r._model = _FakeModel()
    r._model_name = "fake"
    # _ensure_model() вернёт stub.
    monkeypatch.setenv("KRAB_RAG_MMR_ENABLED", "1")

    debug_calls: list[dict] = []

    def _fake_debug(event, **kw):
        debug_calls.append({"event": event, **kw})

    # Patch logger внутри модуля.
    from src.core import memory_retrieval as mr

    monkeypatch.setattr(mr.logger, "debug", _fake_debug)

    # Создаём 3 SearchResult для MMR.
    base = datetime.now(timezone.utc)
    results = [
        SearchResult("a", "c", "ставили krab на macos", base, 1.0),
        SearchResult("b", "c", "почти дубль macos1", base, 0.9),
        SearchResult("d", "c", "настройка voice gateway", base, 0.5),
    ]

    # Создаём stub conn + мок _fetch_chunks / _fetch_context / fused.
    fused = {"a": 0.9, "b": 0.8, "d": 0.4}

    # Мокаем внутренние методы, чтобы _materialize_results не лез в БД.
    def _fake_fetch_chunks(conn, ids):
        return {
            r.message_id: {
                "chunk_id": r.message_id,
                "chat_id": r.chat_id,
                "start_ts": r.timestamp.isoformat(),
                "end_ts": r.timestamp.isoformat(),
                "text_redacted": r.text_redacted,
            }
            for r in results
            if r.message_id in ids
        }

    def _fake_fetch_context(conn, cid, with_context):
        return cid, [], []

    monkeypatch.setattr(HybridRetriever, "_fetch_chunks", staticmethod(_fake_fetch_chunks))
    monkeypatch.setattr(HybridRetriever, "_fetch_context", staticmethod(_fake_fetch_context))

    out = r._materialize_results(
        conn=None,  # type: ignore[arg-type]
        fused=fused,
        top_k=2,
        with_context=0,
        decay_fn=lambda _age: 1.0,
    )
    assert len(out) <= 2
    # Должен сработать cosine путь.
    modes = [c.get("mode") for c in debug_calls if c["event"] == "memory_mmr_mode"]
    assert "cosine" in modes


def test_materialize_jaccard_fallback_when_model_none(monkeypatch):
    """При self._model=None используется Jaccard fallback (логирует mode=jaccard)."""
    from datetime import datetime, timezone

    from src.core.memory_retrieval import HybridRetriever, SearchResult

    r = HybridRetriever.__new__(HybridRetriever)
    r._rrf_k = 60
    r._last_query = ""
    r._now = lambda: datetime.now(timezone.utc)
    r._model = None
    r._model_name = None  # _ensure_model() вернёт None

    monkeypatch.setenv("KRAB_RAG_MMR_ENABLED", "1")

    debug_calls: list[dict] = []

    def _fake_debug(event, **kw):
        debug_calls.append({"event": event, **kw})

    from src.core import memory_retrieval as mr

    monkeypatch.setattr(mr.logger, "debug", _fake_debug)

    base = datetime.now(timezone.utc)
    results = [
        SearchResult("a", "c", "текст один", base, 1.0),
        SearchResult("b", "c", "текст один дубль", base, 0.9),
        SearchResult("d", "c", "совсем другое", base, 0.5),
    ]

    def _fake_fetch_chunks(conn, ids):
        return {
            r.message_id: {
                "chunk_id": r.message_id,
                "chat_id": r.chat_id,
                "start_ts": r.timestamp.isoformat(),
                "end_ts": r.timestamp.isoformat(),
                "text_redacted": r.text_redacted,
            }
            for r in results
            if r.message_id in ids
        }

    def _fake_fetch_context(conn, cid, with_context):
        return cid, [], []

    monkeypatch.setattr(HybridRetriever, "_fetch_chunks", staticmethod(_fake_fetch_chunks))
    monkeypatch.setattr(HybridRetriever, "_fetch_context", staticmethod(_fake_fetch_context))

    out = r._materialize_results(
        conn=None,  # type: ignore[arg-type]
        fused={"a": 0.9, "b": 0.8, "d": 0.4},
        top_k=2,
        with_context=0,
        decay_fn=lambda _age: 1.0,
    )
    assert len(out) <= 2
    modes = [c.get("mode") for c in debug_calls if c["event"] == "memory_mmr_mode"]
    assert "jaccard" in modes


def _np(values):
    """Мини-shim: возвращает объект с .tolist() (имитирует np.ndarray.encode output)."""

    class _Vec(list):
        def tolist(self):
            return list(self)

    return _Vec(values)


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
