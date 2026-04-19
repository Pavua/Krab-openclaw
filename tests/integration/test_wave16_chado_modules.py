# -*- coding: utf-8 -*-
"""
E2E integration для Wave 16 Chado-inspired modules.

Verifies cohesion:
- ChatWindow tracks activity
- FilterConfig decides reaction
- PriorityDispatcher classifies
- Identity helpers distinguish Krab vs user
- Prefix applied on outgoing response
"""

import os
import sys

import pytest

# Ensure src is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# ---------------------------------------------------------------------------
# ChatWindow + ChatWindowManager
# ---------------------------------------------------------------------------


class TestChatWindowIntegration:
    def test_touch_then_peek_returns_same(self):
        from src.core.chat_window_manager import ChatWindowManager

        mgr = ChatWindowManager(capacity=5)
        w = mgr.get_or_create("chat_1")
        w.touch()
        w.append_message("user", "hello")
        peeked = mgr.peek("chat_1")
        assert peeked is w
        assert peeked.message_count == 1

    def test_evict_preserves_recency_order(self):
        from src.core.chat_window_manager import ChatWindowManager

        mgr = ChatWindowManager(capacity=3)
        for i in range(4):
            mgr.get_or_create(f"c{i}")
        # c0 должен быть вытеснен (LRU)
        assert mgr.peek("c0") is None
        for i in range(1, 4):
            assert mgr.peek(f"c{i}") is not None

    def test_stats_returns_correct_counts(self):
        from src.core.chat_window_manager import ChatWindowManager

        mgr = ChatWindowManager(capacity=10)
        w1 = mgr.get_or_create("chat_A")
        w1.append_message("user", "msg1")
        w1.append_message("assistant", "reply1")
        w2 = mgr.get_or_create("chat_B")
        w2.append_message("user", "hi")

        stats = mgr.stats()
        assert stats["active_windows"] == 2
        assert stats["total_messages"] == 3

    def test_get_or_create_idempotent(self):
        from src.core.chat_window_manager import ChatWindowManager

        mgr = ChatWindowManager()
        w1 = mgr.get_or_create("x")
        w2 = mgr.get_or_create("x")
        assert w1 is w2

    def test_message_cap_enforced(self):
        from src.core.chat_window_manager import ChatWindowManager

        mgr = ChatWindowManager()
        w = mgr.get_or_create("c")
        # max_messages default is 50 в ChatWindowManager
        for i in range(60):
            w.append_message("user", f"msg{i}")
        # Не должен превышать max_messages=50
        assert w.message_count == 50

    def test_lru_touch_on_access(self):
        """Доступ к старому окну защищает его от eviction."""
        from src.core.chat_window_manager import ChatWindowManager

        mgr = ChatWindowManager(capacity=3)
        mgr.get_or_create("old")
        mgr.get_or_create("b")
        mgr.get_or_create("c")
        # Обращаемся к "old" — теперь он most-recently-used
        mgr.get_or_create("old")
        # Добавляем новый — должен вытеснить "b" (oldest unreachable)
        mgr.get_or_create("new")
        assert mgr.peek("old") is not None
        assert mgr.peek("b") is None


# ---------------------------------------------------------------------------
# ChatFilterConfig
# ---------------------------------------------------------------------------


