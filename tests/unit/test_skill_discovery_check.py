# -*- coding: utf-8 -*-
"""
Тесты для src/core/skill_discovery_check.py.

Покрытие:
1. Возвращает пустой список когда все skills импортируются чисто.
2. Возвращает предупреждение если директория не существует.
3. Формат предупреждения при import failure содержит имя модуля и ошибку.
4. Не бросает исключение при ImportError в skill-модуле.
5. Предупреждение при отсутствующем docstring.
6. Предупреждение при пустом (no public names) модуле.
7. Пропускает файлы начинающиеся с _ (__init__, __pycache__).
8. Возвращает предупреждение если src_root не найден (None).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.skill_discovery_check import check_all_skills_discovered


class TestCheckAllSkillsDiscovered:
    """Юнит-тесты check_all_skills_discovered()."""

    def test_real_skills_importable_returns_no_import_failures(self) -> None:
        """Реальные skills в src/skills/ импортируются без ошибок."""
        warnings = check_all_skills_discovered(scan_dirs=("skills",))
        import_failures = [w for w in warnings if "skill_module_import_failed" in w]
        assert import_failures == [], f"Неожиданные import failures: {import_failures}"

    def test_missing_directory_warns(self, tmp_path: Path) -> None:
        """Несуществующая директория → предупреждение skill_discovery_dir_missing."""
        warnings = check_all_skills_discovered(
            scan_dirs=("nonexistent_subdir",),
            src_root=tmp_path,
        )
        assert any("skill_discovery_dir_missing" in w for w in warnings)

    def test_src_root_none_warns(self) -> None:
        """src_root=None и невозможность определить → предупреждение."""
        with patch("src.core.skill_discovery_check._find_src_root", return_value=None):
            warnings = check_all_skills_discovered()
        assert any("skill_discovery_src_root_not_found" in w for w in warnings)

    def test_does_not_raise_on_import_error(self, tmp_path: Path) -> None:
        """ImportError в skill-модуле не бросает исключение, только warning."""
        # Создаём фейковый skills/ с невалидным модулем
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        bad_module = skills_dir / "broken_skill.py"
        bad_module.write_text("raise ImportError('intentional test error')\n", encoding="utf-8")

        # Патчим importlib.import_module чтобы симулировать failure
        original_import = __import__

        def patched_import(name: str, *args, **kwargs):
            if "broken_skill" in name:
                raise ImportError("intentional test error")
            return original_import(name, *args, **kwargs)

        with patch("src.core.skill_discovery_check.importlib.import_module", side_effect=patched_import):
            # Не должно бросать
            warnings = check_all_skills_discovered(
                scan_dirs=("skills",),
                src_root=tmp_path,
            )

        failure_warnings = [w for w in warnings if "skill_module_import_failed" in w]
        assert len(failure_warnings) >= 1

    def test_import_failure_warning_format(self, tmp_path: Path) -> None:
        """Формат warning при import failure: содержит имя модуля и сообщение об ошибке."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "bad_mod.py").write_text("x = 1\n", encoding="utf-8")

        err_msg = "something broke badly"

        def always_fail(name: str, *args, **kwargs):
            if "bad_mod" in name:
                raise ImportError(err_msg)
            raise ImportError("unexpected import")

        with patch("src.core.skill_discovery_check.importlib.import_module", side_effect=always_fail):
            warnings = check_all_skills_discovered(
                scan_dirs=("skills",),
                src_root=tmp_path,
            )

        failure_warnings = [w for w in warnings if "skill_module_import_failed" in w]
        assert len(failure_warnings) == 1
        w = failure_warnings[0]
        assert "bad_mod" in w
        assert err_msg in w

    def test_module_without_docstring_warns(self, tmp_path: Path) -> None:
        """Модуль без docstring → предупреждение skill_module_without_docstring."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "nodoc_skill.py").write_text("x = 42\n", encoding="utf-8")

        fake_module = types.ModuleType("src.skills.nodoc_skill")
        fake_module.__doc__ = None  # нет docstring
        fake_module.some_func = lambda: None

        with patch(
            "src.core.skill_discovery_check.importlib.import_module",
            return_value=fake_module,
        ):
            warnings = check_all_skills_discovered(
                scan_dirs=("skills",),
                src_root=tmp_path,
            )

        assert any("skill_module_without_docstring" in w for w in warnings)

    def test_empty_module_warns(self, tmp_path: Path) -> None:
        """Модуль без публичных имён → предупреждение skill_module_empty."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "empty_skill.py").write_text('"""docstring"""\n', encoding="utf-8")

        fake_module = types.ModuleType("src.skills.empty_skill")
        fake_module.__doc__ = "This module has a docstring but no public names"
        # dir() вернёт только dunder-атрибуты — публичных нет

        with patch(
            "src.core.skill_discovery_check.importlib.import_module",
            return_value=fake_module,
        ):
            warnings = check_all_skills_discovered(
                scan_dirs=("skills",),
                src_root=tmp_path,
            )

        assert any("skill_module_empty" in w for w in warnings)

    def test_skips_dunder_files(self, tmp_path: Path) -> None:
        """Файлы начинающиеся с _ (__init__.py, _private.py) не сканируются."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "__init__.py").write_text("", encoding="utf-8")
        (skills_dir / "_internal.py").write_text("raise RuntimeError('should not import')\n", encoding="utf-8")

        # Если _-файлы не пропускаются — import_module бросит RuntimeError.
        # Проверяем что check не упал и нет импорта этих файлов.
        import_calls: list[str] = []

        def track_import(name: str, *args, **kwargs):
            import_calls.append(name)
            raise ImportError(f"module {name} not found")

        with patch("src.core.skill_discovery_check.importlib.import_module", side_effect=track_import):
            warnings = check_all_skills_discovered(
                scan_dirs=("skills",),
                src_root=tmp_path,
            )

        # Ни __init__, ни _internal не должны появляться в вызовах import
        for call in import_calls:
            assert "__init__" not in call, f"__init__.py не должен импортироваться, но был: {call}"
            assert "_internal" not in call, f"_internal.py не должен импортироваться, но был: {call}"

    def test_returns_list(self) -> None:
        """Функция всегда возвращает list[str]."""
        result = check_all_skills_discovered()
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, str)
