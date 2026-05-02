# -*- coding: utf-8 -*-
"""
Wave 14-F (Session 33): per-session Sentry event dedupe.

Один и тот же runtime-error может уходить в Sentry сотни раз за инцидент
(пример: 226× `db_corruption_detected_runtime` за один morning incident).
Все 226 — same root cause, same stack — но Sentry квота забивается.

_before_send теперь дропает дубликаты в рамках одного процесса по ключу
f"{event_name}:{error_type}". Set живёт на process lifetime; restart Krab
получает fresh signal.
"""

from __future__ import annotations

import pytest

from src.bootstrap import sentry_init
from src.bootstrap.sentry_init import _before_send, _reset_dedupe_state_for_tests


@pytest.fixture(autouse=True)
def _reset_dedupe_state(monkeypatch: pytest.MonkeyPatch):
    """Перед каждым тестом — чистый state и default mode."""
    monkeypatch.setenv("KRAB_SENTRY_DEDUPE_MODE", "once_per_session")
    monkeypatch.delenv("KRAB_SENTRY_DEDUPE_EVERY_NTH", raising=False)
    monkeypatch.delenv("KRAB_SENTRY_DEDUPE_MAX_SIZE", raising=False)
    _reset_dedupe_state_for_tests()
    yield
    _reset_dedupe_state_for_tests()


def _make_event(event_name: str, error_type: str, value: str = "boom") -> dict:
    return {
        "transaction": event_name,
        "exception": {"values": [{"type": error_type, "value": value}]},
    }


def test_first_event_passes() -> None:
    """Первое появление (event_name, error_type) → проходит."""
    event = _make_event("db_corruption_detected_runtime", "DatabaseError")
    assert _before_send(event, {}) is event


def test_duplicate_event_dropped() -> None:
    """Второе и последующие с тем же ключом → None (drop) в режиме once_per_session."""
    event_a = _make_event("db_corruption_detected_runtime", "DatabaseError")
    event_b = _make_event("db_corruption_detected_runtime", "DatabaseError")
    event_c = _make_event("db_corruption_detected_runtime", "DatabaseError")
    assert _before_send(event_a, {}) is event_a
    assert _before_send(event_b, {}) is None
    assert _before_send(event_c, {}) is None


def test_different_event_passes() -> None:
    """Разные (event_name, error_type) — независимые корзины дедупа."""
    e1 = _make_event("db_corruption_detected_runtime", "DatabaseError")
    e2 = _make_event("db_corruption_detected_runtime", "OperationalError")
    e3 = _make_event("memory_indexer_failed", "DatabaseError")
    assert _before_send(e1, {}) is e1
    assert _before_send(e2, {}) is e2
    assert _before_send(e3, {}) is e3
    # Дубликаты каждой — drop.
    assert _before_send(_make_event("db_corruption_detected_runtime", "DatabaseError"), {}) is None
    assert _before_send(_make_event("db_corruption_detected_runtime", "OperationalError"), {}) is None
    assert _before_send(_make_event("memory_indexer_failed", "DatabaseError"), {}) is None


def test_dedupe_disabled_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """KRAB_SENTRY_DEDUPE_MODE=disabled → все события проходят."""
    monkeypatch.setenv("KRAB_SENTRY_DEDUPE_MODE", "disabled")
    _reset_dedupe_state_for_tests()
    for _ in range(5):
        ev = _make_event("db_corruption_detected_runtime", "DatabaseError")
        assert _before_send(ev, {}) is ev


def test_every_nth_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """every_nth с N=3 → events #1, #4, #7, ... проходят, остальные drop."""
    monkeypatch.setenv("KRAB_SENTRY_DEDUPE_MODE", "every_nth")
    monkeypatch.setenv("KRAB_SENTRY_DEDUPE_EVERY_NTH", "3")
    _reset_dedupe_state_for_tests()
    results: list[bool] = []
    for _ in range(10):
        ev = _make_event("db_corruption_detected_runtime", "DatabaseError")
        results.append(_before_send(ev, {}) is not None)
    # Counts after each call: 1,2,3,4,5,6,7,8,9,10.
    # i=1 first → passes. After: ((count-1) % 3)==0 means count in {1,4,7,10}.
    # i.e. indices 0,3,6,9 (0-indexed) → True; rest False.
    assert results == [True, False, False, True, False, False, True, False, False, True]


def test_set_evicts_at_max_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """LRU eviction: при превышении max_size — выкидываем oldest entry."""
    monkeypatch.setenv("KRAB_SENTRY_DEDUPE_MAX_SIZE", "3")
    _reset_dedupe_state_for_tests()
    # Заполняем до лимита 3 разными ключами — все проходят.
    assert _before_send(_make_event("ev_a", "TypeA"), {}) is not None
    assert _before_send(_make_event("ev_b", "TypeB"), {}) is not None
    assert _before_send(_make_event("ev_c", "TypeC"), {}) is not None
    # Размер == 3.
    assert len(sentry_init._session_seen_events) == 3
    assert "ev_a:TypeA" in sentry_init._session_seen_events
    # Добавляем 4-й ключ → "ev_a:TypeA" должен быть выкинут (oldest).
    assert _before_send(_make_event("ev_d", "TypeD"), {}) is not None
    assert len(sentry_init._session_seen_events) == 3
    assert "ev_a:TypeA" not in sentry_init._session_seen_events
    assert "ev_d:TypeD" in sentry_init._session_seen_events
    # Поскольку ev_a выкинут — это снова "первое появление" → проходит.
    assert _before_send(_make_event("ev_a", "TypeA"), {}) is not None


def test_dedupe_does_not_break_benign_filter() -> None:
    """Benign-маркеры (userbot_not_ready) дропаются раньше дедупа — он не должен
    их учитывать (т.е. не записывать ключ benign-события в seen-set)."""
    benign = {"extra": {"error_code": "userbot_not_ready"}}
    assert _before_send(benign, {}) is None
    # Set должен остаться пустым — benign event не попал в дедуп.
    assert len(sentry_init._session_seen_events) == 0


def test_logger_only_event_dedupes_via_message() -> None:
    """logger.error без exception — ключ строится из message."""
    e1 = {"message": "memory_indexer batch failed: corruption"}
    e2 = {"message": "memory_indexer batch failed: corruption"}
    assert _before_send(e1, {}) is e1
    assert _before_send(e2, {}) is None
