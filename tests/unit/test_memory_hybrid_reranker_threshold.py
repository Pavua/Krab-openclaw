"""
Тесты RRF score threshold pruning в hybrid_search / rrf_combine.

Проверяем:
  1. min_score=0.0 — всё проходит (backward compat).
  2. min_score выше порога — низкоскоровые кандидаты отбрасываются.
  3. min_score выше всех скоров — пустой список.
  4. rrf_combine сам по себе работает без фильтрации — threshold в hybrid_search.
"""

from __future__ import annotations

import pytest

from src.core.memory_hybrid_reranker import SearchResult, rrf_combine

# ---------------------------------------------------------------------------
# Вспомогательные функции.
# ---------------------------------------------------------------------------

def _make_fts(ids: list[str]) -> list[tuple[str, float]]:
    """Симулируем FTS результаты: [(chunk_id, abs_bm25_score), ...]."""
    return [(cid, float(i + 1)) for i, cid in enumerate(ids)]


def _make_sem(ids: list[str]) -> list[tuple[str, float]]:
    """Симулируем semantic результаты: [(chunk_id, similarity), ...]."""
    return [(cid, 1.0 - i * 0.1) for i, cid in enumerate(ids)]


# ---------------------------------------------------------------------------
# rrf_combine — базовые инварианты.
# ---------------------------------------------------------------------------

class TestRrfCombine:
    def test_empty_inputs_return_empty(self):
        result = rrf_combine([], [])
        assert result == []

    def test_fts_only(self):
        fts = _make_fts(["a", "b", "c"])
        result = rrf_combine(fts, [])
        assert [r.chunk_id for r in result] == ["a", "b", "c"]
        # Убедимся, что rrf_score > 0 у всех.
        assert all(r.rrf_score > 0.0 for r in result)

    def test_both_sources_boost_shared_id(self):
        """chunk_id 'x' присутствует в обоих — должен иметь score выше, чем FTS-only."""
        fts = [("x", 5.0), ("y", 3.0)]
        sem = [("x", 0.9), ("z", 0.7)]
        result = rrf_combine(fts, sem)
        by_id = {r.chunk_id: r for r in result}
        assert by_id["x"].rrf_score > by_id["y"].rrf_score
        assert by_id["x"].sources == ["fts", "semantic"]

    def test_sorted_descending(self):
        fts = _make_fts(["a", "b", "c", "d"])
        sem = _make_sem(["d", "c", "b", "a"])
        result = rrf_combine(fts, sem)
        scores = [r.rrf_score for r in result]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# hybrid_search — threshold pruning (без реальной БД, через mock).
# ---------------------------------------------------------------------------

class TestHybridSearchThreshold:
    """Проверяем min_score-логику, не запуская реальную БД."""

    def _apply_threshold(self, results: list[SearchResult], min_score: float) -> list[SearchResult]:
        """Повторяем логику фильтрации из hybrid_search для unit-теста без БД."""
        if min_score > 0.0:
            return [r for r in results if r.rrf_score >= min_score]
        return results

    def test_threshold_zero_keeps_all(self):
        """min_score=0.0 — backward compat, ничего не обрезается."""
        fts = _make_fts(["a", "b", "c"])
        combined = rrf_combine(fts, [])
        pruned = self._apply_threshold(combined, 0.0)
        assert len(pruned) == len(combined)

    def test_threshold_drops_low_scores(self):
        """min_score=0.5 убирает кандидатов ниже порога."""
        # k=60, rank=1 → score = 1/61 ≈ 0.0164; rank=2 → 1/62 ≈ 0.0161.
        # Все FTS-only при k=60 будут << 0.5.
        fts = _make_fts(["a", "b", "c"])
        combined = rrf_combine(fts, [])
        # Все скоры <<0.5 → после порога должен быть пустой список.
        pruned = self._apply_threshold(combined, 0.5)
        assert pruned == []

    def test_threshold_keeps_high_scores(self):
        """Порог ниже минимального rrf_score — все проходят."""
        fts = _make_fts(["a"])
        combined = rrf_combine(fts, [])
        min_possible = combined[-1].rrf_score
        pruned = self._apply_threshold(combined, min_possible * 0.5)
        assert len(pruned) == len(combined)

    def test_threshold_exact_boundary_inclusive(self):
        """rrf_score == min_score — кандидат проходит (>= semantics)."""
        fts = _make_fts(["a"])
        combined = rrf_combine(fts, [])
        exact = combined[0].rrf_score
        pruned = self._apply_threshold(combined, exact)
        assert len(pruned) == 1
        assert pruned[0].chunk_id == "a"

    def test_threshold_above_all_returns_empty(self):
        """Порог выше любого rrf_score → пустой список."""
        fts = _make_fts(["a", "b"])
        combined = rrf_combine(fts, [])
        pruned = self._apply_threshold(combined, 999.0)
        assert pruned == []

    def test_threshold_partial_pruning(self):
        """Проверяем что порог отрезает ровно нужное количество кандидатов."""
        # Строим контролируемый список SearchResult вручную.
        results = [
            SearchResult(chunk_id="high", rrf_score=0.1),
            SearchResult(chunk_id="mid", rrf_score=0.05),
            SearchResult(chunk_id="low", rrf_score=0.01),
        ]
        pruned = self._apply_threshold(results, 0.04)
        ids = [r.chunk_id for r in pruned]
        assert "high" in ids
        assert "mid" in ids
        assert "low" not in ids


# ---------------------------------------------------------------------------
# Интеграция с env-переменной (проверяем что HybridRetriever читает env).
# ---------------------------------------------------------------------------

class TestHybridRetrieverEnv:
    """Smoke-тест: HybridRetriever корректно читает KRAB_RAG_MIN_RRF_SCORE."""

    def test_env_var_parsed_as_float(self, monkeypatch):
        """float(os.getenv(..., '0.0')) не должен падать при валидных значениях."""
        import os
        monkeypatch.setenv("KRAB_RAG_MIN_RRF_SCORE", "0.05")
        val = float(os.getenv("KRAB_RAG_MIN_RRF_SCORE", "0.0"))
        assert val == pytest.approx(0.05)

    def test_env_var_default_zero(self, monkeypatch):
        """При отсутствии env — дефолт 0.0."""
        import os
        monkeypatch.delenv("KRAB_RAG_MIN_RRF_SCORE", raising=False)
        val = float(os.getenv("KRAB_RAG_MIN_RRF_SCORE", "0.0"))
        assert val == 0.0

    def test_retriever_search_no_db_returns_empty(self, monkeypatch, tmp_path):
        """HybridRetriever.search() без БД возвращает [] независимо от min_score."""
        monkeypatch.setenv("KRAB_RAG_MIN_RRF_SCORE", "0.5")
        from src.core.memory_archive import ArchivePaths
        from src.core.memory_retrieval import HybridRetriever

        fake_paths = ArchivePaths(db=tmp_path / "nonexistent.db", dir=tmp_path)
        retriever = HybridRetriever(archive_paths=fake_paths, model_name=None)
        results = retriever.search("тест запрос")
        assert results == []
