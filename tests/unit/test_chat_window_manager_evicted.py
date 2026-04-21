# -*- coding: utf-8 -*-
"""Тесты счётчиков eviction ChatWindowManager (lru / idle)."""

import time

import pytest

from src.core.chat_window_manager import ChatWindowManager


def test_lru_eviction_increments_lru_counter() -> None:
    mgr = ChatWindowManager(capacity=2)
    assert mgr.get_eviction_counts() == {"lru": 0, "idle": 0}

    mgr.get_or_create("a")
    mgr.get_or_create("b")
    # Третий chat_id вытолкнет LRU
    mgr.get_or_create("c")

    counts = mgr.get_eviction_counts()
    assert counts["lru"] == 1
    assert counts["idle"] == 0


def test_lru_eviction_multiple() -> None:
    mgr = ChatWindowManager(capacity=1)
    mgr.get_or_create("x")
    mgr.get_or_create("y")
    mgr.get_or_create("z")

    counts = mgr.get_eviction_counts()
    assert counts["lru"] == 2
    assert counts["idle"] == 0


def test_idle_eviction_increments_idle_counter() -> None:
    mgr = ChatWindowManager(capacity=10)
    mgr.get_or_create("chat1")
    mgr.get_or_create("chat2")

    # Принудительно сделать окна «старыми»
    for w in mgr._windows.values():
        w.last_activity_at = time.time() - 9999

    removed = mgr.evict_idle(timeout_sec=1)
    assert removed == 2

    counts = mgr.get_eviction_counts()
    assert counts["idle"] == 2
    assert counts["lru"] == 0


def test_idle_eviction_partial() -> None:
    mgr = ChatWindowManager(capacity=10)
    mgr.get_or_create("active")
    mgr.get_or_create("stale")

    # Только stale устаревшее
    mgr._windows["stale"].last_activity_at = time.time() - 9999

    removed = mgr.evict_idle(timeout_sec=1)
    assert removed == 1

    counts = mgr.get_eviction_counts()
    assert counts["idle"] == 1
    assert counts["lru"] == 0


def test_get_eviction_counts_returns_copy() -> None:
    """get_eviction_counts() не должен возвращать внутренний dict."""
    mgr = ChatWindowManager(capacity=1)
    counts = mgr.get_eviction_counts()
    counts["lru"] = 999  # мутируем копию
    assert mgr.get_eviction_counts()["lru"] == 0
