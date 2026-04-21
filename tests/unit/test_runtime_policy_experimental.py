# -*- coding: utf-8 -*-
"""Тесты allow_experimental_for_chat из runtime_policy."""
from __future__ import annotations

import pytest

from src.core.runtime_policy import allow_experimental_for_chat


class TestAllowExperimentalEnv:
    def test_env_set_to_1_allows_any_chat(self, monkeypatch):
        monkeypatch.setenv("KRAB_EXPERIMENTAL", "1")
        assert allow_experimental_for_chat(chat_id=999999) is True

    def test_env_set_to_1_allows_none_chat(self, monkeypatch):
        monkeypatch.setenv("KRAB_EXPERIMENTAL", "1")
        assert allow_experimental_for_chat(chat_id=None) is True

    def test_env_not_set_denies_unknown_chat(self, monkeypatch):
        monkeypatch.delenv("KRAB_EXPERIMENTAL", raising=False)
        monkeypatch.delenv("OWNER_CHAT_ID", raising=False)
        monkeypatch.delenv("OWNER_CHAT_IDS", raising=False)
        assert allow_experimental_for_chat(chat_id=123456) is False

    def test_env_set_to_0_denies(self, monkeypatch):
        monkeypatch.setenv("KRAB_EXPERIMENTAL", "0")
        monkeypatch.delenv("OWNER_CHAT_ID", raising=False)
        monkeypatch.delenv("OWNER_CHAT_IDS", raising=False)
        assert allow_experimental_for_chat(chat_id=555) is False

    def test_env_set_to_empty_denies(self, monkeypatch):
        monkeypatch.setenv("KRAB_EXPERIMENTAL", "")
        monkeypatch.delenv("OWNER_CHAT_ID", raising=False)
        monkeypatch.delenv("OWNER_CHAT_IDS", raising=False)
        assert allow_experimental_for_chat(chat_id=555) is False


class TestAllowExperimentalOwnerChat:
    def test_none_chat_id_is_owner_context(self, monkeypatch):
        monkeypatch.delenv("KRAB_EXPERIMENTAL", raising=False)
        monkeypatch.delenv("OWNER_CHAT_ID", raising=False)
        monkeypatch.delenv("OWNER_CHAT_IDS", raising=False)
        assert allow_experimental_for_chat(chat_id=None) is True

    def test_zero_chat_id_is_owner_context(self, monkeypatch):
        monkeypatch.delenv("KRAB_EXPERIMENTAL", raising=False)
        monkeypatch.delenv("OWNER_CHAT_ID", raising=False)
        monkeypatch.delenv("OWNER_CHAT_IDS", raising=False)
        assert allow_experimental_for_chat(chat_id=0) is True

    def test_owner_chat_id_env_matches(self, monkeypatch):
        monkeypatch.delenv("KRAB_EXPERIMENTAL", raising=False)
        monkeypatch.setenv("OWNER_CHAT_ID", "100500")
        monkeypatch.delenv("OWNER_CHAT_IDS", raising=False)
        assert allow_experimental_for_chat(chat_id=100500) is True

    def test_owner_chat_id_env_non_match(self, monkeypatch):
        monkeypatch.delenv("KRAB_EXPERIMENTAL", raising=False)
        monkeypatch.setenv("OWNER_CHAT_ID", "100500")
        monkeypatch.delenv("OWNER_CHAT_IDS", raising=False)
        assert allow_experimental_for_chat(chat_id=999) is False

    def test_owner_chat_ids_env_matches_first(self, monkeypatch):
        monkeypatch.delenv("KRAB_EXPERIMENTAL", raising=False)
        monkeypatch.delenv("OWNER_CHAT_ID", raising=False)
        monkeypatch.setenv("OWNER_CHAT_IDS", "100500,200600,300700")
        assert allow_experimental_for_chat(chat_id=200600) is True

    def test_owner_chat_ids_env_no_match(self, monkeypatch):
        monkeypatch.delenv("KRAB_EXPERIMENTAL", raising=False)
        monkeypatch.delenv("OWNER_CHAT_ID", raising=False)
        monkeypatch.setenv("OWNER_CHAT_IDS", "100500,200600")
        assert allow_experimental_for_chat(chat_id=999) is False

    def test_owner_chat_ids_invalid_entry_skipped(self, monkeypatch):
        monkeypatch.delenv("KRAB_EXPERIMENTAL", raising=False)
        monkeypatch.delenv("OWNER_CHAT_ID", raising=False)
        monkeypatch.setenv("OWNER_CHAT_IDS", "not_a_number,100500")
        assert allow_experimental_for_chat(chat_id=100500) is True

    def test_env_krab_experimental_overrides_everything(self, monkeypatch):
        monkeypatch.setenv("KRAB_EXPERIMENTAL", "1")
        monkeypatch.delenv("OWNER_CHAT_ID", raising=False)
        monkeypatch.delenv("OWNER_CHAT_IDS", raising=False)
        # Любой chat_id пропускается при KRAB_EXPERIMENTAL=1
        assert allow_experimental_for_chat(chat_id=42) is True
