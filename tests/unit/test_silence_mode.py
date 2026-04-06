# -*- coding: utf-8 -*-
"""Тесты SilenceManager — per-chat и глобальный mute."""
import time
from unittest.mock import patch

import pytest

# Graceful import
try:
    from src.core.silence_mode import SilenceManager
except ImportError:
    pytest.skip("src.core.silence_mode not available", allow_module_level=True)


class TestSilenceManager:
    def setup_method(self):
        self.sm = SilenceManager()

    # ── Per-chat tests ──

    def test_mute_chat_basic(self):
        self.sm.mute_chat("123", 10)
        assert self.sm.is_chat_muted("123")
        assert not self.sm.is_chat_muted("456")

    def test_unmute_chat(self):
        self.sm.mute_chat("123", 10)
        assert self.sm.unmute_chat("123")
        assert not self.sm.is_chat_muted("123")

    def test_unmute_chat_not_muted(self):
        assert not self.sm.unmute_chat("999")

    def test_chat_mute_expiry(self):
        self.sm.mute_chat("123", 1)  # 1 minute
        assert self.sm.is_chat_muted("123")
        # Simulate time passing
        self.sm._chat_mutes["123"] = time.monotonic() - 1
        assert not self.sm.is_chat_muted("123")

    def test_chat_mute_remaining(self):
        self.sm.mute_chat("123", 10)
        remaining = self.sm.chat_mute_remaining_sec("123")
        assert 500 < remaining <= 600  # ~10 min

    def test_chat_mute_remaining_expired(self):
        self.sm.mute_chat("123", 1)
        self.sm._chat_mutes["123"] = time.monotonic() - 1
        assert self.sm.chat_mute_remaining_sec("123") == 0.0

    def test_chat_mute_remaining_not_muted(self):
        assert self.sm.chat_mute_remaining_sec("999") == 0.0

    # ── Global tests ──

    def test_mute_global(self):
        self.sm.mute_global(30)
        assert self.sm.is_global_muted()

    def test_unmute_global(self):
        self.sm.mute_global(30)
        assert self.sm.unmute_global()
        assert not self.sm.is_global_muted()

    def test_unmute_global_not_muted(self):
        assert not self.sm.unmute_global()

    def test_global_expiry(self):
        self.sm.mute_global(1)
        self.sm._global_until = time.monotonic() - 1
        assert not self.sm.is_global_muted()

    def test_global_remaining(self):
        self.sm.mute_global(10)
        remaining = self.sm.global_mute_remaining_sec()
        assert 500 < remaining <= 600

    # ── Composite ──

    def test_is_silenced_chat_only(self):
        self.sm.mute_chat("123", 10)
        assert self.sm.is_silenced("123")
        assert not self.sm.is_silenced("456")

    def test_is_silenced_global(self):
        self.sm.mute_global(10)
        assert self.sm.is_silenced("123")
        assert self.sm.is_silenced("456")

    def test_is_silenced_both(self):
        self.sm.mute_chat("123", 10)
        self.sm.mute_global(10)
        assert self.sm.is_silenced("123")

    # ── Auto-silence ──

    def test_auto_silence_owner_typing(self):
        self.sm.auto_silence_owner_typing("123", 5)
        assert self.sm.is_chat_muted("123")
        remaining = self.sm.chat_mute_remaining_sec("123")
        assert 250 < remaining <= 300

    def test_auto_silence_no_override_longer_mute(self):
        self.sm.mute_chat("123", 30)  # 30 min manual
        self.sm.auto_silence_owner_typing("123", 5)  # 5 min auto
        remaining = self.sm.chat_mute_remaining_sec("123")
        assert remaining > 300  # Still >5 min (manual mute preserved)

    # ── Status ──

    def test_status_empty(self):
        st = self.sm.status()
        assert not st["global_muted"]
        assert st["total_muted"] == 0
        assert st["muted_chats"] == {}

    def test_status_with_mutes(self):
        self.sm.mute_chat("123", 10)
        self.sm.mute_global(20)
        st = self.sm.status()
        assert st["global_muted"]
        assert "123" in st["muted_chats"]
        assert st["total_muted"] == 2

    def test_format_status_smoke(self):
        self.sm.mute_chat("123", 10)
        text = self.sm.format_status()
        assert "Режим тишины" in text
        assert "123" in text