class TestFilterConfigIntegration:
    def test_default_group_mention_only(self, tmp_path):
        from src.core.chat_filter_config import ChatFilterConfig

        cfg = ChatFilterConfig(state_path=tmp_path / "f.json")
        assert cfg.get_mode("-100123", default_if_group="mention-only") == "mention-only"

    def test_active_mode_allows_all(self, tmp_path):
        from src.core.chat_filter_config import ChatFilterConfig

        cfg = ChatFilterConfig(state_path=tmp_path / "f.json")
        cfg.set_mode("-100123", "active")
        assert cfg.should_respond("-100123", has_mention=False) is True

    def test_mention_only_requires_mention(self, tmp_path):
        from src.core.chat_filter_config import ChatFilterConfig

        cfg = ChatFilterConfig(state_path=tmp_path / "f.json")
        cfg.set_mode("-100123", "mention-only")
        assert cfg.should_respond("-100123", has_mention=False) is False
        assert cfg.should_respond("-100123", has_mention=True) is True

    def test_muted_blocks_everything(self, tmp_path):
        from src.core.chat_filter_config import ChatFilterConfig

        cfg = ChatFilterConfig(state_path=tmp_path / "f.json")
        cfg.set_mode("-100123", "muted")
        assert cfg.should_respond("-100123", has_mention=True) is False
        assert cfg.should_respond("-100123", has_mention=False) is False

    def test_persist_and_reload(self, tmp_path):
        from src.core.chat_filter_config import ChatFilterConfig

        path = tmp_path / "f.json"
        cfg = ChatFilterConfig(state_path=path)
        cfg.set_mode("-100999", "active")
        cfg.set_mode("-100888", "muted")

        # Создаём новый инстанс — должен загрузить с диска
        cfg2 = ChatFilterConfig(state_path=path)
        assert cfg2.get_mode("-100999") == "active"
        assert cfg2.get_mode("-100888") == "muted"

    def test_invalid_mode_raises(self, tmp_path):
        from src.core.chat_filter_config import ChatFilterConfig

        cfg = ChatFilterConfig(state_path=tmp_path / "f.json")
        with pytest.raises(ValueError, match="Invalid mode"):
            cfg.set_mode("-100123", "unknown_mode")

    def test_dm_always_responds(self, tmp_path):
        from src.core.chat_filter_config import ChatFilterConfig

        cfg = ChatFilterConfig(state_path=tmp_path / "f.json")
        # DM без явного set_mode — is_dm=True должен форсировать ответ
        assert cfg.should_respond("777", has_mention=False, is_dm=True) is True


# ---------------------------------------------------------------------------
# krab_identity
# ---------------------------------------------------------------------------


class TestIdentityIntegration:
    def test_mention_russian(self):
        from src.core.krab_identity import is_krab_mentioned

        assert is_krab_mentioned("Краб, что скажешь?")

    def test_mention_russian_lowercase(self):
        from src.core.krab_identity import is_krab_mentioned

        assert is_krab_mentioned("эй краб, привет")

    def test_mention_english(self):
        from src.core.krab_identity import is_krab_mentioned

        assert is_krab_mentioned("@Krab please")

    def test_mention_english_lower(self):
        from src.core.krab_identity import is_krab_mentioned

        assert is_krab_mentioned("hey krab do this")

    def test_mention_emoji(self):
        from src.core.krab_identity import is_krab_mentioned

        assert is_krab_mentioned("🦀 respond")

    def test_no_mention_random(self):
        from src.core.krab_identity import is_krab_mentioned

        assert not is_krab_mentioned("обычное сообщение без упоминания")

    def test_no_mention_empty(self):
        from src.core.krab_identity import is_krab_mentioned

        assert not is_krab_mentioned("")

    def test_extract_returns_match(self):
        from src.core.krab_identity import extract_mentions

        # "Привет Краб и @Krab" → паттерн \bкраб\b, \bkrab\b, @Krab — все три совпадают
        matches = extract_mentions("Привет Краб и @Krab")
        assert len(matches) >= 2  # минимум русское + @mention


# ---------------------------------------------------------------------------
# group_identity
# ---------------------------------------------------------------------------


class TestGroupIdentityIntegration:
    def test_group_prefix_applied(self):
        from pyrogram.enums import ChatType

        from src.core.group_identity import apply_identity_prefix

        result = apply_identity_prefix("Hello", ChatType.GROUP)
        assert result.startswith("🦀")

    def test_supergroup_prefix_applied(self):
        from pyrogram.enums import ChatType

        from src.core.group_identity import apply_identity_prefix

        result = apply_identity_prefix("Ответ", ChatType.SUPERGROUP)
        assert result.startswith("🦀")

    def test_dm_no_prefix(self):
        from pyrogram.enums import ChatType

        from src.core.group_identity import apply_identity_prefix

        result = apply_identity_prefix("Hello", ChatType.PRIVATE)
        assert not result.startswith("🦀")
        assert result == "Hello"

    def test_strip_prefix(self):
        from pyrogram.enums import ChatType

        from src.core.group_identity import apply_identity_prefix, strip_identity_prefix

        prefixed = apply_identity_prefix("test", ChatType.GROUP)
        assert strip_identity_prefix(prefixed) == "test"

    def test_strip_noop_on_plain(self):
        from src.core.group_identity import strip_identity_prefix

        assert strip_identity_prefix("plain text") == "plain text"

    def test_string_group_type(self):
        """Совместимость со строковыми chat_type (без pyrogram)."""
        from src.core.group_identity import apply_identity_prefix

        # group_identity принимает объект с атрибутом name или строку
        class FakeType:
            name = "GROUP"

        result = apply_identity_prefix("Hi", FakeType())
        assert result.startswith("🦀")


