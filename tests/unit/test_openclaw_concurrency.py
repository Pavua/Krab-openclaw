# -*- coding: utf-8 -*-
"""
Wave 14-B: tests for OpenClaw gateway concurrency backpressure.

Verifies module-level semaphore limits parallel requests, queue-wait warnings,
and timeout escape (no indefinite hangs).
"""

from __future__ import annotations

import asyncio
import importlib

import pytest


def _set_env_concurrency(monkeypatch, value: str | None):
    if value is None:
        monkeypatch.delenv("KRAB_OPENCLAW_MAX_CONCURRENT", raising=False)
    else:
        monkeypatch.setenv("KRAB_OPENCLAW_MAX_CONCURRENT", value)


@pytest.mark.asyncio
async def test_semaphore_limits_concurrent_requests(monkeypatch):
    """semaphore=2, fire 5 requests, verify max 2 concurrent."""
    _set_env_concurrency(monkeypatch, "2")
    import src.openclaw_client as mod

    importlib.reload(mod)
    try:
        assert mod._OPENCLAW_MAX_CONCURRENT == 2

        active = 0
        max_active = 0
        lock = asyncio.Lock()

        async def task():
            nonlocal active, max_active
            async with mod._gateway_slot(chat_id="t"):
                async with lock:
                    active += 1
                    max_active = max(max_active, active)
                await asyncio.sleep(0.05)
                async with lock:
                    active -= 1

        await asyncio.gather(*(task() for _ in range(5)))
        assert max_active == 2, f"expected max 2 concurrent, got {max_active}"
    finally:
        _set_env_concurrency(monkeypatch, None)
        importlib.reload(mod)


@pytest.mark.asyncio
async def test_queue_wait_logged(monkeypatch):
    """Slow first request, second waits >threshold -> openclaw_request_queued warning."""
    _set_env_concurrency(monkeypatch, "1")
    import src.openclaw_client as mod

    importlib.reload(mod)
    try:
        # Patch threshold down so test stays fast
        monkeypatch.setattr(mod, "_OPENCLAW_QUEUE_WARN_SEC", 0.05)

        captured: list[dict] = []

        def fake_warning(event, **kw):
            captured.append({"event": event, **kw})

        monkeypatch.setattr(mod.logger, "warning", fake_warning)

        async def slow():
            async with mod._gateway_slot(chat_id="slow"):
                await asyncio.sleep(0.15)

        async def fast():
            await asyncio.sleep(0.01)
            async with mod._gateway_slot(chat_id="fast", request_id="rid-1"):
                pass

        await asyncio.gather(slow(), fast())

        events = [c for c in captured if c["event"] == "openclaw_request_queued"]
        assert events, f"no openclaw_request_queued event captured, got {captured}"
        assert events[0]["chat_id"] == "fast"
        assert events[0]["request_id"] == "rid-1"
        assert events[0]["queue_wait_ms"] >= 50.0
    finally:
        _set_env_concurrency(monkeypatch, None)
        importlib.reload(mod)


@pytest.mark.asyncio
async def test_timeout_releases(monkeypatch):
    """Semaphore wait > timeout -> OpenClawSemaphoreTimeoutError raised, no hang."""
    _set_env_concurrency(monkeypatch, "1")
    import src.openclaw_client as mod

    importlib.reload(mod)
    try:
        # Tighten timeout so test runs in under a second
        monkeypatch.setattr(mod, "_OPENCLAW_QUEUE_TIMEOUT_SEC", 0.1)

        holder_release = asyncio.Event()

        async def holder():
            async with mod._gateway_slot(chat_id="holder"):
                await holder_release.wait()

        async def waiter():
            async with mod._gateway_slot(chat_id="waiter"):
                pass

        holder_task = asyncio.create_task(holder())
        await asyncio.sleep(0.01)  # let holder acquire
        with pytest.raises(mod.OpenClawSemaphoreTimeoutError):
            await waiter()
        holder_release.set()
        await holder_task
    finally:
        _set_env_concurrency(monkeypatch, None)
        importlib.reload(mod)


@pytest.mark.asyncio
async def test_env_override_concurrency(monkeypatch):
    """KRAB_OPENCLAW_MAX_CONCURRENT=5 -> 5 max concurrent."""
    _set_env_concurrency(monkeypatch, "5")
    import src.openclaw_client as mod

    importlib.reload(mod)
    try:
        assert mod._OPENCLAW_MAX_CONCURRENT == 5

        active = 0
        max_active = 0
        lock = asyncio.Lock()

        async def task():
            nonlocal active, max_active
            async with mod._gateway_slot():
                async with lock:
                    active += 1
                    max_active = max(max_active, active)
                await asyncio.sleep(0.03)
                async with lock:
                    active -= 1

        await asyncio.gather(*(task() for _ in range(10)))
        assert max_active == 5, f"expected max 5 concurrent, got {max_active}"
    finally:
        _set_env_concurrency(monkeypatch, None)
        importlib.reload(mod)


def test_resolve_max_concurrent_clamps(monkeypatch):
    """Helper clamps to range 1-10 with default 3."""
    import src.openclaw_client as mod

    monkeypatch.delenv("KRAB_OPENCLAW_MAX_CONCURRENT", raising=False)
    assert mod._resolve_max_concurrent_requests() == 3

    monkeypatch.setenv("KRAB_OPENCLAW_MAX_CONCURRENT", "0")
    assert mod._resolve_max_concurrent_requests() == 1

    monkeypatch.setenv("KRAB_OPENCLAW_MAX_CONCURRENT", "999")
    assert mod._resolve_max_concurrent_requests() == 10

    monkeypatch.setenv("KRAB_OPENCLAW_MAX_CONCURRENT", "garbage")
    assert mod._resolve_max_concurrent_requests() == 3

    monkeypatch.setenv("KRAB_OPENCLAW_MAX_CONCURRENT", "5")
    assert mod._resolve_max_concurrent_requests() == 5
