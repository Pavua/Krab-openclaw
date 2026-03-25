# -*- coding: utf-8 -*-
"""
Тесты permission-safe поведения handoff exporter.

Зачем:
- часть optional источников bundle может жить в account-local директориях другой учётки;
- exporter не должен падать целиком, если такой файл недоступен по PermissionError.
"""

from __future__ import annotations

from pathlib import Path

import scripts.export_handoff_bundle as handoff_module


def test_copy_if_exists_ignores_permission_error(tmp_path: Path, monkeypatch) -> None:
    """Optional account-local файл без доступа не должен ломать exporter."""
    src = tmp_path / "forbidden.txt"
    dst = tmp_path / "copied.txt"
    original_exists = Path.exists

    def _patched_exists(path: Path) -> bool:
        if path == src:
            raise PermissionError("denied")
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", _patched_exists)

    handoff_module._copy_if_exists(src, dst)

    assert dst.exists() is False
