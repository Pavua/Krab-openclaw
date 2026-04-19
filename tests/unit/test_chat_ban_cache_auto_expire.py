# -*- coding: utf-8 -*-
"""
Тесты auto-purge поведения ChatBanCache (Wave 29-TT).

Покрываем:
1. is_banned на истёкшей записи → False + запись удалена из памяти + auto_purged лог.
2. is_banned на активной записи → True + запись не тронута.
3. sweep_expired удаляет только истёкшие, не трогает permanent и активные.
4. sweep_expired возвращает правильный count.
5. periodic_cleanup вызывает sweep (mock).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.chat_ban_cache import ChatBanCache


@pytest.fixture
def cache_at(tmp_path: Path):
    """Фабрика: cache с инжектируемыми часами."""

    def _make(now: datetime) -> ChatBanCache:
        clock = [now]
        return ChatBanCache(
            storage_path=tmp_path / "ban.json",
            now_fn=lambda: clock[0],
        ), clock

    return _make


def _utc(**kwargs) -> datetime:
    return datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc) + timedelta(**kwargs)


# ---------------------------------------------------------------------------
# 1. Expired entry → is_banned=False + auto-purged
# ---------------------------------------------------------------------------

def test_expired_entry_returns_false_and_purged(cache_at, tmp_path):
    t0 = _utc()
    cache, clock = cache_at(t0)

    # Баним с cooldown 1 час
    cache.mark_banned(-100123, "UserBannedInChannel", cooldown_hours=1.0)
    assert cache.is_banned(-100123) is True

    # Прокручиваем часы за expires_at
    clock[0] = _utc(hours=2)

    with patch.object(cache._ChatBanCache__class__ if hasattr(cache, '_ChatBanCache__class__') else type(cache), '_persist_to_disk', wraps=cache._persist_to_disk) if False else __import__('contextlib').nullcontext():
        result = cache.is_banned(-100123)

    assert result is False
    # Запись должна быть вычищена из внутреннего dict
    assert "-100123" not in cache._entries


def test_expired_entry_logs_auto_purged(cache_at):
    t0 = _utc()
    cache, clock = cache_at(t0)
    cache.mark_banned(-999, "ChatWriteForbidden", cooldown_hours=1.0)

    clock[0] = _utc(hours=2)

    with patch("src.core.chat_ban_cache.logger") as mock_logger:
        cache.is_banned(-999)
        mock_logger.info.assert_called_with("chat_ban_cache_auto_purged", chat_id="-999")


# ---------------------------------------------------------------------------
# 2. Active entry remains after is_banned
# ---------------------------------------------------------------------------

def test_active_entry_not_purged(cache_at):
    t0 = _utc()
    cache, clock = cache_at(t0)

    cache.mark_banned(-200, "UserBannedInChannel", cooldown_hours=6.0)
    # Часы не двигаем — запись активна
    assert cache.is_banned(-200) is True
    assert "-200" in cache._entries


# ---------------------------------------------------------------------------
# 3. sweep_expired — удаляет только истёкшие
# ---------------------------------------------------------------------------

def test_sweep_expired_removes_only_expired(cache_at):
    t0 = _utc()
    cache, clock = cache_at(t0)

    cache.mark_banned(-100, "UserBannedInChannel", cooldown_hours=1.0)  # истечёт
    cache.mark_banned(-200, "UserBannedInChannel", cooldown_hours=6.0)  # активен
    cache.mark_banned(-300, "UserBannedInChannel", cooldown_hours=None)  # permanent

    clock[0] = _utc(hours=2)  # -100 истёк, -200 активен, -300 permanent

    count = cache.sweep_expired()

    assert count == 1
    assert "-100" not in cache._entries
    assert "-200" in cache._entries
    assert "-300" in cache._entries


def test_sweep_expired_returns_zero_when_nothing_to_purge(cache_at):
    t0 = _utc()
    cache, clock = cache_at(t0)

    cache.mark_banned(-100, "UserBannedInChannel", cooldown_hours=6.0)
    # Часы не двигаем — ничего не истекло
    count = cache.sweep_expired()
    assert count == 0
    assert "-100" in cache._entries


def test_sweep_expired_multiple_entries(cache_at):
    t0 = _utc()
    cache, clock = cache_at(t0)

    for i in range(5):
        cache.mark_banned(-(100 + i), "UserBannedInChannel", cooldown_hours=1.0)
    # Один активный
    cache.mark_banned(-999, "UserBannedInChannel", cooldown_hours=6.0)

    clock[0] = _utc(hours=2)

    count = cache.sweep_expired()
    assert count == 5
    assert "-999" in cache._entries


# ---------------------------------------------------------------------------
# 4. list_entries не возвращает истёкшие (regression guard)
# ---------------------------------------------------------------------------

def test_list_entries_excludes_expired(cache_at):
    t0 = _utc()
    cache, clock = cache_at(t0)

    cache.mark_banned(-100, "UserBannedInChannel", cooldown_hours=1.0)
    cache.mark_banned(-200, "UserBannedInChannel", cooldown_hours=6.0)

    clock[0] = _utc(hours=2)

    entries = cache.list_entries()
    chat_ids = [e["chat_id"] for e in entries]
    assert "-100" not in chat_ids
    assert "-200" in chat_ids


# ---------------------------------------------------------------------------
# 5. periodic_cleanup вызывает sweep_expired
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_periodic_cleanup_calls_sweep(cache_at):
    t0 = _utc()
    cache, clock = cache_at(t0)

    call_count = [0]
    original_sweep = cache.sweep_expired

    def fake_sweep():
        call_count[0] += 1
        return original_sweep()

    cache.sweep_expired = fake_sweep  # type: ignore[method-assign]

    # Запускаем periodic_cleanup с очень коротким интервалом
    task = asyncio.create_task(cache.periodic_cleanup(interval_seconds=0.05))
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # За 200ms с интервалом 50ms должно быть хотя бы 2 вызова
    assert call_count[0] >= 2
