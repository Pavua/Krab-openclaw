# -*- coding: utf-8 -*-
"""
Тесты для src/core/subprocess_env.py.

Покрытие:
1. Базовый вызов возвращает dict.
2. Malloc-ключи удалены из результата.
3. Homebrew-пути присутствуют в PATH.
4. Homebrew-пути не дублируются если уже есть.
5. Результат — копия, не ссылка на os.environ.
6. Без PATH в среде — PATH формируется из homebrew-префиксов.
7. Порядок: homebrew-пути идут первыми в PATH.
8. Прочие переменные окружения сохраняются.
9. Несколько malloc-ключей одновременно — все удалены.
"""

from __future__ import annotations

import os

import pytest

from src.core.subprocess_env import (
    _HOMEBREW_PATH_PREFIXES,
    _MALLOC_DEBUG_KEYS,
    clean_subprocess_env,
)


class TestCleanSubprocessEnv:
    """Юнит-тесты clean_subprocess_env()."""

    def test_returns_dict(self) -> None:
        """Возвращает словарь."""
        result = clean_subprocess_env()
        assert isinstance(result, dict)

    def test_malloc_keys_removed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Все malloc-ключи удалены из результата."""
        for key in _MALLOC_DEBUG_KEYS:
            monkeypatch.setenv(key, "1")

        result = clean_subprocess_env()

        for key in _MALLOC_DEBUG_KEYS:
            assert key not in result, f"Ключ {key} не должен присутствовать в результате"

    def test_malloc_keys_absent_by_default_no_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Если malloc-ключей нет в окружении — функция не падает."""
        for key in _MALLOC_DEBUG_KEYS:
            monkeypatch.delenv(key, raising=False)

        result = clean_subprocess_env()
        assert isinstance(result, dict)

    def test_homebrew_paths_present_in_path(self) -> None:
        """Homebrew-пути присутствуют в PATH."""
        result = clean_subprocess_env()
        path_entries = result["PATH"].split(os.pathsep)

        for prefix in _HOMEBREW_PATH_PREFIXES:
            assert prefix in path_entries, f"{prefix} должен быть в PATH"

    def test_homebrew_paths_not_duplicated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Если homebrew-пути уже есть в PATH — они не дублируются."""
        existing = os.pathsep.join(list(_HOMEBREW_PATH_PREFIXES) + ["/usr/bin"])
        monkeypatch.setenv("PATH", existing)

        result = clean_subprocess_env()
        path_entries = result["PATH"].split(os.pathsep)

        for prefix in _HOMEBREW_PATH_PREFIXES:
            assert path_entries.count(prefix) == 1, f"{prefix} дублируется в PATH"

    def test_result_is_copy_not_original(self) -> None:
        """Возвращаемый dict — копия, изменение не затрагивает os.environ."""
        result = clean_subprocess_env()
        result["_KRAB_TEST_SENTINEL"] = "test_value"

        assert "_KRAB_TEST_SENTINEL" not in os.environ

    def test_empty_path_becomes_homebrew_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Если PATH пустой — формируется только из homebrew-префиксов."""
        monkeypatch.setenv("PATH", "")

        result = clean_subprocess_env()
        path_entries = result["PATH"].split(os.pathsep)

        for prefix in _HOMEBREW_PATH_PREFIXES:
            assert prefix in path_entries

    def test_no_path_env_at_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Если PATH полностью отсутствует — формируется из homebrew-префиксов."""
        monkeypatch.delenv("PATH", raising=False)

        result = clean_subprocess_env()
        assert "PATH" in result

        path_entries = result["PATH"].split(os.pathsep)
        for prefix in _HOMEBREW_PATH_PREFIXES:
            assert prefix in path_entries

    def test_homebrew_paths_come_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Homebrew-пути добавляются в начало PATH, а не в конец."""
        monkeypatch.setenv("PATH", "/usr/bin:/bin")

        result = clean_subprocess_env()
        path_entries = result["PATH"].split(os.pathsep)

        # Все homebrew-пути должны идти ДО /usr/bin
        usr_bin_idx = path_entries.index("/usr/bin")
        for prefix in _HOMEBREW_PATH_PREFIXES:
            if prefix in path_entries:
                assert path_entries.index(prefix) < usr_bin_idx

    def test_other_env_vars_preserved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Прочие переменные окружения сохраняются без изменений."""
        monkeypatch.setenv("KRAB_CUSTOM_VAR", "hello_krab")

        result = clean_subprocess_env()
        assert result.get("KRAB_CUSTOM_VAR") == "hello_krab"

    def test_multiple_malloc_keys_all_removed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Несколько malloc-ключей выставлены одновременно — все удалены."""
        sample = list(_MALLOC_DEBUG_KEYS)[:3]
        for key in sample:
            monkeypatch.setenv(key, "YES")

        result = clean_subprocess_env()

        for key in sample:
            assert key not in result