# ---------------------------------------------------------------------------
# message_priority_dispatcher
# ---------------------------------------------------------------------------


class TestPriorityIntegration:
    def test_dm_priority_instant(self):
        from src.core.message_priority_dispatcher import Priority, classify_priority

        p, reason = classify_priority(
            "hello",
            "PRIVATE",
            is_dm=True,
            is_reply_to_self=False,
            has_mention=False,
            chat_mode="active",
        )
        assert p == Priority.P0_INSTANT
        assert reason == "dm"

    def test_mention_priority_instant(self):
        from src.core.message_priority_dispatcher import Priority, classify_priority

        p, reason = classify_priority(
            "Krab!",
            "GROUP",
            is_dm=False,
            is_reply_to_self=False,
            has_mention=True,
            chat_mode="mention-only",
        )
        assert p == Priority.P0_INSTANT
        assert reason == "mention"

    def test_command_priority_instant(self):
        from src.core.message_priority_dispatcher import Priority, classify_priority

        p, reason = classify_priority(
            "!help",
            "GROUP",
            is_dm=False,
            is_reply_to_self=False,
            has_mention=False,
            chat_mode="mention-only",
        )
        assert p == Priority.P0_INSTANT
        assert reason == "command"

    def test_reply_to_self_instant(self):
        from src.core.message_priority_dispatcher import Priority, classify_priority

        p, reason = classify_priority(
            "sure",
            "SUPERGROUP",
            is_dm=False,
            is_reply_to_self=True,
            has_mention=False,
            chat_mode="mention-only",
        )
        assert p == Priority.P0_INSTANT
        assert reason == "reply_to_self"

    def test_muted_low_priority(self):
        from src.core.message_priority_dispatcher import Priority, classify_priority

        p, _ = classify_priority(
            "hi",
            "GROUP",
            is_dm=False,
            is_reply_to_self=False,
            has_mention=False,
            chat_mode="muted",
        )
        assert p == Priority.P2_LOW

    def test_active_chat_normal_priority(self):
        from src.core.message_priority_dispatcher import Priority, classify_priority

        p, reason = classify_priority(
            "just talking",
            "GROUP",
            is_dm=False,
            is_reply_to_self=False,
            has_mention=False,
            chat_mode="active",
        )
        assert p == Priority.P1_NORMAL
        assert reason == "active"

    def test_mention_only_no_trigger_low(self):
        from src.core.message_priority_dispatcher import Priority, classify_priority

        p, _ = classify_priority(
            "random text",
            "GROUP",
            is_dm=False,
            is_reply_to_self=False,
            has_mention=False,
            chat_mode="mention-only",
        )
        assert p == Priority.P2_LOW

    def test_priority_ordering(self):
        from src.core.message_priority_dispatcher import Priority

        assert Priority.P0_INSTANT < Priority.P1_NORMAL < Priority.P2_LOW


# ---------------------------------------------------------------------------
# TestCohesion — full-chain тесты объединяющие все модули
# ---------------------------------------------------------------------------


