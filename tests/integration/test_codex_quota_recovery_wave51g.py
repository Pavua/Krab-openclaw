"""Wave 51-G: integration test для full quota → recovery cycle (Wave 44-V).

Покрывает реальный жизненный цикл:
  1. codex CLI subprocess emit'ит quota error → ``mark_codex_disabled`` записывает
     state в JSON (transient/weekly).
  2. Probe daemon (``_run_codex_quota_recovery_loop``) каждый час проверяет
     ``list_accounts()`` и при появлении available account вызывает
     ``mark_codex_recovered`` + emit'ит owner alert.
  3. После recovery — ``is_codex_disabled() is False`` → routing layer снова
     направляет в codex (без gemini fallback).

Все subprocess + network вызовы mock'нуты — никаких real codex CLI calls.
``freezegun`` управляет временем для cooldown'а expiry без реального ожидания.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from freezegun import freeze_time

from src.integrations import codex_account_rotator as rotator
from src.integrations import codex_quota_state as cqs
from src.integrations.codex_quota_state import (
    CodexQuotaExhaustedError,
    classify_quota,
    cooldown_for_kind,
    is_codex_disabled,
    is_quota_error,
    mark_codex_disabled,
    mark_codex_recovered,
)

# ---------------------------------------------------------------------------
# Fixtures: изоляция state файлов в tmp_path
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Перенаправляет codex_quota_state.STATE_FILE и rotator state в tmp_path.

    Возвращает dict с путями для assertion'ов.
    """
    quota_state = tmp_path / "codex_quota_state.json"
    rotator_state = tmp_path / "codex_accounts.json"
    accounts_dir = tmp_path / ".codex_accounts"
    accounts_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(cqs, "STATE_FILE", quota_state)
    monkeypatch.setattr(rotator, "STATE_FILE", rotator_state)
    monkeypatch.setattr(rotator, "ACCOUNTS_DIR", accounts_dir)

    return {
        "quota_state": quota_state,
        "rotator_state": rotator_state,
        "accounts_dir": accounts_dir,
    }


def _make_account(accounts_dir: Path, name: str, *, logged_in: bool = True) -> Path:
    """Создаёт fake codex account с auth.json."""
    acc = accounts_dir / name
    acc.mkdir(parents=True, exist_ok=True)
    if logged_in:
        (acc / "auth.json").write_text(
            json.dumps({"token": "fake"}), encoding="utf-8"
        )
    return acc


def _seed_rotator_quota(
    rotator_state_path: Path, account_name: str, until: datetime
) -> None:
    """Записывает quota_exhausted_until для аккаунта в state-файл rotator'а."""
    state: dict[str, Any] = {}
    if rotator_state_path.exists():
        state = json.loads(rotator_state_path.read_text(encoding="utf-8"))
    state[account_name] = {
        "calls_today": 1,
        "last_used": datetime.now(timezone.utc).isoformat(),
        "quota_exhausted_until": until.isoformat(),
    }
    rotator_state_path.parent.mkdir(parents=True, exist_ok=True)
    rotator_state_path.write_text(json.dumps(state), encoding="utf-8")


# ---------------------------------------------------------------------------
# Mock probe loop helper — выполняет один tick без asyncio.sleep
# ---------------------------------------------------------------------------


