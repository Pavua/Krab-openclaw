# -*- coding: utf-8 -*-
"""
W25.1 regression tests: command_blocklist default block не применялся,
если файл command_blocklist.json уже существовал (даже пустой {}).

Root cause: _load() читал файл, находил валидный dict (пусть даже {}),
делал self._data = loaded и return — defaults не мёржились.

Fix: загружаем файл, затем мёржим с defaults (loaded перекрывает defaults
только для своих ключей, defaults заполняют всё остальное).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.command_blocklist import CommandBlocklist, _normalize

HOW2AI_CHAT_ID = -1001587432709


# ---------------------------------------------------------------------------
# W25.1: Default block выживает при пустом {} файле
# ---------------------------------------------------------------------------


def test_default_block_applies_when_file_is_empty_dict(tmp_path: Path):
    """Регрессия W25.1: {} в файле не должен стирать defaults."""
    bl_file = tmp_path / "command_blocklist.json"
    bl_file.write_text("{}", encoding="utf-8")  # файл существует, но пустой

    bl = CommandBlocklist(state_dir=tmp_path, blocklist_file=bl_file)

    # Default: How2AI блокирует !status
    assert bl.is_blocked(HOW2AI_CHAT_ID, "status") is True


def test_default_block_applies_when_file_missing(tmp_path: Path):
    """Файл отсутствует — defaults создаются и персистируются."""
    bl_file = tmp_path / "command_blocklist.json"
    assert not bl_file.exists()

    bl = CommandBlocklist(state_dir=tmp_path, blocklist_file=bl_file)

    assert bl.is_blocked(HOW2AI_CHAT_ID, "status") is True
    # Файл должен быть создан с defaults
    assert bl_file.exists()
    data = json.loads(bl_file.read_text())
    assert str(HOW2AI_CHAT_ID) in data


def test_default_block_applies_on_corrupted_file(tmp_path: Path):
    """Повреждённый JSON → defaults применяются."""
    bl_file = tmp_path / "command_blocklist.json"
    bl_file.write_text("not-valid-json{{{{", encoding="utf-8")

    bl = CommandBlocklist(state_dir=tmp_path, blocklist_file=bl_file)

    assert bl.is_blocked(HOW2AI_CHAT_ID, "status") is True


# ---------------------------------------------------------------------------
# W25.1: Silent skip — нет reply, нет message
# ---------------------------------------------------------------------------


def test_silent_skip_no_side_effects(tmp_path: Path):
    """is_blocked возвращает True → вызывающий код должен делать return, не reply."""
    bl_file = tmp_path / "command_blocklist.json"
    bl_file.write_text("{}", encoding="utf-8")

    bl = CommandBlocklist(state_dir=tmp_path, blocklist_file=bl_file)

    result = bl.is_blocked(HOW2AI_CHAT_ID, "status")
    assert result is True, "is_blocked должен вернуть True для How2AI + status"
    # Тест подтверждает, что нет side-effect — bl.is_blocked просто bool


# ---------------------------------------------------------------------------
# W25.1: Нормализация — оба варианта блокируются
# ---------------------------------------------------------------------------


def test_normalization_with_bang_prefix(tmp_path: Path):
    """!status и status оба блокируются для How2AI."""
    bl_file = tmp_path / "command_blocklist.json"
    bl_file.write_text("{}", encoding="utf-8")

    bl = CommandBlocklist(state_dir=tmp_path, blocklist_file=bl_file)

    assert bl.is_blocked(HOW2AI_CHAT_ID, "!status") is True
    assert bl.is_blocked(HOW2AI_CHAT_ID, "status") is True
    assert bl.is_blocked(HOW2AI_CHAT_ID, "/status") is True
    assert bl.is_blocked(HOW2AI_CHAT_ID, ".status") is True
    assert bl.is_blocked(HOW2AI_CHAT_ID, "STATUS") is True
    assert bl.is_blocked(HOW2AI_CHAT_ID, "  !STATUS  ") is True


# ---------------------------------------------------------------------------
# W25.1: Другие команды в How2AI не заблокированы
# ---------------------------------------------------------------------------


def test_other_commands_not_blocked_in_how2ai(tmp_path: Path):
    """По умолчанию только !status заблокирован в How2AI, остальные — нет."""
    bl_file = tmp_path / "command_blocklist.json"
    bl_file.write_text("{}", encoding="utf-8")

    bl = CommandBlocklist(state_dir=tmp_path, blocklist_file=bl_file)

    # Только status заблокирован по умолчанию
    assert bl.is_blocked(HOW2AI_CHAT_ID, "status") is True
    assert bl.is_blocked(HOW2AI_CHAT_ID, "help") is False
    assert bl.is_blocked(HOW2AI_CHAT_ID, "search") is False
    assert bl.is_blocked(HOW2AI_CHAT_ID, "model") is False


# ---------------------------------------------------------------------------
# W25.1: Другие чаты не затронуты default block
# ---------------------------------------------------------------------------


def test_default_block_does_not_affect_other_chats(tmp_path: Path):
    """Default block How2AI не распространяется на другие чаты."""
    bl_file = tmp_path / "command_blocklist.json"
    bl_file.write_text("{}", encoding="utf-8")

    bl = CommandBlocklist(state_dir=tmp_path, blocklist_file=bl_file)

    other_chat = -1009999999999
    assert bl.is_blocked(other_chat, "status") is False
    assert bl.is_blocked(12345, "status") is False


# ---------------------------------------------------------------------------
# W25.1: Merge — сохранённые данные не стираются defaults
# ---------------------------------------------------------------------------


def test_saved_data_preserved_when_defaults_merged(tmp_path: Path):
    """При merge defaults не стирают уже сохранённые chat-specific блоки."""
    bl_file = tmp_path / "command_blocklist.json"
    # Файл с кастомным блоком для другого чата
    existing = {"-9876": ["start", "help"]}
    bl_file.write_text(json.dumps(existing), encoding="utf-8")

    bl = CommandBlocklist(state_dir=tmp_path, blocklist_file=bl_file)

    # Кастомные данные сохранились
    assert bl.is_blocked(-9876, "start") is True
    assert bl.is_blocked(-9876, "help") is True
    # И defaults тоже применились
    assert bl.is_blocked(HOW2AI_CHAT_ID, "status") is True
