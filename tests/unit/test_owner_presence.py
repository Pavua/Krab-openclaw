# -*- coding: utf-8 -*-
"""
Регрессии `src/core/owner_presence.py` — трекер присутствия owner для
Idea 17 (Smart auto-respond when offline).

Что тестируем:

1. **Initial state.** Без героуртбита tracker считает owner онлайн (is_offline=False).
2. **Threshold detection.** После записи heartbeat и протекания > threshold
   минут is_offline возвращает True.
3. **Idempotent record.** record_owner_seen с более старым timestamp не
   двигает last_seen_at назад.
4. **Persistence round-trip.** state переживает рестарт (новая instance на
   том же path подхватывает last_seen_at).
5. **Holdover variants.** pick_holdover_message возвращает корректный текст
   для каждой персоны и fallback'ит на casual для unknown.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.core.owner_presence import (
    HOLDOVER_MESSAGES,
    OwnerPresenceTracker,
    owner_presence_tracker,
    pick_holdover_message,
)


@pytest.fixture
def tracker(tmp_path: Path) -> OwnerPresenceTracker:
    """Свежий tracker с изолированным storage на каждый тест."""
    return OwnerPresenceTracker(storage_path=tmp_path / "owner_presence.json")


def test_initial_state_is_online(tracker: OwnerPresenceTracker) -> None:
    """Без heartbeat'а считаем owner онлайн (защита от false-positive после wipe)."""
    assert tracker.last_seen_at() is None
    assert tracker.is_offline(threshold_min=120) is False
    assert tracker.offline_duration_minutes() is None


def test_offline_after_threshold(tmp_path: Path) -> None:
    """После записи heartbeat и протекания threshold-времени is_offline=True."""
    clock = [datetime(2026, 4, 28, 10, 0, 0, tzinfo=timezone.utc)]
    tracker = OwnerPresenceTracker(
        storage_path=tmp_path / "owner_presence.json",
        now_fn=lambda: clock[0],
    )
    tracker.record_owner_seen()
    # Сразу после записи — онлайн.
    assert tracker.is_offline(threshold_min=120) is False

    # Через 1 час — всё ещё онлайн (порог 120 мин).
    clock[0] = clock[0] + timedelta(hours=1)
    assert tracker.is_offline(threshold_min=120) is False

    # Через 3 часа — офлайн.
    clock[0] = clock[0] + timedelta(hours=2)
    assert tracker.is_offline(threshold_min=120) is True
    duration = tracker.offline_duration_minutes()
    assert duration is not None
    assert 179 <= duration <= 181  # ~180 min


def test_record_does_not_move_backwards(tmp_path: Path) -> None:
    """Backfill старого heartbeat не должен «забыть» более свежую активность."""
    clock = [datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)]
    tracker = OwnerPresenceTracker(
        storage_path=tmp_path / "owner_presence.json",
        now_fn=lambda: clock[0],
    )
    tracker.record_owner_seen()
    fresh = tracker.last_seen_at()
    assert fresh is not None

    # Пытаемся записать heartbeat «из прошлого» — должен быть проигнорирован.
    older = clock[0] - timedelta(hours=5)
    tracker.record_owner_seen(when=older)
    assert tracker.last_seen_at() == fresh

    # А вот более свежий — продвигает.
    newer = clock[0] + timedelta(minutes=10)
    tracker.record_owner_seen(when=newer)
    assert tracker.last_seen_at() == newer


def test_persistence_round_trip(tmp_path: Path) -> None:
    """State переживает рестарт через файл."""
    path = tmp_path / "presence.json"
    fixed = datetime(2026, 4, 28, 14, 30, 0, tzinfo=timezone.utc)

    t1 = OwnerPresenceTracker(storage_path=path, now_fn=lambda: fixed)
    t1.record_owner_seen()

    t2 = OwnerPresenceTracker(storage_path=path, now_fn=lambda: fixed + timedelta(hours=3))
    assert t2.last_seen_at() == fixed
    assert t2.is_offline(threshold_min=120) is True


def test_holdover_message_variants() -> None:
    """Все три персона-варианта возвращают непустую строку, fallback на casual."""
    for persona in ("casual", "formal", "business"):
        msg = pick_holdover_message(persona)
        assert msg == HOLDOVER_MESSAGES[persona]
        assert len(msg) > 10  # sanity
    # Unknown / None / пустая → casual fallback.
    assert pick_holdover_message("unknown") == HOLDOVER_MESSAGES["casual"]
    assert pick_holdover_message(None) == HOLDOVER_MESSAGES["casual"]
    assert pick_holdover_message("") == HOLDOVER_MESSAGES["casual"]


def test_singleton_is_owner_presence_tracker() -> None:
    """Module-level singleton существует и имеет правильный тип."""
    assert isinstance(owner_presence_tracker, OwnerPresenceTracker)


def test_reset_clears_state(tracker: OwnerPresenceTracker) -> None:
    """reset() полностью очищает state."""
    tracker.record_owner_seen()
    assert tracker.last_seen_at() is not None
    tracker.reset()
    assert tracker.last_seen_at() is None
    assert tracker.is_offline(threshold_min=1) is False


def test_naive_datetime_promoted_to_utc(tracker: OwnerPresenceTracker) -> None:
    """Naive datetime в record_owner_seen считается UTC, не падает с tz-comparison."""
    naive = datetime(2026, 4, 28, 9, 0, 0)
    tracker.record_owner_seen(when=naive)
    seen = tracker.last_seen_at()
    assert seen is not None
    assert seen.tzinfo is not None