async def _probe_tick(notifier: Any) -> bool:
    """Симулирует одну итерацию ``_run_codex_quota_recovery_loop`` (без sleep).

    Returns True если был emit'нут recovery alert.
    """
    from src.integrations.codex_account_rotator import list_accounts

    if not is_codex_disabled():
        return False
    accounts = list_accounts()
    available = [
        a for a in accounts if a.get("available") and a.get("logged_in")
    ]
    if not available:
        return False
    if mark_codex_recovered():
        await notifier(
            f"✅ Codex восстановлен — accounts: {len(available)}."
        )
        return True
    return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_quota_recovery_cycle(
    isolated_state: dict[str, Path],
) -> None:
    """End-to-end: quota error → mark_disabled → cooldown → probe → recover."""
    accounts_dir = isolated_state["accounts_dir"]
    rotator_state = isolated_state["rotator_state"]
    _make_account(accounts_dir, "main")

    # 1. Симулируем quota error из mock subprocess
    fake_stderr = "Error: rate limit exceeded — quota_exhausted weekly limit"
    assert is_quota_error(stderr=fake_stderr) is True
    kind = classify_quota(stderr=fake_stderr)
    assert kind == "weekly"

    # 2. Mark disabled (как делает openclaw_client при CodexQuotaExhaustedError)
    with freeze_time("2026-05-10 10:00:00", tz_offset=0):
        # Аккаунт в quota cooldown'е (weekly = 7 дней)
        _seed_rotator_quota(
            rotator_state,
            "main",
            datetime.now(timezone.utc) + cooldown_for_kind(kind),
        )
        transition = mark_codex_disabled(
            fallback_model="gemini-3-pro-preview", kind=kind
        )
    assert transition is True
    assert is_codex_disabled() is True

    # 3. Сразу после disable — probe не должен recovery'ить (account ещё в cooldown)
    alerts: list[str] = []

    async def notifier(msg: str) -> None:
        alerts.append(msg)

    with freeze_time("2026-05-10 10:30:00"):
        recovered = await _probe_tick(notifier)
    assert recovered is False
    assert is_codex_disabled() is True

    # 4. Fast-forward через 7d+1h — cooldown expired, account снова available
    with freeze_time("2026-05-17 11:00:00"):
        recovered = await _probe_tick(notifier)
    assert recovered is True
    assert is_codex_disabled() is False
    assert any("Codex восстановлен" in m for m in alerts)


@pytest.mark.asyncio
async def test_transient_vs_weekly_cooldown_distinction(
    isolated_state: dict[str, Path],
) -> None:
    """1h transient vs 168h weekly — корректные cooldown durations."""
    transient_msg = "HTTP 429 rate limit exceeded"
    weekly_msg = "weekly quota exhausted — 7 day cap reached"

    assert classify_quota(stderr=transient_msg) == "transient"
    assert classify_quota(stderr=weekly_msg) == "weekly"

    assert cooldown_for_kind("transient") == timedelta(hours=1)
    assert cooldown_for_kind("weekly") == timedelta(days=7)


@pytest.mark.asyncio
async def test_recovery_probe_skipped_during_cooldown(
    isolated_state: dict[str, Path],
) -> None:
    """Probe не должен recovery'ить пока account в cooldown'е."""
    accounts_dir = isolated_state["accounts_dir"]
    rotator_state = isolated_state["rotator_state"]
    _make_account(accounts_dir, "main")

    with freeze_time("2026-05-10 10:00:00"):
        _seed_rotator_quota(
            rotator_state,
            "main",
            datetime.now(timezone.utc) + timedelta(hours=1),
        )
        mark_codex_disabled(fallback_model="gemini", kind="transient")

    alerts: list[str] = []

    async def notifier(msg: str) -> None:
        alerts.append(msg)

    # Через 30 минут — всё ещё в cooldown
    with freeze_time("2026-05-10 10:30:00"):
        recovered = await _probe_tick(notifier)
    assert recovered is False
    assert is_codex_disabled() is True
    assert alerts == []


