# -*- coding: utf-8 -*-
"""
Кэш с TTL (Фаза 6 — Производительность).

Бэкенд вынесен в методы _backend_* для последующей замены на Redis
без изменения публичного API (get/set/clear_expired).

Почему хранилище в ~/.openclaw/krab_runtime_state/:
- файлы в корне проекта принадлежат тому пользователю, кто создал репо;
- при запуске под другим macOS аккаунтом корень проекта = read-only для "others";
- ~/.openclaw/krab_runtime_state/ — user-specific runtime каталог, всегда rw для текущего юзера.
"""

import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

from src.core.exceptions import CacheError
from src.core.logger import get_logger

from .config import config

logger = get_logger(__name__)

# TTL по умолчанию для ответов (1 час), чтобы не раздувать хранилище
DEFAULT_TTL_SECONDS = 3600

# Корневая директория для cache DB — user-specific, избегаем readonly-проблем при
# запуске под другим macOS аккаунтом, чем владелец репозитория.
_CACHE_DIR = Path.home() / ".openclaw" / "krab_runtime_state"
# Fallback: если HOME недоступен (другая учётка без прав), используем /tmp
_CACHE_DIR_FALLBACK = Path("/tmp") / "krab_runtime_cache"


def _resolve_cache_path(db_name: str) -> str:
    """
    Возвращает абсолютный путь к cache DB.
    Директория создаётся автоматически если не существует.

    Fallback-стратегия: если ~/.openclaw недоступна (например, запуск от другой
    macOS учётки без прав), используем /tmp/krab_runtime_cache — это лучше чем краш.
    /tmp-кэш эфемерен, но поддерживает работу бота без ошибок.
    """
    for cache_dir in (_CACHE_DIR, _CACHE_DIR_FALLBACK):
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            # Пробуем записать тестовый файл, чтобы убедиться в реальном rw-доступе
            test_path = cache_dir / ".write_test"
            test_path.write_text("ok")
            test_path.unlink(missing_ok=True)
            return str(cache_dir / db_name)
        except (PermissionError, OSError):
            continue
    # Если оба варианта недоступны — возвращаем /tmp (будет ошибка при connect, но не при импорте)
    return str(_CACHE_DIR_FALLBACK / db_name)


class CacheManager:
    """
    Кэш с TTL. Текущая реализация — SQLite; логика чтения/записи в _backend_*
    для лёгкой замены на Redis.
    """

    def __init__(self, db_name: str = "cache.db"):
        self.db_path = _resolve_cache_path(db_name)
        self._init_db()
        self.clear_expired()

    def _init_db(self) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS cache (
                        key TEXT PRIMARY KEY,
                        value TEXT,
                        expires_at REAL
                    )
                """)
                conn.commit()
        except sqlite3.Error as e:
            logger.error("cache_init_failed", path=self.db_path, error=str(e))
            raise CacheError(f"Cache init failed: {e}", retryable=True) from e

    # --- Backend abstraction (подмена на Redis позже) ---

    def _backend_get(self, key: str) -> Optional[tuple[str, float]]:
        """Возвращает (value, expires_at) или None. Не логирует промахи."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
            )
            row = cursor.fetchone()
            return (row[0], row[1]) if row else None

    def _backend_set(self, key: str, value: str, expires_at: float) -> None:
        """Сохраняет запись с временем истечения."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
                (key, value, expires_at),
            )
            conn.commit()

    def _backend_delete(self, key: str) -> None:
        """Удаляет одну запись."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            conn.commit()

    def _backend_clear_expired(self) -> None:
        """Удаляет все просроченные записи."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM cache WHERE expires_at < ?", (time.time(),))
            conn.commit()

    # --- Public API ---

    def get(self, key: str) -> Optional[str]:
        """Возвращает значение, если ключ есть и не истёк. При ошибке — None."""
        try:
            row = self._backend_get(key)
            if not row:
                logger.debug("cache_miss", key=key)
                return None
            value, expires_at = row
            if time.time() < expires_at:
                logger.debug("cache_hit", key=key)
                return value
            logger.debug("cache_expired", key=key)
            self._backend_delete(key)
            return None
        except sqlite3.Error as e:
            logger.warning("cache_get_error", key=key, error=str(e))
            return None

    def set(self, key: str, value: str, ttl: int = DEFAULT_TTL_SECONDS) -> None:
        """Сохраняет значение с TTL в секундах. При ошибке бросает CacheError."""
        expires_at = time.time() + ttl
        try:
            self._backend_set(key, value, expires_at)
            logger.debug("cache_set", key=key, ttl=ttl)
        except sqlite3.Error as e:
            logger.error("cache_set_error", key=key, error=str(e))
            raise CacheError(f"Cache set failed: {e}", retryable=True) from e

    def clear_expired(self) -> None:
        """Удаляет все просроченные записи. Вызывается при старте и при необходимости."""
        try:
            self._backend_clear_expired()
        except sqlite3.Error as e:
            logger.warning("cache_clear_expired_error", error=str(e))


    def delete(self, key: str) -> None:
        """Удаляет запись по ключу. При ошибке молча логирует."""
        try:
            self._backend_delete(key)
            logger.debug("cache_delete", key=key)
        except sqlite3.Error as e:
            logger.warning("cache_delete_error", key=key, error=str(e))


# TTL для истории чатов (24 часа) — переживает рестарт бота
HISTORY_CACHE_TTL = 86400

# Singleton для кэша поиска
search_cache = CacheManager("search_cache.db")

# Singleton для кэша истории диалогов
history_cache = CacheManager("history_cache.db")
