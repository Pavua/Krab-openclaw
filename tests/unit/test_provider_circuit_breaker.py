# -*- coding: utf-8 -*-
"""Unit tests for ProviderCircuitBreaker."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.core.provider_circuit_breaker import (
    FAILURE_THRESHOLD,
    RECOVERY_SEC,
    ProviderCircuitBreaker,
    _normalize_provider,
)


@pytest.fixture()
def cb(tmp_path: Path) -> ProviderCircuitBreaker:
    """Fresh circuit breaker backed by a temp file."""
    return ProviderCircuitBreaker(state_file=tmp_path / "cb.json")


# ── Core behaviour ─────────────────────────────────────────────────────────────

def test_circuit_trips_after_threshold(cb: ProviderCircuitBreaker) -> None:
    """After FAILURE_THRESHOLD auth errors the provider must be tripped."""
    for i in range(FAILURE_THRESHOLD - 1):
        tripped = cb.record_failure("openai-codex", "auth")
        assert not tripped, f"should not trip on failure {i + 1}"

    tripped = cb.record_failure("openai-codex", "auth")
    assert tripped, "must trip on the threshold-th failure"
    assert cb.is_tripped("openai-codex")


def test_circuit_not_tripped_for_unknown_provider(cb: ProviderCircuitBreaker) -> None:
    assert not cb.is_tripped("unknown-provider")


def test_untracked_error_kind_ignored(cb: ProviderCircuitBreaker) -> None:
    """network / timeout errors must not count toward the threshold."""
    for _ in range(FAILURE_THRESHOLD + 5):
        cb.record_failure("openai-codex", "network")
    assert not cb.is_tripped("openai-codex")


def test_success_resets_counter(cb: ProviderCircuitBreaker) -> None:
    """A success before threshold clears accumulated failures."""
    for _ in range(FAILURE_THRESHOLD - 1):
        cb.record_failure("google-antigravity", "quota")
    cb.record_success("google-antigravity")

    # One more failure should NOT trip (counter was reset)
    tripped = cb.record_failure("google-antigravity", "quota")
    assert not tripped


def test_circuit_recovers_after_cooldown(
    cb: ProviderCircuitBreaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After RECOVERY_SEC the provider should no longer be tripped."""
    # Trip the circuit
    for _ in range(FAILURE_THRESHOLD):
        cb.record_failure("qwen-portal", "auth")
    assert cb.is_tripped("qwen-portal")

    # Fast-forward time past recovery
    future = time.time() + RECOVERY_SEC + 1
    monkeypatch.setattr(time, "time", lambda: future)

    assert not cb.is_tripped("qwen-portal")


def test_state_persists_to_file(tmp_path: Path) -> None:
    """Failures written by one instance must be visible to a second instance."""
    state_file = tmp_path / "cb.json"
    cb1 = ProviderCircuitBreaker(state_file=state_file)
    for _ in range(FAILURE_THRESHOLD):
        cb1.record_failure("openai-codex", "auth")

    cb2 = ProviderCircuitBreaker(state_file=state_file)
    assert cb2.is_tripped("openai-codex")


def test_no_double_trip_accumulation(cb: ProviderCircuitBreaker) -> None:
    """While tripped, additional failures must not reset or extend cooldown."""
    for _ in range(FAILURE_THRESHOLD):
        cb.record_failure("openai-codex", "auth")

    tripped_until_before = cb.get_status()["openai-codex"]["tripped_until"]
    cb.record_failure("openai-codex", "auth")  # extra call while tripped
    tripped_until_after = cb.get_status()["openai-codex"]["tripped_until"]

    assert tripped_until_before == tripped_until_after


def test_reset_provider_clears_trip(cb: ProviderCircuitBreaker) -> None:
    """reset_provider() must immediately clear the trip state."""
    for _ in range(FAILURE_THRESHOLD):
        cb.record_failure("openai-codex", "auth")
    assert cb.is_tripped("openai-codex")

    cb.reset_provider("openai-codex")
    assert not cb.is_tripped("openai-codex")


# ── Normalization ──────────────────────────────────────────────────────────────

def test_normalize_provider_strips_model() -> None:
    assert _normalize_provider("openai-codex/gpt-5.4") == "openai-codex"


def test_normalize_provider_lowercases() -> None:
    assert _normalize_provider("Google-Antigravity") == "google-antigravity"


def test_normalize_provider_empty_string() -> None:
    assert _normalize_provider("") == ""
