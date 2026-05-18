# -*- coding: utf-8 -*-
"""S69 Wave 6: per-model LLM latency p50/p95/p99 tracking tests."""

from __future__ import annotations


def _reset_state() -> None:
    """Clear the in-memory deque registry between tests."""
    from src.core.metrics import model_latency as ml

    ml._reset_for_tests()


def test_record_latency_per_model() -> None:
    """record_latency stores samples per-model independently."""
    _reset_state()
    from src.core.metrics.model_latency import _LATENCIES, record_latency

    record_latency("google/gemini-3-pro", 1200.0)
    record_latency("google/gemini-3-pro", 800.0)
    record_latency("codex-cli/gpt-5", 5000.0)

    assert "google/gemini-3-pro" in _LATENCIES
    assert "codex-cli/gpt-5" in _LATENCIES
    assert len(_LATENCIES["google/gemini-3-pro"]) == 2
    assert len(_LATENCIES["codex-cli/gpt-5"]) == 1
    # Stored as seconds (ms / 1000).
    assert _LATENCIES["google/gemini-3-pro"][0] == 1.2
    assert _LATENCIES["codex-cli/gpt-5"][0] == 5.0


def test_percentile_computation() -> None:
    """p50/p95/p99 computed using nearest-rank from the rolling window."""
    _reset_state()
    from src.core.metrics.model_latency import get_percentiles, record_latency

    # 100 samples: 1ms, 2ms, ..., 100ms -> seconds 0.001 .. 0.100.
    for i in range(1, 101):
        record_latency("test/model", float(i))

    pcts = get_percentiles("test/model")
    # Nearest-rank: p50 = index ceil(0.5*100)-1 = 49 → sample value 50 ms = 0.05s
    assert pcts["p50"] == 0.050
    # p95 = index ceil(0.95*100)-1 = 94 → 95 ms = 0.095s
    assert pcts["p95"] == 0.095
    # p99 = index ceil(0.99*100)-1 = 98 → 99 ms = 0.099s
    assert pcts["p99"] == 0.099


def test_rolling_window_eviction() -> None:
    """deque(maxlen=100) evicts oldest sample once we exceed 100 entries."""
    _reset_state()
    from src.core.metrics.model_latency import (
        _LATENCIES,
        _WINDOW_SIZE,
        get_percentiles,
        record_latency,
    )

    # 150 samples — only last 100 survive.
    for i in range(1, 151):
        record_latency("test/evict", float(i))

    assert len(_LATENCIES["test/evict"]) == _WINDOW_SIZE
    # Oldest value should be sample #51 (51 ms = 0.051 s).
    assert _LATENCIES["test/evict"][0] == 0.051
    # Now p50 = ceil(0.5*100)-1 = 49 in sorted [51..150] = 100 ms.
    pcts = get_percentiles("test/evict")
    assert pcts["p50"] == 0.100


def test_get_percentiles_empty_model_returns_zero() -> None:
    """get_percentiles for unknown model returns all zeros — fail-safe."""
    _reset_state()
    from src.core.metrics.model_latency import get_percentiles

    pcts = get_percentiles("never/seen")
    assert pcts == {"p50": 0.0, "p95": 0.0, "p99": 0.0}


def test_lazy_import_pattern_wire_in() -> None:
    """Wire-in sites import via `from src.core.metrics.model_latency` lazily.

    Verifies that record_latency / record_latency_seconds are importable
    via the wire-in path used by openclaw_client.py / google_genai_direct.py /
    cli_subprocess_bypass.py.
    """
    _reset_state()
    # Path used by src/openclaw_client.py.
    # Path used by integrations/*.py (record_latency_seconds).
    from src.core.metrics.model_latency import record_latency, record_latency_seconds

    record_latency("wire/a", 100.0)
    record_latency_seconds("wire/b", 0.5)

    from src.core.metrics.model_latency import get_percentiles

    assert get_percentiles("wire/a")["p50"] == 0.1
    assert get_percentiles("wire/b")["p50"] == 0.5


def test_record_latency_fail_safe_on_empty_model() -> None:
    """Empty/None model name does not crash and does not record."""
    _reset_state()
    from src.core.metrics.model_latency import _LATENCIES, record_latency

    record_latency("", 1000.0)
    record_latency(None, 1000.0)  # type: ignore[arg-type]
    assert _LATENCIES == {}


def test_record_latency_updates_prometheus_gauges_if_available() -> None:
    """If prometheus_client is installed, gauges get updated with percentiles."""
    _reset_state()
    from src.core.metrics import model_latency as ml

    if ml.krab_model_latency_p50_seconds is None:
        # prometheus_client not installed in this env — skip.
        return

    for v in (10.0, 20.0, 30.0, 40.0, 50.0):
        ml.record_latency("prom/test", v)

    p50_val = ml.krab_model_latency_p50_seconds.labels(model="prom/test")._value.get()
    assert p50_val > 0.0
