# -*- coding: utf-8 -*-
"""Wave 69 tests: stateful counters в paid_gemini_guard.

Покрывают:
    * snapshot имеет правильную dict-структуру;
    * blocked_count инкрементируется при PaidGeminiGuardError;
    * allowed_count инкрементируется при allow-list pass-through;
    * warned_count инкрементируется в warn mode;
    * last_blocked_at/host/model заполняются корректно;
    * thread-safety: concurrent increment не теряет события.
"""

from __future__ import annotations

import threading

import httpx
import pytest

from src.integrations import paid_gemini_guard as guard_mod
from src.integrations.paid_gemini_guard import (
    PaidGeminiGuardError,
    _trigger,
    get_paid_gemini_guard_stats,
    reset_paid_gemini_guard_stats,
)


@pytest.fixture(autouse=True)
def _reset_stats() -> None:
    """Изолируем counters между тестами."""
    reset_paid_gemini_guard_stats()
    yield
    reset_paid_gemini_guard_stats()


def test_stats_snapshot_has_expected_shape() -> None:
    snap = get_paid_gemini_guard_stats()
    assert set(snap.keys()) == {
        "blocked_count",
        "allowed_count",
        "warned_count",
        "last_blocked_at",
        "last_blocked_host",
        "last_blocked_model",
    }
    assert snap["blocked_count"] == 0
    assert snap["allowed_count"] == 0
    assert snap["warned_count"] == 0
    assert snap["last_blocked_at"] is None
    assert snap["last_blocked_host"] is None
    assert snap["last_blocked_model"] is None


def test_blocked_count_increments_on_block(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "1")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3-pro-preview:generateContent"
    )
    with pytest.raises(PaidGeminiGuardError):
        _trigger(url)
    with pytest.raises(PaidGeminiGuardError):
        _trigger(url)
    snap = get_paid_gemini_guard_stats()
    assert snap["blocked_count"] == 2
    assert snap["allowed_count"] == 0
    assert snap["warned_count"] == 0
    assert snap["last_blocked_at"] is not None
    assert snap["last_blocked_host"] == "generativelanguage.googleapis.com"
    assert snap["last_blocked_model"] == "gemini-3-pro-preview"


def test_allowed_count_increments_for_gemma(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "1")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemma-4-e4b-it:generateContent"
    )
    # Gemma — в allow-list (Wave 25-E). Не должно raise.
    _trigger(url)
    _trigger(url)
    _trigger(url)
    snap = get_paid_gemini_guard_stats()
    assert snap["allowed_count"] == 3
    assert snap["blocked_count"] == 0
    assert snap["last_blocked_at"] is None


def test_warned_count_increments_in_warn_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "warn")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3-pro-preview:generateContent"
    )
    _trigger(url)
    _trigger(url)
    snap = get_paid_gemini_guard_stats()
    assert snap["warned_count"] == 2
    assert snap["blocked_count"] == 0
    assert snap["allowed_count"] == 0


def test_off_mode_does_not_touch_counters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "0")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3-pro-preview:generateContent"
    )
    _trigger(url)
    _trigger(url)
    snap = get_paid_gemini_guard_stats()
    assert snap["blocked_count"] == 0
    assert snap["allowed_count"] == 0
    assert snap["warned_count"] == 0


def test_thread_safety_concurrent_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Concurrent _trigger() из N threads — итоговый count == ровно N attempts."""
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "1")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3-pro-preview:generateContent"
    )

    thread_count = 16
    per_thread_calls = 25
    barrier = threading.Barrier(thread_count)

    def worker() -> None:
        barrier.wait()  # все стартуют одновременно для max contention
        for _ in range(per_thread_calls):
            try:
                _trigger(url)
            except PaidGeminiGuardError:
                pass

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    snap = get_paid_gemini_guard_stats()
    assert snap["blocked_count"] == thread_count * per_thread_calls


def test_module_lock_is_threading_lock() -> None:
    """Sanity: _stats_lock — действительно lock object."""
    lock = guard_mod._stats_lock
    # threading.Lock — это factory; isinstance с _thread.LockType сложно portable.
    # Достаточно проверить acquire/release API.
    assert hasattr(lock, "acquire")
    assert hasattr(lock, "release")
    acquired = lock.acquire(timeout=0.1)
    assert acquired is True
    lock.release()


def test_httpx_module_importable() -> None:
    """Sanity: httpx ещё резолвится после нашего monkey-patch helper module load."""
    assert httpx is not None
