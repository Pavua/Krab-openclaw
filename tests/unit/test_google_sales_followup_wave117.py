"""Wave 117: tests для scripts/krab_google_sales_followup.py.

Покрытие:
- preflight state отсутствует → skip;
- vertex_quota_status=ok → action=approved;
- vertex_quota_status=unknown → skip;
- blocked впервые → инициализация first_blocked_at, action=skip;
- blocked < 7 дней → action=skip;
- blocked ≥ 7 дней без previous reminder → action=send_followup;
- blocked ≥ 7 дней с недавним reminder → throttled, action=skip.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

# Динамический импорт скрипта без модификации sys.path.
_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "krab_google_sales_followup.py"
)
_spec = importlib.util.spec_from_file_location(
    "krab_google_sales_followup", _SCRIPT
)
assert _spec is not None and _spec.loader is not None
followup = importlib.util.module_from_spec(_spec)
sys.modules["krab_google_sales_followup"] = followup
_spec.loader.exec_module(followup)


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_state(tmp_path: Path) -> dict[str, Path]:
    return {
        "preflight": tmp_path / "anthropic_vertex_status.json",
        "followup": tmp_path / "google_sales_followup_state.json",
    }


def _write_preflight(path: Path, status: str, ts: datetime | None = None) -> None:
    ts = ts or datetime.now(timezone.utc)
    payload = {
        "timestamp": ts.isoformat(),
        "vertex_quota_status": status,
        "error": None,
        "model": "claude-sonnet-4-5",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


class _SenderSpy:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.return_value: bool = True

    def __call__(self, text: str) -> bool:
        self.calls.append(text)
        return self.return_value


# ─── Tests ──────────────────────────────────────────────────────────────────


def test_missing_preflight_state_file_skips(tmp_state: dict[str, Path]) -> None:
    """Нет файла Wave 104 — нечего проверять."""
    sender = _SenderSpy()
    now = datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc)
    res = followup.run_followup(
        now=now,
        preflight_path=tmp_state["preflight"],
        followup_state_path=tmp_state["followup"],
        threshold_days=7,
        alert_telegram=True,
        telegram_sender=sender,
    )
    assert res["action"] == "skip"
    assert res["status"] == "missing_state_file"
    assert sender.calls == []


def test_approved_clears_state(tmp_state: dict[str, Path]) -> None:
    """vertex_quota_status=ok → action=approved + сброс first_blocked_at."""
    # Сначала прокатим blocked, чтобы у followup state появился first_blocked_at.
    _write_preflight(tmp_state["preflight"], "blocked")
    sender = _SenderSpy()
    t0 = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    followup.run_followup(
        now=t0,
        preflight_path=tmp_state["preflight"],
        followup_state_path=tmp_state["followup"],
        threshold_days=7,
        alert_telegram=True,
        telegram_sender=sender,
    )
    assert tmp_state["followup"].exists()

    # Теперь Google approve'нули.
    _write_preflight(tmp_state["preflight"], "ok")
    t1 = datetime(2026, 5, 8, 10, 0, tzinfo=timezone.utc)
    res = followup.run_followup(
        now=t1,
        preflight_path=tmp_state["preflight"],
        followup_state_path=tmp_state["followup"],
        threshold_days=7,
        alert_telegram=True,
        telegram_sender=sender,
    )
    assert res["action"] == "approved"
    assert res["status"] == "ok"
    state = json.loads(tmp_state["followup"].read_text())
    assert state["first_blocked_at"] is None
    assert state["cleared_at"] == t1.isoformat()
    assert sender.calls == []


def test_unknown_status_skips(tmp_state: dict[str, Path]) -> None:
    """vertex_quota_status=unknown → skip без записи state."""
    _write_preflight(tmp_state["preflight"], "unknown")
    sender = _SenderSpy()
    now = datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc)
    res = followup.run_followup(
        now=now,
        preflight_path=tmp_state["preflight"],
        followup_state_path=tmp_state["followup"],
        threshold_days=7,
        alert_telegram=True,
        telegram_sender=sender,
    )
    assert res["action"] == "skip"
    assert res["status"] == "unknown"
    assert sender.calls == []


def test_blocked_first_time_initializes_state(tmp_state: dict[str, Path]) -> None:
    """Первый blocked-snapshot фиксирует first_blocked_at, не шлёт reminder."""
    _write_preflight(tmp_state["preflight"], "blocked")
    sender = _SenderSpy()
    now = datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc)
    res = followup.run_followup(
        now=now,
        preflight_path=tmp_state["preflight"],
        followup_state_path=tmp_state["followup"],
        threshold_days=7,
        alert_telegram=True,
        telegram_sender=sender,
    )
    assert res["action"] == "skip"
    assert res["days_pending"] == 0
    state = json.loads(tmp_state["followup"].read_text())
    assert state["first_blocked_at"] == now.isoformat()
    assert sender.calls == []


def test_blocked_below_threshold_skips(tmp_state: dict[str, Path]) -> None:
    """Blocked < 7 дней → skip."""
    _write_preflight(tmp_state["preflight"], "blocked")
    # Сидим followup state как будто впервые увидели 3 дня назад.
    seed = {
        "first_blocked_at": datetime(
            2026, 5, 9, 10, 0, tzinfo=timezone.utc
        ).isoformat(),
        "reminders_sent": 0,
    }
    tmp_state["followup"].write_text(json.dumps(seed), encoding="utf-8")

    sender = _SenderSpy()
    now = datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc)  # 3 дня
    res = followup.run_followup(
        now=now,
        preflight_path=tmp_state["preflight"],
        followup_state_path=tmp_state["followup"],
        threshold_days=7,
        alert_telegram=True,
        telegram_sender=sender,
    )
    assert res["action"] == "skip"
    assert res["days_pending"] == 3
    assert sender.calls == []


def test_blocked_at_threshold_emits_reminder(tmp_state: dict[str, Path]) -> None:
    """Blocked ≥ 7 дней без предыдущего reminder → send_followup + Telegram."""
    _write_preflight(tmp_state["preflight"], "blocked")
    seed = {
        "first_blocked_at": datetime(
            2026, 5, 5, 10, 0, tzinfo=timezone.utc
        ).isoformat(),
        "reminders_sent": 0,
    }
    tmp_state["followup"].write_text(json.dumps(seed), encoding="utf-8")

    sender = _SenderSpy()
    now = datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc)  # 7 дней
    res = followup.run_followup(
        now=now,
        preflight_path=tmp_state["preflight"],
        followup_state_path=tmp_state["followup"],
        threshold_days=7,
        alert_telegram=True,
        telegram_sender=sender,
    )
    assert res["action"] == "send_followup"
    assert res["days_pending"] == 7
    assert len(sender.calls) == 1
    assert "7" in sender.calls[0]
    state = json.loads(tmp_state["followup"].read_text())
    assert state["last_reminder_at"] == now.isoformat()
    assert state["reminders_sent"] == 1


def test_throttled_when_recent_reminder(tmp_state: dict[str, Path]) -> None:
    """Reminder отправляли 2 дня назад → throttled, action=skip."""
    _write_preflight(tmp_state["preflight"], "blocked")
    seed = {
        "first_blocked_at": datetime(
            2026, 4, 20, 10, 0, tzinfo=timezone.utc
        ).isoformat(),
        "last_reminder_at": datetime(
            2026, 5, 10, 10, 0, tzinfo=timezone.utc
        ).isoformat(),
        "reminders_sent": 1,
    }
    tmp_state["followup"].write_text(json.dumps(seed), encoding="utf-8")

    sender = _SenderSpy()
    now = datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc)
    res = followup.run_followup(
        now=now,
        preflight_path=tmp_state["preflight"],
        followup_state_path=tmp_state["followup"],
        threshold_days=7,
        reminder_interval_days=7,
        alert_telegram=True,
        telegram_sender=sender,
    )
    assert res["action"] == "skip"
    assert res["days_pending"] >= 7
    assert sender.calls == []


def test_alert_telegram_off_skips_send(tmp_state: dict[str, Path]) -> None:
    """alert_telegram=False — пишем state, но не дёргаем sender."""
    _write_preflight(tmp_state["preflight"], "blocked")
    seed = {
        "first_blocked_at": datetime(
            2026, 5, 1, 10, 0, tzinfo=timezone.utc
        ).isoformat(),
        "reminders_sent": 0,
    }
    tmp_state["followup"].write_text(json.dumps(seed), encoding="utf-8")

    sender = _SenderSpy()
    now = datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc)  # 11 дней
    res = followup.run_followup(
        now=now,
        preflight_path=tmp_state["preflight"],
        followup_state_path=tmp_state["followup"],
        threshold_days=7,
        alert_telegram=False,
        telegram_sender=sender,
    )
    assert res["action"] == "send_followup"
    assert sender.calls == []
    state = json.loads(tmp_state["followup"].read_text())
    assert state["reminders_sent"] == 1
