# -*- coding: utf-8 -*-
"""
Тесты per-chat filter mode (Chado §3 P2).

Покрываем:
1) get_chat_mode возвращает "active" по умолчанию (DM)
2) set_chat_mode сохраняет + round-trip через get_chat_mode
3) персистентность через файл (перезагрузка нового экземпляра)
4) get_mode_for_chat читает из chat_filter_config
5) невалидный mode вызывает ValueError
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.chat_filter_config import ChatFilterConfig


# ─── фикстуры ─────────────────────────────────────────────────────────────────


@pytest.fixture
def cfg(tmp_path: Path) -> ChatFilterConfig:
    """ChatFilterConfig с временным state-файлом."""
    return ChatFilterConfig(state_path=tmp_path / "chat_filter_modes.json")


# ─── 1. default "active" ──────────────────────────────────────────────────────


class TestDefaultMode:
    def test_dm_default_is_active(self, cfg: ChatFilterConfig) -> None:
        # DM (is_group=False) → "active"
        assert cfg.get_chat_mode(123456789, is_group=False) == "active"

    def test_group_default_is_mention_only(self, cfg: ChatFilterConfig) -> None:
        # группа без явного правила → "mention-only"
        assert cfg.get_chat_mode(-1001234567890, is_group=True) == "mention-only"

    def test_unknown_chat_id_dm_default(self, cfg: ChatFilterConfig) -> None:
        assert cfg.get_chat_mode(999999999, is_group=False) == "active"


# ─── 2. set_chat_mode + round-trip ────────────────────────────────────────────


class TestSetGet:
    def test_set_active(self, cfg: ChatFilterConfig) -> None:
        cfg.set_chat_mode(-100111, "active")
        assert cfg.get_chat_mode(-100111) == "active"

    def test_set_mention_only(self, cfg: ChatFilterConfig) -> None:
        cfg.set_chat_mode(-100222, "mention-only")
        assert cfg.get_chat_mode(-100222) == "mention-only"

    def test_set_muted(self, cfg: ChatFilterConfig) -> None:
        cfg.set_chat_mode(-100333, "muted")
        assert cfg.get_chat_mode(-100333) == "muted"

    def test_overwrite_mode(self, cfg: ChatFilterConfig) -> None:
        cfg.set_chat_mode(-100444, "muted")
        cfg.set_chat_mode(-100444, "active")
        assert cfg.get_chat_mode(-100444) == "active"


# ─── 3. персистентность через файл ────────────────────────────────────────────


class TestPersistence:
    def test_persists_to_file_and_reloads(self, tmp_path: Path) -> None:
        state = tmp_path / "chat_filter_modes.json"
        cfg1 = ChatFilterConfig(state_path=state)
        cfg1.set_chat_mode(-100555, "muted")

        # Новый экземпляр читает тот же файл
        cfg2 = ChatFilterConfig(state_path=state)
        assert cfg2.get_chat_mode(-100555) == "muted"

    def test_file_created_on_set(self, tmp_path: Path) -> None:
        state = tmp_path / "subdir" / "chat_filter_modes.json"
        cfg = ChatFilterConfig(state_path=state)
        cfg.set_chat_mode(-100666, "active")
        assert state.exists()

    def test_multiple_chats_persist(self, tmp_path: Path) -> None:
        state = tmp_path / "chat_filter_modes.json"
        cfg1 = ChatFilterConfig(state_path=state)
        cfg1.set_chat_mode(-100001, "muted")
        cfg1.set_chat_mode(-100002, "mention-only")
        cfg1.set_chat_mode(-100003, "active")

        cfg2 = ChatFilterConfig(state_path=state)
        assert cfg2.get_chat_mode(-100001) == "muted"
        assert cfg2.get_chat_mode(-100002) == "mention-only"
        assert cfg2.get_chat_mode(-100003) == "active"


# ─── 4. get_mode_for_chat делегирует в chat_filter_config ─────────────────────


class TestGetModeForChat:
    def test_reads_from_config(self, tmp_path: Path) -> None:
        from src.core import message_priority_dispatcher as mpd
        from src.core.chat_filter_config import ChatFilterConfig

        cfg = ChatFilterConfig(state_path=tmp_path / "chat_filter_modes.json")
        cfg.set_chat_mode(-100777, "muted")

        # Подменяем singleton внутри модуля chat_filter_config
        with patch("src.core.chat_filter_config.chat_filter_config", cfg):
            mode = mpd.get_mode_for_chat(-100777, is_group=True)
        assert mode == "muted"

    def test_default_dm_via_dispatcher(self, tmp_path: Path) -> None:
        from src.core import message_priority_dispatcher as mpd
        from src.core.chat_filter_config import ChatFilterConfig

        cfg = ChatFilterConfig(state_path=tmp_path / "chat_filter_modes.json")
        with patch("src.core.chat_filter_config.chat_filter_config", cfg):
            mode = mpd.get_mode_for_chat(999, is_group=False)
        assert mode == "active"


# ─── 5. invalid mode → ValueError ────────────────────────────────────────────


class TestInvalidMode:
    def test_invalid_mode_raises(self, cfg: ChatFilterConfig) -> None:
        with pytest.raises(ValueError, match="Invalid mode"):
            cfg.set_chat_mode(-100888, "banana")

    def test_empty_mode_raises(self, cfg: ChatFilterConfig) -> None:
        with pytest.raises(ValueError):
            cfg.set_chat_mode(-100889, "")

    def test_case_sensitive(self, cfg: ChatFilterConfig) -> None:
        with pytest.raises(ValueError):
            cfg.set_chat_mode(-100890, "Active")  # не "active"
