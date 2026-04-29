# -*- coding: utf-8 -*-
"""Тесты per-handler latency метрик (Idea 23).

Проверяем:
1. Histogram observe — krab_handler_latency_seconds emit'ит bucket/sum/count.
2. Counter increment — krab_handler_invocations_total{handler,status}.
3. Async context manager `time_handler` — auto-times при успешном выходе.
4. Error path — exception → status='error' (но не глотает исключение).
"""

from __future__ import annotations

import asyncio

import pytest

from src.core import prometheus_metrics as pm


def _collect_samples(metric):
    """Вытащить (name, labels, value) из prometheus_client metric.collect()."""
    out: list[tuple[str, dict[str, str], float]] = []
    for fam in metric.collect():
        for sample in fam.samples:
            out.append((sample.name, dict(sample.labels), float(sample.value)))
    return out


@pytest.mark.skipif(
    pm._handler_latency_seconds is None,
    reason="prometheus_client недоступен в окружении",
)
def test_observe_handler_latency_emits_histogram():
    """observe_handler_latency пишет в histogram (buckets/sum/count)."""
    handler = "test_obs_hist"
    pm.observe_handler_latency(handler, 0.42, status="success")

    samples = _collect_samples(pm._handler_latency_seconds)
    relevant = [s for s in samples if s[1].get("handler") == handler]
    assert relevant, "histogram должен emit'ить sample-ы для нашего handler"

    # _count >= 1
    counts = [v for n, _, v in relevant if n.endswith("_count")]
    assert counts and counts[0] >= 1.0

    # _sum >= 0.4 (мы записали 0.42)
    sums = [v for n, _, v in relevant if n.endswith("_sum")]
    assert sums and sums[0] >= 0.4


@pytest.mark.skipif(
    pm._handler_invocations_total is None,
    reason="prometheus_client недоступен в окружении",
)
def test_observe_handler_latency_increments_counter():
    """observe_handler_latency инкрементирует counter с лейблом status."""
    handler = "test_obs_counter"
    pm.observe_handler_latency(handler, 0.1, status="success")
    pm.observe_handler_latency(handler, 0.2, status="error")
    pm.observe_handler_latency(handler, 0.3, status="error")

    samples = _collect_samples(pm._handler_invocations_total)
    by_status: dict[str, float] = {}
    for name, labels, value in samples:
        if labels.get("handler") != handler:
            continue
        if not name.endswith("_total"):
            continue
        by_status[labels.get("status", "")] = value

    assert by_status.get("success", 0.0) >= 1.0
    assert by_status.get("error", 0.0) >= 2.0


@pytest.mark.skipif(
    pm._handler_latency_seconds is None,
    reason="prometheus_client недоступен в окружении",
)
def test_time_handler_context_manager_auto_times():
    """Async context manager автоматически замеряет latency при успешном выходе."""
    handler = "test_ctx_success"

    async def _run() -> None:
        async with pm.time_handler(handler):
            await asyncio.sleep(0.01)

    asyncio.run(_run())

    samples = _collect_samples(pm._handler_invocations_total)
    success_total = sum(
        v
        for n, lbl, v in samples
        if lbl.get("handler") == handler and lbl.get("status") == "success" and n.endswith("_total")
    )
    assert success_total >= 1.0


@pytest.mark.skipif(
    pm._handler_invocations_total is None,
    reason="prometheus_client недоступен в окружении",
)
def test_time_handler_context_manager_error_path():
    """При исключении внутри context manager — status='error', exception не глотается."""
    handler = "test_ctx_error"

    async def _run() -> None:
        async with pm.time_handler(handler):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(_run())

    samples = _collect_samples(pm._handler_invocations_total)
    error_total = sum(
        v
        for n, lbl, v in samples
        if lbl.get("handler") == handler and lbl.get("status") == "error" and n.endswith("_total")
    )
    assert error_total >= 1.0
