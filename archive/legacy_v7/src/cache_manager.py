
import sqlite3
import time
import os
import json
from typing import Optional, Any
from structlog import get_logger
from .config import config

logger = get_logger(__name__)

class CacheManager:
    """
    Simple SQLite-based cache with TTL support.
    """
    def __init__(self, db_name: str = "cache.db"):
        self.db_path = os.path.join(config.BASE_DIR, db_name)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    expires_at REAL
                )
            """)
            conn.commit()

    def get(self, key: str) -> Optional[str]:
        """Retrieve value if exists and not expired"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
            )
            row = cursor.fetchone()
            
            if row:
                value, expires_at = row
                if time.time() < expires_at:
                    logger.debug("cache_hit", key=key)
                    return value
                else:
                    logger.debug("cache_expired", key=key)
                    # Cleanup expired
                    conn.execute("DELETE FROM cache WHERE key = ?", (key,))
                    conn.commit()
            else:
                logger.debug("cache_miss", key=key)
                
        return None

    def set(self, key: str, value: str, ttl: int = 3600):
        """Set value with TTL (seconds)"""
        expires_at = time.time() + ttl
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
                (key, value, expires_at)
            )
            conn.commit()
            logger.debug("cache_set", key=key, ttl=ttl)

    def clear_expired(self):
        """Remove all expired entries"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM cache WHERE expires_at < ?", (time.time(),))
            conn.commit()

# Singleton instance for search cache
search_cache = CacheManager("search_cache.db")