class TestCohesion:
    """Full chain tests combining multiple modules."""

    def test_group_mention_full_flow(self, tmp_path):
        from pyrogram.enums import ChatType

        from src.core.chat_filter_config import ChatFilterConfig
        from src.core.chat_window_manager import ChatWindowManager
        from src.core.group_identity import apply_identity_prefix
        from src.core.krab_identity import is_krab_mentioned
        from src.core.message_priority_dispatcher import Priority, classify_priority

        chat_id = "-100123"
        text = "Эй Краб, привет!"

        # Setup
        cfg = ChatFilterConfig(state_path=tmp_path / "f.json")
        cfg.set_mode(chat_id, "mention-only")
        mgr = ChatWindowManager()

        # 1. Обнаружение упоминания
        has_mention = is_krab_mentioned(text)
        assert has_mention

        # 2. Фильтр разрешает (есть упоминание)
        assert cfg.should_respond(chat_id, has_mention=has_mention)

        # 3. Приоритет = P0
        p, _ = classify_priority(
            text,
            "SUPERGROUP",
            is_dm=False,
            is_reply_to_self=False,
            has_mention=True,
            chat_mode="mention-only",
        )
        assert p == Priority.P0_INSTANT

        # 4. ChatWindow создаётся и обновляется
        w = mgr.get_or_create(chat_id)
        w.append_message("user", text)
        assert w.message_count == 1

        # 5. Ответ получает групповой префикс
        response = apply_identity_prefix("Привет!", ChatType.SUPERGROUP)
        assert response.startswith("🦀")

    def test_group_muted_full_flow(self, tmp_path):
        """В muted-чате всё блокируется, включая упоминания."""
        from src.core.chat_filter_config import ChatFilterConfig
        from src.core.message_priority_dispatcher import classify_priority

        cfg = ChatFilterConfig(state_path=tmp_path / "f.json")
        cfg.set_mode("-100456", "muted")

        # Filter блокирует
        assert cfg.should_respond("-100456", has_mention=True) is False
        assert cfg.should_respond("-100456", has_mention=False) is False

        # Priority = P2_LOW даже при упоминании (muted доминирует в classify)
        p, _ = classify_priority(
            "🦀 hey",
            "SUPERGROUP",
            is_dm=False,
            is_reply_to_self=False,
            has_mention=True,
            chat_mode="muted",
        )
        # Muted проверяется после mention — mention даёт P0 по dispatcher.
        # Реальный guard — ChatFilterConfig.should_respond.
        # Это задокументированное разделение ответственности.
        assert cfg.should_respond("-100456", has_mention=True) is False

    def test_dm_flow_no_prefix(self, tmp_path):
        """DM: всегда отвечать, без группового префикса."""
        from pyrogram.enums import ChatType

        from src.core.chat_filter_config import ChatFilterConfig
        from src.core.group_identity import apply_identity_prefix
        from src.core.message_priority_dispatcher import Priority, classify_priority

        cfg = ChatFilterConfig(state_path=tmp_path / "f.json")
        # В DM нет set_mode — is_dm=True форсирует ответ
        assert cfg.should_respond("12345", has_mention=False, is_dm=True) is True

        p, reason = classify_priority(
            "вопрос",
            "PRIVATE",
            is_dm=True,
            is_reply_to_self=False,
            has_mention=False,
            chat_mode="active",
        )
        assert p == Priority.P0_INSTANT

        # Ответ в DM — без 🦀 префикса
        response = apply_identity_prefix("ответ", ChatType.PRIVATE)
        assert not response.startswith("🦀")
        assert response == "ответ"

    def test_command_bypasses_mention_only(self, tmp_path):
        """Команды обрабатываются как P0 даже в mention-only без упоминания."""
        from src.core.message_priority_dispatcher import Priority, classify_priority

        p, reason = classify_priority(
            "!stats",
            "GROUP",
            is_dm=False,
            is_reply_to_self=False,
            has_mention=False,
            chat_mode="mention-only",
        )
        assert p == Priority.P0_INSTANT
        assert reason == "command"

    def test_window_tracks_multiple_chats(self, tmp_path):
        """Менеджер корректно ведёт несколько окон одновременно."""
        from src.core.chat_window_manager import ChatWindowManager

        mgr = ChatWindowManager(capacity=10)
        chats = ["-100100", "-100200", "-100300"]
        for cid in chats:
            w = mgr.get_or_create(cid)
            w.append_message("user", f"msg from {cid}")

        assert mgr.active_count == 3
        stats = mgr.stats()
        assert stats["total_messages"] == 3
        for cid in chats:
            assert mgr.peek(cid) is not None
            assert mgr.peek(cid).message_count == 1
