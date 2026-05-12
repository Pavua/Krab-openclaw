# -*- coding: utf-8 -*-
"""Memory Phase 2 retrieval metrics (Wave 22 + Wave 74).

Counters + histograms по retrieval mode/latency/duration/outcome. Все helpers
fail-safe и no-op без prometheus_client.
"""

from __future__ import annotations

try:
    from prometheus_client import Counter as _Counter  # type: ignore[import-not-found]
    from prometheus_client import Histogram as _Histogram  # type: ignore[import-not-found]

    _memory_retrieval_mode_total = _Counter(
        "krab_memory_retrieval_mode_total",
        "Количество retrieval queries по режиму (fts/vec/hybrid/none)",
        ["mode"],
    )
    _memory_retrieval_latency_seconds = _Histogram(
        "krab_memory_retrieval_latency_seconds",
        "Latency retrieval per phase (fts/vec/mmr/total)",
        ["phase"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    )
    _vec_query_duration_seconds = _Histogram(
        "krab_vec_query_duration_seconds",
        "Latency of sqlite-vec MATCH queries (linear scan over vec_chunks)",
        ["k"],
        buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
    )
    _memory_retrieval_duration_seconds = _Histogram(
        "krab_memory_retrieval_duration_seconds",
        "Duration of Memory Phase 2 hybrid retrieval per phase",
        ["phase"],
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    )
    _memory_retrieval_total = _Counter(
        "krab_memory_retrieval_total",
        "Memory Phase 2 retrieval calls by outcome",
        ["outcome"],
    )
except Exception:  # noqa: BLE001
    _memory_retrieval_mode_total = None  # type: ignore[assignment]
    _memory_retrieval_latency_seconds = None  # type: ignore[assignment]
    _vec_query_duration_seconds = None  # type: ignore[assignment]
    _memory_retrieval_duration_seconds = None  # type: ignore[assignment]
    _memory_retrieval_total = None  # type: ignore[assignment]


_RETRIEVAL_PHASE_ALIASES = {"fts": "fts5"}
_RETRIEVAL_VALID_PHASES = frozenset({"embedding", "fts5", "vec", "rrf", "mmr", "rerank", "total"})
_RETRIEVAL_VALID_OUTCOMES = frozenset({"success", "timeout", "error"})


def _facade():
    """Lazy import фасада."""
    import src.core.prometheus_metrics as _pm  # noqa: PLC0415

    return _pm


def record_retrieval_duration(phase: str, seconds: float) -> None:
    """Wave 74: фиксирует latency phase. Legacy "fts" → "fts5". Fail-safe."""
    try:
        canonical = _RETRIEVAL_PHASE_ALIASES.get(phase, phase)
        if canonical not in _RETRIEVAL_VALID_PHASES:
            return
        metric = _facade()._memory_retrieval_duration_seconds
        if metric is not None:
            metric.labels(phase=canonical).observe(seconds)
    except Exception:  # noqa: BLE001
        pass


def inc_retrieval_outcome(outcome: str) -> None:
    """Wave 74: инкрементирует krab_memory_retrieval_total{outcome=...}. Fail-safe."""
    try:
        if outcome not in _RETRIEVAL_VALID_OUTCOMES:
            return
        metric = _facade()._memory_retrieval_total
        if metric is not None:
            metric.labels(outcome=outcome).inc()
    except Exception:  # noqa: BLE001
        pass
