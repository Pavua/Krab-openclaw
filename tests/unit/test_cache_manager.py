
import pytest
import os
import time
import sqlite3
from src.cache_manager import CacheManager

class TestCacheManager:
    @pytest.fixture
    def cache(self, tmp_path):
        # Create a temporary DB
        db_path = tmp_path / "test_cache.db"
        # We need to monkeypath config.BASE_DIR or just init CacheManager with full path if possible?
        # CacheManager takes db_name and joins with config.BASE_DIR.
        # Let's mock config.BASE_DIR
        
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
