# -*- coding: utf-8 -*-
"""
Тесты для src/core/trusted_guests.py

Покрывает:
  1. Пустой config → is_trusted False
  2. add_trusted → is_trusted True (тот же чат, тот же user)
  3. Разные чаты → False (не перетекает)
  4. Username match когда user_id=0 (неизвестен)
  5. Username match case-insensitive (без @)
  6. Round-trip persist (save → reload)
  7. remove_trusted → is_trusted False после удаления
  8. remove by username
  9. list_trusted — правильный набор записей
  10. Несколько users в одном чате — независимы
  11. Дашка @dodik_ggt — дефолтный config содержит её
  12. Defaults применяются к обоим группам
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.core.trusted_guests import TrustedGuestsStore


@pytest.fixture()
def store(tmp_path: Path) -> TrustedGuestsStore:
    """Изолированный store с пустым (отсутствующим) config файлом."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # Не создаём файл — store инициализирует дефолты сам.
    # Для чистоты тестов пишем пустой объект вручную.
    cfg = state_dir / "trusted_guests.json"
    cfg.write_text("{}", encoding="utf-8")
    return TrustedGuestsStore(state_dir=state_dir)


@pytest.fixture()
def default_store(tmp_path: Path) -> TrustedGuestsStore:
    """Store с отсутствующим файлом → ожидаем дефолты из _DEFAULT_CONFIG."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    return TrustedGuestsStore(state_dir=state_dir)


# ── 1. Пустой config → is_trusted False ───────────────────────────────────────

def test_empty_config_returns_false(store: TrustedGuestsStore) -> None:
    assert store.is_trusted(chat_id=-100123, user_id=999, username="@anyuser") is False


# ── 2. add_trusted → is_trusted True (тот же чат, тот же user) ───────────────

def test_add_by_user_id(store: TrustedGuestsStore) -> None:
    store.add_trusted(chat_id=-100111, user_id=42, username=None)
    assert store.is_trusted(chat_id=-100111, user_id=42, username=None) is True


# ── 3. Разные чаты → False ────────────────────────────────────────────────────

def test_different_chat_returns_false(store: TrustedGuestsStore) -> None:
    store.add_trusted(chat_id=-100111, user_id=42, username=None)
    assert store.is_trusted(chat_id=-100222, user_id=42, username=None) is False


# ── 4. Username match когда user_id=0 ─────────────────────────────────────────

def test_username_match_without_user_id(store: TrustedGuestsStore) -> None:
    store.add_trusted(chat_id=-100111, user_id=0, username="@dodik_ggt")
    assert store.is_trusted(chat_id=-100111, user_id=0, username="@dodik_ggt") is True


# ── 5. Username match case-insensitive (без @ ) ────────────────────────────────

def test_username_case_insensitive(store: TrustedGuestsStore) -> None:
    store.add_trusted(chat_id=-100111, user_id=0, username="@Dodik_GGT")
    assert store.is_trusted(chat_id=-100111, user_id=0, username="dodik_ggt") is True
    assert store.is_trusted(chat_id=-100111, user_id=0, username="DODIK_GGT") is True


# ── 6. Round-trip persist ─────────────────────────────────────────────────────

def test_round_trip_persist(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    cfg = state_dir / "trusted_guests.json"
    cfg.write_text("{}", encoding="utf-8")

    s1 = TrustedGuestsStore(state_dir=state_dir)
    s1.add_trusted(chat_id=-100555, user_id=77, username="@dashka")

    # Новый инстанс читает тот же файл
    s2 = TrustedGuestsStore(state_dir=state_dir)
    assert s2.is_trusted(chat_id=-100555, user_id=77, username="@dashka") is True
    assert s2.is_trusted(chat_id=-100555, user_id=0, username="dashka") is True


# ── 7. remove_trusted → False после удаления ──────────────────────────────────

def test_remove_by_user_id(store: TrustedGuestsStore) -> None:
    store.add_trusted(chat_id=-100111, user_id=99, username=None)
    assert store.is_trusted(chat_id=-100111, user_id=99) is True
    store.remove_trusted(chat_id=-100111, user_id=99)
    assert store.is_trusted(chat_id=-100111, user_id=99) is False


# ── 8. Remove by username ──────────────────────────────────────────────────────

def test_remove_by_username(store: TrustedGuestsStore) -> None:
    store.add_trusted(chat_id=-100111, user_id=0, username="@vanya")
    assert store.is_trusted(chat_id=-100111, user_id=0, username="vanya") is True
    store.remove_trusted(chat_id=-100111, user_id=0, username="@vanya")
    assert store.is_trusted(chat_id=-100111, user_id=0, username="vanya") is False


# ── 9. list_trusted — правильный набор ────────────────────────────────────────

def test_list_trusted(store: TrustedGuestsStore) -> None:
    store.add_trusted(chat_id=-100111, user_id=10, username=None)
    store.add_trusted(chat_id=-100111, user_id=0, username="@dashka")

    entries = store.list_trusted(chat_id=-100111)
    user_ids = [e["user_id"] for e in entries if e["user_id"]]
    usernames = [e["username"] for e in entries if e["username"]]

    assert 10 in user_ids
    assert "@dashka" in usernames


# ── 10. Несколько users в одном чате — независимы ─────────────────────────────

def test_multiple_users_same_chat(store: TrustedGuestsStore) -> None:
    store.add_trusted(chat_id=-100111, user_id=1, username=None)
    store.add_trusted(chat_id=-100111, user_id=2, username=None)
    store.add_trusted(chat_id=-100111, user_id=3, username=None)

    assert store.is_trusted(chat_id=-100111, user_id=1) is True
    assert store.is_trusted(chat_id=-100111, user_id=2) is True
    assert store.is_trusted(chat_id=-100111, user_id=3) is True
    assert store.is_trusted(chat_id=-100111, user_id=4) is False

    store.remove_trusted(chat_id=-100111, user_id=2)
    assert store.is_trusted(chat_id=-100111, user_id=1) is True
    assert store.is_trusted(chat_id=-100111, user_id=2) is False
    assert store.is_trusted(chat_id=-100111, user_id=3) is True


# ── 11. Дашка @dodik_ggt — дефолтный config содержит её ──────────────────────

def test_default_config_contains_dodik(default_store: TrustedGuestsStore) -> None:
    # YMB FAMILY FOREVER
    assert default_store.is_trusted(
        chat_id=-1001804661353, user_id=0, username="@dodik_ggt"
    ) is True
    # How2AI
    assert default_store.is_trusted(
        chat_id=-1001587432709, user_id=0, username="dodik_ggt"
    ) is True


# ── 12. Defaults применяются к обоим группам ─────────────────────────────────

def test_defaults_applied_to_both_groups(default_store: TrustedGuestsStore) -> None:
    all_data = default_store.all_chats()
    assert "-1001804661353" in all_data
    assert "-1001587432709" in all_data

    # @dodik_ggt НЕ доверен в произвольной другой группе
    assert default_store.is_trusted(
        chat_id=-100999, user_id=0, username="@dodik_ggt"
    ) is False


# ── Дополнительный: user_id=0 не матчит по user_id ────────────────────────────

def test_zero_user_id_not_matched_by_id(store: TrustedGuestsStore) -> None:
    """user_id=0 никогда не должен давать True по user_id match."""
    store.add_trusted(chat_id=-100111, user_id=0, username=None)
    # 0 не является реальным user_id — матч по нему не должен срабатывать
    assert store.is_trusted(chat_id=-100111, user_id=0, username=None) is False


# ── Список пустого чата ────────────────────────────────────────────────────────

def test_list_empty_chat(store: TrustedGuestsStore) -> None:
    assert store.list_trusted(chat_id=-100999) == []
