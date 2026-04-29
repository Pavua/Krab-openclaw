# -*- coding: utf-8 -*-
"""Unit-тесты для tool_result_cache (Idea 10)."""

from __future__ import annotations

import asyncio

import pytest

from src.core.tool_result_cache import (
    ToolResultCache,
    _stable_args_hash,
    acached_tool_call,
    cached_tool_call,
)


def _make_clock(start: float = 1000.0) -> tuple[list[float], callable]:
    """Возвращает (mutable_now, now_fn) для инжекции в кэш."""
    holder = [start]
    return holder, lambda: holder[0]


# ---- 1. get/set roundtrip -------------------------------------------------


def test_get_set_roundtrip_returns_cached_payload() -> None:
    cache = ToolResultCache(max_entries=10)
    cache.set("web_search", "abc123", {"results": ["x"]})

    cached = cache.get("web_search", "abc123")
    assert cached == {"results": ["x"]}

    # miss для другого args_hash
    assert cache.get("web_search", "different") is None
    # miss для другого tool с тем же hash
    assert cache.get("weather", "abc123") is None


# ---- 2. TTL expiry --------------------------------------------------------


def test_ttl_expiry_drops_entry_after_window() -> None:
    clock, now_fn = _make_clock()
    cache = ToolResultCache(max_entries=10, default_ttl_sec=60.0, now_fn=now_fn)

    cache.set("custom_tool", "k", "payload")
    assert cache.get("custom_tool", "k") == "payload"

    # сдвигаемся внутрь окна — всё ещё доступно
    clock[0] += 30.0
    assert cache.get("custom_tool", "k") == "payload"

    # перешагнули TTL → запись истекла
    clock[0] += 31.0
    assert cache.get("custom_tool", "k") is None

    stats = cache.stats()
    assert stats["size"] == 0  # ленивое удаление сработало


# ---- 3. LRU eviction ------------------------------------------------------


def test_lru_eviction_drops_oldest_when_full() -> None:
    cache = ToolResultCache(max_entries=3, default_ttl_sec=600.0)

    cache.set("tool", "a", 1)
    cache.set("tool", "b", 2)
    cache.set("tool", "c", 3)
    assert cache.stats()["size"] == 3

    # Touch "a" → теперь "b" — самая старая
    assert cache.get("tool", "a") == 1

    # Добавляем 4-ю — должна выпасть "b"
    cache.set("tool", "d", 4)

    assert cache.get("tool", "b") is None
    assert cache.get("tool", "a") == 1
    assert cache.get("tool", "c") == 3
    assert cache.get("tool", "d") == 4
    assert cache.stats()["evictions"] == 1


# ---- 4. Per-tool TTL override --------------------------------------------


def test_per_tool_ttl_overrides_default() -> None:
    clock, now_fn = _make_clock()
    cache = ToolResultCache(max_entries=10, default_ttl_sec=60.0, now_fn=now_fn)

    # web_search override = 300s; default = 60s
    cache.set("web_search", "q1", "search-result")
    cache.set("custom", "q2", "custom-result")

    # Через 100s: default-tool истёк, web_search ещё жив
    clock[0] += 100.0
    assert cache.get("web_search", "q1") == "search-result"
    assert cache.get("custom", "q2") is None

    # currency = 3600s, define = 86400s
    cache.set("currency", "usd_eur", 0.92)
    cache.set("define", "крабы", "членистоногие")

    clock[0] += 3500.0  # currency почти истёк, но ещё в окне
    assert cache.get("currency", "usd_eur") == 0.92
    assert cache.get("define", "крабы") == "членистоногие"

    clock[0] += 200.0  # currency истёк, define ещё нет
    assert cache.get("currency", "usd_eur") is None
    assert cache.get("define", "крабы") == "членистоногие"


# ---- 5. args_hash determinism --------------------------------------------


def test_args_hash_is_deterministic_and_order_independent() -> None:
    h1 = _stable_args_hash({"city": "Madrid", "units": "metric"})
    h2 = _stable_args_hash({"units": "metric", "city": "Madrid"})
    h3 = _stable_args_hash({"city": "Madrid", "units": "imperial"})

    assert h1 == h2  # порядок ключей не важен
    assert h1 != h3  # разные значения → разный хэш

    # Несериализуемые объекты не должны падать
    class Weird:
        def __repr__(self) -> str:
            return "Weird()"

    h_weird = _stable_args_hash({"obj": Weird()})
    assert isinstance(h_weird, str)
    assert len(h_weird) == 32

    # Список и tuple дают одинаковый JSON → одинаковый хэш
    assert _stable_args_hash([1, 2, 3]) == _stable_args_hash([1, 2, 3])


# ---- 6. cached_tool_call wrapper -----------------------------------------


def test_cached_tool_call_wrapper_invokes_fn_once_per_window() -> None:
    cache = ToolResultCache(max_entries=10, default_ttl_sec=600.0)
    call_counter = {"n": 0}

    def expensive() -> str:
        call_counter["n"] += 1
        return f"result-{call_counter['n']}"

    args = {"q": "krab"}
    r1 = cached_tool_call("web_search", args, expensive, cache=cache)
    r2 = cached_tool_call("web_search", args, expensive, cache=cache)
    r3 = cached_tool_call("web_search", args, expensive, cache=cache)

    assert r1 == r2 == r3 == "result-1"
    assert call_counter["n"] == 1  # fn вызвана единожды

    # Разные args → fn вызывается заново
    r_other = cached_tool_call("web_search", {"q": "other"}, expensive, cache=cache)
    assert r_other == "result-2"
    assert call_counter["n"] == 2

    # ttl_sec=0 → bypass кэша
    r_bypass = cached_tool_call("web_search", args, expensive, cache=cache, ttl_sec=0)
    assert r_bypass == "result-3"
    assert call_counter["n"] == 3


def test_acached_tool_call_async_wrapper() -> None:
    cache = ToolResultCache(max_entries=10, default_ttl_sec=600.0)
    counter = {"n": 0}

    async def expensive_async() -> str:
        counter["n"] += 1
        await asyncio.sleep(0)
        return f"async-{counter['n']}"

    async def runner() -> tuple[str, str]:
        a = await acached_tool_call("weather", {"city": "Madrid"}, expensive_async, cache=cache)
        b = await acached_tool_call("weather", {"city": "Madrid"}, expensive_async, cache=cache)
        return a, b

    a, b = asyncio.run(runner())
    assert a == b == "async-1"
    assert counter["n"] == 1


# ---- bonus: invalidate / clear / stats --------------------------------


def test_invalidate_and_clear_and_stats() -> None:
    cache = ToolResultCache(max_entries=5)
    cache.set("tool", "a", 1)
    cache.set("tool", "b", 2)

    assert cache.invalidate("tool", "a") is True
    assert cache.invalidate("tool", "a") is False
    assert cache.get("tool", "a") is None
    assert cache.get("tool", "b") == 2

    stats_before = cache.stats()
    assert stats_before["size"] == 1
    assert stats_before["hits"] >= 1

    cache.clear()
    assert cache.stats()["size"] == 0


def test_none_result_is_not_cached() -> None:
    """None как результат — это часто 'tool failed', не кэшируем."""
    cache = ToolResultCache(max_entries=5)
    counter = {"n": 0}

    def fn_returning_none():
        counter["n"] += 1
        return None

    cached_tool_call("tool", {"k": "v"}, fn_returning_none, cache=cache)
    cached_tool_call("tool", {"k": "v"}, fn_returning_none, cache=cache)
    assert counter["n"] == 2  # оба раза вызвалась


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
