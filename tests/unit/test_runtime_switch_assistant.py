# -*- coding: utf-8 -*-
"""
Тесты runtime switch assistant.
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    from scripts import runtime_switch_assistant as assistant
except (ImportError, ModuleNotFoundError, FileNotFoundError):
    pytest.skip("scripts.runtime_switch_assistant not available", allow_module_level=True)


def test_action_status_writes_latest_artifacts(monkeypatch, tmp_path: Path) -> None:
    """Status-режим должен писать latest JSON/MD без runtime side-effects."""
    monkeypatch.setattr(assistant, "OPS_DIR", tmp_path)
    monkeypatch.setattr(
        assistant,
        "build_runtime_report",
        lambda: {
            "current_account": {"user": "user3"},
            "ownership": {"verdict": "runtime_not_running", "foreign_runtime_detected": False},
            "recommendations": ["cold start допустим"],
        },
    )

    payload = assistant.action_status()

    assert payload["ok"] is True
    assert payload["action"] == "status"
    assert (tmp_path / "runtime_switch_assistant_latest.json").exists() is True
    assert (tmp_path / "runtime_switch_assistant_latest.md").exists() is True


def test_switch_to_current_refuses_foreign_runtime(monkeypatch, tmp_path: Path) -> None:
    """Нельзя запускать runtime поверх уже активной чужой учётки."""
    monkeypatch.setattr(assistant, "OPS_DIR", tmp_path)
    monkeypatch.setattr(
        assistant,
        "build_runtime_report",
        lambda: {
            "current_account": {"user": "user3"},
            "ownership": {"verdict": "foreign_runtime_detected", "foreign_runtime_detected": True},
            "recommendations": ["чужой runtime уже найден"],
        },
    )

    payload = assistant.action_switch_to_current()

    assert payload["ok"] is False
    assert payload["action"] == "switch_to_current"
    assert payload["executed_steps"] == []


def test_return_to_pablito_refuses_non_pablito(monkeypatch, tmp_path: Path) -> None:
    """Reclaim на pablito нельзя выполнять из другой учётки."""
    monkeypatch.setattr(assistant, "OPS_DIR", tmp_path)
    monkeypatch.setattr(
        assistant,
        "build_runtime_report",
        lambda: {
            "current_account": {"user": "user3"},
            "ownership": {"verdict": "runtime_not_running", "foreign_runtime_detected": False},
            "recommendations": [],
        },
    )

    payload = assistant.action_return_to_pablito()

    assert payload["ok"] is False
    assert payload["action"] == "return_to_pablito"
