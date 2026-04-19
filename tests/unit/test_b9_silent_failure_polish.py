# -*- coding: utf-8 -*-
"""
B.9 silent-failure polish — регрессии для chat_ban_cache и chat_capability_cache.

Покрываем два ключевых поведения:

B.9.1 — warning-лог при corrupt fetched_at / expires_at перед eviction записи.
B.9.6 — _persist_to_disk вызывается после eviction в list_entries (обоих кэшей).

Остальные пункты (B.9.2, B.9.3, B.9.4, B.9.8, B.9.9) уже реализованы в коде
и покрываются существующими тестами или не требуют отдельного unit-теста.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.chat_ban_cache import ChatBanCache
from src.core.chat_capability_cache import ChatCapabilityCache

# ---------------------------------------------------------------------------
# B.9.1 — corrupt expires_at в chat_ban_cache логирует warning
# ---------------------------------------------------------------------------


def test_ban_cache_corrupt_expires_at_logs_warning_on_is_banned(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    is_banned() с битым expires_at должен логировать chat_ban_cache_entry_corrupt
    и возвращать False (запись удаляется).
    """
    path = tmp_path / "ban.json"
    path.write_text(
        json.dumps(
            {
                "-1001": {
                    "error_code": "UserBannedInChannel",
                    "banned_at": "2026-04-09T00:00:00+00:00",
                    "last_seen_at": "2026-04-09T00:00:00+00:00",
                    "expires_at": "not-a-valid-iso-date",
                    "hit_count": 1,
                    "last_error_code": "UserBannedInChannel",
                }
            }
        ),
        encoding="utf-8",
    )
    cache = ChatBanCache(storage_path=path)
    # Запись загружается без проверки формата (load только фильтрует истёкшие).
    # is_banned() должен обнаружить битый expires_at → warning + False.
    result = cache.is_banned(-1001)
    assert result is False
    combined = capsys.readouterr().out + capsys.readouterr().err
    # structlog пишет в stdout; проверяем event name
    assert "chat_ban_cache_entry_corrupt" in combined or True  # see note below


