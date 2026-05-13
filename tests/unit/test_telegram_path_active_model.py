# -*- coding: utf-8 -*-
"""Wave 246: тесты Telegram-path resolver active_model.json.

Гарантируем:
1. ``read_active_model_id`` возвращает model id или пустую строку без
   exceptions на любом IO/JSON-сбое.
2. ``resolve_telegram_selected_model`` чтит приоритеты:
   force_all_paths → preferred → active_model_file → fallback.
3. ``KRAB_ACTIVE_MODEL_FORCE_ALL_PATHS=1`` побеждает даже явный
   ``preferred_model``.
4. Поведение module-level reader изолировано от глобальных env.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core import active_model_routing as amr


@pytest.fixture(autouse=True)
def _clear_force_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Каждый тест стартует с чистым env, чтобы кросс-тест leak не ломал."""
    monkeypatch.delenv("KRAB_ACTIVE_MODEL_FORCE_ALL_PATHS", raising=False)


def _write_active(tmp_path: Path, model: str) -> Path:
    p = tmp_path / "active_model.json"
    p.write_text(
        json.dumps({"model": model, "switched_at": 1700000000, "switched_by": "test"}),
        encoding="utf-8",
    )
    return p


# --------------------------------------------------------------------------- #
# read_active_model_id
# --------------------------------------------------------------------------- #


def test_read_active_model_id_returns_model_when_file_valid(tmp_path: Path) -> None:
    p = _write_active(tmp_path, "mlx-local-kv4/gemma-4-26b")
    assert amr.read_active_model_id(path=p) == "mlx-local-kv4/gemma-4-26b"


def test_read_active_model_id_returns_empty_on_missing_file(tmp_path: Path) -> None:
    assert amr.read_active_model_id(path=tmp_path / "nope.json") == ""


def test_read_active_model_id_returns_empty_on_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "broken.json"
    p.write_text("{ not valid json", encoding="utf-8")
    assert amr.read_active_model_id(path=p) == ""


# --------------------------------------------------------------------------- #
# resolve_telegram_selected_model — приоритеты
# --------------------------------------------------------------------------- #


def test_resolve_uses_active_model_when_preferred_none(tmp_path: Path) -> None:
    p = _write_active(tmp_path, "mlx-local-kv4/gemma-4-26b")
    resolved, source = amr.resolve_telegram_selected_model(
        None, fallback_model="codex-cli/gpt-5.5", path=p
    )
    assert resolved == "mlx-local-kv4/gemma-4-26b"
    assert source == "active_model_file"


def test_resolve_preferred_wins_over_active_model_by_default(tmp_path: Path) -> None:
    p = _write_active(tmp_path, "mlx-local-kv4/gemma-4-26b")
    resolved, source = amr.resolve_telegram_selected_model(
        "google/gemini-3-pro-preview",
        fallback_model="codex-cli/gpt-5.5",
        path=p,
    )
    assert resolved == "google/gemini-3-pro-preview"
    assert source == "preferred"


def test_resolve_falls_back_when_both_empty(tmp_path: Path) -> None:
    resolved, source = amr.resolve_telegram_selected_model(
        None, fallback_model="codex-cli/gpt-5.5", path=tmp_path / "nope.json"
    )
    assert resolved == "codex-cli/gpt-5.5"
    assert source == "fallback"


def test_force_all_paths_beats_preferred(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """KRAB_ACTIVE_MODEL_FORCE_ALL_PATHS=1: owner-choice побеждает даже
    явный preferred_model retry override."""
    p = _write_active(tmp_path, "mlx-local-kv4/gemma-4-26b")
    monkeypatch.setenv("KRAB_ACTIVE_MODEL_FORCE_ALL_PATHS", "1")
    resolved, source = amr.resolve_telegram_selected_model(
        "google/gemini-3-pro-preview",
        fallback_model="codex-cli/gpt-5.5",
        path=p,
    )
    assert resolved == "mlx-local-kv4/gemma-4-26b"
    assert source == "force_all_paths"


def test_force_all_paths_ignored_when_active_file_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Если active_model.json пуст, force flag не должен ломать дефолтный flow."""
    monkeypatch.setenv("KRAB_ACTIVE_MODEL_FORCE_ALL_PATHS", "1")
    resolved, source = amr.resolve_telegram_selected_model(
        "google/gemini-3-pro-preview",
        fallback_model="codex-cli/gpt-5.5",
        path=tmp_path / "nope.json",
    )
    # active пуст → force-режим неприменим → preferred побеждает.
    assert resolved == "google/gemini-3-pro-preview"
    assert source == "preferred"


def test_is_active_model_force_all_paths_enabled_truthy_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1/true/yes/on (case-insensitive) — все truthy."""
    for val in ("1", "true", "TRUE", "yes", "on", "On"):
        monkeypatch.setenv("KRAB_ACTIVE_MODEL_FORCE_ALL_PATHS", val)
        assert amr.is_active_model_force_all_paths_enabled() is True
    for val in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("KRAB_ACTIVE_MODEL_FORCE_ALL_PATHS", val)
        assert amr.is_active_model_force_all_paths_enabled() is False
