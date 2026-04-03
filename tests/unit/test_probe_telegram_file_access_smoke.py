# -*- coding: utf-8 -*-
"""
Тесты для `probe_telegram_file_access_smoke.py`.

Зачем нужны:
- deterministic smoke-скрипт не должен ошибаться в выборе reply и verdict;
- эти тесты фиксируют core-логику без живого Telegram, чтобы не ловить регресс
  только уже на owner-аккаунте.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts.probe_telegram_file_access_smoke import _format_probe_command
from scripts.probe_telegram_file_access_smoke import (
    _extract_verdict,
    _normalize_paths,
    _select_probe_reply,
)
from src.handlers.command_handlers import _get_message_command_tail


def test_extract_verdict_returns_marker_from_text() -> None:
    assert _extract_verdict("Статус: file_read_confirmed для пути") == "file_read_confirmed"
    assert _extract_verdict("directory_access_not_confirmed: permission denied") == "directory_access_not_confirmed"
    assert _extract_verdict("ничего похожего тут нет") == ""


def test_select_probe_reply_prefers_explicit_reply() -> None:
    history = [
        {"id": 11, "text": "file_read_confirmed", "reply_to_message_id": None},
        {"id": 12, "text": "directory_access_confirmed", "reply_to_message_id": 10},
    ]

    match = _select_probe_reply(history, sent_id=10)

    assert match is not None
    assert match.verdict == "directory_access_confirmed"
    assert match.message["id"] == 12


def test_select_probe_reply_falls_back_to_first_newer_verdict() -> None:
    history = [
        {"id": 9, "text": "file_read_confirmed", "reply_to_message_id": 8},
        {"id": 11, "text": "not_found", "reply_to_message_id": None},
        {"id": 12, "text": "file_read_not_confirmed", "reply_to_message_id": None},
    ]

    match = _select_probe_reply(history, sent_id=10)

    assert match is not None
    assert match.verdict == "not_found"
    assert match.message["id"] == 11


def test_normalize_paths_deduplicates_and_expands_home() -> None:
    paths = _normalize_paths(["~/Downloads", str(Path.home() / "Downloads")])
    assert paths == [str(Path.home() / "Downloads")]


def test_normalize_paths_rejects_relative_path() -> None:
    try:
        _normalize_paths(["relative/path.txt"])
    except ValueError as exc:
        assert "абсолютным" in str(exc)
    else:
        raise AssertionError("Ожидали ValueError для относительного пути")


def test_format_probe_command_quotes_path_with_spaces() -> None:
    path = "/Users/pablito/Library/Group Containers/example file.txt"
    assert _format_probe_command(path) == '!probe "/Users/pablito/Library/Group Containers/example file.txt"'


def test_get_message_command_tail_preserves_spaces_inside_path() -> None:
    message = SimpleNamespace(text='!probe /Users/pablito/Library/Group Containers/example file.txt', caption=None)
    assert _get_message_command_tail(message) == "/Users/pablito/Library/Group Containers/example file.txt"
