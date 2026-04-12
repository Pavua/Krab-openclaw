# -*- coding: utf-8 -*-
"""
Тесты антиспам фильтра для групп (spam_guard).
Охватывает: конфиг (enable/disable/action), детект flood/links/fwd_links,
edge-cases flood tracker, classify_message.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

try:
    from src.core.spam_guard import (
        DEFAULT_ACTION,
        FLOOD_MSG_LIMIT,
        FLOOD_WINDOW_SEC,
        LINK_LIMIT,
        VALID_ACTIONS,
        _check_flood,
        _count_links,
        _flood_tracker,
        _is_forwarded_with_links,
        classify_message,
        get_action,
        get_status,
        is_enabled,
        set_action,
        set_enabled,
    )
except ImportError:
    pytest.skip("src.core.spam_guard not available", allow_module_level=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    """Перенаправляет _CONFIG_PATH во временный файл и сбрасывает flood tracker."""
    cfg_path = tmp_path / "spam_filter_config.json"
    monkeypatch.setattr("src.core.spam_guard._CONFIG_PATH", cfg_path)
    # Сброс flood tracker перед каждым тестом
    _flood_tracker.clear()
    yield
    _flood_tracker.clear()


def _make_msg(text="", forward_origin=None, forward_from=None,
              forward_from_chat=None, forward_date=None, caption=None):
    """Создаёт mock-сообщение."""
    return SimpleNamespace(
        text=text,
        caption=caption,
        forward_origin=forward_origin,
        forward_from=forward_from,
        forward_from_chat=forward_from_chat,
        forward_date=forward_date,
    )


# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

class TestConstants:
    def test_valid_actions(self):
        assert VALID_ACTIONS == {"ban", "mute", "delete"}

    def test_default_action(self):
        assert DEFAULT_ACTION == "delete"
        assert DEFAULT_ACTION in VALID_ACTIONS

    def test_flood_limits(self):
        assert FLOOD_MSG_LIMIT == 5
        assert FLOOD_WINDOW_SEC == 10.0

    def test_link_limit(self):
        assert LINK_LIMIT == 3


# ---------------------------------------------------------------------------
# Конфиг: enable/disable
# ---------------------------------------------------------------------------

class TestSetEnabled:
    def test_enable_chat(self):
        set_enabled(123, True)
        assert is_enabled(123) is True

    def test_disable_chat(self):
        set_enabled(123, True)
        set_enabled(123, False)
        assert is_enabled(123) is False

    def test_disabled_by_default(self):
        assert is_enabled(999) is False

    def test_enable_string_chat_id(self):
        set_enabled("456", True)
        assert is_enabled("456") is True
        assert is_enabled(456) is True  # int и str — одно и то же

    def test_enable_preserves_action(self):
        set_enabled(10, False)
        set_action(10, "ban")
        set_enabled(10, True)
        assert get_action(10) == "ban"

    def test_multiple_chats_independent(self):
        set_enabled(1, True)
        set_enabled(2, False)
        assert is_enabled(1) is True
        assert is_enabled(2) is False


# ---------------------------------------------------------------------------
# Конфиг: action
# ---------------------------------------------------------------------------

class TestSetAction:
    def test_set_ban(self):
        set_action(100, "ban")
        assert get_action(100) == "ban"

    def test_set_mute(self):
        set_action(100, "mute")
        assert get_action(100) == "mute"

    def test_set_delete(self):
        set_action(100, "delete")
        assert get_action(100) == "delete"

    def test_default_action_for_new_chat(self):
        assert get_action(999) == DEFAULT_ACTION

    def test_invalid_action_raises(self):
        with pytest.raises(ValueError, match="Недопустимое действие"):
            set_action(100, "kick")

    def test_invalid_action_empty_raises(self):
        with pytest.raises(ValueError):
            set_action(100, "")

    def test_invalid_action_case_sensitive(self):
        with pytest.raises(ValueError):
            set_action(100, "BAN")


# ---------------------------------------------------------------------------
# Конфиг: get_status
# ---------------------------------------------------------------------------

class TestGetStatus:
    def test_status_fresh_chat(self):
        st = get_status(777)
        assert st["enabled"] is False
        assert st["action"] == DEFAULT_ACTION
        assert st["chat_id"] == "777"

    def test_status_after_enable(self):
        set_enabled(777, True)
        st = get_status(777)
        assert st["enabled"] is True

    def test_status_after_set_action(self):
        set_action(777, "mute")
        st = get_status(777)
        assert st["action"] == "mute"

    def test_status_chat_id_as_string(self):
        st = get_status(42)
        assert st["chat_id"] == "42"


# ---------------------------------------------------------------------------
# Детект ссылок
# ---------------------------------------------------------------------------

class TestCountLinks:
    def test_no_links(self):
        assert _count_links("Привет, как дела?") == 0

    def test_http_link(self):
        assert _count_links("Смотри: https://example.com") == 1

    def test_https_link(self):
        assert _count_links("https://google.com/search?q=test") == 1

    def test_tme_link(self):
        assert _count_links("Канал: t.me/some_channel") == 1

    def test_mention(self):
        assert _count_links("Привет @username_bot") == 1

    def test_short_mention_ignored(self):
        # @abc = 3 символа, меньше 4 — не считается
        assert _count_links("@abc") == 0

    def test_multiple_links(self):
        text = "https://a.com https://b.com https://c.com https://d.com"
        assert _count_links(text) == 4

    def test_exactly_link_limit(self):
        text = "https://a.com https://b.com https://c.com"
        assert _count_links(text) == LINK_LIMIT

    def test_empty_text(self):
        assert _count_links("") == 0

    def test_none_text(self):
        assert _count_links(None) == 0

    def test_mixed_links_and_mentions(self):
        text = "https://x.com @user1234 t.me/chan"
        assert _count_links(text) == 3


# ---------------------------------------------------------------------------
# Детект forwarded+links
# ---------------------------------------------------------------------------

class TestForwardedWithLinks:
    def test_not_forwarded(self):
        msg = _make_msg(text="https://example.com")
        assert _is_forwarded_with_links(msg) is False

    def test_forwarded_no_links(self):
        msg = _make_msg(text="Привет!", forward_date=12345)
        assert _is_forwarded_with_links(msg) is False

    def test_forwarded_with_link(self):
        msg = _make_msg(text="https://spam.com", forward_date=12345)
        assert _is_forwarded_with_links(msg) is True

    def test_forwarded_via_forward_origin(self):
        msg = _make_msg(text="t.me/spam_chan", forward_origin=object())
        assert _is_forwarded_with_links(msg) is True

    def test_forwarded_via_forward_from(self):
        msg = _make_msg(text="@spambot", forward_from=object())
        assert _is_forwarded_with_links(msg) is True

    def test_forwarded_with_caption_link(self):
        msg = _make_msg(caption="https://promo.link", forward_date=99)
        assert _is_forwarded_with_links(msg) is True

    def test_forwarded_no_text_no_caption(self):
        msg = _make_msg(forward_date=12345)
        assert _is_forwarded_with_links(msg) is False


# ---------------------------------------------------------------------------
# Flood tracker
# ---------------------------------------------------------------------------

class TestCheckFlood:
    def test_no_flood_below_limit(self):
        for _ in range(FLOOD_MSG_LIMIT):
            _check_flood(1001, 1)
        # Ровно на лимите — не флуд
        result = _check_flood(1001, 1)
        assert result is True  # 6-е сообщение = превышение

    def test_no_flood_exactly_limit(self):
        # 5 сообщений = лимит, 6-е = флуд
        for i in range(FLOOD_MSG_LIMIT):
            r = _check_flood(2001, 2)
        # 5-е сообщение: len(dq) == 5 > 5 → False (ещё не флуд)
        # Пересчёт: append происходит ПОСЛЕ сравнения, len > LIMIT
        # После 5 appends len==5, 5>5 = False. После 6-го len==6, 6>5 = True.
        assert r is False  # 5-е сообщение — не флуд

    def test_flood_on_sixth(self):
        for _ in range(FLOOD_MSG_LIMIT):
            _check_flood(3001, 3)
        r = _check_flood(3001, 3)  # 6-е
        assert r is True

    def test_different_users_independent(self):
        for _ in range(FLOOD_MSG_LIMIT + 1):
            _check_flood(4001, 10)
        # Другой пользователь не должен получить флуд
        assert _check_flood(4001, 11) is False

    def test_different_chats_independent(self):
        for _ in range(FLOOD_MSG_LIMIT + 1):
            _check_flood(5001, 5)
        # Другой чат не должен получить флуд
        assert _check_flood(5002, 5) is False

    def test_flood_clears_after_window(self):
        # Заполняем flood tracker
        for _ in range(FLOOD_MSG_LIMIT + 1):
            _check_flood(6001, 6)
        # Эмулируем прошедшее время — напрямую устаревляем метки
        key = "6001"
        old_time = time.monotonic() - FLOOD_WINDOW_SEC - 1
        dq = _flood_tracker[key][6]
        for i in range(len(dq)):
            dq[i] = old_time  # нельзя напрямую индексировать deque через присвоение
        # Переинициализируем
        import collections
        _flood_tracker[key][6] = collections.deque([old_time] * (FLOOD_MSG_LIMIT + 1))
        # После истечения окна — флуда нет
        assert _check_flood(6001, 6) is False


# ---------------------------------------------------------------------------
# classify_message
# ---------------------------------------------------------------------------

class TestClassifyMessage:
    def test_normal_message_returns_none(self):
        msg = _make_msg(text="Привет!")
        assert classify_message(9001, 1, msg) is None

    def test_flood_detected(self):
        msg = _make_msg(text="Привет!")
        for _ in range(FLOOD_MSG_LIMIT):
            classify_message(9002, 2, msg)
        result = classify_message(9002, 2, msg)
        assert result == "flood"

    def test_too_many_links(self):
        text = " ".join(f"https://site{i}.com" for i in range(LINK_LIMIT + 1))
        msg = _make_msg(text=text)
        result = classify_message(9003, 3, msg)
        assert result == "links"

    def test_exactly_link_limit_not_spam(self):
        # Ровно 3 ссылки — не спам (>3 триггерит)
        text = " ".join(f"https://site{i}.com" for i in range(LINK_LIMIT))
        msg = _make_msg(text=text)
        result = classify_message(9004, 4, msg)
        assert result is None

    def test_forwarded_with_link_detected(self):
        msg = _make_msg(text="https://promo.site", forward_date=12345)
        result = classify_message(9005, 5, msg)
        assert result == "fwd_links"

    def test_forwarded_without_link_not_spam(self):
        msg = _make_msg(text="Простой текст без ссылок", forward_date=12345)
        result = classify_message(9006, 6, msg)
        assert result is None

    def test_flood_takes_priority_over_links(self):
        # Флуд детектируется первым (до проверки ссылок)
        text = " ".join(f"https://site{i}.com" for i in range(LINK_LIMIT + 1))
        msg = _make_msg(text=text)
        for _ in range(FLOOD_MSG_LIMIT):
            classify_message(9007, 7, msg)
        result = classify_message(9007, 7, msg)
        assert result == "flood"

    def test_empty_message_no_spam(self):
        msg = _make_msg(text="")
        assert classify_message(9008, 8, msg) is None

    def test_different_users_same_chat(self):
        """Flood tracker изолирован по user_id."""
        msg = _make_msg(text="hi")
        for _ in range(FLOOD_MSG_LIMIT + 1):
            classify_message(9009, 10, msg)
        # Другой пользователь — без флуда
        result = classify_message(9009, 11, msg)
        assert result is None


# ---------------------------------------------------------------------------
# Конфиг: персистентность (JSON файл)
# ---------------------------------------------------------------------------

class TestConfigPersistence:
    def test_config_persisted_to_file(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "sub" / "spam_filter_config.json"
        monkeypatch.setattr("src.core.spam_guard._CONFIG_PATH", cfg_path)
        set_enabled(42, True)
        assert cfg_path.exists()
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert data["42"]["enabled"] is True

    def test_config_survives_reload(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "spam_filter_config.json"
        monkeypatch.setattr("src.core.spam_guard._CONFIG_PATH", cfg_path)
        set_enabled(55, True)
        set_action(55, "ban")
        # Перечитываем без монкипатча модульных переменных
        assert is_enabled(55) is True
        assert get_action(55) == "ban"

    def test_corrupted_config_returns_defaults(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "spam_filter_config.json"
        cfg_path.write_text("NOT JSON", encoding="utf-8")
        monkeypatch.setattr("src.core.spam_guard._CONFIG_PATH", cfg_path)
        assert is_enabled(99) is False
        assert get_action(99) == DEFAULT_ACTION
