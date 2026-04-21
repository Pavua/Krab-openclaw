"""Тесты для _read_current_session_id в sentry_integration."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.sentry_integration import _read_current_session_id


def test_returns_none_if_file_absent(tmp_path, monkeypatch):
    """Если .remember/current_session.md нет — возвращает None."""
    monkeypatch.chdir(tmp_path)
    assert _read_current_session_id() is None


def test_extracts_session_number(tmp_path, monkeypatch):
    """Извлекает номер сессии из строки '# Session 17 — ...'."""
    monkeypatch.chdir(tmp_path)
    remember = tmp_path / ".remember"
    remember.mkdir()
    (remember / "current_session.md").write_text(
        "# Session 17 — some description\n\nContent here.",
        encoding="utf-8",
    )
    result = _read_current_session_id()
    assert result == "17"


def test_fallback_to_first_32_chars(tmp_path, monkeypatch):
    """Если паттерн Session N не найден — возвращает первые 32 символа первой строки."""
    monkeypatch.chdir(tmp_path)
    remember = tmp_path / ".remember"
    remember.mkdir()
    first_line = "my-custom-session-identifier-xyz-extra"
    (remember / "current_session.md").write_text(
        first_line + "\n\nSome other content.",
        encoding="utf-8",
    )
    result = _read_current_session_id()
    assert result == first_line[:32]


def test_case_insensitive_session_match(tmp_path, monkeypatch):
    """Поиск паттерна SESSION N нечувствителен к регистру."""
    monkeypatch.chdir(tmp_path)
    remember = tmp_path / ".remember"
    remember.mkdir()
    (remember / "current_session.md").write_text(
        "SESSION 42 active",
        encoding="utf-8",
    )
    result = _read_current_session_id()
    assert result == "42"
