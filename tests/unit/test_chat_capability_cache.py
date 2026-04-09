# -*- coding: utf-8 -*-
"""
Регрессии `src/core/chat_capability_cache.py` — persisted per-chat capability
cache (slow_mode_seconds + voice/text permissions).

Что тестируем:

1. **Empty cache** возвращает None для всех читающих API.
2. **upsert + read** round-trip: записали → get/is_voice_allowed/get_slow_mode.
3. **TTL expiry** через подставленный `fetched_at` в прошлом.
4. **upsert_from_chat** с разными вариантами pyrogram Chat:
   - `slow_mode_delay` + `permissions.can_send_voices` (explicit True/False)
   - `can_send_media_messages` как fallback когда `can_send_voices` нет
   - Permissions полностью отсутствует → voice_allowed=None
5. **Persistence round-trip**: upsert → new instance с тем же path → state.
6. **Malformed JSON** не ронит при загрузке.
7. **invalidate** убирает запись.
8. **list_entries** возвращает только активные (не истёкшие).
9. **is_voice_allowed** возвращает True/False/None правильно — важно чтобы
   caller мог отличить «не знаю» от «точно нельзя».
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.chat_capability_cache import (
    ChatCapabilityCache,
    chat_capability_cache,
)


@pytest.fixture
def cache(tmp_path: Path) -> ChatCapabilityCache:
    return ChatCapabilityCache(storage_path=tmp_path / "chat_capability_cache.json")


def test_empty_cache_returns_none(cache: ChatCapabilityCache) -> None:
    assert cache.get(-1001587432709) is None
    assert cache.is_voice_allowed(-1001587432709) is None
    assert cache.get_slow_mode_seconds(-1001587432709) is None
    assert cache.list_entries() == []


def test_upsert_and_read_round_trip(cache: ChatCapabilityCache) -> None:
    cache.upsert(
        -100,
        slow_mode_seconds=10,
        voice_allowed=False,
        text_allowed=True,
        chat_type="SUPERGROUP",
    )
    entry = cache.get(-100)
    assert entry is not None
    assert entry["slow_mode_seconds"] == 10
    assert entry["voice_allowed"] is False
    assert entry["text_allowed"] is True
    assert entry["chat_type"] == "SUPERGROUP"
    # Convenience accessors
    assert cache.is_voice_allowed(-100) is False
    assert cache.get_slow_mode_seconds(-100) == 10


def test_is_voice_allowed_distinguishes_true_false_none(cache: ChatCapabilityCache) -> None:
    """
    Caller'у критически важно отличать «Telegram явно сказал нельзя»
    от «данных пока нет». `None` → неизвестно, default разрешать.
    """
    cache.upsert(-10, slow_mode_seconds=None, voice_allowed=True, text_allowed=True)
    cache.upsert(-20, slow_mode_seconds=None, voice_allowed=False, text_allowed=True)
    cache.upsert(-30, slow_mode_seconds=None, voice_allowed=None, text_allowed=True)

    assert cache.is_voice_allowed(-10) is True
    assert cache.is_voice_allowed(-20) is False
    assert cache.is_voice_allowed(-30) is None  # explicitly unknown
    assert cache.is_voice_allowed(-999) is None  # no entry at all


def test_ttl_expiry_returns_none_and_evicts(tmp_path: Path) -> None:
    """
    Fake clock: upsert в t0, потом «переводим часы» на 48h вперёд (за TTL 24h).
    Public API only — без прямой мутации `_entries`.
    """
    clock = [datetime(2026, 4, 9, 12, 0, 0, tzinfo=timezone.utc)]
    cache = ChatCapabilityCache(
        storage_path=tmp_path / "chat_capability_cache.json",
        now_fn=lambda: clock[0],
    )
    cache.upsert(-100, slow_mode_seconds=10, voice_allowed=False, text_allowed=True)

    # Переводим часы на 48 часов вперёд — TTL (24h) истёк.
    clock[0] = clock[0] + timedelta(hours=48)
    assert cache.get(-100, ttl_hours=24) is None
    # И list_entries тоже вернёт пусто (запись вычищена через public API).
    assert cache.list_entries(ttl_hours=24) == []


def test_upsert_from_chat_explicit_can_send_voices() -> None:
    cache = ChatCapabilityCache()
    chat = SimpleNamespace(
        id=-100,
        type=SimpleNamespace(name="SUPERGROUP"),
        slow_mode_delay=10,
        permissions=SimpleNamespace(
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_voices=False,
        ),
    )
    entry = cache.upsert_from_chat(chat)
    assert entry["slow_mode_seconds"] == 10
    assert entry["voice_allowed"] is False
    assert entry["text_allowed"] is True
    assert entry["chat_type"] == "SUPERGROUP"


def test_upsert_from_chat_fallback_to_can_send_media() -> None:
    """
    Если `can_send_voices` отсутствует (None) — используем `can_send_media_messages`
    как proxy: media permissions имплицитно подразумевают voices.
    """
    cache = ChatCapabilityCache()
    chat = SimpleNamespace(
        id=-200,
        type=SimpleNamespace(name="GROUP"),
        slow_mode_delay=None,
        permissions=SimpleNamespace(
            can_send_messages=True,
            can_send_media_messages=False,
            can_send_voices=None,
        ),
    )
    entry = cache.upsert_from_chat(chat)
    # can_send_voices=None, но can_send_media_messages=False → voice_allowed=False
    assert entry["voice_allowed"] is False
    assert entry["text_allowed"] is True


def test_upsert_from_chat_without_permissions_leaves_voice_unknown() -> None:
    """Если Chat.permissions нет вообще (например PRIVATE chat) — None, не True."""
    cache = ChatCapabilityCache()
    chat = SimpleNamespace(
        id=123,
        type=SimpleNamespace(name="PRIVATE"),
        slow_mode_delay=None,
        permissions=None,
    )
    entry = cache.upsert_from_chat(chat)
    assert entry["voice_allowed"] is None
    assert entry["text_allowed"] is None
    assert entry["slow_mode_seconds"] is None


def test_invalidate_removes_entry(cache: ChatCapabilityCache) -> None:
    cache.upsert(-100, slow_mode_seconds=5, voice_allowed=True, text_allowed=True)
    assert cache.invalidate(-100) is True
    assert cache.get(-100) is None
    assert cache.invalidate(-100) is False  # idempotent


def test_list_entries_filters_expired(tmp_path: Path) -> None:
    """
    Fake clock: два upsert'а с разным временем, TTL 24h, старший истёк.
    Мы двигаем часы между upsert'ами чтобы получить разные fetched_at.
    """
    clock = [datetime(2026, 4, 9, 12, 0, 0, tzinfo=timezone.utc)]
    cache = ChatCapabilityCache(
        storage_path=tmp_path / "chat_capability_cache.json",
        now_fn=lambda: clock[0],
    )
    # -100 записан в t0
    cache.upsert(-100, slow_mode_seconds=10, voice_allowed=False, text_allowed=True)
    # Переводим часы на 48h вперёд и записываем -200 «сейчас»
    clock[0] = clock[0] + timedelta(hours=48)
    cache.upsert(-200, slow_mode_seconds=None, voice_allowed=True, text_allowed=True)

    # TTL 24h → -100 (возраст 48h) истёк, -200 (0h) валиден.
    entries = cache.list_entries(ttl_hours=24)
    assert len(entries) == 1
    assert entries[0]["chat_id"] == "-200"


def test_persistence_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    a = ChatCapabilityCache(storage_path=path)
    a.upsert(-300, slow_mode_seconds=5, voice_allowed=False, text_allowed=True)
    a.upsert(-301, slow_mode_seconds=None, voice_allowed=True, text_allowed=True)

    b = ChatCapabilityCache(storage_path=path)
    assert b.is_voice_allowed(-300) is False
    assert b.is_voice_allowed(-301) is True
    assert b.get_slow_mode_seconds(-300) == 5
    assert b.get_slow_mode_seconds(-301) is None


def test_malformed_json_does_not_raise(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    path.write_text("this is not json", encoding="utf-8")
    cache = ChatCapabilityCache(storage_path=path)  # не должно бросать
    assert cache.list_entries() == []
    cache.upsert(-400, slow_mode_seconds=10, voice_allowed=True, text_allowed=True)
    assert cache.is_voice_allowed(-400) is True


def test_configure_default_path_reloads(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    cache = ChatCapabilityCache(storage_path=first)
    cache.upsert(-500, slow_mode_seconds=5, voice_allowed=True, text_allowed=True)
    assert cache.is_voice_allowed(-500) is True

    # second.json valid content
    second.write_text(
        json.dumps(
            {
                "-501": {
                    "chat_id": "-501",
                    "chat_type": "GROUP",
                    "slow_mode_seconds": 15,
                    "voice_allowed": False,
                    "text_allowed": True,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cache.configure_default_path(second)
    assert cache.is_voice_allowed(-500) is None  # previous state gone
    assert cache.is_voice_allowed(-501) is False
    assert cache.get_slow_mode_seconds(-501) == 15


def test_empty_chat_id_is_rejected_on_upsert() -> None:
    cache = ChatCapabilityCache()
    with pytest.raises(ValueError):
        cache.upsert("", slow_mode_seconds=0, voice_allowed=True, text_allowed=True)


def test_singleton_exists() -> None:
    assert isinstance(chat_capability_cache, ChatCapabilityCache)
