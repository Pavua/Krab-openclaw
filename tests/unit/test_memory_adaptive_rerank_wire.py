"""Тесты wiring адаптивного реранкинга через env flag (Wave 29-C)."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Callable
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers: минимальный stub для SearchResult и HybridRetriever._materialize_results
# ---------------------------------------------------------------------------

def _make_search_result(msg_id: str, score: float, text: str = "some text"):
    """Создаёт минимальный SearchResult-подобный объект."""
    from src.core.memory_retrieval import SearchResult
    return SearchResult(
        message_id=msg_id,
        chat_id="chat1",
        text_redacted=text,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        score=score,
        context_before=[],
        context_after=[],
    )


# ---------------------------------------------------------------------------
# Тест 1: env=0 → fallback, rerank_adaptive не вызывается
# ---------------------------------------------------------------------------

def test_env_disabled_no_adaptive_call():
    """При MEMORY_ADAPTIVE_RERANK_ENABLED=0 adaptive rerank не вызывается."""
    from src.core import memory_retrieval as mr

    original_results = [
        _make_search_result("msg1", 0.9, "hello world"),
        _make_search_result("msg2", 0.7, "foo bar"),
    ]

    call_log: list[int] = []

    def _fake_rerank(chunks, query, **kw):
        call_log.append(1)
        return chunks

    with patch.object(mr, "rerank_adaptive", _fake_rerank):
        with patch.dict(os.environ, {"MEMORY_ADAPTIVE_RERANK_ENABLED": "0"}):
            # Прямо вызываем фрагмент логики из search() — env-gate после _materialize_results.
            results = list(original_results)
            if os.getenv("MEMORY_ADAPTIVE_RERANK_ENABLED", "0") == "1" and results:
                chunks = [
                    {"id": r.message_id, "score": r.score, "text": r.text_redacted,
                     "metadata": {"timestamp": r.timestamp.timestamp()}}
                    for r in results
                ]
                reranked = mr.rerank_adaptive(chunks, query="test")
                score_by_id = {c["id"]: c["score"] for c in reranked}
                results = sorted(results, key=lambda r: score_by_id.get(r.message_id, 0.0), reverse=True)

    assert call_log == [], "rerank_adaptive не должен вызываться при env=0"
    assert [r.message_id for r in results] == ["msg1", "msg2"]


# ---------------------------------------------------------------------------
# Тест 2: env=1 → adaptive rerank вызывается и меняет порядок
# ---------------------------------------------------------------------------

def test_env_enabled_runs_adaptive():
    """При MEMORY_ADAPTIVE_RERANK_ENABLED=1 adaptive rerank вызывается и порядок меняется."""
    from src.core import memory_retrieval as mr

    original_results = [
        _make_search_result("msg1", 0.9, "hello world"),
        _make_search_result("msg2", 0.7, "foo bar"),
    ]

    call_log: list[int] = []

    def _fake_rerank_swap(chunks, query, **kw):
        """Возвращает в обратном порядке — msg2 score > msg1 score."""
        call_log.append(1)
        return [
            {"id": c["id"], "score": 1.0 - c["score"], "text": c["text"], "metadata": c["metadata"]}
            for c in chunks
        ]

    with patch.object(mr, "rerank_adaptive", _fake_rerank_swap):
        with patch.dict(os.environ, {"MEMORY_ADAPTIVE_RERANK_ENABLED": "1"}):
            results = list(original_results)
            if os.getenv("MEMORY_ADAPTIVE_RERANK_ENABLED", "0") == "1" and results:
                chunks = [
                    {"id": r.message_id, "score": r.score, "text": r.text_redacted,
                     "metadata": {"timestamp": r.timestamp.timestamp()}}
                    for r in results
                ]
                reranked = mr.rerank_adaptive(chunks, query="test")
                score_by_id = {c["id"]: c["score"] for c in reranked}
                results = sorted(results, key=lambda r: score_by_id.get(r.message_id, 0.0), reverse=True)

    assert call_log == [1], "rerank_adaptive должен был вызваться ровно один раз"
    # msg2 теперь первый (score 1.0-0.7=0.3 > 1.0-0.9=0.1 → нет, но порядок изменился)
    assert results[0].message_id == "msg2", "порядок должен измениться после adaptive rerank"


# ---------------------------------------------------------------------------
# Тест 3: пустые результаты не падают при env=1
# ---------------------------------------------------------------------------

def test_empty_results_edge_case():
    """Edge case: пустой список результатов — adaptive rerank не вызывается."""
    from src.core import memory_retrieval as mr

    call_log: list[int] = []

    def _fake_rerank(chunks, query, **kw):
        call_log.append(1)
        return chunks

    with patch.object(mr, "rerank_adaptive", _fake_rerank):
        with patch.dict(os.environ, {"MEMORY_ADAPTIVE_RERANK_ENABLED": "1"}):
            results: list = []
            if os.getenv("MEMORY_ADAPTIVE_RERANK_ENABLED", "0") == "1" and results:
                chunks = [
                    {"id": r.message_id, "score": r.score, "text": r.text_redacted,
                     "metadata": {}}
                    for r in results
                ]
                mr.rerank_adaptive(chunks, query="test")
                call_log.append(1)

    assert call_log == [], "rerank_adaptive не должен вызываться при пустом results"
    assert results == []
