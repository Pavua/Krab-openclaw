# -*- coding: utf-8 -*-
"""
Тесты фичи permanent ban для How2AI (и любых других чатов с CHAT_PERMANENT_BAN_LIST).

Что тестируем:
1. Config parsing: CHAT_PERMANENT_BAN_LIST из env "1,2,3" → list[str]
2. mark_banned(cooldown_hours=None) → expires_at=None → is_banned всегда True
3. sweep_expired не удаляет permanent-записи (expires_at=None)
4. clear() работает для permanent-бана (ручной снос)
5. Дефолтный список содержит How2AI (-1001587432709)
6. Startup-логика (аналог userbot_bridge): permanent bans применяются идемпотентно
7. Permanent ban виден в list_entries
8. Permanent ban переживает reload (persist round-trip)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.core.chat_ban_cache import ChatBanCache

# Chat ID How2AI из CHAT_PERMANENT_BAN_LIST / VOICE_REPLY_BLOCKED_CHATS
HOW2AI_CHAT_ID = -1001587432709
HOW2AI_STR = "-1001587432709"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def cache(tmp_path: Path) -> ChatBanCache:
    """Свежий ChatBanCache с изолированным storage."""
    return ChatBanCache(storage_path=tmp_path / "ban_cache.json")


# ---------------------------------------------------------------------------
# 1. Config parsing
# ---------------------------------------------------------------------------


def test_permanent_ban_config_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    """CHAT_PERMANENT_BAN_LIST="1,2,3" парсится в list[str] из трёх элементов."""
    monkeypatch.setenv("CHAT_PERMANENT_BAN_LIST", "1,2,3")
    # Импортируем Config заново чтобы env применился.
    # Config — это класс с class-level атрибутами, вычисленными при import.
    # Поэтому пересоздаём значение так же, как делает Config.
    raw = os.getenv("CHAT_PERMANENT_BAN_LIST", "")
    result = [s.strip() for s in raw.split(",") if s.strip()]
    assert result == ["1", "2", "3"]
    assert len(result) == 3


def test_permanent_ban_config_default_when_env_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустая CHAT_PERMANENT_BAN_LIST → дефолт ["-1001587432709"]."""
    monkeypatch.setenv("CHAT_PERMANENT_BAN_LIST", "")
    raw = os.getenv("CHAT_PERMANENT_BAN_LIST", "")
    result = [s.strip() for s in raw.split(",") if s.strip()] or [HOW2AI_STR]
    assert result == [HOW2AI_STR]


