# -*- coding: utf-8 -*-
"""Wave 50-B: deprecation guard для krab-tor MCP wrapper.

`krab-tor` (Wave 44-Z) заменён на `tor-full` (Wave 45-F, 25 tools, superset).
Эти тесты гарантируют, что:

- legacy module импортируется (preserved для archaeology / fallback);
- inventory помечает `krab-tor` как `status="deprecated"`;
- system prompt suffix для агентного контура продвигает `tor-full`,
  а не `krab-tor`;
- existing test_mcp_tor_server.py всё ещё проходит (regression safety).
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

# Repo root: tests/unit/<this>.py → ../..
_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 1) Module still importable (preserved для emergency fallback)
# ---------------------------------------------------------------------------


def test_mcp_tor_server_module_imports_with_deprecation_warning() -> None:
    """``src/mcp_tor_server.py`` должен оставаться importable.

    Хотя LaunchAgent unloaded оркестратором, файл preserved ради
    archaeology + emergency fallback. Modulé docstring должен содержать
    DEPRECATED Wave 50-B mark — это сигнал для разработчиков, читающих
    файл.
    """
    # Reload (might have been imported earlier by other tests).
    if "src.mcp_tor_server" in sys.modules:
        importlib.reload(sys.modules["src.mcp_tor_server"])
    else:
        importlib.import_module("src.mcp_tor_server")

    module = sys.modules["src.mcp_tor_server"]
    assert module.__doc__ is not None
    assert "DEPRECATED Wave 50-B" in module.__doc__
    assert "tor-full" in module.__doc__
    # MCP объект всё ещё определён (на случай manual run).
    assert hasattr(module, "mcp")
    assert hasattr(module, "tor_status")
    assert hasattr(module, "tor_fetch")
    assert hasattr(module, "tor_check_exit_ip")


# ---------------------------------------------------------------------------
# 2) Existing tests still pass (regression check)
# ---------------------------------------------------------------------------


def test_existing_tor_server_tests_still_pass() -> None:
    """``tests/unit/test_mcp_tor_server.py`` не должен сломаться deprecation-ом.

    Запускаем pytest подпроцессом, фильтруя только tor_server tests.
    """
    test_file = _REPO_ROOT / "tests" / "unit" / "test_mcp_tor_server.py"
    assert test_file.exists(), f"Expected legacy tests at {test_file}"

    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-q", "--no-header"],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"test_mcp_tor_server.py regression after Wave 50-B deprecation:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# 3) Inventory marks krab-tor deprecated
# ---------------------------------------------------------------------------


def _load_inventory() -> dict:
    inventory_path = _REPO_ROOT / "scripts" / "mcp_inventory.toml"
    assert inventory_path.exists()
    try:
        import tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[import-not-found]
    with inventory_path.open("rb") as fh:
        return tomllib.load(fh)


def test_inventory_marks_krab_tor_deprecated() -> None:
    """`scripts/mcp_inventory.toml` должен содержать krab-tor с status=deprecated."""
    inventory = _load_inventory()
    assert "krab-tor" in inventory, "krab-tor entry должен присутствовать (с deprecation tag)"
    krab_tor = inventory["krab-tor"]
    assert krab_tor.get("status") == "deprecated", (
        f"krab-tor должен иметь status='deprecated', got: {krab_tor.get('status')!r}"
    )
    notes = str(krab_tor.get("notes", ""))
    assert "tor-full" in notes, "Notes должны указывать на replacement (tor-full)"
    assert "Wave 50-B" in notes or "Wave 50-B" in str(krab_tor.get("description", ""))


def test_inventory_tor_full_references_replacement() -> None:
    """`tor-full` notes должны упоминать replacement role относительно krab-tor."""
    inventory = _load_inventory()
    assert "tor-full" in inventory
    tor_full = inventory["tor-full"]
    notes = str(tor_full.get("notes", ""))
    assert "krab-tor" in notes, "tor-full notes должны явно упоминать krab-tor"


# ---------------------------------------------------------------------------
# 4) Access control hint prefers tor-full
# ---------------------------------------------------------------------------


def test_access_control_prefers_tor_full_in_hints() -> None:
    """`KRAB_EXTERNAL_MCP_HINT_ENABLED` suffix должен продвигать tor-full, не krab-tor.

    Wave 50-B: после deprecation `krab-tor` LLM не должна получать hint
    с упоминанием снятого MCP — иначе будет пытаться вызвать недоступный
    инструмент.
    """
    access_control_path = _REPO_ROOT / "src" / "userbot" / "access_control.py"
    source = access_control_path.read_text(encoding="utf-8")

    # Find mcp_hint block (between '🌐 Внешние MCP-инструменты' and end of mcp_hint = (...))
    assert "🌐 Внешние MCP-инструменты" in source, (
        "MCP hint block должен присутствовать в access_control.py"
    )
    # Bullet line «- krab-tor —» как первый-toolt в hint должен исчезнуть.
    assert "- krab-tor — " not in source, (
        "Wave 50-B: hint о krab-tor должен быть удалён или заменён на tor-full"
    )
    assert "tor-full" in source, "tor-full должен упоминаться как replacement"


# ---------------------------------------------------------------------------
# 5) Plist marked deprecated (preservation, not removal)
# ---------------------------------------------------------------------------


def test_plist_preserved_with_deprecation_marker() -> None:
    """Plist должен оставаться в репо с deprecation comment header."""
    plist_path = _REPO_ROOT / "scripts" / "launchagents" / "com.krab.mcp-tor.plist"
    assert plist_path.exists(), "Plist preserved для git history"
    text = plist_path.read_text(encoding="utf-8")
    assert "DEPRECATED Wave 50-B" in text
    assert "tor-full" in text