@pytest.mark.asyncio
async def test_owner_alert_on_recovery_transition(
    isolated_state: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Recovery transition emit'ит structlog event и owner alert."""
    accounts_dir = isolated_state["accounts_dir"]
    rotator_state = isolated_state["rotator_state"]
    _make_account(accounts_dir, "main")

    with freeze_time("2026-05-10 10:00:00"):
        _seed_rotator_quota(
            rotator_state,
            "main",
            datetime.now(timezone.utc) + timedelta(hours=1),
        )
        mark_codex_disabled(fallback_model="gemini", kind="transient")

    alerts: list[str] = []

    async def notifier(msg: str) -> None:
        alerts.append(msg)

    # После expiry cooldown'а — capture stdout (structlog рендерит туда)
    with freeze_time("2026-05-10 12:00:00"):
        recovered = await _probe_tick(notifier)
    captured = capsys.readouterr()

    assert recovered is True
    # structlog event "codex_recovered_transition" должен быть в stdout
    assert "codex_recovered_transition" in captured.out, (
        f"expected codex_recovered_transition в stdout, got: {captured.out!r}"
    )
    assert len(alerts) == 1
    assert "Codex восстановлен" in alerts[0]


@pytest.mark.asyncio
async def test_double_disable_idempotent_no_duplicate_alert(
    isolated_state: dict[str, Path],
) -> None:
    """Repeated mark_codex_disabled — НЕ повторяет transition (debounced).

    Гарантия: owner получает только один alert per disable cycle, не каждые 30s.
    """
    first = mark_codex_disabled(fallback_model="gemini", kind="weekly")
    second = mark_codex_disabled(fallback_model="gemini", kind="weekly")
    third = mark_codex_disabled(fallback_model="claude", kind="weekly")

    assert first is True  # Первый — transition
    assert second is False  # Уже disabled — no transition
    assert third is False  # Изменили fallback_model, но всё ещё no transition


@pytest.mark.asyncio
async def test_concurrent_quota_state_writes_atomic(
    isolated_state: dict[str, Path],
) -> None:
    """Atomic state writes — write через .json.tmp + replace.

    Проверяет, что после mark_codex_disabled не остаётся .tmp файла, и финальный
    JSON корректно parse'ится (single-writer atomicity guarantee).
    """
    quota_state = isolated_state["quota_state"]

    mark_codex_disabled(fallback_model="gemini", kind="weekly")

    # Финальный файл существует и parse'ится
    assert quota_state.exists()
    payload = json.loads(quota_state.read_text(encoding="utf-8"))
    assert payload["disabled"] is True
    assert payload["last_fallback_model"] == "gemini"

    # .tmp файл не должен оставаться после успешной записи
    tmp_file = quota_state.with_suffix(".json.tmp")
    assert not tmp_file.exists()

    # Несколько последовательных recovery — атомарны
    mark_codex_recovered()
    payload2 = json.loads(quota_state.read_text(encoding="utf-8"))
    assert payload2["disabled"] is False
    assert payload2["recovered_at"] is not None
    assert not tmp_file.exists()


@pytest.mark.asyncio
async def test_routing_decision_after_recovery(
    isolated_state: dict[str, Path],
) -> None:
    """После recovery — is_codex_disabled() False → routing использует primary codex.

    Симулирует поведение routing layer'а (openclaw_client checks is_codex_disabled
    перед выбором model).
    """
    accounts_dir = isolated_state["accounts_dir"]
    rotator_state = isolated_state["rotator_state"]
    _make_account(accounts_dir, "main")

    with freeze_time("2026-05-10 10:00:00"):
        _seed_rotator_quota(
            rotator_state, "main", datetime.now(timezone.utc) + timedelta(hours=1)
        )
        mark_codex_disabled(fallback_model="gemini", kind="transient")

    # Routing должен skip codex (disabled)
    assert is_codex_disabled() is True

    alerts: list[str] = []

    async def notifier(msg: str) -> None:
        alerts.append(msg)

    # Recovery
    with freeze_time("2026-05-10 12:00:00"):
        recovered = await _probe_tick(notifier)
    assert recovered is True

    # Routing теперь снова направляет в codex
    assert is_codex_disabled() is False


def test_codex_quota_exhausted_error_carries_kind() -> None:
    """``CodexQuotaExhaustedError.kind`` пробрасывается в openclaw_client для cooldown choice."""
    err_weekly = CodexQuotaExhaustedError("all exhausted", kind="weekly")
    err_transient = CodexQuotaExhaustedError(kind="transient")

    assert err_weekly.kind == "weekly"
    assert err_transient.kind == "transient"
    assert isinstance(err_weekly, RuntimeError)
