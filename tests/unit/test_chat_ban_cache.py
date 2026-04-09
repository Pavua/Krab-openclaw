# -*- coding: utf-8 -*-
"""
Регрессии `src/core/chat_ban_cache.py` — persisted ban cache для чатов, где
Telegram API стабильно отказывает в send.

Что тестируем:

1. **Базовая семантика:** is_banned/mark_banned/clear, нормализация chat_id.
2. **Идемпотентность mark_banned.** Повторный mark в том же окне не двигает
   expires_at (иначе ban становится "permanent-через-повторы").
3. **Expiry логика.** Истёкшая запись возвращает is_banned=False и вычищается
   из памяти при is_banned/list_entries (ленивое expiry).
4. **Persistent mode (cooldown_hours=None).** Permanent ban держится до clear.
5. **Persistence round-trip.** После mark_banned и перезагрузки cache со
   свежим path то же самое состояние должно быть на месте. А истёкшие
   записи при загрузке фильтруются.
6. **Повреждённый JSON.** Не должен ронять cache, только лог warning.
7. **Изоляция singleton vs instance.** Модульный singleton существует и
   работает, но для тестов используем свежий instance чтобы не загрязнять.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.core.chat_ban_cache import BANNED_ERROR_CODES, ChatBanCache, chat_ban_cache


@pytest.fixture
def cache(tmp_path: Path) -> ChatBanCache:
    """Свежий ChatBanCache с изолированным storage на каждый тест."""
    return ChatBanCache(storage_path=tmp_path / "chat_ban_cache.json")


def test_empty_cache_returns_false(cache: ChatBanCache) -> None:
    assert cache.is_banned("-1001587432709") is False
    assert cache.is_banned(0) is False
    assert cache.is_banned(None) is False
    assert cache.list_entries() == []


def test_mark_and_is_banned(cache: ChatBanCache) -> None:
    cache.mark_banned(-1001587432709, "UserBannedInChannel")
    assert cache.is_banned(-1001587432709) is True
    # chat_id нормализуется в строку, так что int/str формы эквивалентны.
    assert cache.is_banned("-1001587432709") is True


def test_mark_empty_chat_id_is_noop(cache: ChatBanCache) -> None:
    cache.mark_banned("", "UserBannedInChannel")
    cache.mark_banned(None, "UserBannedInChannel")
    assert cache.list_entries() == []


def test_clear_removes_entry(cache: ChatBanCache) -> None:
    cache.mark_banned(-100, "UserBannedInChannel")
    assert cache.clear(-100) is True
    assert cache.is_banned(-100) is False
    # Повторный clear — идемпотентен, возвращает False.
    assert cache.clear(-100) is False


def test_mark_idempotent_does_not_extend_cooldown(
    cache: ChatBanCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Повторный mark_banned(...) в том же окне должен оставить expires_at
    неизменным. Иначе "спамим в забаненный чат" = "вечный ban", что прячет
    реальное снятие ограничения Telegram'ом.
    """
    cache.mark_banned(-200, "UserBannedInChannel", cooldown_hours=1)
    entries_before = cache.list_entries()
    assert len(entries_before) == 1
    original_expires = entries_before[0]["expires_at"]
    original_banned_at = entries_before[0]["banned_at"]

    cache.mark_banned(-200, "UserBannedInChannel", cooldown_hours=1)
    cache.mark_banned(-200, "ChatWriteForbidden", cooldown_hours=24)
    entries_after = cache.list_entries()
    assert len(entries_after) == 1
    assert entries_after[0]["expires_at"] == original_expires
    assert entries_after[0]["banned_at"] == original_banned_at
    assert entries_after[0]["hit_count"] == 3
    # last_error_code обновился на самый свежий.
    assert entries_after[0]["last_error_code"] == "ChatWriteForbidden"


def test_expired_entry_is_not_banned(tmp_path: Path) -> None:
    """
    Fake clock: ban помечается в t0, потом "часы" переводим на час вперёд
    за пределы cooldown'а. is_banned должен вернуть False и вычистить запись.
    Это чище чем прямая мутация `_entries` — мы работаем только через public API.
    """
    # Mutable обёртка: closure над nonlocal не подошла бы элегантно, а list
    # даёт простой «указатель» на текущее «сейчас» в fake clock.
    clock = [datetime(2026, 4, 9, 12, 0, 0, tzinfo=timezone.utc)]

    cache = ChatBanCache(
        storage_path=tmp_path / "chat_ban_cache.json",
        now_fn=lambda: clock[0],
    )
    cache.mark_banned(-300, "UserBannedInChannel", cooldown_hours=1)
    assert cache.is_banned(-300) is True

    # Переводим часы на 2 часа вперёд — cooldown (1h) истёк.
    clock[0] = clock[0] + timedelta(hours=2)
    assert cache.is_banned(-300) is False
    # Запись должна вычиститься, следующий list_entries её не увидит.
    assert cache.list_entries() == []


