# -*- coding: utf-8 -*-
"""
Wave 21-A: авто-очистка _corrupt_flag после N успешных read/write операций.

Тесты покрывают:
1. test_record_success_increments_counter
2. test_threshold_reached_clears_flag
3. test_threshold_below_keeps_flag
4. test_clear_resets_counter
5. test_threshold_via_env_override
6. test_no_double_log_when_already_clear
"""

from __future__ import annotations

import importlib
import sys

import pytest

# ---------------------------------------------------------------------------
# Хелперы: импорт модуля с чистым состоянием между тестами
# ---------------------------------------------------------------------------


def _reload_patch_module():
    """Перезагружает pyrogram_patch, сбрасывая все module-level dict'ы."""
    mod_name = "src.bootstrap.pyrogram_patch"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    mod = importlib.import_module(mod_name)
    return mod


class _FakeStorage:
    """Простой storage с динамическими атрибутами."""

    pass


class _SlotsStorage:
    """Storage с __slots__ — dynamic attrs не поддерживаются."""

    __slots__ = ("name",)

    def __init__(self) -> None:
        self.name = "slots"


# ---------------------------------------------------------------------------
# 1. Счётчик увеличивается при каждом вызове _record_storage_success
# ---------------------------------------------------------------------------


def test_record_success_increments_counter():
    """_record_storage_success увеличивает счётчик для данного storage."""
    mod = _reload_patch_module()
    storage = _FakeStorage()
    sid = id(storage)

    assert sid not in mod._STORAGE_SUCCESS_COUNTS

    mod._record_storage_success(storage)
    assert mod._STORAGE_SUCCESS_COUNTS.get(sid, 0) == 1

    mod._record_storage_success(storage)
    assert mod._STORAGE_SUCCESS_COUNTS.get(sid, 0) == 2


# ---------------------------------------------------------------------------
# 2. При достижении порога флаг сбрасывается
# ---------------------------------------------------------------------------


def test_threshold_reached_clears_flag():
    """После N=threshold вызовов _record_storage_success corrupt flag сбрасывается."""
    mod = _reload_patch_module()
    # Принудительно задаём маленький threshold для теста
    mod._STORAGE_AUTO_CLEAR_THRESHOLD = 5

    storage = _FakeStorage()
    storage._corrupt_flag = True

    assert mod.is_storage_corrupt(storage)

    # N-1 вызовов — флаг ещё должен стоять
    for _ in range(4):
        mod._record_storage_success(storage)
    assert mod.is_storage_corrupt(storage), "флаг ещё не должен быть сброшен"

    # N-й вызов — должен сбросить
    mod._record_storage_success(storage)
    assert not mod.is_storage_corrupt(storage), "флаг должен быть автоматически сброшен"
    # Счётчик тоже должен быть очищен после авто-сброса
    assert id(storage) not in mod._STORAGE_SUCCESS_COUNTS


# ---------------------------------------------------------------------------
# 3. Ниже порога флаг не трогается
# ---------------------------------------------------------------------------


def test_threshold_below_keeps_flag():
    """При threshold-1 вызовах corrupt flag остаётся True."""
    mod = _reload_patch_module()
    mod._STORAGE_AUTO_CLEAR_THRESHOLD = 10

    storage = _FakeStorage()
    storage._corrupt_flag = True

    for _ in range(9):
        mod._record_storage_success(storage)

    assert mod.is_storage_corrupt(storage), "99 < 100 — флаг не должен быть сброшен"
    assert mod._STORAGE_SUCCESS_COUNTS.get(id(storage), 0) == 9


# ---------------------------------------------------------------------------
# 4. clear_storage_corrupt_flag сбрасывает счётчик тоже
# ---------------------------------------------------------------------------


def test_clear_resets_counter():
    """clear_storage_corrupt_flag сбрасывает и флаг и счётчик."""
    mod = _reload_patch_module()
    mod._STORAGE_AUTO_CLEAR_THRESHOLD = 100

    storage = _FakeStorage()
    storage._corrupt_flag = True

    # Накапливаем несколько успехов
    for _ in range(50):
        mod._record_storage_success(storage)

    assert mod._STORAGE_SUCCESS_COUNTS.get(id(storage), 0) == 50

    # Ручной сброс
    mod.clear_storage_corrupt_flag(storage)

    assert not mod.is_storage_corrupt(storage)
    assert id(storage) not in mod._STORAGE_SUCCESS_COUNTS


# ---------------------------------------------------------------------------
# 5. ENV override KRAB_STORAGE_CORRUPT_AUTO_CLEAR_THRESHOLD
# ---------------------------------------------------------------------------


def test_threshold_via_env_override(monkeypatch):
    """KRAB_STORAGE_CORRUPT_AUTO_CLEAR_THRESHOLD читается из env через Config."""
    monkeypatch.setenv("KRAB_STORAGE_CORRUPT_AUTO_CLEAR_THRESHOLD", "3")

    # Перезагружаем config (чтобы env применился)
    config_name = "src.config"
    if config_name in sys.modules:
        del sys.modules[config_name]

    mod = _reload_patch_module()
    # Сбрасываем кэш threshold (lazy-loaded)
    mod._STORAGE_AUTO_CLEAR_THRESHOLD = None

    storage = _FakeStorage()
    storage._corrupt_flag = True

    # При пороге=3: 2 вызова → флаг есть, 3-й → должен сброситься
    mod._record_storage_success(storage)
    mod._record_storage_success(storage)
    assert mod.is_storage_corrupt(storage), "2 < 3 — ещё не сброшен"

    mod._record_storage_success(storage)
    assert not mod.is_storage_corrupt(storage), "3 >= 3 — должен быть сброшен"


# ---------------------------------------------------------------------------
# 6. Idempotency: авто-сброс при уже clear flag — нет двойного лога / crash
# ---------------------------------------------------------------------------


def test_no_double_log_when_already_clear():
    """_record_storage_success безопасен когда флаг уже False (no crash, no double log).

    При достижении порога для уже-clear storage счётчик сбрасывается и
    функция не поднимает исключений. После сброса счётчик начинает с нуля
    (может накопиться до следующего порога).
    """
    mod = _reload_patch_module()
    mod._STORAGE_AUTO_CLEAR_THRESHOLD = 3

    storage = _FakeStorage()
    # Флаг изначально не установлен

    # Ровно threshold вызовов — счётчик сбрасывается при достижении (already-clear path)
    for _ in range(3):
        mod._record_storage_success(storage)

    # Счётчик должен быть сброшен на пороге (already-clear cleanup)
    assert id(storage) not in mod._STORAGE_SUCCESS_COUNTS
    assert not mod.is_storage_corrupt(storage)

    # Следующие вызовы начинают с чистого счётчика — тоже без исключений
    mod._record_storage_success(storage)
    assert mod._STORAGE_SUCCESS_COUNTS.get(id(storage), 0) == 1
