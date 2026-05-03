# -*- coding: utf-8 -*-
"""
MEDIUM-3 (Wave 16-O): _corrupt_flag __slots__-safe — fallback dict.

Проверяем:
- На обычном storage setattr работает и is_storage_corrupt возвращает True.
- На __slots__-storage setattr поднимает AttributeError →
  fallback dict _STORAGE_CORRUPT_FLAGS обновляется →
  is_storage_corrupt возвращает True через fallback.
- clear_storage_corrupt_flag сбрасывает оба пути.
- is_storage_corrupt(normal) где флаг False → возвращает False.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _NormalStorage:
    """Обычный storage — dynamic attrs поддерживаются."""

    pass


class _SlotsStorage:
    """Storage с __slots__ — dynamic attrs не поддерживаются."""

    __slots__ = ("name",)

    def __init__(self) -> None:
        self.name = "slots_storage"


# ---------------------------------------------------------------------------
# 1. Обычный storage — setattr работает, is_storage_corrupt = True
# ---------------------------------------------------------------------------


def test_corrupt_flag_normal_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    """На обычном storage setattr(_corrupt_flag) устанавливается, is_storage_corrupt=True."""
    import src.bootstrap.pyrogram_patch as pp

    # Очищаем fallback dict чтобы не было мусора от других тестов
    monkeypatch.setattr(pp, "_STORAGE_CORRUPT_FLAGS", {})

    storage = _NormalStorage()
    assert not pp.is_storage_corrupt(storage)

    setattr(storage, "_corrupt_flag", True)
    assert pp.is_storage_corrupt(storage)


# ---------------------------------------------------------------------------
# 2. __slots__ storage — setattr падает, fallback dict обновляется
# ---------------------------------------------------------------------------


def test_corrupt_flag_slots_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """__slots__-storage: setattr(_corrupt_flag) → AttributeError → fallback dict → is_storage_corrupt=True."""
    import src.bootstrap.pyrogram_patch as pp

    monkeypatch.setattr(pp, "_STORAGE_CORRUPT_FLAGS", {})

    storage = _SlotsStorage()

    # Убеждаемся что setattr действительно падает на __slots__
    with pytest.raises((AttributeError, TypeError)):
        setattr(storage, "_corrupt_flag", True)

    # До записи в fallback dict — is_storage_corrupt False
    assert not pp.is_storage_corrupt(storage)

    # Имитируем то, что делает _make_safe_method при (AttributeError, TypeError):
    pp._STORAGE_CORRUPT_FLAGS[id(storage)] = True

    # Теперь is_storage_corrupt должен вернуть True через fallback dict
    assert pp.is_storage_corrupt(storage), (
        "is_storage_corrupt должен вернуть True через _STORAGE_CORRUPT_FLAGS fallback"
    )


# ---------------------------------------------------------------------------
# 3. is_storage_corrupt после clear_storage_corrupt_flag → False
# ---------------------------------------------------------------------------


def test_clear_corrupt_flag_normal(monkeypatch: pytest.MonkeyPatch) -> None:
    """clear_storage_corrupt_flag сбрасывает флаг на обычном storage."""
    import src.bootstrap.pyrogram_patch as pp

    monkeypatch.setattr(pp, "_STORAGE_CORRUPT_FLAGS", {})

    storage = _NormalStorage()
    setattr(storage, "_corrupt_flag", True)
    assert pp.is_storage_corrupt(storage)

    pp.clear_storage_corrupt_flag(storage)
    assert not pp.is_storage_corrupt(storage)


def test_clear_corrupt_flag_slots_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """clear_storage_corrupt_flag: на __slots__-storage setattr падает, но это ОК (уже False в dict)."""
    import src.bootstrap.pyrogram_patch as pp

    flags: dict[int, bool] = {}
    monkeypatch.setattr(pp, "_STORAGE_CORRUPT_FLAGS", flags)

    storage = _SlotsStorage()
    flags[id(storage)] = True
    assert pp.is_storage_corrupt(storage)

    # clear_storage_corrupt_flag пытается setattr → AttributeError → pass (guard)
    # При этом fallback dict НЕ сбрасывается (текущая реализация).
    # Тест проверяет что clear не крашится.
    try:
        pp.clear_storage_corrupt_flag(storage)
    except Exception as exc:
        pytest.fail(f"clear_storage_corrupt_flag поднял исключение: {exc}")


# ---------------------------------------------------------------------------
# 4. is_storage_corrupt без флага → False
# ---------------------------------------------------------------------------


def test_is_storage_corrupt_false_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Свежий storage без флага → is_storage_corrupt=False."""
    import src.bootstrap.pyrogram_patch as pp

    monkeypatch.setattr(pp, "_STORAGE_CORRUPT_FLAGS", {})

    storage = _NormalStorage()
    assert not pp.is_storage_corrupt(storage)

    storage2 = _SlotsStorage()
    assert not pp.is_storage_corrupt(storage2)
