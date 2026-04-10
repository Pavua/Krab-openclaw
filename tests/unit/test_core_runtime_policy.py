# -*- coding: utf-8 -*-
"""Тесты для src/core/runtime_policy.py — runtime-режимы и provider policy."""

from __future__ import annotations

from src.core.runtime_policy import (
    current_runtime_mode,
    provider_runtime_policy,
    runtime_mode_release_safe,
)

# ---------------------------------------------------------------------------
# current_runtime_mode
# ---------------------------------------------------------------------------


class TestCurrentRuntimeMode:
    """Определение канонического runtime-режима из переменных окружения."""

    def test_default_personal_runtime(self, monkeypatch):
        """Без переменных окружения возвращается personal-runtime."""
        monkeypatch.delenv("KRAB_RUNTIME_MODE", raising=False)
        monkeypatch.delenv("OPENCLAW_RUNTIME_MODE", raising=False)
        assert current_runtime_mode() == "personal-runtime"

    def test_alias_personal(self, monkeypatch):
        """Алиас 'personal' нормализуется в 'personal-runtime'."""
        monkeypatch.setenv("KRAB_RUNTIME_MODE", "personal")
        assert current_runtime_mode() == "personal-runtime"

    def test_alias_release(self, monkeypatch):
        """Алиас 'release' нормализуется в 'release-safe-runtime'."""
        monkeypatch.setenv("KRAB_RUNTIME_MODE", "release")
        assert current_runtime_mode() == "release-safe-runtime"

    def test_alias_release_safe(self, monkeypatch):
        monkeypatch.setenv("KRAB_RUNTIME_MODE", "release-safe")
        assert current_runtime_mode() == "release-safe-runtime"

    def test_alias_lab(self, monkeypatch):
        monkeypatch.setenv("KRAB_RUNTIME_MODE", "lab")
        assert current_runtime_mode() == "lab-runtime"

    def test_alias_lab_runtime(self, monkeypatch):
        monkeypatch.setenv("KRAB_RUNTIME_MODE", "lab-runtime")
        assert current_runtime_mode() == "lab-runtime"

    def test_case_insensitive(self, monkeypatch):
        """Значение переменной регистронезависимо."""
        monkeypatch.setenv("KRAB_RUNTIME_MODE", "RELEASE")
        assert current_runtime_mode() == "release-safe-runtime"

    def test_openclaw_fallback_env(self, monkeypatch):
        """KRAB_RUNTIME_MODE пустой — берётся OPENCLAW_RUNTIME_MODE."""
        monkeypatch.setenv("KRAB_RUNTIME_MODE", "")
        monkeypatch.setenv("OPENCLAW_RUNTIME_MODE", "lab")
        assert current_runtime_mode() == "lab-runtime"

    def test_unknown_value_defaults_to_personal(self, monkeypatch):
        """Неизвестный алиас -> personal-runtime (safe default)."""
        monkeypatch.setenv("KRAB_RUNTIME_MODE", "some-exotic-mode")
        assert current_runtime_mode() == "personal-runtime"


# ---------------------------------------------------------------------------
# runtime_mode_release_safe
# ---------------------------------------------------------------------------


class TestRuntimeModeReleaseSafe:
    """Проверка флага release-safe для режима."""

    def test_release_safe_runtime_is_safe(self):
        assert runtime_mode_release_safe("release-safe-runtime") is True

    def test_personal_runtime_is_not_safe(self):
        assert runtime_mode_release_safe("personal-runtime") is False

    def test_lab_runtime_is_not_safe(self):
        assert runtime_mode_release_safe("lab-runtime") is False

    def test_empty_string_is_not_safe(self):
        assert runtime_mode_release_safe("") is False

    def test_case_insensitive_check(self):
        """Проверка нечувствительна к регистру: strip().lower() применяется внутри."""
        assert runtime_mode_release_safe("Release-Safe-Runtime") is True


# ---------------------------------------------------------------------------
# provider_runtime_policy
# ---------------------------------------------------------------------------


