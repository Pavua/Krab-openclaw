# -*- coding: utf-8 -*-
"""Tests для S66 W4: local_share_policy.

Phase 4 prep — env-инфраструктура для per-task-type local share control.
Routing решения тут НЕ принимаются — только чтение env + clamp.
"""

from __future__ import annotations

import pytest

from src.core import local_share_policy as lsp

# ── known task types ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "task_type,env_value,expected",
    [
        ("translation", "1.0", 1.0),
        ("qa", "0.3", 0.3),
        ("summarization", "0.5", 0.5),
        ("chat", "0.1", 0.1),
        ("code", "0.0", 0.0),
    ],
)
def test_get_local_share_for_known_tasks(
    monkeypatch: pytest.MonkeyPatch,
    task_type: str,
    env_value: str,
    expected: float,
) -> None:
    """Все 5 task_types читают свой env-var корректно."""
    monkeypatch.setenv(f"KRAB_LOCAL_SHARE_{task_type.upper()}", env_value)
    assert lsp.get_local_share_for_task(task_type) == pytest.approx(expected)


def test_get_local_share_known_task_unset_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env unset → 0.0 (conservative default — cloud-only)."""
    monkeypatch.delenv("KRAB_LOCAL_SHARE_TRANSLATION", raising=False)
    assert lsp.get_local_share_for_task("translation") == 0.0


def test_get_local_share_unknown_task_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Неизвестный task_type → 0.0, env даже не читается."""
    monkeypatch.setenv("KRAB_LOCAL_SHARE_UNKNOWN", "0.9")
    assert lsp.get_local_share_for_task("unknown") == 0.0


def test_get_local_share_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """task_type регистро-независимый."""
    monkeypatch.setenv("KRAB_LOCAL_SHARE_TRANSLATION", "0.7")
    assert lsp.get_local_share_for_task("TRANSLATION") == pytest.approx(0.7)
    assert lsp.get_local_share_for_task("Translation") == pytest.approx(0.7)


# ── clamping / invalid input ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("-0.5", 0.0),  # negative clamps к 0.0
        ("1.5", 1.0),  # >1.0 clamps к 1.0
        ("2.0", 1.0),
        ("not-a-number", 0.0),
        ("", 0.0),
        ("nan", 0.0),  # nan filtered → 0.0
        ("inf", 0.0),  # inf filtered → 0.0
    ],
)
def test_get_local_share_clamps_invalid_to_zero(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    expected: float,
) -> None:
    """Некорректные / out-of-range значения → 0.0 или граница [0, 1]."""
    monkeypatch.setenv("KRAB_LOCAL_SHARE_QA", raw)
    assert lsp.get_local_share_for_task("qa") == pytest.approx(expected)


# ── aggregate api ────────────────────────────────────────────────────────────


def test_get_all_local_share_envs_returns_5_categories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_all_local_share_envs returns dict со всеми 5 task_types."""
    for task in lsp.TASK_TYPES:
        monkeypatch.delenv(f"KRAB_LOCAL_SHARE_{task.upper()}", raising=False)

    result = lsp.get_all_local_share_envs()
    assert set(result.keys()) == {"translation", "qa", "summarization", "chat", "code"}
    assert len(result) == 5
    # Все default → 0.0
    assert all(v == 0.0 for v in result.values())


def test_get_all_local_share_envs_reflects_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aggregate картина отражает выставленные env-vars."""
    monkeypatch.setenv("KRAB_LOCAL_SHARE_TRANSLATION", "1.0")
    monkeypatch.setenv("KRAB_LOCAL_SHARE_CODE", "0.0")
    monkeypatch.setenv("KRAB_LOCAL_SHARE_QA", "0.3")
    monkeypatch.delenv("KRAB_LOCAL_SHARE_SUMMARIZATION", raising=False)
    monkeypatch.delenv("KRAB_LOCAL_SHARE_CHAT", raising=False)

    result = lsp.get_all_local_share_envs()
    assert result["translation"] == pytest.approx(1.0)
    assert result["qa"] == pytest.approx(0.3)
    assert result["code"] == 0.0
    assert result["summarization"] == 0.0
    assert result["chat"] == 0.0