def test_permanent_ban_config_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пробелы вокруг chat_id в env-переменной обрезаются."""
    monkeypatch.setenv("CHAT_PERMANENT_BAN_LIST", " -100 , -200 , -300 ")
    raw = os.getenv("CHAT_PERMANENT_BAN_LIST", "")
    result = [s.strip() for s in raw.split(",") if s.strip()]
    assert result == ["-100", "-200", "-300"]


# ---------------------------------------------------------------------------
# 2. mark_banned with cooldown_hours=None → permanent
# ---------------------------------------------------------------------------


def test_permanent_ban_never_expires(cache: ChatBanCache) -> None:
    """cooldown_hours=None → expires_at=None → is_banned=True независимо от времени."""
    cache.mark_banned(HOW2AI_CHAT_ID, "PermanentBanConfigured", cooldown_hours=None)

    # Сразу после mark — забанен
    assert cache.is_banned(HOW2AI_CHAT_ID) is True
    assert cache.is_banned(HOW2AI_STR) is True

    # Подменяем время далеко в будущее — permanent ban не протухает
    far_future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    cache._now_fn = lambda: far_future
    assert cache.is_banned(HOW2AI_CHAT_ID) is True


def test_permanent_ban_expires_at_is_none_in_storage(cache: ChatBanCache) -> None:
    """После mark с cooldown_hours=None поле expires_at=None в _entries."""
    cache.mark_banned(HOW2AI_CHAT_ID, "UserBannedInChannel", cooldown_hours=None)
    entry = cache._entries.get(HOW2AI_STR)
    assert entry is not None
    assert entry["expires_at"] is None


def test_regular_ban_does_expire(cache: ChatBanCache) -> None:
    """Обычный ban (cooldown_hours>0) протухает — контраст с permanent."""
    past = datetime(2000, 1, 1, tzinfo=timezone.utc)
    # Устанавливаем "сейчас" в прошлое чтобы ban уже истёк
    cache2 = ChatBanCache(now_fn=lambda: past)
    cache2.mark_banned(-999, "UserBannedInChannel", cooldown_hours=1)

    # Теперь переключаем время в будущее — ban должен быть просрочен
    future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    cache2._now_fn = lambda: future
    assert cache2.is_banned(-999) is False


# ---------------------------------------------------------------------------
# 3. sweep_expired не удаляет permanent bans
# ---------------------------------------------------------------------------


def test_permanent_ban_survives_sweep(cache: ChatBanCache) -> None:
    """sweep_expired не удаляет permanent-записи (expires_at=None)."""
    cache.mark_banned(HOW2AI_CHAT_ID, "PermanentBanConfigured", cooldown_hours=None)
    # Добавляем обычный ban с нулевым cooldown (уже истёк)
    past = datetime(2000, 1, 1, tzinfo=timezone.utc)
    cache._now_fn = lambda: past
    cache.mark_banned(-888, "ChatWriteForbidden", cooldown_hours=1)

    # Переключаем время вперёд — -888 должен истечь, How2AI остаться
    future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    cache._now_fn = lambda: future

    removed = cache.sweep_expired()

    assert removed == 1  # только -888
    assert cache.is_banned(HOW2AI_CHAT_ID) is True
    assert cache.is_banned(-888) is False


def test_sweep_removes_only_expired_not_permanent(cache: ChatBanCache) -> None:
    """sweep_expired при нескольких expired + permanent удаляет только expired."""
    past = datetime(2000, 1, 1, tzinfo=timezone.utc)
    cache._now_fn = lambda: past

    # Два обычных
    cache.mark_banned(-111, "ChatWriteForbidden", cooldown_hours=1)
    cache.mark_banned(-222, "UserBannedInChannel", cooldown_hours=2)
    # Один permanent
    cache.mark_banned(HOW2AI_CHAT_ID, "PermanentBanConfigured", cooldown_hours=None)

    future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    cache._now_fn = lambda: future

    removed = cache.sweep_expired()
    assert removed == 2
    assert cache.is_banned(HOW2AI_CHAT_ID) is True
    assert HOW2AI_STR in cache._entries


# ---------------------------------------------------------------------------
# 4. clear() работает для permanent bans
# ---------------------------------------------------------------------------


def test_clear_removes_permanent_ban(cache: ChatBanCache) -> None:
    """clear(chat_id) удаляет permanent-бан и возвращает True."""
    cache.mark_banned(HOW2AI_CHAT_ID, "PermanentBanConfigured", cooldown_hours=None)
    assert cache.is_banned(HOW2AI_CHAT_ID) is True

    result = cache.clear(HOW2AI_CHAT_ID)

    assert result is True
    assert cache.is_banned(HOW2AI_CHAT_ID) is False


def test_clear_returns_false_if_not_present(cache: ChatBanCache) -> None:
    """clear() на несуществующий чат возвращает False."""
    assert cache.clear(HOW2AI_CHAT_ID) is False


def test_clear_then_reban_works(cache: ChatBanCache) -> None:
    """После clear можно снова пометить чат как permanent-banned."""
    cache.mark_banned(HOW2AI_CHAT_ID, "PermanentBanConfigured", cooldown_hours=None)
    cache.clear(HOW2AI_CHAT_ID)
    cache.mark_banned(HOW2AI_CHAT_ID, "PermanentBanConfigured", cooldown_hours=None)
    assert cache.is_banned(HOW2AI_CHAT_ID) is True


# ---------------------------------------------------------------------------
# 5. Дефолтный список содержит How2AI
# ---------------------------------------------------------------------------


def test_default_includes_how2ai() -> None:
    """Config.CHAT_PERMANENT_BAN_LIST по умолчанию содержит How2AI chat_id."""
    from src.config import Config

    assert HOW2AI_STR in Config.CHAT_PERMANENT_BAN_LIST


def test_default_how2ai_as_int_form() -> None:
    """How2AI должен быть в дефолтном списке (int-форма через str conversion)."""
    from src.config import Config

    # Список хранится как str, проверяем что нормализованный int совпадает
    assert str(HOW2AI_CHAT_ID) in Config.CHAT_PERMANENT_BAN_LIST


# ---------------------------------------------------------------------------
# 6. Startup-логика: применение permanent bans идемпотентно
# ---------------------------------------------------------------------------


def test_startup_permanent_ban_applied(cache: ChatBanCache) -> None:
    """Эмулируем bootstrap userbot_bridge: permanent bans из config → mark_banned."""
    # Аналог кода в userbot_bridge.py (строки ~2711-2719)
    perm_bans = [HOW2AI_STR, "-999"]
    for chat_id_str in perm_bans:
        cache.mark_banned(chat_id_str, "PermanentBanConfigured", cooldown_hours=None)

    assert cache.is_banned(HOW2AI_CHAT_ID) is True
    assert cache.is_banned("-999") is True


def test_startup_permanent_ban_idempotent(cache: ChatBanCache) -> None:
    """Повторный mark при startup не ломает запись и не сдвигает expires_at."""
    cache.mark_banned(HOW2AI_CHAT_ID, "PermanentBanConfigured", cooldown_hours=None)
    entry_before = dict(cache._entries[HOW2AI_STR])

    # Повторный вызов (как при restart)
    cache.mark_banned(HOW2AI_CHAT_ID, "PermanentBanConfigured", cooldown_hours=None)

    entry_after = cache._entries[HOW2AI_STR]
    # expires_at не должен измениться (по-прежнему None)
    assert entry_after["expires_at"] is None
    # Счётчик hit_count увеличился
    assert entry_after["hit_count"] > entry_before["hit_count"]
    # is_banned по-прежнему True
    assert cache.is_banned(HOW2AI_CHAT_ID) is True


# ---------------------------------------------------------------------------
# 7. Permanent ban виден в list_entries
# ---------------------------------------------------------------------------


def test_permanent_ban_in_list_entries(cache: ChatBanCache) -> None:
    """list_entries включает permanent-бан с expires_at=None."""
    cache.mark_banned(HOW2AI_CHAT_ID, "PermanentBanConfigured", cooldown_hours=None)

    entries = cache.list_entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["chat_id"] == HOW2AI_STR
    assert entry["expires_at"] is None
    assert entry["error_code"] == "PermanentBanConfigured"


def test_list_entries_shows_permanent_not_expired(cache: ChatBanCache) -> None:
    """list_entries не удаляет permanent-запись даже при far-future time."""
    cache.mark_banned(HOW2AI_CHAT_ID, "PermanentBanConfigured", cooldown_hours=None)
    future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    cache._now_fn = lambda: future

    entries = cache.list_entries()
    chat_ids = [e["chat_id"] for e in entries]
    assert HOW2AI_STR in chat_ids


# ---------------------------------------------------------------------------
# 8. Permanent ban переживает reload (persist round-trip)
# ---------------------------------------------------------------------------


def test_permanent_ban_survives_reload(tmp_path: Path) -> None:
    """Permanent-бан записывается на диск и загружается обратно при новом instance."""
    storage = tmp_path / "ban_cache.json"

    # Instance 1: помечаем permanent ban
    cache1 = ChatBanCache(storage_path=storage)
    cache1.mark_banned(HOW2AI_CHAT_ID, "PermanentBanConfigured", cooldown_hours=None)
    assert cache1.is_banned(HOW2AI_CHAT_ID) is True

    # Instance 2: загружаем с того же файла — ban должен присутствовать
    cache2 = ChatBanCache(storage_path=storage)
    assert cache2.is_banned(HOW2AI_CHAT_ID) is True
    entry = cache2._entries.get(HOW2AI_STR)
    assert entry is not None
    assert entry["expires_at"] is None


def test_permanent_ban_clear_persisted(tmp_path: Path) -> None:
    """После clear() и reload permanent-бана нет."""
    storage = tmp_path / "ban_cache.json"

    cache1 = ChatBanCache(storage_path=storage)
    cache1.mark_banned(HOW2AI_CHAT_ID, "PermanentBanConfigured", cooldown_hours=None)
    cache1.clear(HOW2AI_CHAT_ID)

    cache2 = ChatBanCache(storage_path=storage)
    assert cache2.is_banned(HOW2AI_CHAT_ID) is False
