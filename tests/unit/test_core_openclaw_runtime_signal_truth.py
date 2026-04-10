# -*- coding: utf-8 -*-
"""
Тесты для src/core/openclaw_runtime_signal_truth.py — парсинг gateway-логов
и sessions.json для выявления silent fallbacks и auth-ошибок.

HIGH RISK: 0 тестов ранее. Покрываем ключевые функции:
broken_models_from_signal_log, runtime_auth_failed_providers_from_signal_log,
resolve_probe_runtime_truth, model_matches, provider_from_model, parse_gateway_log_epoch.
"""

from __future__ import annotations

import json
import time

import pytest

from src.core.openclaw_runtime_signal_truth import (
    _session_updated_at_epoch,
    broken_models_from_signal_log,
    compose_session_runtime_model,
    model_matches,
    parse_gateway_log_epoch,
    provider_from_model,
    resolve_probe_runtime_truth,
    runtime_auth_failed_providers_from_signal_log,
)

# ------------------------------------------------------------------
# model_matches
# ------------------------------------------------------------------


class TestModelMatches:
    def test_exact_match(self) -> None:
        assert model_matches("google/gemini-3-pro", "google/gemini-3-pro") is True

    def test_short_key_matches_full(self) -> None:
        assert model_matches("gemini-3-pro", "google/gemini-3-pro") is True

    def test_no_match(self) -> None:
        assert model_matches("gemini-3-pro", "openai/gpt-4") is False

    def test_provider_prefix_no_match(self) -> None:
        assert model_matches("google/gemini-3-pro", "openai/gemini-3-pro") is False


# ------------------------------------------------------------------
# provider_from_model
# ------------------------------------------------------------------


class TestProviderFromModel:
    def test_with_slash(self) -> None:
        assert provider_from_model("google/gemini-3-pro") == "google"

    def test_without_slash(self) -> None:
        assert provider_from_model("gemini-3-pro") == ""


# ------------------------------------------------------------------
# compose_session_runtime_model
# ------------------------------------------------------------------


class TestComposeSessionRuntimeModel:
    def test_both_fields(self) -> None:
        assert (
            compose_session_runtime_model({"modelProvider": "google", "model": "gemini-3-pro"})
            == "google/gemini-3-pro"
        )

    def test_model_only(self) -> None:
        assert compose_session_runtime_model({"model": "gemini-3-pro"}) == "gemini-3-pro"

    def test_empty(self) -> None:
        assert compose_session_runtime_model({}) == ""


# ------------------------------------------------------------------
# parse_gateway_log_epoch
# ------------------------------------------------------------------


class TestParseGatewayLogEpoch:
    def test_iso_z_format(self) -> None:
        epoch = parse_gateway_log_epoch("2026-04-10T02:00:00Z [info] something")
        assert epoch is not None
        assert epoch > 0

    def test_iso_offset_format(self) -> None:
        epoch = parse_gateway_log_epoch("2026-04-10T04:00:00+02:00 [info] something")
        assert epoch is not None

    def test_no_timestamp(self) -> None:
        assert parse_gateway_log_epoch("random log line without ts") is None


# ------------------------------------------------------------------
# _session_updated_at_epoch
# ------------------------------------------------------------------


class TestSessionUpdatedAtEpoch:
    def test_epoch_seconds(self) -> None:
        assert _session_updated_at_epoch({"updatedAt": 1700000000}) == 1700000000.0

    def test_epoch_millis(self) -> None:
        assert _session_updated_at_epoch({"updatedAt": 1700000000000}) == pytest.approx(
            1700000000.0, abs=1
        )

    def test_iso_string(self) -> None:
        val = _session_updated_at_epoch({"updatedAt": "2026-04-10T00:00:00Z"})
        assert val > 0

    def test_missing_key(self) -> None:
        assert _session_updated_at_epoch({}) == 0.0

    def test_alternative_keys(self) -> None:
        val = _session_updated_at_epoch({"lastUpdatedAt": 1700000000})
        assert val == 1700000000.0


# ------------------------------------------------------------------
# broken_models_from_signal_log
# ------------------------------------------------------------------