def test_permanent_ban_never_expires(cache: ChatBanCache) -> None:
    """cooldown_hours=None → permanent mark, expires_at=None, is_banned всегда True."""
    cache.mark_banned(-400, "UserBannedInChannel", cooldown_hours=None)
    assert cache.is_banned(-400) is True
    entries = cache.list_entries()
    assert len(entries) == 1
    assert entries[0]["expires_at"] is None
    # И после клина работает всё равно штатно.
    cache.clear(-400)
    assert cache.is_banned(-400) is False


def test_list_entries_returns_copies(cache: ChatBanCache) -> None:
    """Мутация возвращённого snapshot'а не должна затрагивать cache."""
    cache.mark_banned(-500, "UserBannedInChannel", cooldown_hours=1)
    entries = cache.list_entries()
    assert len(entries) == 1
    entries[0]["hit_count"] = 99999
    # А вот реальная запись должна остаться прежней.
    real = cache.list_entries()
    assert real[0]["hit_count"] == 1


def test_persistence_round_trip(tmp_path: Path) -> None:
    """
    Mark → новая instance на том же пути → is_banned=True, поля сохранены.
    Плюс проверяем что истёкшая запись не восстанавливается при load.
    """
    path = tmp_path / "cache.json"
    cache1 = ChatBanCache(storage_path=path)
    cache1.mark_banned(-600, "UserBannedInChannel", cooldown_hours=6)
    cache1.mark_banned(-601, "ChatWriteForbidden", cooldown_hours=None)

    # Свежая instance читает с диска.
    cache2 = ChatBanCache(storage_path=path)
    assert cache2.is_banned(-600) is True
    assert cache2.is_banned(-601) is True

    entries = sorted(cache2.list_entries(), key=lambda e: str(e["chat_id"]))
    assert {e["chat_id"] for e in entries} == {"-600", "-601"}
    assert any(e["expires_at"] is None for e in entries)  # permanent ban сохранён


def test_persistence_filters_expired_on_load(tmp_path: Path) -> None:
    """Истёкшие записи на диске не должны восстанавливаться в новой instance."""
    path = tmp_path / "cache.json"
    expired_iso = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    valid_iso = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    path.write_text(
        json.dumps(
            {
                "-700": {
                    "error_code": "UserBannedInChannel",
                    "banned_at": "2026-04-01T00:00:00+00:00",
                    "last_seen_at": "2026-04-01T00:00:00+00:00",
                    "expires_at": expired_iso,
                    "hit_count": 1,
                    "last_error_code": "UserBannedInChannel",
                },
                "-701": {
                    "error_code": "UserBannedInChannel",
                    "banned_at": "2026-04-09T00:00:00+00:00",
                    "last_seen_at": "2026-04-09T00:00:00+00:00",
                    "expires_at": valid_iso,
                    "hit_count": 1,
                    "last_error_code": "UserBannedInChannel",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cache = ChatBanCache(storage_path=path)
    assert cache.is_banned(-700) is False
    assert cache.is_banned(-701) is True


def test_malformed_json_does_not_raise(tmp_path: Path) -> None:
    """Битый JSON в cache файле не должен ронять bootstrap."""
    path = tmp_path / "cache.json"
    path.write_text("this is not json", encoding="utf-8")
    cache = ChatBanCache(storage_path=path)  # не должно бросать
    assert cache.list_entries() == []
    # Can still write after failed load.
    cache.mark_banned(-800, "UserBannedInChannel")
    assert cache.is_banned(-800) is True


def test_configure_default_path_reloads(tmp_path: Path) -> None:
    """
    `configure_default_path` должен очистить текущее состояние и прочитать
    новое с диска. Это используется bootstrap для re-init после переезда.
    """
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    c = ChatBanCache(storage_path=first)
    c.mark_banned(-900, "UserBannedInChannel")
    assert c.is_banned(-900) is True

    # Подложим другой state в second
    second.write_text(
        json.dumps(
            {
                "-901": {
                    "error_code": "ChatWriteForbidden",
                    "banned_at": "2026-04-09T00:00:00+00:00",
                    "last_seen_at": "2026-04-09T00:00:00+00:00",
                    "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                    "hit_count": 1,
                    "last_error_code": "ChatWriteForbidden",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    c.configure_default_path(second)
    assert c.is_banned(-900) is False  # прежнее state сброшено
    assert c.is_banned(-901) is True  # новое загружено


def test_banned_error_codes_contains_expected_codes() -> None:
    """Whitelist содержит коды, которые мы реально ожидаем из Pyrofork."""
    assert "UserBannedInChannel" in BANNED_ERROR_CODES
    assert "ChatWriteForbidden" in BANNED_ERROR_CODES


def test_singleton_is_chat_ban_cache_instance() -> None:
    """Модульный singleton существует и является ChatBanCache."""
    assert isinstance(chat_ban_cache, ChatBanCache)