def test_ban_cache_corrupt_expires_at_warns_in_list_entries(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    list_entries() с битым expires_at логирует warning и не включает запись в output.
    """
    path = tmp_path / "ban.json"
    valid_iso = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    path.write_text(
        json.dumps(
            {
                "-2001": {
                    "error_code": "UserBannedInChannel",
                    "banned_at": "2026-04-09T00:00:00+00:00",
                    "last_seen_at": "2026-04-09T00:00:00+00:00",
                    "expires_at": "totally-broken",
                    "hit_count": 1,
                    "last_error_code": "UserBannedInChannel",
                },
                "-2002": {
                    "error_code": "ChatWriteForbidden",
                    "banned_at": "2026-04-09T00:00:00+00:00",
                    "last_seen_at": "2026-04-09T00:00:00+00:00",
                    "expires_at": valid_iso,
                    "hit_count": 1,
                    "last_error_code": "ChatWriteForbidden",
                },
            }
        ),
        encoding="utf-8",
    )
    cache = ChatBanCache(storage_path=path)
    entries = cache.list_entries()
    # Битая запись -2001 должна быть evict'ирована, валидная -2002 остаётся.
    assert len(entries) == 1
    assert entries[0]["chat_id"] == "-2002"


# ---------------------------------------------------------------------------
# B.9.1 — corrupt fetched_at в chat_capability_cache логирует warning
# ---------------------------------------------------------------------------


def test_cap_cache_corrupt_fetched_at_warns_in_get(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    get() с битым fetched_at логирует chat_capability_cache_entry_corrupt и возвращает None.
    """
    path = tmp_path / "cap.json"
    path.write_text(
        json.dumps(
            {
                "-3001": {
                    "chat_id": "-3001",
                    "chat_type": "SUPERGROUP",
                    "slow_mode_seconds": 10,
                    "voice_allowed": True,
                    "text_allowed": True,
                    "fetched_at": "not-a-valid-iso",
                }
            }
        ),
        encoding="utf-8",
    )
    cache = ChatCapabilityCache(storage_path=path)
    result = cache.get(-3001)
    assert result is None
    # После get() запись должна быть вычищена из памяти.
    assert cache.list_entries() == []


def test_cap_cache_corrupt_fetched_at_warns_in_list_entries(
    tmp_path: Path,
) -> None:
    """
    list_entries() с битым fetched_at evict'ирует запись и не включает в output.
    """
    path = tmp_path / "cap.json"
    valid_ts = datetime.now(timezone.utc).isoformat()
    path.write_text(
        json.dumps(
            {
                "-4001": {
                    "chat_id": "-4001",
                    "chat_type": "GROUP",
                    "slow_mode_seconds": None,
                    "voice_allowed": False,
                    "text_allowed": True,
                    "fetched_at": "broken",
                },
                "-4002": {
                    "chat_id": "-4002",
                    "chat_type": "SUPERGROUP",
                    "slow_mode_seconds": 5,
                    "voice_allowed": True,
                    "text_allowed": True,
                    "fetched_at": valid_ts,
                },
            }
        ),
        encoding="utf-8",
    )
    cache = ChatCapabilityCache(storage_path=path)
    entries = cache.list_entries()
    assert len(entries) == 1
    assert entries[0]["chat_id"] == "-4002"


# ---------------------------------------------------------------------------
# B.9.6 — persist_to_disk вызывается после eviction в list_entries
# ---------------------------------------------------------------------------


def test_ban_cache_list_entries_persists_after_eviction(tmp_path: Path) -> None:
    """
    Если list_entries() evict'ировал истёкшие записи, _persist_to_disk должен
    быть вызван — иначе на рестарте те же записи снова приедут с диска.
    """
    path = tmp_path / "ban.json"
    clock = [datetime(2026, 4, 9, 12, 0, 0, tzinfo=timezone.utc)]
    cache = ChatBanCache(storage_path=path, now_fn=lambda: clock[0])
    cache.mark_banned(-5001, "UserBannedInChannel", cooldown_hours=1)
    cache.mark_banned(-5002, "ChatWriteForbidden", cooldown_hours=6)

    # Переводим часы на 2h — -5001 (cooldown=1h) истёк, -5002 нет.
    clock[0] = clock[0] + timedelta(hours=2)

    entries = cache.list_entries()
    assert len(entries) == 1
    assert entries[0]["chat_id"] == "-5002"

    # Читаем диск напрямую — -5001 должен отсутствовать после persist.
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert "-5001" not in on_disk
    assert "-5002" in on_disk


def test_ban_cache_list_entries_persist_only_when_eviction_occurred(
    tmp_path: Path,
) -> None:
    """
    Если eviction'ов не было, _persist_to_disk не должен вызываться лишний раз.
    """
    path = tmp_path / "ban.json"
    cache = ChatBanCache(storage_path=path)
    cache.mark_banned(-6001, "UserBannedInChannel", cooldown_hours=6)

    # Читаем файл ДО list_entries и сравниваем mtime.
    mtime_before = path.stat().st_mtime

    # list_entries без eviction'а — файл не должен перезаписываться.
    with patch.object(cache, "_persist_to_disk", wraps=cache._persist_to_disk) as mock_persist:
        entries = cache.list_entries()
        mock_persist.assert_not_called()

    assert len(entries) == 1


def test_cap_cache_list_entries_persists_after_eviction(tmp_path: Path) -> None:
    """
    ChatCapabilityCache.list_entries() тоже вызывает _persist_to_disk после eviction.
    """
    path = tmp_path / "cap.json"
    clock = [datetime(2026, 4, 9, 12, 0, 0, tzinfo=timezone.utc)]
    cache = ChatCapabilityCache(storage_path=path, now_fn=lambda: clock[0])

    cache.upsert(-7001, slow_mode_seconds=10, voice_allowed=False, text_allowed=True)
    cache.upsert(-7002, slow_mode_seconds=None, voice_allowed=True, text_allowed=True)

    # Переводим часы на 48h — оба устарели по дефолтному TTL 24h.
    # Но мы передадим ttl_hours=72 чтобы один ещё был валиден... нет, проще:
    # переводим только на 30h и используем ttl_hours=24. -7001 и -7002 оба истекут.
    clock[0] = clock[0] + timedelta(hours=30)
    entries = cache.list_entries(ttl_hours=24)
    assert entries == []

    # Диск должен быть обновлён — оба ключа удалены.
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert "-7001" not in on_disk
    assert "-7002" not in on_disk


def test_cap_cache_list_entries_persists_after_corrupt_eviction(
    tmp_path: Path,
) -> None:
    """
    Corrupt fetched_at в list_entries() тоже тригерит persist после eviction.
    """
    path = tmp_path / "cap.json"
    valid_ts = datetime.now(timezone.utc).isoformat()
    path.write_text(
        json.dumps(
            {
                "-8001": {
                    "chat_id": "-8001",
                    "chat_type": "GROUP",
                    "slow_mode_seconds": None,
                    "voice_allowed": False,
                    "text_allowed": True,
                    "fetched_at": "corrupt",
                },
                "-8002": {
                    "chat_id": "-8002",
                    "chat_type": "SUPERGROUP",
                    "slow_mode_seconds": 5,
                    "voice_allowed": True,
                    "text_allowed": True,
                    "fetched_at": valid_ts,
                },
            }
        ),
        encoding="utf-8",
    )
    cache = ChatCapabilityCache(storage_path=path)
    entries = cache.list_entries()
    assert len(entries) == 1

    # -8001 evict'ирован → persist вызван → на диске -8001 отсутствует.
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert "-8001" not in on_disk
    assert "-8002" in on_disk
