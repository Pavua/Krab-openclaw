# -*- coding: utf-8 -*-
"""Тесты expiry-поведения ChatBanCache.

Проверяем:
- is_banned возвращает True внутри окна
- is_banned возвращает False после истечения + запись удаляется из dict
- prune_expired удаляет истёкшие пакетом, оставляет живые, возвращает count
- потокобезопасность (RLock уже есть в реализации — smoke-тест с threads)
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from src.core.chat_ban_cache import ChatBanCache

# ---------------------------------------------------------------------------
# Вспомогательный factory
# ---------------------------------------------------------------------------


def _make_cache(now: datetime) -> ChatBanCache:
    """ChatBanCache без диска, с фиксированным now."""
    return ChatBanCache(storage_path=None, now_fn=lambda: now)


# ---------------------------------------------------------------------------
# is_banned внутри окна → True
# ---------------------------------------------------------------------------


def test_is_banned_true_inside_window():
    now = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)
    cache = _make_cache(now)
    cache.mark_banned("-100123", "UserBannedInChannel", cooldown_hours=6.0)
    assert cache.is_banned("-100123") is True


# ---------------------------------------------------------------------------
# is_banned после истечения → False + запись удаляется из dict
# ---------------------------------------------------------------------------


def test_is_banned_false_after_expiry_and_entry_removed():
    ban_time = datetime(2026, 4, 21, 9, 0, 0, tzinfo=timezone.utc)
    cache = _make_cache(ban_time)
    cache.mark_banned("-100456", "UserBannedInChannel", cooldown_hours=1.0)

    # Перематываем время — баним на 1 час, проверяем через 3 часа
    check_time = ban_time + timedelta(hours=3)
    cache._now_fn = lambda: check_time  # type: ignore[method-assign]

    assert cache.is_banned("-100456") is False
    # Запись должна быть удалена из in-memory dict
    assert "-100456" not in cache._entries


# ---------------------------------------------------------------------------
# prune_expired: удаляет истёкшие пакетом, сохраняет живые, возвращает count
# ---------------------------------------------------------------------------


def test_prune_expired_removes_batch_keeps_live():
    now = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)
    cache = _make_cache(now)

    # Помечаем 3 чата с коротким cooldown и 2 — с длинным
    for cid in ["-1001", "-1002", "-1003"]:
        cache.mark_banned(cid, "ChatWriteForbidden", cooldown_hours=0.001)  # ~3.6 сек
    for cid in ["-2001", "-2002"]:
        cache.mark_banned(cid, "UserBannedInChannel", cooldown_hours=24.0)

    # Перематываем время: первые 3 уже истекли, последние 2 — нет
    cache._now_fn = lambda: now + timedelta(hours=1)  # type: ignore[method-assign]

    pruned = cache.prune_expired()

    assert pruned == 3
    assert "-1001" not in cache._entries
    assert "-1002" not in cache._entries
    assert "-1003" not in cache._entries
    assert "-2001" in cache._entries
    assert "-2002" in cache._entries


def test_prune_expired_returns_zero_when_nothing_expired():
    now = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)
    cache = _make_cache(now)
    cache.mark_banned("-9001", "ChatWriteForbidden", cooldown_hours=6.0)
    # Не двигаем время — всё живое
    assert cache.prune_expired() == 0
    assert "-9001" in cache._entries


# ---------------------------------------------------------------------------
# permanent ban (cooldown_hours=None) не затрагивается prune_expired
# ---------------------------------------------------------------------------


def test_prune_expired_skips_permanent_bans():
    now = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)
    cache = _make_cache(now)
    cache.mark_banned("-777", "ChannelPrivate", cooldown_hours=None)

    # Перематываем далеко вперёд
    cache._now_fn = lambda: now + timedelta(days=365)  # type: ignore[method-assign]

    pruned = cache.prune_expired()
    assert pruned == 0
    assert "-777" in cache._entries


# ---------------------------------------------------------------------------
# Потокобезопасность: concurrent is_banned + prune_expired не роняют
# ---------------------------------------------------------------------------


def test_concurrent_is_banned_and_prune_expired_thread_safe():
    ban_time = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)
    cache = _make_cache(ban_time)

    for i in range(20):
        cache.mark_banned(f"-{i}", "UserBannedInChannel", cooldown_hours=0.001)

    # Перематываем — все истекли
    cache._now_fn = lambda: ban_time + timedelta(hours=1)  # type: ignore[method-assign]

    errors: list[Exception] = []

    def reader():
        try:
            for i in range(20):
                cache.is_banned(f"-{i}")
        except Exception as e:
            errors.append(e)

    def pruner():
        try:
            cache.prune_expired()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    threads += [threading.Thread(target=pruner) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert errors == [], f"Thread errors: {errors}"
