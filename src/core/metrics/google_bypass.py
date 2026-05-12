# -*- coding: utf-8 -*-
"""Wave 20-B: Google direct bypass metrics — invocations counter + latency &
thoughts-tokens histograms. record_google_bypass_call читает метрики через
facade чтобы тесты могли patch'ить facade-уровень."""

from __future__ import annotations

try:
    from prometheus_client import Counter as _CounterBypass  # type: ignore[import-not-found]
    from prometheus_client import Histogram as _HistogramBypass  # type: ignore[import-not-found]

    krab_google_direct_bypass_total = _CounterBypass(
        "krab_google_direct_bypass_total",
        "Google direct bypass invocations (обходит OpenClaw WebSocket transport regression)",
        ["model", "outcome"],
    )
    krab_google_direct_bypass_latency_seconds = _HistogramBypass(
        "krab_google_direct_bypass_latency_seconds",
        "Google direct bypass полная latency одного completion (секунды)",
        ["model"],
        buckets=(0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0, 34.0, 55.0),
    )
    krab_google_direct_bypass_thoughts_tokens = _HistogramBypass(
        "krab_google_direct_bypass_thoughts_tokens",
        "Thoughts-токены, использованные Gemini extended thinking в bypass-вызове",
        ["model"],
        buckets=(0, 50, 100, 200, 500, 1000, 2000, 5000, 10000),
    )
except Exception:  # noqa: BLE001
    krab_google_direct_bypass_total = None  # type: ignore[assignment]
    krab_google_direct_bypass_latency_seconds = None  # type: ignore[assignment]
    krab_google_direct_bypass_thoughts_tokens = None  # type: ignore[assignment]


def _facade():
    """Lazy import фасада — позволяет тестам patch'ить facade-атрибуты."""
    import src.core.prometheus_metrics as _pm  # noqa: PLC0415

    return _pm


def record_google_bypass_call(
    *,
    model: str,
    outcome: str,
    latency_sec: float,
    thoughts_tokens: int = 0,
) -> None:
    """Записать metrics для одного bypass invocation. Fail-safe."""
    try:
        m = (model or "unknown")[:80]
        o = (outcome or "unknown")[:20]
        pm = _facade()
        if pm.krab_google_direct_bypass_total is not None:
            pm.krab_google_direct_bypass_total.labels(model=m, outcome=o).inc()
        if pm.krab_google_direct_bypass_latency_seconds is not None:
            pm.krab_google_direct_bypass_latency_seconds.labels(model=m).observe(
                max(0.0, float(latency_sec))
            )
        if thoughts_tokens > 0 and pm.krab_google_direct_bypass_thoughts_tokens is not None:
            pm.krab_google_direct_bypass_thoughts_tokens.labels(model=m).observe(
                float(thoughts_tokens)
            )
    except Exception:  # noqa: BLE001
        pass
