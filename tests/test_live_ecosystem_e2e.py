"""
Тесты для scripts/live_ecosystem_e2e.py.

Проверяем bootstrap локального venv, чтобы live E2E не падал,
если его запускают системным `python3`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_e2e_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "live_ecosystem_e2e.py"
    spec = importlib.util.spec_from_file_location("live_ecosystem_e2e", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_inject_repo_site_packages_adds_local_venv_path(tmp_path, monkeypatch):
    module = _load_e2e_module()
    site_packages = tmp_path / ".venv" / "lib" / "python3.12" / "site-packages"
    site_packages.mkdir(parents=True)

    monkeypatch.setattr(module.sys, "path", ["/tmp/original"])

    injected = module._inject_repo_site_packages(tmp_path)

    assert injected == [str(site_packages.resolve())]
    assert module.sys.path[0] == str(site_packages.resolve())


def test_inject_repo_site_packages_skips_existing_path(tmp_path, monkeypatch):
    module = _load_e2e_module()
    site_packages = tmp_path / ".venv" / "lib" / "python3.12" / "site-packages"
    site_packages.mkdir(parents=True)
    existing = str(site_packages.resolve())

    monkeypatch.setattr(module.sys, "path", [existing, "/tmp/original"])

    injected = module._inject_repo_site_packages(tmp_path)

    assert injected == []
    assert module.sys.path[0] == existing
