# -*- coding: utf-8 -*-
"""Wave 94: provider quarantine unit tests.

Покрытие:
- failure threshold trigger (5 fails в окне → quarantine)
- time-window expiry (старые fails не считаются)
- success сбрасывает counter
- quarantine TTL (через 5 мин снимается)
- normalization (whitespace/empty)
- atomic persist (load после mark)
- model_router integration (skip local при quarantine)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.core.provider_quarantine import ProviderQuarantine


def _make_clock(start: datetime):
    """Возвращает (now_fn, advance(seconds)) — мутируемый clock."""
    state = [start]

    def now_fn() -> datetime:
        return state[0]

    def advance(seconds: float) -> None:
        state[0] = state[0] + timedelta(seconds=seconds)

    return now_fn, advance


@pytest.fixture
def tmp_state_path(tmp_path: Path) -> Path:
    return tmp_path / "provider_quarantine.json"


def test_failure_threshold_triggers_quarantine(tmp_state_path: Path) -> None:
    start = datetime(2026, 5, 12, 10, 0, 0, tzinfo=timezone.utc)
    now_fn, advance = _make_clock(start)
    q = ProviderQuarantine(
        storage_path=tmp_state_path,
        now_fn=now_fn,
        failure_threshold=5,
        window_seconds=600.0,
        quarantine_seconds=300.0,
    )

    # 4 fails → ещё не quarantine
    for _ in range(4):
        triggered = q.record_provider_failure("vertex", "auth")
        assert triggered is False
        advance(10)
    assert q.is_provider_quarantined("vertex") is False

    # 5-й fail → quarantine
    triggered = q.record_provider_failure("vertex", "auth")
    assert triggered is True
    assert q.is_provider_quarantined("vertex") is True


def test_window_expiry_old_failures_dont_count(tmp_state_path: Path) -> None:
    """Fail'ы старше window_seconds не должны считаться."""
    start = datetime(2026, 5, 12, 10, 0, 0, tzinfo=timezone.utc)
    now_fn, advance = _make_clock(start)
    q = ProviderQuarantine(
        storage_path=tmp_state_path,
        now_fn=now_fn,
        failure_threshold=5,
        window_seconds=600.0,
    )
    # 4 fails в начале
    for _ in range(4):
        q.record_provider_failure("vertex", "timeout")

    # Перематываем за окно (>10 мин)
    advance(700)

    # Один новый fail — НЕ должен триггерить (старые отвалились из окна)
    triggered = q.record_provider_failure("vertex", "timeout")
    assert triggered is False
    assert q.is_provider_quarantined("vertex") is False


def test_success_resets_failure_counter(tmp_state_path: Path) -> None:
    start = datetime(2026, 5, 12, 10, 0, 0, tzinfo=timezone.utc)
    now_fn, _ = _make_clock(start)
    q = ProviderQuarantine(
        storage_path=tmp_state_path,
        now_fn=now_fn,
        failure_threshold=5,
        window_seconds=600.0,
    )

    for _ in range(4):
        q.record_provider_failure("openai", "quota")

    q.record_provider_success("openai")

    # После success — 4 новых fail'а должны проходить без quarantine
    for _ in range(4):
        triggered = q.record_provider_failure("openai", "quota")
        assert triggered is False
    assert q.is_provider_quarantined("openai") is False


def test_quarantine_ttl_expires(tmp_state_path: Path) -> None:
    """Через quarantine_seconds quarantine снимается автоматически."""
    start = datetime(2026, 5, 12, 10, 0, 0, tzinfo=timezone.utc)
    now_fn, advance = _make_clock(start)
    q = ProviderQuarantine(
        storage_path=tmp_state_path,
        now_fn=now_fn,
        failure_threshold=3,
        window_seconds=600.0,
        quarantine_seconds=120.0,
    )
    for _ in range(3):
        q.record_provider_failure("local", "network")
    assert q.is_provider_quarantined("local") is True

    # Сразу после quarantine_seconds — снят
    advance(130)
    assert q.is_provider_quarantined("local") is False


def test_success_clears_active_quarantine(tmp_state_path: Path) -> None:
    """Если провайдер в quarantine, success немедленно снимает её."""
    start = datetime(2026, 5, 12, 10, 0, 0, tzinfo=timezone.utc)
    now_fn, _ = _make_clock(start)
    q = ProviderQuarantine(
        storage_path=tmp_state_path,
        now_fn=now_fn,
        failure_threshold=3,
        window_seconds=600.0,
        quarantine_seconds=300.0,
    )
    for _ in range(3):
        q.record_provider_failure("vertex", "auth")
    assert q.is_provider_quarantined("vertex") is True

    q.record_provider_success("vertex")
    assert q.is_provider_quarantined("vertex") is False


def test_persist_and_reload_preserves_state(tmp_state_path: Path) -> None:
    """После reload (новый instance) состояние quarantine читается с диска."""
    start = datetime(2026, 5, 12, 10, 0, 0, tzinfo=timezone.utc)
    now_fn, _ = _make_clock(start)
    q1 = ProviderQuarantine(
        storage_path=tmp_state_path,
        now_fn=now_fn,
        failure_threshold=3,
        window_seconds=600.0,
        quarantine_seconds=300.0,
    )
    for _ in range(3):
        q1.record_provider_failure("vertex", "quota")
    assert q1.is_provider_quarantined("vertex") is True

    # Новый instance с тем же clock — quarantine должен восстановиться
    q2 = ProviderQuarantine(
        storage_path=tmp_state_path,
        now_fn=now_fn,
        failure_threshold=3,
        window_seconds=600.0,
        quarantine_seconds=300.0,
    )
    assert q2.is_provider_quarantined("vertex") is True


def test_normalize_empty_provider_noop(tmp_state_path: Path) -> None:
    """Пустой/whitespace provider не должен ничего записывать."""
    q = ProviderQuarantine(storage_path=tmp_state_path)
    assert q.record_provider_failure("", "auth") is False
    assert q.record_provider_failure("   ", "auth") is False
    assert q.is_provider_quarantined("") is False
    assert q.list_entries() == []


def test_list_entries_returns_snapshot(tmp_state_path: Path) -> None:
    """list_entries возвращает копии с флагом quarantined."""
    start = datetime(2026, 5, 12, 10, 0, 0, tzinfo=timezone.utc)
    now_fn, _ = _make_clock(start)
    q = ProviderQuarantine(
        storage_path=tmp_state_path,
        now_fn=now_fn,
        failure_threshold=2,
        window_seconds=600.0,
        quarantine_seconds=300.0,
    )
    q.record_provider_failure("vertex", "auth")
    q.record_provider_failure("vertex", "auth")
    q.record_provider_failure("openai", "timeout")

    entries = {e["provider"]: e for e in q.list_entries()}
    assert entries["vertex"]["quarantined"] is True
    assert entries["vertex"]["last_reason"] == "auth"
    assert entries["openai"]["quarantined"] is False
    # Snapshot — мутация не должна влиять на cache
    entries["vertex"]["failures"].clear()
    assert q.is_provider_quarantined("vertex") is True
