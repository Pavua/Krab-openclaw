"""Wave 24-A: Тесты для codex_account_rotator.py.

8 тестов покрывают:
- list_accounts() при пустом каталоге
- list_accounts() с залогиненным аккаунтом
- get_next_codex_home() round-robin при равном usage
- record_call() обновляет calls_today
- record_call() с quota error → marks exhausted
- record_quota_exhaustion() обновляет until
- _is_available() при истёкшем exhaustion → True
- _is_available() при будущем exhaustion → False
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Модуль тестируется через прямой импорт с patch ACCOUNTS_DIR и STATE_FILE
from src.integrations.codex_account_rotator import (
    _is_available,
    get_account_name_from_home,
    get_next_codex_home,
    list_accounts,
    record_call,
    record_quota_exhaustion,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_accounts_dir(tmp_path: Path) -> Path:
    """Временная директория ~/.codex_accounts для тестов."""
    accounts = tmp_path / "codex_accounts"
    accounts.mkdir()
    return accounts


@pytest.fixture()
def tmp_state_file(tmp_path: Path) -> Path:
    """Временный state JSON файл."""
    state_dir = tmp_path / "krab_runtime_state"
    state_dir.mkdir(parents=True)
    return state_dir / "codex_accounts.json"


# ---------------------------------------------------------------------------
# Тест 1: list_accounts() при пустом каталоге → []
# ---------------------------------------------------------------------------


def test_list_accounts_empty_dir(tmp_accounts_dir: Path, tmp_state_file: Path) -> None:
    """Пустой каталог → возвращает пустой список."""
    with (
        patch("src.integrations.codex_account_rotator.ACCOUNTS_DIR", tmp_accounts_dir),
        patch("src.integrations.codex_account_rotator.STATE_FILE", tmp_state_file),
    ):
        result = list_accounts()
    assert result == []


# ---------------------------------------------------------------------------
# Тест 2: list_accounts() с auth.json → возвращает запись
# ---------------------------------------------------------------------------


def test_list_accounts_with_logged_in_account(
    tmp_accounts_dir: Path, tmp_state_file: Path
) -> None:
    """Аккаунт с auth.json отображается как logged_in и available."""
    acc_dir = tmp_accounts_dir / "primary"
    acc_dir.mkdir()
    (acc_dir / "auth.json").write_text('{"token": "test"}', encoding="utf-8")

    with (
        patch("src.integrations.codex_account_rotator.ACCOUNTS_DIR", tmp_accounts_dir),
        patch("src.integrations.codex_account_rotator.STATE_FILE", tmp_state_file),
    ):
        result = list_accounts()

    assert len(result) == 1
    assert result[0]["name"] == "primary"
    assert result[0]["logged_in"] is True
    assert result[0]["available"] is True
    assert result[0]["calls_today"] == 0


def test_list_accounts_without_auth_json(
    tmp_accounts_dir: Path, tmp_state_file: Path
) -> None:
    """Аккаунт без auth.json отображается как not logged_in и not available."""
    acc_dir = tmp_accounts_dir / "account2"
    acc_dir.mkdir()
    # Нет auth.json

    with (
        patch("src.integrations.codex_account_rotator.ACCOUNTS_DIR", tmp_accounts_dir),
        patch("src.integrations.codex_account_rotator.STATE_FILE", tmp_state_file),
    ):
        result = list_accounts()

    assert len(result) == 1
    assert result[0]["logged_in"] is False
    assert result[0]["available"] is False


# ---------------------------------------------------------------------------
# Тест 3: get_next_codex_home() round-robin при equal usage
# ---------------------------------------------------------------------------


def test_get_next_codex_home_round_robin(
    tmp_accounts_dir: Path, tmp_state_file: Path
) -> None:
    """LRU: аккаунт без last_used выбирается первым."""
    # Создаём два залогиненных аккаунта
    for name in ("primary", "account2"):
        d = tmp_accounts_dir / name
        d.mkdir()
        (d / "auth.json").write_text('{"token": "t"}', encoding="utf-8")

    # State: primary использовался, account2 — нет
    state = {
        "primary": {
            "calls_today": 5,
            "last_used": "2026-05-05T10:00:00+00:00",
            "quota_exhausted_until": None,
        }
    }
    tmp_state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_state_file.write_text(json.dumps(state), encoding="utf-8")

    with (
        patch("src.integrations.codex_account_rotator.ACCOUNTS_DIR", tmp_accounts_dir),
        patch("src.integrations.codex_account_rotator.STATE_FILE", tmp_state_file),
    ):
        chosen = get_next_codex_home()

    assert chosen is not None
    # account2 не имеет last_used → должен быть выбран (LRU)
    assert "account2" in chosen


# ---------------------------------------------------------------------------
# Тест 4: record_call() обновляет calls_today
# ---------------------------------------------------------------------------


def test_record_call_increments_calls_today(
    tmp_accounts_dir: Path, tmp_state_file: Path
) -> None:
    """record_call() увеличивает calls_today на 1."""
    tmp_state_file.parent.mkdir(parents=True, exist_ok=True)

    with (
        patch("src.integrations.codex_account_rotator.ACCOUNTS_DIR", tmp_accounts_dir),
        patch("src.integrations.codex_account_rotator.STATE_FILE", tmp_state_file),
    ):
        record_call("primary", success=True)
        record_call("primary", success=True)

    state = json.loads(tmp_state_file.read_text(encoding="utf-8"))
    assert state["primary"]["calls_today"] == 2
    assert state["primary"]["last_used"] is not None


# ---------------------------------------------------------------------------
# Тест 5: record_call() с quota error → marks exhausted
# ---------------------------------------------------------------------------


def test_record_call_quota_error_marks_exhausted(
    tmp_accounts_dir: Path, tmp_state_file: Path
) -> None:
    """record_call() с quota ошибкой выставляет quota_exhausted_until."""
    tmp_state_file.parent.mkdir(parents=True, exist_ok=True)

    with (
        patch("src.integrations.codex_account_rotator.ACCOUNTS_DIR", tmp_accounts_dir),
        patch("src.integrations.codex_account_rotator.STATE_FILE", tmp_state_file),
    ):
        record_call("primary", success=False, error="quota exceeded for this billing period")

    state = json.loads(tmp_state_file.read_text(encoding="utf-8"))
    assert state["primary"]["quota_exhausted_until"] is not None
    # Должно быть в будущем
    until = datetime.fromisoformat(state["primary"]["quota_exhausted_until"])
    assert until > datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Тест 6: record_quota_exhaustion() обновляет until
# ---------------------------------------------------------------------------


def test_record_quota_exhaustion_sets_until(
    tmp_accounts_dir: Path, tmp_state_file: Path
) -> None:
    """record_quota_exhaustion() корректно сохраняет дату."""
    tmp_state_file.parent.mkdir(parents=True, exist_ok=True)
    reset_dt = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)

    with (
        patch("src.integrations.codex_account_rotator.ACCOUNTS_DIR", tmp_accounts_dir),
        patch("src.integrations.codex_account_rotator.STATE_FILE", tmp_state_file),
    ):
        record_quota_exhaustion("account2", reset_dt)

    state = json.loads(tmp_state_file.read_text(encoding="utf-8"))
    assert state["account2"]["quota_exhausted_until"] == reset_dt.isoformat()


# ---------------------------------------------------------------------------
# Тест 7: _is_available() при истёкшем exhaustion → True
# ---------------------------------------------------------------------------


def test_is_available_expired_exhaustion_returns_true() -> None:
    """Дата exhaustion в прошлом → аккаунт available."""
    past_dt = datetime.now(timezone.utc) - timedelta(hours=1)
    account_state = {"quota_exhausted_until": past_dt.isoformat()}
    assert _is_available(account_state) is True


# ---------------------------------------------------------------------------
# Тест 8: _is_available() при будущем exhaustion → False
# ---------------------------------------------------------------------------


def test_is_available_future_exhaustion_returns_false() -> None:
    """Дата exhaustion в будущем → аккаунт недоступен."""
    future_dt = datetime.now(timezone.utc) + timedelta(hours=10)
    account_state = {"quota_exhausted_until": future_dt.isoformat()}
    assert _is_available(account_state) is False


# ---------------------------------------------------------------------------
# Тест бонус: get_account_name_from_home
# ---------------------------------------------------------------------------


def test_get_account_name_from_home() -> None:
    """get_account_name_from_home извлекает имя аккаунта из пути."""
    assert get_account_name_from_home("/home/user/.codex_accounts/account2") == "account2"
    assert get_account_name_from_home("/home/user/.codex_accounts/primary") == "primary"


def test_get_next_codex_home_no_accounts(
    tmp_accounts_dir: Path, tmp_state_file: Path
) -> None:
    """Нет залогиненных аккаунтов → get_next_codex_home() возвращает None."""
    with (
        patch("src.integrations.codex_account_rotator.ACCOUNTS_DIR", tmp_accounts_dir),
        patch("src.integrations.codex_account_rotator.STATE_FILE", tmp_state_file),
    ):
        result = get_next_codex_home()
    assert result is None
