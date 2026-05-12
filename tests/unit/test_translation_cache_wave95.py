# -*- coding: utf-8 -*-
"""Wave 95: tests для content-hash translation cache."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator

import pytest

from src.core.translation_cache import (
    TranslationCache,
    _hash_key,
)


@pytest.fixture
def env_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("KRAB_TRANSLATION_CACHE_ENABLED", "1")
    yield


@pytest.fixture
def env_off(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("KRAB_TRANSLATION_CACHE_ENABLED", "0")
    yield


def _make_cache(
    tmp_path: Path,
    *,
    max_entries: int = 5000,
    ttl: float = 7 * 24 * 3600.0,
    now: float = 1_000_000.0,
) -> TranslationCache:
    storage = tmp_path / "translation_cache.json"
    clock = {"now": now}
    return TranslationCache(
        storage_path=storage,
        max_entries=max_entries,
        ttl_seconds=ttl,
        now_fn=lambda: clock["now"],
    )


def test_hash_key_stable_and_targeted() -> None:
    """Один и тот же (text, lang) → один key, разные lang → разные keys."""
    k1 = _hash_key("Hello world", "ru")
    k2 = _hash_key("Hello world", "ru")
    k3 = _hash_key("Hello world", "es")
    assert k1 == k2
    assert k1 != k3
    assert len(k1) == 16


def test_store_and_lookup_hit(env_on: None, tmp_path: Path) -> None:
    """Store → lookup возвращает кэшированное значение."""
    cache = _make_cache(tmp_path)
    cache.store("Привет", "en", "Hello")
    assert cache.lookup("Привет", "en") == "Hello"
    # Hit увеличил hit_count внутри entry.
    stats = cache.stats()
    assert stats["size"] == 1
    assert stats["total_lifetime_hits"] == 1


def test_lookup_miss_when_absent(env_on: None, tmp_path: Path) -> None:
    """Lookup на отсутствующий ключ → None."""
    cache = _make_cache(tmp_path)
    assert cache.lookup("Нет такого", "en") is None
    assert cache.stats()["size"] == 0


def test_lru_eviction(env_on: None, tmp_path: Path) -> None:
    """При переполнении max_entries старый entry вылетает."""
    cache = _make_cache(tmp_path, max_entries=3)
    cache.store("a", "ru", "A")
    cache.store("b", "ru", "B")
    cache.store("c", "ru", "C")
    # Тач "a" чтобы он стал MRU.
    assert cache.lookup("a", "ru") == "A"
    cache.store("d", "ru", "D")  # evicts "b" (LRU)
    assert cache.lookup("b", "ru") is None
    assert cache.lookup("a", "ru") == "A"
    assert cache.lookup("c", "ru") == "C"
    assert cache.lookup("d", "ru") == "D"
    assert cache.stats()["size"] == 3


def test_ttl_expiry(env_on: None, tmp_path: Path) -> None:
    """Entry старше TTL → miss + удалён."""
    storage = tmp_path / "tc.json"
    clock = {"now": 1_000_000.0}
    cache = TranslationCache(
        storage_path=storage,
        ttl_seconds=100.0,
        now_fn=lambda: clock["now"],
    )
    cache.store("foo", "en", "Foo")
    assert cache.lookup("foo", "en") == "Foo"
    # Сдвигаем время за TTL.
    clock["now"] = 1_000_000.0 + 200.0
    assert cache.lookup("foo", "en") is None
    assert cache.stats()["size"] == 0


def test_atomic_persist_and_reload(env_on: None, tmp_path: Path) -> None:
    """Persist → читаемый JSON; новый cache на том же path подхватывает entries."""
    cache1 = _make_cache(tmp_path)
    cache1.store("hello", "ru", "привет")
    storage = tmp_path / "translation_cache.json"
    assert storage.exists()
    # Файл — валидный JSON со схемой {entries: ...}.
    payload = json.loads(storage.read_text(encoding="utf-8"))
    assert payload.get("version") == 1
    assert isinstance(payload.get("entries"), dict)
    # Tempfile-обломков не осталось.
    leftovers = [
        p for p in tmp_path.iterdir() if p.name.startswith(".translation_cache.")
    ]
    assert leftovers == []
    # Новый cache читает state.
    cache2 = _make_cache(tmp_path)
    assert cache2.lookup("hello", "ru") == "привет"


def test_env_gate_off_disables_lookup_and_store(
    env_off: None,
    tmp_path: Path,
) -> None:
    """KRAB_TRANSLATION_CACHE_ENABLED=0 → lookup/store no-op."""
    cache = _make_cache(tmp_path)
    cache.store("phrase", "ru", "фраза")
    # Store no-op → ничего не сохранено.
    assert cache.stats()["size"] == 0
    # Lookup тоже всегда None.
    assert cache.lookup("phrase", "ru") is None


def test_clear_removes_entries(env_on: None, tmp_path: Path) -> None:
    """Clear возвращает количество удалённых + persist'ит пустой state."""
    cache = _make_cache(tmp_path)
    cache.store("x", "ru", "Икс")
    cache.store("y", "ru", "Игрек")
    n = cache.clear()
    assert n == 2
    assert cache.stats()["size"] == 0
    assert cache.lookup("x", "ru") is None


def test_load_skips_expired_entries(env_on: None, tmp_path: Path) -> None:
    """Старые entries в JSON не попадают в memory."""
    storage = tmp_path / "translation_cache.json"
    # Записываем вручную: один свежий, один протухший.
    payload = {
        "version": 1,
        "entries": {
            "fresh": {"translation": "fresh-val", "ts": 999_999.0, "hit_count": 0},
            "stale": {"translation": "stale-val", "ts": 1.0, "hit_count": 0},
        },
    }
    storage.write_text(json.dumps(payload), encoding="utf-8")
    clock = {"now": 1_000_000.0}
    cache = TranslationCache(
        storage_path=storage,
        ttl_seconds=100.0,
        now_fn=lambda: clock["now"],
    )
    # Только fresh загружен.
    assert cache.stats()["size"] == 1
