# -*- coding: utf-8 -*-
"""S69 Wave 6: per-model LLM latency tracking (p50/p95/p99).

Operator wants to compare latency between models: cloud vs local primary,
codex vs Gemini, paid vs free tier — without aggregating all bypass calls
into a single histogram bucket.

Design:
    In-memory `deque(maxlen=100)` per model holds last 100 latency samples
    (seconds). On metrics scrape, we re-render Prometheus gauges with
    freshly-computed percentiles from the rolling window. This is the same
    "compute on demand" pattern as `collect.py` (Wave 75 / 79).

Metrics:
    krab_model_latency_p50_seconds{model}
    krab_model_latency_p95_seconds{model}
    krab_model_latency_p99_seconds{model}

Wire-in points (S69 W6):
    - src/openclaw_client.py : local_primary_bypass_ok (elapsed_ms)
    - src/integrations/google_genai_direct.py : google_genai_direct_complete_done
    - src/integrations/cli_subprocess_bypass.py : cli_subprocess_complete_done

Fail-safe: все хелперы заворачивают в try/except. prometheus_client
soft-import — модуль безопасен при отсутствии зависимости.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any

# Per-model rolling window of latency samples (seconds).
_WINDOW_SIZE: int = 100
_LATENCIES: dict[str, deque[float]] = {}
_LOCK = threading.Lock()

try:
    from prometheus_client import Gauge as _Gauge  # type: ignore[import-not-found]

    krab_model_latency_p50_seconds: Any = _Gauge(
        "krab_model_latency_p50_seconds",
        "Per-model LLM call latency, p50 over last 100 calls (seconds)",
        ["model"],
    )
    krab_model_latency_p95_seconds: Any = _Gauge(
        "krab_model_latency_p95_seconds",
        "Per-model LLM call latency, p95 over last 100 calls (seconds)",
        ["model"],
    )
    krab_model_latency_p99_seconds: Any = _Gauge(
        "krab_model_latency_p99_seconds",
        "Per-model LLM call latency, p99 over last 100 calls (seconds)",
        ["model"],
    )
except Exception:  # noqa: BLE001
    krab_model_latency_p50_seconds = None  # type: ignore[assignment]
    krab_model_latency_p95_seconds = None  # type: ignore[assignment]
    krab_model_latency_p99_seconds = None  # type: ignore[assignment]


def _percentile(sorted_samples: list[float], q: float) -> float:
    """Nearest-rank percentile (no interpolation). 0 ≤ q ≤ 1.

    Empty list → 0.0. Single element → that element. Otherwise pick the
    sample at index ``ceil(q * n) - 1`` clamped to [0, n-1].
    """
    n = len(sorted_samples)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_samples[0])
    # Nearest-rank: index of the q-th percentile in a sorted list of n.
    import math

    idx = max(0, min(n - 1, math.ceil(q * n) - 1))
    return float(sorted_samples[idx])


def get_percentiles(model: str) -> dict[str, float]:
    """Return {p50, p95, p99} for `model` from current rolling window.

    Returns zeros if model has no samples yet. Computes percentiles over
    a snapshot copy so we don't hold the lock during sort.
    """
    with _LOCK:
        dq = _LATENCIES.get(model)
        samples = list(dq) if dq else []
    if not samples:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
    samples.sort()
    return {
        "p50": _percentile(samples, 0.5),
        "p95": _percentile(samples, 0.95),
        "p99": _percentile(samples, 0.99),
    }


def record_latency(model: str, elapsed_ms: float) -> None:
    """Record one latency sample for `model` (input is milliseconds).

    Appends to in-memory deque (evicts oldest if > 100), then updates the
    three Prometheus gauges with freshly-computed percentiles. Fail-safe.
    """
    try:
        if not model:
            return
        m = str(model)[:80]
        elapsed_sec = max(0.0, float(elapsed_ms) / 1000.0)
        with _LOCK:
            dq = _LATENCIES.get(m)
            if dq is None:
                dq = deque(maxlen=_WINDOW_SIZE)
                _LATENCIES[m] = dq
            dq.append(elapsed_sec)
        pcts = get_percentiles(m)
        if krab_model_latency_p50_seconds is not None:
            krab_model_latency_p50_seconds.labels(model=m).set(pcts["p50"])
        if krab_model_latency_p95_seconds is not None:
            krab_model_latency_p95_seconds.labels(model=m).set(pcts["p95"])
        if krab_model_latency_p99_seconds is not None:
            krab_model_latency_p99_seconds.labels(model=m).set(pcts["p99"])
    except Exception:  # noqa: BLE001
        pass


def record_latency_seconds(model: str, elapsed_sec: float) -> None:
    """Convenience wrapper for callsites already holding seconds."""
    try:
        record_latency(model, float(elapsed_sec) * 1000.0)
    except Exception:  # noqa: BLE001
        pass


def _reset_for_tests() -> None:
    """Clear in-memory state. Tests only."""
    with _LOCK:
        _LATENCIES.clear()
