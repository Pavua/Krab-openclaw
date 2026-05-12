"""
Wave 74: тесты Prometheus метрик Memory Phase 2 hybrid retrieval.

Проверяют:
  - record_retrieval_duration() пишет в histogram per-phase;
  - legacy "fts" автоматически маппится в "fts5";
  - inc_retrieval_outcome() инкрементирует counter с валидным outcome;
  - search() через _observe_phase кормит обе histogram;
  - error-путь в search() инкрементирует outcome=error;
  - success-путь инкрементирует outcome=success.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core import prometheus_metrics as pm
from src.core.memory_archive import ArchivePaths, create_schema
from src.core.memory_retrieval import HybridRetriever, _inc_outcome, _observe_phase


def _hist_sample(histogram, **labels) -> float:
    """Возвращает _sum для конкретного label set из prometheus_client Histogram."""
    for metric in histogram.collect():
        for sample in metric.samples:
            if sample.name.endswith("_sum") and sample.labels == labels:
                return sample.value
    return 0.0


def _counter_value(counter, **labels) -> float:
    """Возвращает текущее значение counter с label set."""
    for metric in counter.collect():
        for sample in metric.samples:
            if sample.name.endswith("_total") and sample.labels == labels:
                return sample.value
    return 0.0


@pytest.mark.skipif(
    pm._memory_retrieval_duration_seconds is None,
    reason="prometheus_client недоступен",
)
def test_record_retrieval_duration_writes_per_phase() -> None:
    """Записываем 0.123s в каждую phase, проверяем _sum."""
    hist = pm._memory_retrieval_duration_seconds

    phases = ["embedding", "fts5", "vec", "rrf", "mmr", "rerank", "total"]
    before = {p: _hist_sample(hist, phase=p) for p in phases}

    for p in phases:
        pm.record_retrieval_duration(p, 0.123)

    after = {p: _hist_sample(hist, phase=p) for p in phases}
    for p in phases:
        assert after[p] == pytest.approx(before[p] + 0.123, abs=1e-6), p


@pytest.mark.skipif(
    pm._memory_retrieval_duration_seconds is None,
    reason="prometheus_client недоступен",
)
def test_record_retrieval_duration_aliases_fts_to_fts5() -> None:
    """Legacy phase 'fts' автоматически пишется как 'fts5'."""
    hist = pm._memory_retrieval_duration_seconds
    before = _hist_sample(hist, phase="fts5")

    pm.record_retrieval_duration("fts", 0.5)

    after = _hist_sample(hist, phase="fts5")
    assert after == pytest.approx(before + 0.5, abs=1e-6)


@pytest.mark.skipif(
    pm._memory_retrieval_duration_seconds is None,
    reason="prometheus_client недоступен",
)
def test_record_retrieval_duration_unknown_phase_ignored() -> None:
    """Unknown phase — no-op (не падает, не плодит ярлыки)."""
    # Должен просто молча проигнорировать.
    pm.record_retrieval_duration("frobnicate", 1.0)
    # Дополнительно — повторный вызов с правильной phase всё ещё работает.
    hist = pm._memory_retrieval_duration_seconds
    before = _hist_sample(hist, phase="total")
    pm.record_retrieval_duration("total", 0.01)
    after = _hist_sample(hist, phase="total")
    assert after == pytest.approx(before + 0.01, abs=1e-6)


@pytest.mark.skipif(
    pm._memory_retrieval_total is None,
    reason="prometheus_client недоступен",
)
def test_inc_retrieval_outcome_valid_outcomes() -> None:
    """Все три valid outcome инкрементируются; невалидный — no-op."""
    counter = pm._memory_retrieval_total
    before = {
        o: _counter_value(counter, outcome=o) for o in ("success", "timeout", "error")
    }

    pm.inc_retrieval_outcome("success")
    pm.inc_retrieval_outcome("timeout")
    pm.inc_retrieval_outcome("error")
    pm.inc_retrieval_outcome("garbage")  # должен быть no-op

    after = {
        o: _counter_value(counter, outcome=o) for o in ("success", "timeout", "error")
    }
    assert after["success"] == before["success"] + 1
    assert after["timeout"] == before["timeout"] + 1
    assert after["error"] == before["error"] + 1


@pytest.mark.skipif(
    pm._memory_retrieval_duration_seconds is None,
    reason="prometheus_client недоступен",
)
def test_observe_phase_writes_both_histograms() -> None:
    """_observe_phase кормит legacy и Wave 74 histogram одновременно."""
    legacy = pm._memory_retrieval_latency_seconds
    new = pm._memory_retrieval_duration_seconds

    legacy_before = _hist_sample(legacy, phase="rrf")
    new_before = _hist_sample(new, phase="rrf")

    _observe_phase("rrf", 0.42)

    assert _hist_sample(legacy, phase="rrf") == pytest.approx(legacy_before + 0.42, abs=1e-6)
    assert _hist_sample(new, phase="rrf") == pytest.approx(new_before + 0.42, abs=1e-6)


@pytest.mark.skipif(
    pm._memory_retrieval_total is None,
    reason="prometheus_client недоступен",
)
def test_search_increments_outcome_success(tmp_path: Path) -> None:
    """search() на пустой БД возвращает [] но всё равно проходит — outcome=success."""
    db_path = tmp_path / "archive.db"
    conn = sqlite3.connect(db_path)
    create_schema(conn)
    conn.close()

    retriever = HybridRetriever(archive_paths=ArchivePaths(db=db_path, dir=tmp_path))
    counter = pm._memory_retrieval_total
    before = _counter_value(counter, outcome="success")

    out = retriever.search("anything", top_k=5)
    assert out == []

    after = _counter_value(counter, outcome="success")
    assert after == before + 1


@pytest.mark.skipif(
    pm._memory_retrieval_total is None,
    reason="prometheus_client недоступен",
)
def test_search_increments_outcome_error_on_exception(tmp_path: Path) -> None:
    """Исключение из _search_impl → outcome=error, исключение пробрасывается."""
    db_path = tmp_path / "archive.db"
    conn = sqlite3.connect(db_path)
    create_schema(conn)
    conn.close()

    retriever = HybridRetriever(archive_paths=ArchivePaths(db=db_path, dir=tmp_path))
    counter = pm._memory_retrieval_total
    before = _counter_value(counter, outcome="error")

    with patch.object(
        retriever, "_search_impl", side_effect=RuntimeError("boom")
    ):
        # Исключение пробрасывается. Тип может измениться из-за sentry_perf
        # context manager stub, но outcome=error должен быть инкрементирован.
        with pytest.raises(Exception):
            retriever.search("query", top_k=5)

    after = _counter_value(counter, outcome="error")
    assert after == before + 1


@pytest.mark.skipif(
    pm._memory_retrieval_total is None,
    reason="prometheus_client недоступен",
)
def test_search_increments_outcome_timeout(tmp_path: Path) -> None:
    """TimeoutError из _search_impl → outcome=timeout."""
    db_path = tmp_path / "archive.db"
    conn = sqlite3.connect(db_path)
    create_schema(conn)
    conn.close()

    retriever = HybridRetriever(archive_paths=ArchivePaths(db=db_path, dir=tmp_path))
    counter = pm._memory_retrieval_total
    before = _counter_value(counter, outcome="timeout")

    with patch.object(retriever, "_search_impl", side_effect=TimeoutError("slow")):
        with pytest.raises(Exception):
            retriever.search("query", top_k=5)

    after = _counter_value(counter, outcome="timeout")
    assert after == before + 1


def test_inc_outcome_helper_is_silent_on_failure() -> None:
    """_inc_outcome никогда не падает, даже если prom-call внутри бросит."""
    # Просто smoke-test: вызов не бросает.
    _inc_outcome("success")
    _inc_outcome("garbage")  # should silently be ignored
