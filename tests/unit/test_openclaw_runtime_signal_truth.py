# -*- coding: utf-8 -*-
"""
Тесты src/core/openclaw_runtime_signal_truth.py — парсинг gateway-логов,
определение broken-моделей, auth-ошибок и silent fallback.

Используем фикстурные log-строки, tmp_path для файлов.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.core.openclaw_runtime_signal_truth import (
    _read_sessions_payload,
    _session_updated_at_epoch,
    _unique_existing_paths,
    broken_models_from_signal_log,
    compose_session_runtime_model,
    model_matches,
    parse_gateway_log_epoch,
    provider_from_model,
    recent_gateway_log_lines,
    resolve_probe_runtime_truth,
    runtime_auth_failed_providers_from_signal_log,
)

# -- фикстуры --


def _ts_iso(offset_sec: float = 0.0) -> str:
    """ISO timestamp сдвинутый от текущего момента."""
    dt = datetime.fromtimestamp(time.time() + offset_sec, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@pytest.fixture
def log_file(tmp_path: Path) -> Path:
    return tmp_path / "gateway.log"


# -- model_matches --


class TestModelMatches:
    """Сопоставление модели: full key и хвост после provider-префикса."""

    def test_exact_match(self) -> None:
        assert model_matches("gemini-2.5-pro", "gemini-2.5-pro") is True

    def test_provider_prefix_match(self) -> None:
        assert model_matches("gemini-2.5-pro", "google/gemini-2.5-pro") is True

    def test_no_match(self) -> None:
        assert model_matches("gemini-2.5-pro", "openai/gpt-4o") is False

    def test_partial_name_no_match(self) -> None:
        # "pro" != "gemini-2.5-pro"
        assert model_matches("pro", "google/gemini-2.5-pro") is False


# -- provider_from_model --


def test_provider_from_model_with_slash() -> None:
    assert provider_from_model("google/gemini-2.5-pro") == "google"


def test_provider_from_model_no_slash() -> None:
    assert provider_from_model("gemini-2.5-pro") == ""


# -- compose_session_runtime_model --


def test_compose_full() -> None:
    sess = {"modelProvider": "google", "model": "gemini-2.5-pro"}
    assert compose_session_runtime_model(sess) == "google/gemini-2.5-pro"


def test_compose_model_only() -> None:
    sess = {"model": "gemini-2.5-pro"}
    assert compose_session_runtime_model(sess) == "gemini-2.5-pro"


def test_compose_empty() -> None:
    assert compose_session_runtime_model({}) == ""


# -- _session_updated_at_epoch --


class TestSessionUpdatedAtEpoch:
    """Нормализация updatedAt из sessions.json в epoch seconds."""

    def test_millis(self) -> None:
        # 1712000000000 ms = 1712000000.0 s
        epoch = _session_updated_at_epoch({"updatedAt": 1712000000000})
        assert epoch == pytest.approx(1712000000.0)

    def test_seconds(self) -> None:
        epoch = _session_updated_at_epoch({"updatedAt": 1712000000})
        assert epoch == pytest.approx(1712000000.0)

    def test_iso_string(self) -> None:
        epoch = _session_updated_at_epoch({"updatedAt": "2024-04-01T20:00:00Z"})
        assert epoch > 0

    def test_missing_keys(self) -> None:
        assert _session_updated_at_epoch({}) == 0.0

    def test_fallback_key(self) -> None:
        # lastUpdatedAt — альтернативное имя
        epoch = _session_updated_at_epoch({"lastUpdatedAt": 1712000000})
        assert epoch == pytest.approx(1712000000.0)


# -- parse_gateway_log_epoch --


def test_parse_iso_timestamp() -> None:
    line = "2026-04-09T12:30:00.123Z [info] something"
    epoch = parse_gateway_log_epoch(line)
    assert epoch is not None
    assert epoch > 0


def test_parse_no_timestamp() -> None:
    assert parse_gateway_log_epoch("no timestamp here") is None


# -- recent_gateway_log_lines --


def test_recent_lines_filters_old(log_file: Path) -> None:
    """Строки старше max_age_sec отфильтровываются."""
    old_ts = "2020-01-01T00:00:00Z"
    fresh_ts = _ts_iso()
    log_file.write_text(
        f"{old_ts} [info] old event\n{fresh_ts} [info] fresh event\n",
        encoding="utf-8",
    )
    lines = recent_gateway_log_lines(log_file, max_age_sec=60)
    assert len(lines) == 1
    assert "fresh event" in lines[0]


def test_recent_lines_keeps_unparseable(log_file: Path) -> None:
    """Строки без timestamp сохраняются (conservative fallback)."""
    log_file.write_text("no-timestamp line\n", encoding="utf-8")
    lines = recent_gateway_log_lines(log_file, max_age_sec=60)
    assert len(lines) == 1


def test_recent_lines_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent.log"
    assert recent_gateway_log_lines(missing) == []


# -- broken_models_from_signal_log --


def test_broken_models_not_found(log_file: Path) -> None:
    ts = _ts_iso()
    log_file.write_text(
        f'{ts} [warn] Model "openai/gpt-5-turbo" not found in registry\n'
        f'{ts} [warn] Model "google/gemini-9" not found\n',
        encoding="utf-8",
    )
    broken = broken_models_from_signal_log(log_file)
    assert "openai/gpt-5-turbo" in broken
    assert "google/gemini-9" in broken


def test_broken_models_does_not_exist(log_file: Path) -> None:
    ts = _ts_iso()
    log_file.write_text(
        f"{ts} [warn] model `fake-model-xyz` does not exist\n",
        encoding="utf-8",
    )
    broken = broken_models_from_signal_log(log_file)
    assert "fake-model-xyz" in broken


def test_broken_models_none_path() -> None:
    assert broken_models_from_signal_log(None) == set()


# -- runtime_auth_failed_providers_from_signal_log --


def test_auth_failed_providers(log_file: Path) -> None:
    ts = _ts_iso()
    log_file.write_text(
        f"{ts} runtime_missing_scope_model_request model=google/gemini-2.5-pro foo\n"
        f"{ts} runtime_missing_scope_model_request model=anthropic/claude-4 bar\n",
        encoding="utf-8",
    )
    failed = runtime_auth_failed_providers_from_signal_log(log_file)
    assert "google" in failed
    assert "anthropic" in failed


def test_auth_failed_providers_none_path() -> None:
    assert runtime_auth_failed_providers_from_signal_log(None) == set()


# -- _unique_existing_paths --


def test_unique_existing_paths(tmp_path: Path) -> None:
    a = tmp_path / "a.log"
    a.touch()
    result = _unique_existing_paths([a, a, None, tmp_path / "nonexistent"])
    assert result == [a]


# -- _read_sessions_payload --


def test_read_sessions_list(tmp_path: Path) -> None:
    p = tmp_path / "sessions.json"
    p.write_text(json.dumps([{"id": "s1"}, {"id": "s2"}]))
    assert len(_read_sessions_payload(p)) == 2


def test_read_sessions_dict(tmp_path: Path) -> None:
    p = tmp_path / "sessions.json"
    p.write_text(json.dumps({"a": {"id": "s1"}, "b": {"id": "s2"}}))
    assert len(_read_sessions_payload(p)) == 2


def test_read_sessions_corrupt(tmp_path: Path) -> None:
    p = tmp_path / "sessions.json"
    p.write_text("not json at all")
    assert _read_sessions_payload(p) == []


# -- resolve_probe_runtime_truth --


def test_resolve_confirmed_by_response_model(log_file: Path) -> None:
    """response_model совпадает — подтверждаем requested."""
    ts = _ts_iso()
    log_file.write_text(f"{ts} [info] normal operation\n", encoding="utf-8")
    result = resolve_probe_runtime_truth(
        requested_model="gemini-2.5-pro",
        response_model="google/gemini-2.5-pro",
        gateway_log_path=log_file,
    )
    assert result["reason"] == "requested_model_confirmed"
    assert result["fallback"] is None


def test_resolve_fallback_by_response_model() -> None:
    """response_model отличается — детектируем fallback."""
    result = resolve_probe_runtime_truth(
        requested_model="gemini-2.5-pro",
        response_model="google/gemini-2.5-flash",
    )
    assert result["reason"] == "session_runtime_fallback"
    assert result["fallback"] == "google/gemini-2.5-flash"


def test_resolve_fallback_from_log(log_file: Path) -> None:
    """[model-fallback] запись в логе детектируется."""
    ts = _ts_iso()
    log_file.write_text(
        f'{ts} [model-fallback] Model "gemini-2.5-pro" unavailable. '
        f'Fell back to "gemini-2.5-flash".\n',
        encoding="utf-8",
    )
    result = resolve_probe_runtime_truth(
        requested_model="gemini-2.5-pro",
        gateway_log_path=log_file,
        max_age_sec=60,
    )
    assert result["reason"] == "model_fallback_log"
    assert result["fallback"] == "gemini-2.5-flash"


def test_resolve_no_log_path() -> None:
    """Без gateway_log_path — reason = signal_log_unavailable."""
    result = resolve_probe_runtime_truth(
        requested_model="gemini-2.5-pro",
        gateway_log_path=None,
    )
    assert result["reason"] == "signal_log_unavailable"