class TestBrokenModels:
    def test_not_found_pattern(self, tmp_path) -> None:
        log = tmp_path / "gw.log"
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        log.write_text(
            f'{ts} [error] Model "old-model-v1" not found in available models\n'
            f"{ts} [error] model `broken-model` does not exist\n",
            encoding="utf-8",
        )
        broken = broken_models_from_signal_log(log)
        assert "old-model-v1" in broken
        assert "broken-model" in broken

    def test_no_broken_models(self, tmp_path) -> None:
        log = tmp_path / "gw.log"
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        log.write_text(f"{ts} [info] all models healthy\n", encoding="utf-8")
        assert broken_models_from_signal_log(log) == set()

    def test_nonexistent_path(self, tmp_path) -> None:
        fake = tmp_path / "nonexistent.log"
        assert broken_models_from_signal_log(fake) == set()

    def test_none_path(self) -> None:
        assert broken_models_from_signal_log(None) == set()

    def test_dedup(self, tmp_path) -> None:
        log = tmp_path / "gw.log"
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        log.write_text(
            f'{ts} Model "dup-model" not found\n' * 3,
            encoding="utf-8",
        )
        broken = broken_models_from_signal_log(log)
        assert len(broken) == 1
        assert "dup-model" in broken


# ------------------------------------------------------------------
# runtime_auth_failed_providers_from_signal_log
# ------------------------------------------------------------------


class TestAuthFailedProviders:
    def test_scope_error_pattern(self, tmp_path) -> None:
        log = tmp_path / "gw.log"
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        log.write_text(
            f"{ts} runtime_missing_scope_model_request model=google/gemini-3-pro scope=operator.write\n",
            encoding="utf-8",
        )
        failed = runtime_auth_failed_providers_from_signal_log(log)
        assert "google" in failed

    def test_no_auth_errors(self, tmp_path) -> None:
        log = tmp_path / "gw.log"
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        log.write_text(f"{ts} [info] healthy\n", encoding="utf-8")
        assert runtime_auth_failed_providers_from_signal_log(log) == set()

    def test_none_path(self) -> None:
        assert runtime_auth_failed_providers_from_signal_log(None) == set()


# ------------------------------------------------------------------
# resolve_probe_runtime_truth
# ------------------------------------------------------------------


class TestResolveProbeRuntimeTruth:
    def test_confirmed_without_data(self) -> None:
        """Без логов/sessions — signal_log_unavailable."""
        result = resolve_probe_runtime_truth(requested_model="google/gemini-3-pro")
        assert result["requested"] == "google/gemini-3-pro"
        assert result["reason"] == "signal_log_unavailable"
        assert result["fallback"] is None

    def test_response_model_mismatch(self) -> None:
        """response_model отличается → session_runtime_fallback."""
        result = resolve_probe_runtime_truth(
            requested_model="google/gemini-3-pro",
            response_model="google/gemini-2.5-flash",
        )
        assert result["fallback"] == "google/gemini-2.5-flash"
        assert result["reason"] == "session_runtime_fallback"

    def test_response_model_match(self) -> None:
        """response_model совпадает → без gateway_log_path reason = signal_log_unavailable."""
        result = resolve_probe_runtime_truth(
            requested_model="google/gemini-3-pro",
            response_model="google/gemini-3-pro",
        )
        # Нет fallback, но без gateway_log_path reason показывает unavailable
        assert result["fallback"] is None

    def test_sessions_json_fallback(self, tmp_path) -> None:
        """sessions.json показывает fallback на другую модель."""
        sessions = tmp_path / "sessions.json"
        now_ms = int(time.time() * 1000)
        sessions.write_text(
            json.dumps(
                [
                    {
                        "id": "session:agent:main:openai:abc-123",
                        "modelProvider": "google",
                        "model": "gemini-2.5-flash",
                        "updatedAt": now_ms,
                    },
                ]
            ),
            encoding="utf-8",
        )
        result = resolve_probe_runtime_truth(
            requested_model="google/gemini-3-pro",
            sessions_path=sessions,
        )
        assert result["fallback"] == "google/gemini-2.5-flash"
        assert result["reason"] == "session_runtime_fallback"

    def test_gateway_log_fallback(self, tmp_path) -> None:
        """gateway log содержит model-fallback запись."""
        log = tmp_path / "gw.log"
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        log.write_text(
            f'{ts} [model-fallback] Model "google/gemini-3-pro" is unavailable. '
            f'Fell back to "google/gemini-2.5-flash".\n',
            encoding="utf-8",
        )
        result = resolve_probe_runtime_truth(
            requested_model="google/gemini-3-pro",
            gateway_log_path=log,
        )
        assert result["fallback"] == "google/gemini-2.5-flash"
        assert result["reason"] == "model_fallback_log"

    def test_confirmed_with_clean_log(self, tmp_path) -> None:
        """Чистый лог без fallback → requested_model_confirmed."""
        log = tmp_path / "gw.log"
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        log.write_text(f"{ts} [info] model loaded OK\n", encoding="utf-8")
        result = resolve_probe_runtime_truth(
            requested_model="google/gemini-3-pro",
            gateway_log_path=log,
        )
        assert result["reason"] == "requested_model_confirmed"