class TestProviderRuntimePolicy:
    """Формирование machine-readable policy для провайдеров."""

    def test_known_provider_google_defaults(self, monkeypatch):
        """Провайдер 'google' — release_safe=True, cost_tier=api."""
        monkeypatch.delenv("KRAB_RUNTIME_MODE", raising=False)
        monkeypatch.delenv("OPENCLAW_RUNTIME_MODE", raising=False)
        policy = provider_runtime_policy("google")
        assert policy["release_safe"] is True
        assert policy["cost_tier"] == "api"
        assert policy["primary_policy"] == "release-safe"

    def test_known_provider_google_antigravity_blocked(self, monkeypatch):
        """google-antigravity заблокирован как primary."""
        monkeypatch.delenv("KRAB_RUNTIME_MODE", raising=False)
        monkeypatch.delenv("OPENCLAW_RUNTIME_MODE", raising=False)
        policy = provider_runtime_policy("google-antigravity")
        assert policy["primary_policy"] == "blocked"
        assert policy["release_safe"] is False

    def test_unknown_provider_defaults(self, monkeypatch):
        """Неизвестный провайдер — безопасные defaults."""
        monkeypatch.delenv("KRAB_RUNTIME_MODE", raising=False)
        monkeypatch.delenv("OPENCLAW_RUNTIME_MODE", raising=False)
        policy = provider_runtime_policy("totally-unknown-provider")
        assert policy["primary_policy"] == "fallback-only"
        assert policy["fallback_policy"] == "fallback-only"
        assert policy["release_safe"] is False
        assert policy["cost_tier"] == "unknown"

    def test_readiness_ready_sets_login_state(self, monkeypatch):
        """readiness='ready' -> login_state='ready'."""
        monkeypatch.delenv("KRAB_RUNTIME_MODE", raising=False)
        monkeypatch.delenv("OPENCLAW_RUNTIME_MODE", raising=False)
        policy = provider_runtime_policy("google", readiness="ready")
        assert policy["login_state"] == "ready"

    def test_readiness_blocked_with_helper(self, monkeypatch):
        """readiness='blocked', helper_available=True -> login_required."""
        monkeypatch.delenv("KRAB_RUNTIME_MODE", raising=False)
        monkeypatch.delenv("OPENCLAW_RUNTIME_MODE", raising=False)
        policy = provider_runtime_policy("google", readiness="blocked", helper_available=True)
        assert policy["login_state"] == "login_required"

    def test_readiness_blocked_without_helper(self, monkeypatch):
        """readiness='blocked', helper_available=False -> unavailable."""
        monkeypatch.delenv("KRAB_RUNTIME_MODE", raising=False)
        monkeypatch.delenv("OPENCLAW_RUNTIME_MODE", raising=False)
        policy = provider_runtime_policy("google", readiness="blocked", helper_available=False)
        assert policy["login_state"] == "unavailable"

    def test_oauth_expired_sets_login_required(self, monkeypatch):
        """oauth_status=expired + auth_mode=oauth -> login_required."""
        monkeypatch.delenv("KRAB_RUNTIME_MODE", raising=False)
        monkeypatch.delenv("OPENCLAW_RUNTIME_MODE", raising=False)
        policy = provider_runtime_policy(
            "google",
            readiness="ready",
            auth_mode="oauth",
            oauth_status="expired",
        )
        assert policy["login_state"] == "login_required"

    def test_cli_auth_not_ready(self, monkeypatch):
        """auth_mode=cli, cli_login_ready=False -> понижается stability."""
        monkeypatch.delenv("KRAB_RUNTIME_MODE", raising=False)
        monkeypatch.delenv("OPENCLAW_RUNTIME_MODE", raising=False)
        policy_ready = provider_runtime_policy(
            "google", readiness="ready", auth_mode="cli", cli_login_ready=True
        )
        policy_not_ready = provider_runtime_policy(
            "google", readiness="ready", auth_mode="cli", cli_login_ready=False
        )
        assert policy_not_ready["stability_score"] < policy_ready["stability_score"]

    def test_legacy_flag_downgrades_policy(self, monkeypatch):
        """legacy=True -> primary_policy='lab-only', stability_score <= 0.2."""
        monkeypatch.delenv("KRAB_RUNTIME_MODE", raising=False)
        monkeypatch.delenv("OPENCLAW_RUNTIME_MODE", raising=False)
        policy = provider_runtime_policy("google", legacy=True)
        assert policy["primary_policy"] == "lab-only"
        assert policy["stability_score"] <= 0.2

    def test_quota_exhausted_reduces_stability(self, monkeypatch):
        """quota_state=exhausted снижает stability_score."""
        monkeypatch.delenv("KRAB_RUNTIME_MODE", raising=False)
        monkeypatch.delenv("OPENCLAW_RUNTIME_MODE", raising=False)
        policy_ok = provider_runtime_policy("google", readiness="ready")
        policy_exhausted = provider_runtime_policy(
            "google", readiness="ready", quota_state="exhausted"
        )
        assert policy_exhausted["stability_score"] < policy_ok["stability_score"]

    def test_stability_score_clamped_0_05_to_0_99(self, monkeypatch):
        """Stability score всегда в диапазоне [0.05, 0.99]."""
        monkeypatch.delenv("KRAB_RUNTIME_MODE", raising=False)
        monkeypatch.delenv("OPENCLAW_RUNTIME_MODE", raising=False)
        # Много штрафов одновременно
        policy = provider_runtime_policy(
            "qwen-portal",
            readiness="blocked",
            auth_mode="oauth",
            oauth_status="expired",
            quota_state="exhausted",
            legacy=True,
        )
        assert 0.05 <= policy["stability_score"] <= 0.99

    def test_result_contains_all_required_keys(self, monkeypatch):
        """Результат содержит все обязательные поля."""
        monkeypatch.delenv("KRAB_RUNTIME_MODE", raising=False)
        monkeypatch.delenv("OPENCLAW_RUNTIME_MODE", raising=False)
        policy = provider_runtime_policy("lmstudio")
        required_keys = {
            "runtime_mode",
            "primary_policy",
            "fallback_policy",
            "release_safe",
            "login_state",
            "cost_tier",
            "stability_score",
        }
        assert required_keys.issubset(policy.keys())

    def test_readiness_attention_penalizes_stability(self, monkeypatch):
        """readiness='attention' снижает stability на 0.18."""
        monkeypatch.delenv("KRAB_RUNTIME_MODE", raising=False)
        monkeypatch.delenv("OPENCLAW_RUNTIME_MODE", raising=False)
        base = provider_runtime_policy("google", readiness="ready")
        attention = provider_runtime_policy("google", readiness="attention")
        assert attention["login_state"] == "attention"
        assert attention["stability_score"] < base["stability_score"]

    def test_lmstudio_provider_local_cost_tier(self, monkeypatch):
        """lmstudio провайдер — cost_tier='local', release_safe=True."""
        monkeypatch.delenv("KRAB_RUNTIME_MODE", raising=False)
        monkeypatch.delenv("OPENCLAW_RUNTIME_MODE", raising=False)
        policy = provider_runtime_policy("lmstudio")
        assert policy["cost_tier"] == "local"
        assert policy["release_safe"] is True

    def test_runtime_mode_in_result(self, monkeypatch):
        """Поле runtime_mode в результате соответствует текущему окружению."""
        monkeypatch.setenv("KRAB_RUNTIME_MODE", "lab")
        policy = provider_runtime_policy("google")
        assert policy["runtime_mode"] == "lab-runtime"
