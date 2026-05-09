"""Wave 44-V: tests for codex quota detection + auto-fallback."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.integrations import codex_quota_state as cqs
from src.integrations.codex_quota_state import (
    CODEX_QUOTA_PATTERNS,
    CodexQuotaExhaustedError,
    classify_quota,
    cooldown_for_kind,
    is_quota_error,
    mark_codex_disabled,
    mark_codex_recovered,
)

# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stderr",
    [
        "Error: rate limit exceeded for this account",
        "quota exhausted - try again later",
        "HTTP 429 Too Many Requests",
        "openai.RateLimitError: insufficient quota",
        "You exceeded your weekly limit",
        "Token limit exceeded for week",
        "RateLimitError: try again",
        "OAuth: refresh_token reused, please re-login",
        "You exceeded your current quota, please upgrade",
        "plan_quota reached for this account",
    ],
)
def test_is_quota_error_positive_cases(stderr: str) -> None:
    assert is_quota_error(stderr=stderr) is True


@pytest.mark.parametrize(
    "stderr",
    [
        "",
        "Connection timeout",
        "404 not found",
        "Some random response without keywords",
        "model_response_was_slow",
        "TLS handshake failed",
    ],
)
def test_is_quota_error_negative_cases(stderr: str) -> None:
    assert is_quota_error(stderr=stderr) is False


def test_is_quota_error_combined_stdout_stderr() -> None:
    # Если есть в stdout — тоже True
    assert is_quota_error(stdout="rate limit exceeded") is True
    # Empty в обоих → False
    assert is_quota_error(stderr="", stdout="") is False


def test_codex_quota_patterns_all_compile() -> None:
    """Все паттерны должны быть валидными regex."""
    for p in CODEX_QUOTA_PATTERNS:
        assert hasattr(p, "search")


# ---------------------------------------------------------------------------
# Classification (weekly vs transient)
# ---------------------------------------------------------------------------


def test_classify_quota_weekly_indicators() -> None:
    assert classify_quota(stderr="weekly limit reached") == "weekly"
    assert classify_quota(stderr="7-day quota cap") == "weekly"
    assert classify_quota(stdout="exceeded weekly cap") == "weekly"


def test_classify_quota_transient_default() -> None:
    assert classify_quota(stderr="rate limit exceeded") == "transient"
    assert classify_quota(stderr="HTTP 429") == "transient"


def test_cooldown_for_kind() -> None:
    assert cooldown_for_kind("weekly") == timedelta(days=7)
    assert cooldown_for_kind("transient") == timedelta(hours=1)
    assert cooldown_for_kind("unknown") == timedelta(hours=1)  # fallback


# ---------------------------------------------------------------------------
# CodexQuotaExhaustedError
# ---------------------------------------------------------------------------


def test_codex_quota_exhausted_error_default_kind() -> None:
    exc = CodexQuotaExhaustedError("test")
    assert exc.kind == "weekly"
    assert "test" in str(exc)


def test_codex_quota_exhausted_error_custom_kind() -> None:
    exc = CodexQuotaExhaustedError("transient hit", kind="transient")
    assert exc.kind == "transient"


# ---------------------------------------------------------------------------
# Transition state (debounced notification)
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Перенаправляет state file в tmp_path для изолированных тестов."""
    fake_state = tmp_path / "codex_quota_state.json"
    monkeypatch.setattr(cqs, "STATE_FILE", fake_state)
    return fake_state


def test_mark_codex_disabled_first_call_returns_transition(isolated_state: Path) -> None:
    is_transition = mark_codex_disabled(fallback_model="google/gemini-3-pro-preview")
    assert is_transition is True
    assert isolated_state.exists()
    assert cqs.is_codex_disabled() is True


def test_mark_codex_disabled_idempotent(isolated_state: Path) -> None:
    mark_codex_disabled(fallback_model="google/gemini-3-pro-preview")
    # Second call — already disabled, no transition
    assert mark_codex_disabled(fallback_model="google/gemini-3-pro-preview") is False


def test_mark_codex_recovered_returns_transition_when_disabled(
    isolated_state: Path,
) -> None:
    mark_codex_disabled(fallback_model="google/gemini-3-pro-preview")
    assert mark_codex_recovered() is True
    assert cqs.is_codex_disabled() is False


def test_mark_codex_recovered_no_op_when_already_enabled(isolated_state: Path) -> None:
    # Без предварительного disable — не transition
    assert mark_codex_recovered() is False


# ---------------------------------------------------------------------------
# Account rotator integration
# ---------------------------------------------------------------------------


def test_record_call_quota_uses_custom_cooldown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """record_call с cooldown=7d должен пометить account на 7 дней."""
    from src.integrations import codex_account_rotator as rotator

    fake_state = tmp_path / "codex_accounts.json"
    monkeypatch.setattr(rotator, "STATE_FILE", fake_state)

    rotator.record_call(
        "primary",
        success=False,
        error="rate limit exceeded",
        cooldown=timedelta(days=7),
    )
    state = rotator._load_state()
    assert "primary" in state
    until_str = state["primary"]["quota_exhausted_until"]
    until = datetime.fromisoformat(until_str.replace("Z", "+00:00"))
    delta = until - datetime.now(timezone.utc)
    # Должно быть около 7 дней (с допуском на time.now() drift)
    assert timedelta(days=6, hours=23) <= delta <= timedelta(days=7, minutes=1)


