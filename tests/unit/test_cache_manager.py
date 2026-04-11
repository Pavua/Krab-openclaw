from __future__ import annotations

import time

import pytest

from src.cache_manager import CacheManager


class TestCacheManager:
    @pytest.fixture
    def cache(self, tmp_path):
        with pytest.MonkeyPatch.context() as m:
            from src import config as config_module

            m.setattr(config_module.config, "BASE_DIR", str(tmp_path))

            manager = CacheManager("test_cache.db")
            yield manager

    def test_set_get(self, cache):
        cache.set("foo", "bar", ttl=60)
        assert cache.get("foo") == "bar"

    def test_expiry(self, cache):
        cache.set("quick", "gone", ttl=1)
        time.sleep(1.1)
        assert cache.get("quick") is None

    def test_update(self, cache):
        cache.set("key", "val1", ttl=60)
        assert cache.get("key") == "val1"
        cache.set("key", "val2", ttl=60)
        assert cache.get("key") == "val2"

    def test_miss(self, cache):
        assert cache.get("nonexistent") is None

    def test_delete_removes_key(self, cache):
        cache.set("to_delete", "value", ttl=60)
        assert cache.get("to_delete") == "value"
        cache.delete("to_delete")
        assert cache.get("to_delete") is None

    def test_delete_nonexistent_key_no_error(self, cache):
        # Удаление несуществующего ключа не должно бросать исключение
        cache.delete("ghost_key")

    def test_clear_expired_removes_stale(self, cache):
        cache.set("stale", "data", ttl=1)
        time.sleep(1.1)
        # Просроченная запись в SQLite ещё есть, clear_expired должна убрать её
        cache.clear_expired()
        # После очистки get тоже должен вернуть None
        assert cache.get("stale") is None

    def test_clear_expired_keeps_fresh(self, cache):
        cache.set("fresh", "alive", ttl=60)
        cache.clear_expired()
        assert cache.get("fresh") == "alive"

    def test_large_value(self, cache):
        big = "x" * 100_000
        cache.set("big_key", big, ttl=60)
        assert cache.get("big_key") == big

    def test_concurrent_access(self, cache):
        import threading

        errors: list[Exception] = []

        def writer(i: int) -> None:
            try:
                cache.set(f"thread_key_{i}", f"val_{i}", ttl=60)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def reader(i: int) -> None:
            try:
                cache.get(f"thread_key_{i}")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
        threads += [threading.Thread(target=reader, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Ошибки при конкурентном доступе: {errors}"

    def test_overwrite_resets_ttl(self, cache):
        cache.set("renew", "old", ttl=1)
        time.sleep(0.5)
        cache.set("renew", "new", ttl=60)
        time.sleep(0.7)
        # Старый TTL уже истёк бы, но после перезаписи должен быть свежим
        assert cache.get("renew") == "new"
