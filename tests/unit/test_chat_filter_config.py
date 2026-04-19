# -*- coding: utf-8 -*-
"""
Тесты ChatFilterConfig — per-chat filter config.

Покрываем:
1) set_mode + get_mode roundtrip
2) default_if_group / default DM возвращаются когда правила нет
3) invalid mode raises ValueError
4) reset удаляет правило
5) персистентность через tmp_path
6) list_rules фильтр по mode
7) stats подсчёт по mode
8) should_respond логика для всех трёх mode
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.chat_filter_config import (
    DEFAULT_DM_MODE,
    DEFAULT_GROUP_MODE,
    VALID_MODES,
    ChatFilterConfig,
)

# ─── фикстуры ────────────────────────────────────────────────────────────────


@pytest.fixture
def cfg(tmp_path: Path) -> ChatFilterConfig:
    """ChatFilterConfig с временным state файлом."""
    return ChatFilterConfig(state_path=tmp_path / "chat_filters.json")


# ─── set_mode + get_mode ──────────────────────────────────────────────────────


class TestSetGet:
    def test_set_and_get_roundtrip(self, cfg: ChatFilterConfig) -> None:
        cfg.set_mode("-100111", "muted")
        assert cfg.get_mode("-100111") == "muted"

    def test_set_all_valid_modes(self, cfg: ChatFilterConfig) -> None:
        for mode in VALID_MODES:
            cfg.set_mode("-100999", mode)
            assert cfg.get_mode("-100999") == mode

    def test_chat_id_coerced_to_str(self, cfg: ChatFilterConfig) -> None:
        cfg.set_mode(-100222, "active")
        assert cfg.get_mode(-100222) == "active"
        assert cfg.get_mode("-100222") == "active"

    def test_invalid_mode_raises(self, cfg: ChatFilterConfig) -> None:
        with pytest.raises(ValueError, match="invalid mode"):
            cfg.set_mode("-100333", "unknown-mode")

    def test_set_returns_true(self, cfg: ChatFilterConfig) -> None:
        assert cfg.set_mode("-100444", "mention-only") is True


# ─── defaults ─────────────────────────────────────────────────────────────────


class TestDefaults:
    def test_group_default_when_no_rule(self, cfg: ChatFilterConfig) -> None:
        assert cfg.get_mode("-100555", is_group=True) == DEFAULT_GROUP_MODE

    def test_dm_default_when_no_rule(self, cfg: ChatFilterConfig) -> None:
        assert cfg.get_mode("12345678", is_group=False) == DEFAULT_DM_MODE

    def test_group_default_is_mention_only(self) -> None:
        assert DEFAULT_GROUP_MODE == "mention-only"

    def test_dm_default_is_active(self) -> None:
        assert DEFAULT_DM_MODE == "active"

    def test_explicit_rule_overrides_default(self, cfg: ChatFilterConfig) -> None:
        cfg.set_mode("-100666", "active")
        # Даже если is_group=True, явное правило приоритетнее
        assert cfg.get_mode("-100666", is_group=True) == "active"


# ─── reset ────────────────────────────────────────────────────────────────────


class TestReset:
    def test_reset_removes_rule(self, cfg: ChatFilterConfig) -> None:
        cfg.set_mode("-100777", "muted")
        result = cfg.reset("-100777")
        assert result is True
        # После сброса — дефолт
        assert cfg.get_mode("-100777", is_group=True) == DEFAULT_GROUP_MODE

    def test_reset_nonexistent_returns_false(self, cfg: ChatFilterConfig) -> None:
        assert cfg.reset("-100888") is False

    def test_reset_coerces_chat_id(self, cfg: ChatFilterConfig) -> None:
        cfg.set_mode(-100999, "muted")
        assert cfg.reset(-100999) is True


# ─── persistence ──────────────────────────────────────────────────────────────


class TestPersistence:
    def test_rules_survive_reload(self, tmp_path: Path) -> None:
        state = tmp_path / "chat_filters.json"
        c1 = ChatFilterConfig(state_path=state)
        c1.set_mode("-111", "muted")
        c1.set_mode("-222", "active")

        c2 = ChatFilterConfig(state_path=state)
        assert c2.get_mode("-111") == "muted"
        assert c2.get_mode("-222") == "active"

    def test_note_survives_reload(self, tmp_path: Path) -> None:
        state = tmp_path / "chat_filters.json"
        c1 = ChatFilterConfig(state_path=state)
        c1.set_mode("-333", "mention-only", note="test note")

        c2 = ChatFilterConfig(state_path=state)
        rules = c2.list_rules()
        assert rules[0].note == "test note"

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "chat_filters.json"
        c = ChatFilterConfig(state_path=nested)
        c.set_mode("-444", "muted")
        assert nested.exists()

    def test_missing_file_no_error(self, tmp_path: Path) -> None:
        c = ChatFilterConfig(state_path=tmp_path / "nonexistent.json")
        # Не должно бросать исключений
        assert c.list_rules() == []


# ─── list_rules ───────────────────────────────────────────────────────────────


class TestListRules:
    def test_list_all(self, cfg: ChatFilterConfig) -> None:
        cfg.set_mode("-1", "active")
        cfg.set_mode("-2", "muted")
        cfg.set_mode("-3", "mention-only")
        assert len(cfg.list_rules()) == 3

    def test_filter_by_mode(self, cfg: ChatFilterConfig) -> None:
        cfg.set_mode("-10", "muted")
        cfg.set_mode("-20", "active")
        cfg.set_mode("-30", "muted")
        muted = cfg.list_rules(mode="muted")
        assert len(muted) == 2
        assert all(r.mode == "muted" for r in muted)

    def test_empty_when_no_rules(self, cfg: ChatFilterConfig) -> None:
        assert cfg.list_rules() == []

    def test_sorted_by_updated_at_desc(self, cfg: ChatFilterConfig) -> None:
        import time

        cfg.set_mode("-100", "active")
        time.sleep(0.01)
        cfg.set_mode("-200", "muted")
        rules = cfg.list_rules()
        assert rules[0].chat_id == "-200"


# ─── stats ────────────────────────────────────────────────────────────────────


class TestStats:
    def test_empty_stats(self, cfg: ChatFilterConfig) -> None:
        s = cfg.stats()
        assert s["total_rules"] == 0
        assert s["by_mode"] == {}

    def test_counts_by_mode(self, cfg: ChatFilterConfig) -> None:
        cfg.set_mode("-1", "active")
        cfg.set_mode("-2", "active")
        cfg.set_mode("-3", "muted")
        s = cfg.stats()
        assert s["total_rules"] == 3
        assert s["by_mode"]["active"] == 2
        assert s["by_mode"]["muted"] == 1


# ─── should_respond ───────────────────────────────────────────────────────────


class TestShouldRespond:
    def test_active_mode_always_responds(self, cfg: ChatFilterConfig) -> None:
        cfg.set_mode("-999", "active")
        assert cfg.should_respond("-999", is_group=True) is True
        assert cfg.should_respond("-999", is_group=True, is_mention=False, is_reply=False) is True

    def test_muted_mode_never_responds(self, cfg: ChatFilterConfig) -> None:
        cfg.set_mode("-999", "muted")
        assert cfg.should_respond("-999", is_mention=True) is False
        assert cfg.should_respond("-999", is_reply=True) is False

    def test_mention_only_needs_mention(self, cfg: ChatFilterConfig) -> None:
        cfg.set_mode("-999", "mention-only")
        assert cfg.should_respond("-999", is_mention=False, is_reply=False) is False
        assert cfg.should_respond("-999", is_mention=True) is True

    def test_mention_only_needs_reply(self, cfg: ChatFilterConfig) -> None:
        cfg.set_mode("-999", "mention-only")
        assert cfg.should_respond("-999", is_reply=True) is True

    def test_default_group_mention_only(self, cfg: ChatFilterConfig) -> None:
        # Нет правила — группа получает mention-only
        assert cfg.should_respond("-no-rule", is_group=True, is_mention=False) is False
        assert cfg.should_respond("-no-rule", is_group=True, is_mention=True) is True

    def test_default_dm_active(self, cfg: ChatFilterConfig) -> None:
        # Нет правила — DM получает active
        assert cfg.should_respond("12345", is_group=False) is True