def test_record_call_default_cooldown_24h(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.integrations import codex_account_rotator as rotator

    fake_state = tmp_path / "codex_accounts.json"
    monkeypatch.setattr(rotator, "STATE_FILE", fake_state)

    rotator.record_call("primary", success=False, error="rate limit exceeded")
    state = rotator._load_state()
    until_str = state["primary"]["quota_exhausted_until"]
    until = datetime.fromisoformat(until_str.replace("Z", "+00:00"))
    delta = until - datetime.now(timezone.utc)
    # Default 24h
    assert timedelta(hours=23) <= delta <= timedelta(hours=24, minutes=1)


# ---------------------------------------------------------------------------
# complete_via_cli — codex rotation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_codex_quota_then_fallback_account_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First account hits quota, second account succeeds."""
    from src.integrations import cli_subprocess_bypass as bypass
    from src.integrations import codex_account_rotator as rotator
    from src.integrations import codex_quota_state as quota_state

    fake_state = tmp_path / "codex_accounts.json"
    monkeypatch.setattr(rotator, "STATE_FILE", fake_state)
    monkeypatch.setattr(quota_state, "STATE_FILE", tmp_path / "codex_quota_state.json")

    # Mock 2 accounts available
    accounts_iter = iter(
        [
            "/fake/.codex_accounts/primary",
            "/fake/.codex_accounts/secondary",
            None,  # после двух — никого
        ]
    )

    def fake_get_next() -> str | None:
        try:
            return next(accounts_iter)
        except StopIteration:
            return None

    monkeypatch.setattr(rotator, "get_next_codex_home", fake_get_next)
    monkeypatch.setattr(
        rotator,
        "list_accounts",
        lambda: [
            {"name": "primary", "logged_in": True, "available": True},
            {"name": "secondary", "logged_in": True, "available": True},
        ],
    )

    # Mock subprocess: first call returns quota error, second returns success
    call_count = {"n": 0}

    async def fake_run(**_kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return (1, "", "Error: rate limit exceeded weekly")
        return (0, "fallback success response", "")

    monkeypatch.setattr(bypass, "_run_codex_subprocess_once", fake_run)

    text = await bypass._complete_codex_with_account_rotation(
        binary_path="/usr/bin/codex",
        model_id="gpt-5.5",
        prompt_text="hello",
        timeout_sec=10.0,
    )
    assert text == "fallback success response"
    assert call_count["n"] == 2

    # Primary должен быть помечен exhausted с weekly cooldown
    state = rotator._load_state()
    assert state["primary"]["quota_exhausted_until"] is not None


@pytest.mark.asyncio
async def test_complete_codex_all_accounts_quota_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Все accounts hit quota → CodexQuotaExhaustedError."""
    from src.integrations import cli_subprocess_bypass as bypass
    from src.integrations import codex_account_rotator as rotator
    from src.integrations import codex_quota_state as quota_state

    monkeypatch.setattr(rotator, "STATE_FILE", tmp_path / "codex_accounts.json")
    monkeypatch.setattr(quota_state, "STATE_FILE", tmp_path / "codex_quota_state.json")

    homes = iter(["/fake/primary", "/fake/secondary", None, None])
    monkeypatch.setattr(rotator, "get_next_codex_home", lambda: next(homes, None))
    monkeypatch.setattr(
        rotator,
        "list_accounts",
        lambda: [
            {"name": "primary", "logged_in": True, "available": True},
            {"name": "secondary", "logged_in": True, "available": True},
        ],
    )

    async def fake_run(**_kw):
        return (1, "", "rate limit exceeded weekly quota")

    monkeypatch.setattr(bypass, "_run_codex_subprocess_once", fake_run)

    with pytest.raises(CodexQuotaExhaustedError) as exc_info:
        await bypass._complete_codex_with_account_rotation(
            binary_path="/usr/bin/codex",
            model_id="gpt-5.5",
            prompt_text="hello",
            timeout_sec=10.0,
        )
    assert exc_info.value.kind == "weekly"


@pytest.mark.asyncio
async def test_complete_codex_no_accounts_logged_in_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Если ни один account не залогинен — сразу CodexQuotaExhaustedError."""
    from src.integrations import cli_subprocess_bypass as bypass
    from src.integrations import codex_account_rotator as rotator
    from src.integrations import codex_quota_state as quota_state

    monkeypatch.setattr(rotator, "STATE_FILE", tmp_path / "codex_accounts.json")
    monkeypatch.setattr(quota_state, "STATE_FILE", tmp_path / "codex_quota_state.json")
    monkeypatch.setattr(rotator, "get_next_codex_home", lambda: None)
    monkeypatch.setattr(rotator, "list_accounts", lambda: [])

    with pytest.raises(CodexQuotaExhaustedError):
        await bypass._complete_codex_with_account_rotation(
            binary_path="/usr/bin/codex",
            model_id="gpt-5.5",
            prompt_text="hello",
            timeout_sec=10.0,
        )


# ---------------------------------------------------------------------------
# Owner notification debouncing
# ---------------------------------------------------------------------------


def test_owner_notification_only_fires_once_per_window(isolated_state: Path) -> None:
    """mark_codex_disabled должен вернуть transition=True только при первом вызове."""
    transitions = []
    for _ in range(5):
        transitions.append(mark_codex_disabled(fallback_model="google/gemini-3-pro-preview"))
    assert transitions == [True, False, False, False, False]


def test_recovery_then_disable_again_fires_new_transition(isolated_state: Path) -> None:
    """После recovery — следующий disable снова transition."""
    assert mark_codex_disabled(fallback_model="x") is True
    assert mark_codex_recovered() is True
    assert mark_codex_disabled(fallback_model="x") is True
