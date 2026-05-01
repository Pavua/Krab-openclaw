"""
Unit-тесты для src/core/contact_cache.py
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Фикстура: изолированный кэш через переменную окружения
# ---------------------------------------------------------------------------

@pytest.fixture()
def cache_file(tmp_path, monkeypatch):
    """Перенаправляет _CACHE_PATH на временный файл и перезагружает модуль."""
    cache_path = tmp_path / "contact_cache.json"
    monkeypatch.setenv("KRAB_CONTACT_CACHE_PATH", str(cache_path))

    # Удаляем уже загруженный модуль, чтобы _CACHE_PATH пересчитался
    mod_name = "src.core.contact_cache"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    import src.core.contact_cache as cc  # noqa: PLC0415  (local import is intentional)
    yield cc, cache_path

    # Очищаем после теста
    if mod_name in sys.modules:
        del sys.modules[mod_name]


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------

class TestStoreAndLookup:
    def test_store_and_lookup_by_username(self, cache_file):
        cc, _ = cache_file
        cc.store("p0lrd", 123456789, "p0lrd Display")
        result = cc.lookup("p0lrd")
        assert result is not None
        assert result["peer_id"] == 123456789
        assert result["username"] == "p0lrd"

    def test_lookup_case_insensitive(self, cache_file):
        cc, _ = cache_file
        cc.store("p0lrd", 123456789, "p0lrd Display")
        result = cc.lookup("P0LRD")
        assert result is not None
        assert result["peer_id"] == 123456789

    def test_lookup_strips_at(self, cache_file):
        cc, _ = cache_file
        cc.store("p0lrd", 123456789, "p0lrd Display")
        result = cc.lookup("@p0lrd")
        assert result is not None
        assert result["peer_id"] == 123456789

    def test_empty_cache_returns_none(self, cache_file):
        cc, _ = cache_file
        result = cc.lookup("nobody")
        assert result is None


class TestAlias:
    def test_add_alias_and_lookup(self, cache_file):
        cc, _ = cache_file
        cc.store("p0lrd", 123456789, "Алексей")
        added = cc.add_alias(123456789, "Алексей из армии")
        assert added is True

        result = cc.lookup("Алексей из армии")
        assert result is not None
        assert result["peer_id"] == 123456789

    def test_add_alias_not_found_returns_false(self, cache_file):
        cc, _ = cache_file
        # peer_id не существует в кэше
        result = cc.add_alias(999999, "Кто-то")
        assert result is False

    def test_add_alias_no_duplicate(self, cache_file):
        cc, _ = cache_file
        cc.store("p0lrd", 123456789, "Алексей")
        cc.add_alias(123456789, "Леша")
        cc.add_alias(123456789, "Леша")  # второй раз
        result = cc.lookup("p0lrd")
        assert result is not None
        assert result["aliases"].count("Леша") == 1


class TestSearch:
    def test_search_substring(self, cache_file):
        cc, _ = cache_file
        cc.store("alex", 111, "Алексей Иванов")
        cc.store("bob", 222, "Борис")
        results = cc.search("алек")
        usernames = [r["username"] for r in results]
        assert "alex" in usernames
        assert "bob" not in usernames

    def test_search_by_alias_substring(self, cache_file):
        cc, _ = cache_file
        cc.store("p0lrd", 123456789, "Алексей")
        cc.add_alias(123456789, "Алексей из армии")
        results = cc.search("армии")
        assert any(r["peer_id"] == 123456789 for r in results)

    def test_search_empty_query_returns_empty(self, cache_file):
        cc, _ = cache_file
        cc.store("p0lrd", 123456789, "Алексей")
        assert cc.search("") == []


class TestTTL:
    def _write_old_entry(self, cache_path: Path, username: str, peer_id: int) -> None:
        """Записывает запись с устаревшим timestamp (8 дней назад)."""
        old_ts = (datetime.now(UTC) - timedelta(days=8)).isoformat()
        data = {
            username: {
                "peer_id": peer_id,
                "display_name": username,
                "last_resolved_at": old_ts,
                "aliases": [],
            }
        }
        cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_ttl_expiry(self, cache_file):
        cc, cache_path = cache_file
        self._write_old_entry(cache_path, "olduser", 99999)
        result = cc.lookup("olduser")
        assert result is None

    def test_evict_expired(self, cache_file):
        cc, cache_path = cache_file
        # Одна свежая запись
        cc.store("fresh", 111, "Свежий")
        # Одна устаревшая запись — записываем напрямую поверх файла
        data = json.loads(cache_path.read_text())
        old_ts = (datetime.now(UTC) - timedelta(days=8)).isoformat()
        data["olduser"] = {
            "peer_id": 99999,
            "display_name": "Старый",
            "last_resolved_at": old_ts,
            "aliases": [],
        }
        cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        count = cc.evict_expired()
        assert count == 1

        # Свежая запись должна остаться
        assert cc.lookup("fresh") is not None
        assert cc.lookup("olduser") is None

    def test_fresh_entry_not_evicted(self, cache_file):
        cc, _ = cache_file
        cc.store("fresh", 111, "Свежий")
        count = cc.evict_expired()
        assert count == 0
        assert cc.lookup("fresh") is not None


class TestListAll:
    def test_list_all_returns_all_non_expired(self, cache_file):
        cc, cache_path = cache_file
        cc.store("alice", 1, "Alice")
        cc.store("bob", 2, "Bob")

        # Устаревшая запись
        data = json.loads(cache_path.read_text())
        old_ts = (datetime.now(UTC) - timedelta(days=8)).isoformat()
        data["charlie"] = {
            "peer_id": 3,
            "display_name": "Charlie",
            "last_resolved_at": old_ts,
            "aliases": [],
        }
        cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        all_entries = cc.list_all()
        usernames = [e["username"] for e in all_entries]
        assert "alice" in usernames
        assert "bob" in usernames
        assert "charlie" not in usernames

    def test_list_all_empty_cache(self, cache_file):
        cc, _ = cache_file
        assert cc.list_all() == []


class TestPersistence:
    def test_persistence_roundtrip(self, tmp_path, monkeypatch):
        """Сохраняем через первый экземпляр, читаем через второй."""
        cache_path = tmp_path / "contact_cache.json"
        monkeypatch.setenv("KRAB_CONTACT_CACHE_PATH", str(cache_path))

        mod_name = "src.core.contact_cache"

        # Первый «экземпляр»
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        import src.core.contact_cache as cc1  # noqa: PLC0415
        cc1.store("p0lrd", 123456789, "p0lrd Display")
        del sys.modules[mod_name]

        # Второй «экземпляр» — свежая загрузка модуля
        import src.core.contact_cache as cc2  # noqa: PLC0415
        result = cc2.lookup("p0lrd")
        assert result is not None
        assert result["peer_id"] == 123456789

        del sys.modules[mod_name]
