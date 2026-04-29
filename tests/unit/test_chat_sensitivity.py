# -*- coding: utf-8 -*-
"""
Регрессии `src/core/chat_sensitivity.py` — registry sensitive чатов (Idea 28).

Проверяем:
1. Базовый mark_sensitive / is_sensitive / unmark.
2. Helper'ы should_skip_archive / should_redact в зависимости от level.
3. Persist round-trip через свежий instance с тем же storage_path.
4. Невалидный level → ValueError, пустой chat_id → no-op, битый JSON не ронит.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.chat_sensitivity import (
    SensitiveChatRegistry,
    sensitive_chat_registry,
    valid_levels,
)


@pytest.fixture
def registry(tmp_path: Path) -> SensitiveChatRegistry:
    return SensitiveChatRegistry(storage_path=tmp_path / "sensitive_chats.json")


def test_mark_and_is_sensitive(registry: SensitiveChatRegistry) -> None:
    registry.mark_sensitive(-100123, "финансы", level="no_archive")
    assert registry.is_sensitive(-100123) is True
    assert registry.is_sensitive("-100123") is True
    assert registry.get_level(-100123) == "no_archive"
    assert registry.should_skip_archive(-100123) is True
    assert registry.should_redact(-100123) is False


def test_redact_only_level_helpers(registry: SensitiveChatRegistry) -> None:
    registry.mark_sensitive(42, "work", level="redact_only")
    assert registry.should_redact(42) is True
    assert registry.should_skip_archive(42) is False


def test_unmark_removes_entry(registry: SensitiveChatRegistry) -> None:
    registry.mark_sensitive(7, "family")
    assert registry.unmark(7) is True
    assert registry.is_sensitive(7) is False
    # повторный unmark → False, без падения
    assert registry.unmark(7) is False


def test_invalid_level_raises(registry: SensitiveChatRegistry) -> None:
    with pytest.raises(ValueError):
        registry.mark_sensitive(1, "x", level="bogus")  # type: ignore[arg-type]


def test_empty_chat_id_is_noop(registry: SensitiveChatRegistry) -> None:
    registry.mark_sensitive("", "x")
    registry.mark_sensitive(None, "x")
    assert registry.list_entries() == []


def test_persistence_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "sensitive_chats.json"
    a = SensitiveChatRegistry(storage_path=path)
    a.mark_sensitive(-100, "финансы", level="no_archive")
    a.mark_sensitive(-200, "работа", level="redact_only")
    # свежий instance читает state с диска
    b = SensitiveChatRegistry(storage_path=path)
    assert b.is_sensitive(-100) is True
    assert b.get_level(-200) == "redact_only"
    entries = {e["chat_id"]: e for e in b.list_entries()}
    assert entries["-100"]["reason"] == "финансы"


def test_corrupt_json_does_not_crash(tmp_path: Path) -> None:
    path = tmp_path / "sensitive_chats.json"
    path.write_text("{not valid json", encoding="utf-8")
    reg = SensitiveChatRegistry(storage_path=path)
    assert reg.list_entries() == []
    # запись поверх битого файла нормально persist'ится
    reg.mark_sensitive(1, "x")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "1" in raw


def test_module_singleton_exists() -> None:
    # синглтон существует и имеет ожидаемое API; не мутируем его в этом тесте
    assert hasattr(sensitive_chat_registry, "is_sensitive")
    assert hasattr(sensitive_chat_registry, "mark_sensitive")
    assert "no_archive" in tuple(valid_levels())
    assert "redact_only" in tuple(valid_levels())
