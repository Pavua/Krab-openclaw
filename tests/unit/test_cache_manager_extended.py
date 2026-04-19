# -*- coding: utf-8 -*-
"""
Расширенные тесты для CacheManager.
Покрывают: граничные значения TTL, сериализацию, вытеснение,
множественные ключи, нулевой/минимальный TTL, unicode, binary-like строки.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import unittest.mock as mock

import pytest

from src.cache_manager import DEFAULT_TTL_SECONDS, CacheManager
from src.core.exceptions import CacheError

# ---------------------------------------------------------------------------
# Фикстура: изолированный кэш в tmp_path
# ---------------------------------------------------------------------------


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """Изолированный экземпляр CacheManager с базой во временной директории."""
    import src.cache_manager as cm_mod

    # Переопределяем _CACHE_DIR, чтобы тест не писал в ~/.openclaw
    monkeypatch.setattr(cm_mod, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(cm_mod, "_CACHE_DIR_FALLBACK", tmp_path / "fallback")
    return CacheManager("ext_test.db")


# ---------------------------------------------------------------------------
# 1. Граничный TTL: значение сразу истекает при ttl=0
# ---------------------------------------------------------------------------


class TestTTLEdgeCases:
    def test_zero_ttl_expires_immediately(self, cache):
        """TTL=0 — запись считается просроченной при первом же обращении."""
        cache.set("zero", "v", ttl=0)
        # После истечения get должен вернуть None
        assert cache.get("zero") is None

    def test_negative_ttl_treated_as_expired(self, cache):
        """Отрицательный TTL — expires_at в прошлом, результат None."""
        cache.set("neg", "v", ttl=-1)
        assert cache.get("neg") is None

    def test_very_short_ttl_race(self, cache):
        """TTL=1с: до истечения — значение есть; после — None."""
        cache.set("short", "alive", ttl=1)
        assert cache.get("short") == "alive"
        time.sleep(1.1)
        assert cache.get("short") is None

    def test_default_ttl_constant_used(self, cache):
        """set() без явного ttl использует DEFAULT_TTL_SECONDS."""
        cache.set("default_ttl_key", "hello")
        row = cache._backend_get("default_ttl_key")
        assert row is not None
        value, expires_at = row
        expected = time.time() + DEFAULT_TTL_SECONDS
        # Погрешность ±2 секунды допустима
        assert abs(expires_at - expected) < 2


# ---------------------------------------------------------------------------
# 2. Сериализация: unicode, спецсимволы, длинные строки
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_unicode_value(self, cache):
        """Unicode (кириллица, эмодзи) должен сохраняться и читаться без искажений."""
        value = "Привет 🦀 мир — тест"
        cache.set("unicode_key", value, ttl=60)
        assert cache.get("unicode_key") == value

    def test_unicode_key(self, cache):
        """Ключ на кириллице тоже должен работать корректно."""
        cache.set("ключ_🦀", "значение", ttl=60)
        assert cache.get("ключ_🦀") == "значение"

    def test_empty_string_value(self, cache):
        """Пустая строка — допустимое значение (не None)."""
        cache.set("empty_val", "", ttl=60)
        result = cache.get("empty_val")
        assert result == ""

    def test_json_like_string(self, cache):
        """JSON-строка сохраняется как есть без экранирования."""
        json_str = '{"key": "value", "list": [1, 2, 3]}'
        cache.set("json_key", json_str, ttl=60)
        assert cache.get("json_key") == json_str

    def test_newlines_in_value(self, cache):
        """Значения с переводами строк не должны обрезаться."""
        multiline = "line1\nline2\nline3"
        cache.set("ml_key", multiline, ttl=60)
        assert cache.get("ml_key") == multiline


# ---------------------------------------------------------------------------
# 3. Вытеснение и очистка (eviction / clear_expired)
# ---------------------------------------------------------------------------


class TestEviction:
    def test_clear_expired_removes_multiple_stale(self, cache):
        """clear_expired удаляет сразу несколько просроченных записей."""
        for i in range(5):
            cache.set(f"stale_{i}", f"v{i}", ttl=1)
        time.sleep(1.1)
        cache.clear_expired()
        for i in range(5):
            assert cache.get(f"stale_{i}") is None

    def test_clear_expired_preserves_fresh_entries(self, cache):
        """clear_expired не трогает записи с актуальным TTL."""
        # Просроченные
        for i in range(3):
            cache.set(f"old_{i}", "gone", ttl=1)
        # Свежие
        for i in range(3):
            cache.set(f"new_{i}", f"keep_{i}", ttl=60)
        time.sleep(1.1)
        cache.clear_expired()
        for i in range(3):
            assert cache.get(f"new_{i}") == f"keep_{i}"

    def test_get_auto_deletes_expired_entry(self, cache):
        """get() на просроченной записи удаляет её из БД (lazy eviction)."""
        cache.set("lazy", "data", ttl=1)
        time.sleep(1.1)
        assert cache.get("lazy") is None
        # Проверяем, что запись физически удалена из SQLite
        row = cache._backend_get("lazy")
        assert row is None


# ---------------------------------------------------------------------------
# 4. Множественные ключи и изоляция
# ---------------------------------------------------------------------------


class TestMultipleKeys:
    def test_independent_keys_isolated(self, cache):
        """Разные ключи независимы — изменение одного не влияет на другой."""
        cache.set("a", "1", ttl=60)
        cache.set("b", "2", ttl=60)
        cache.delete("a")
        assert cache.get("a") is None
        assert cache.get("b") == "2"

    def test_same_key_overwrite_idempotent(self, cache):
        """Множественная перезапись одного ключа оставляет последнее значение."""
        for i in range(10):
            cache.set("overwrite_me", str(i), ttl=60)
        assert cache.get("overwrite_me") == "9"


# ---------------------------------------------------------------------------
# 5. Обработка ошибок и деградация
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_get_returns_none_on_db_error(self, cache):
        """get() не бросает исключений при ошибке SQLite, возвращает None."""
        with mock.patch.object(cache, "_backend_get", side_effect=sqlite3.Error("boom")):
            result = cache.get("any_key")
        assert result is None

    def test_set_raises_cache_error_on_db_error(self, cache):
        """set() бросает CacheError при ошибке БД."""
        with mock.patch.object(cache, "_backend_set", side_effect=sqlite3.Error("write fail")):
            with pytest.raises(CacheError):
                cache.set("fail_key", "val", ttl=60)

    def test_delete_silent_on_db_error(self, cache):
        """delete() не бросает исключений при ошибке SQLite — молча логирует."""
        with mock.patch.object(cache, "_backend_delete", side_effect=sqlite3.Error("del fail")):
            cache.delete("any")  # не должно бросить

    def test_clear_expired_silent_on_db_error(self, cache):
        """clear_expired() при ошибке SQLite не падает."""
        with mock.patch.object(
            cache, "_backend_clear_expired", side_effect=sqlite3.Error("clean fail")
        ):
            cache.clear_expired()  # не должно бросить


# ---------------------------------------------------------------------------
# 6. Конкурентный доступ с верификацией данных
# ---------------------------------------------------------------------------


class TestConcurrentAccess:
    def test_concurrent_writes_no_data_loss(self, cache):
        """Параллельные write-треды: все значения сохраняются, нет гонок."""
        errors: list[Exception] = []
        written: dict[str, str] = {}

        def writer(i: int) -> None:
            key = f"cw_{i}"
            val = f"val_{i}"
            written[key] = val
            try:
                cache.set(key, val, ttl=60)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Ошибки записи: {errors}"
        # Все записанные ключи должны читаться корректно
        for key, val in written.items():
            assert cache.get(key) == val
