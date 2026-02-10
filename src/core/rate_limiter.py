# -*- coding: utf-8 -*-
"""
Rate Limiter для Krab v2.5.
Защита от спама / злоупотреблений командами.
Ограничивает количество запросов на пользователя в минуту.
"""

import time
import logging
from collections import defaultdict

logger = logging.getLogger("RateLimiter")

# Дефолтные лимиты
DEFAULT_LIMIT = 10       # запросов
DEFAULT_WINDOW = 60      # секунд (1 минута)


class RateLimiter:
    """
    Скользящее окно для rate limiting.
    Хранит timestamps запросов каждого пользователя.
    """
    
    def __init__(self, limit: int = DEFAULT_LIMIT, window: int = DEFAULT_WINDOW):
        self.limit = limit
        self.window = window
        # user_id -> [timestamp1, timestamp2, ...]
        self._requests = defaultdict(list)
    
    def is_allowed(self, user_id: int) -> bool:
        """
        Проверяет, может ли пользователь отправить ещё один запрос.
        Возвращает True если лимит не превышен.
        """
        now = time.time()
        cutoff = now - self.window
        
        # Убираем устаревшие записи
        self._requests[user_id] = [
            ts for ts in self._requests[user_id] if ts > cutoff
        ]
        
        if len(self._requests[user_id]) >= self.limit:
            logger.warning(f"🚫 Rate limit exceeded for user {user_id}")
            return False
        
        # Регистрируем новый запрос
        self._requests[user_id].append(now)
        return True
    
    def get_remaining(self, user_id: int) -> int:
        """Сколько запросов осталось у пользователя."""
        now = time.time()
        cutoff = now - self.window
        active = [ts for ts in self._requests[user_id] if ts > cutoff]
        return max(0, self.limit - len(active))
    
    def get_reset_time(self, user_id: int) -> float:
        """Через сколько секунд сбросится лимит (до первого освобождения)."""
        if not self._requests[user_id]:
            return 0
        oldest = min(self._requests[user_id])
        return max(0, self.window - (time.time() - oldest))
    
    def get_stats(self) -> dict:
        """Статистика для диагностики."""
        now = time.time()
        cutoff = now - self.window
        active_users = {
            uid: len([ts for ts in timestamps if ts > cutoff])
            for uid, timestamps in self._requests.items()
            if any(ts > cutoff for ts in timestamps)
        }
        return {
            "active_users": len(active_users),
            "limit": self.limit,
            "window_sec": self.window
        }
