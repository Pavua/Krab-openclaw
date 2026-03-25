"""
Тесты для scripts/r20_merge_gate.py.

Фокус: выбор python-интерпретатора должен находить среду, где доступен pytest,
чтобы merge gate не падал ложноположительно из-за "пустого" .venv.
"""

from __future__ import annotations

import importlib.util
import sys
from types import SimpleNamespace
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "r20_merge_gate.py"
    spec = importlib.util.spec_from_file_location("r20_merge_gate", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_python_bin_skips_candidate_without_pytest(monkeypatch, tmp_path):
    module = _load_module()
    root = tmp_path / "krab-r20-test"
    local_python = root / ".venv" / "bin" / "python"
    local_python.parent.mkdir(parents=True)
    local_python.write_text("", encoding="utf-8")
    module.ROOT = root

    monkeypatch.setattr(module.sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr(module.shutil, "which", lambda name: f"/opt/homebrew/bin/{name}")

    calls: list[str] = []

    def _fake_run(cmd, **_kwargs):
        py = cmd[0]
        calls.append(py)
        # .venv не содержит pytest, системный python содержит.
        return SimpleNamespace(returncode=0 if py == "/usr/bin/python3" else 1)

    monkeypatch.setattr(module.subprocess, "run", _fake_run)

    selected = module._python_bin()
    assert selected == "/usr/bin/python3"
    assert str(local_python) in calls
    assert "/usr/bin/python3" in calls


def test_resolve_pytest_targets_falls_back_to_current_suite(tmp_path):
    module = _load_module()
    root = tmp_path / "krab-r20-targets"
    (root / "tests" / "unit").mkdir(parents=True)
    (root / "tests" / "unit" / "test_web_app_runtime_endpoints.py").write_text("", encoding="utf-8")
    (root / "tests" / "test_krab_core_health_watch.py").write_text("", encoding="utf-8")
    module.ROOT = root

    targets = module._resolve_pytest_targets()
    assert "tests/unit/test_web_app_runtime_endpoints.py" in targets
    assert "tests/test_krab_core_health_watch.py" in targets
