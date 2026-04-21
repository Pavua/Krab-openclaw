"""Startup self-test: verify src/skills/*.py modules are registered
or at least importable — warn on discovery gaps.

Chado мёнул что у него 14 skills пролетали мимо L1/L2 grading.
Для Krab — убедимся что нет dark modules.

Public:
- check_all_skills_discovered() -> list[str]
  Returns list of warnings (empty = all good).
"""

from __future__ import annotations

import importlib
from pathlib import Path

from .logger import get_logger

logger = get_logger(__name__)

# Директории для сканирования относительно корня проекта (src/)
_SCAN_DIRS = ("skills", "integrations")


def _find_src_root() -> Path | None:
    """Ищет корень src/ через __file__ цепочку вверх."""
    here = Path(__file__).resolve()
    # Файл лежит в src/core/ — поднимаемся на два уровня → к src/
    candidate = here.parent.parent
    if (candidate / "skills").is_dir():
        return candidate
    return None


def check_all_skills_discovered(
    scan_dirs: tuple[str, ...] | None = None,
    src_root: Path | None = None,
) -> list[str]:
    """Scan src/skills/ and src/integrations/, try import each, check registry.

    Returns list of warning messages:
    - "skill_module_import_failed: <name>: <err>"
    - "skill_module_without_docstring: <name>"
    - "skill_module_empty: <name>"

    Does NOT raise — warnings only. Call at startup after other init.

    Parameters
    ----------
    scan_dirs:
        Tuple of sub-directory names inside src_root to scan.
        Defaults to ("skills", "integrations").
    src_root:
        Override для корня src/. Если None — определяется автоматически через __file__.
    """
    if scan_dirs is None:
        scan_dirs = _SCAN_DIRS

    if src_root is None:
        src_root = _find_src_root()

    warnings: list[str] = []

    if src_root is None:
        warnings.append("skill_discovery_src_root_not_found: unable to locate src/ directory")
        logger.warning("skill_discovery_src_root_not_found")
        return warnings

    for subdir in scan_dirs:
        scan_path = src_root / subdir
        if not scan_path.is_dir():
            warnings.append(f"skill_discovery_dir_missing: {subdir}")
            logger.warning("skill_discovery_dir_missing", subdir=subdir)
            continue

        py_files = sorted(scan_path.glob("*.py"))
        for py_file in py_files:
            name = py_file.stem
            if name.startswith("_"):
                # Пропускаем __init__, __pycache__ и т.п.
                continue

            # Формируем import path: src.skills.foo  →  "src.{subdir}.{name}"
            module_path = f"src.{subdir}.{name}"

            try:
                module = importlib.import_module(module_path)
            except Exception as exc:  # noqa: BLE001
                msg = f"skill_module_import_failed: {module_path}: {exc}"
                warnings.append(msg)
                logger.warning(
                    "skill_module_import_failed",
                    module=module_path,
                    error=str(exc),
                )
                continue

            # Проверяем docstring — dark-module признак
            doc = getattr(module, "__doc__", None)
            if not doc or not doc.strip():
                msg = f"skill_module_without_docstring: {module_path}"
                warnings.append(msg)
                logger.warning("skill_module_without_docstring", module=module_path)

            # Проверяем что модуль содержит хоть что-то публичное
            public_names = [n for n in dir(module) if not n.startswith("_")]
            if not public_names:
                msg = f"skill_module_empty: {module_path}"
                warnings.append(msg)
                logger.warning("skill_module_empty", module=module_path)

    return warnings
