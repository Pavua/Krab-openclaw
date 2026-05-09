"""Tests for Wave 49-C-hexstrike: isolated venv + manual-start LaunchAgent + MCP register.

Risk model: HexStrike-AI orchestrates 151 offensive security tools. The plist
must NOT auto-start (RunAtLoad=false, no KeepAlive), and the inventory entry
must reflect "registered" status with manual-start note. These tests are file/path
based and run without the venv being present.
"""

from __future__ import annotations

import plistlib
import stat
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HEXSTRIKE_REPO = Path("/Users/pablito/Antigravity_AGENTS/hexstrike-ai")
HEXSTRIKE_VENV = HEXSTRIKE_REPO / "hexstrike_env"
PLIST_PATH = REPO_ROOT / "scripts/launchagents/com.krab.hexstrike-server.plist"
TOGGLE_PATH = REPO_ROOT / "scripts/Hexstrike Toggle.command"
INVENTORY_TOML = REPO_ROOT / "scripts/mcp_inventory.toml"


def _load_toml(path: Path) -> dict:
    try:
        import tomllib  # py311+
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore
    with path.open("rb") as f:
        return tomllib.load(f)


def test_hexstrike_isolated_venv_exists():
    """Isolated venv должен жить вне Krab tree."""
    if not HEXSTRIKE_REPO.exists():
        pytest.skip("hexstrike-ai repo not present on this machine")
    # venv может отсутствовать в чистой checkout — но если репозиторий есть,
    # ожидаем что Wave 49-C создал venv.
    assert HEXSTRIKE_VENV.exists(), (
        f"hexstrike isolated venv missing at {HEXSTRIKE_VENV}; "
        "run scripts/Hexstrike Toggle.command bootstrap or recreate via "
        "'/opt/homebrew/bin/python3.13 -m venv hexstrike_env'."
    )
    assert (HEXSTRIKE_VENV / "bin" / "python").exists()
    # Critical: venv path must NOT be inside Krab tree
    krab_root = Path("/Users/pablito/Antigravity_AGENTS/Краб")
    assert krab_root not in HEXSTRIKE_VENV.parents, (
        "hexstrike venv leaked into Krab tree — must be isolated"
    )


def test_hexstrike_launchagent_plist_well_formed():
    """Plist должен быть валидным XML и иметь правильный Label."""
    assert PLIST_PATH.exists(), f"plist missing: {PLIST_PATH}"
    with PLIST_PATH.open("rb") as f:
        plist = plistlib.load(f)
    assert plist["Label"] == "com.krab.hexstrike-server"
    program_args = plist["ProgramArguments"]
    assert program_args[0] == str(HEXSTRIKE_VENV / "bin" / "python")
    assert program_args[1].endswith("hexstrike_server.py")


def test_hexstrike_not_keepalive():
    """Owner explicit start: RunAtLoad=False, KeepAlive отсутствует."""
    with PLIST_PATH.open("rb") as f:
        plist = plistlib.load(f)
    # RunAtLoad must be False (or absent) — never True
    assert plist.get("RunAtLoad", False) is False, (
        "HexStrike server MUST NOT auto-start (offensive tooling)"
    )
    # KeepAlive must be absent or false — server should die when stopped
    keep_alive = plist.get("KeepAlive", False)
    assert keep_alive is False or keep_alive == {}, (
        "HexStrike server MUST NOT keep alive (operator-gated lifecycle)"
    )


def test_hexstrike_toggle_command_exists():
    """Toggle script present + executable."""
    assert TOGGLE_PATH.exists(), f"Toggle script missing: {TOGGLE_PATH}"
    mode = TOGGLE_PATH.stat().st_mode
    # owner exec bit
    assert mode & stat.S_IXUSR, f"{TOGGLE_PATH} not executable; chmod +x required"
    # quick content sanity check — must reference launchctl load AND unload
    content = TOGGLE_PATH.read_text(encoding="utf-8")
    assert "launchctl load" in content
    assert "launchctl unload" in content
    assert "com.krab.hexstrike-server" in content


def test_hexstrike_inventory_entry_status():
    """mcp_inventory.toml has [hexstrike-ai] with status=registered + manual-start note."""
    data = _load_toml(INVENTORY_TOML)
    assert "hexstrike-ai" in data, "hexstrike-ai entry missing in mcp_inventory.toml"
    entry = data["hexstrike-ai"]
    assert entry["transport"] == "stdio"
    assert entry["status"] == "registered"
    # command points to isolated venv
    assert "hexstrike_env/bin/python" in entry["command"]
    # args include --server localhost:8888
    args = entry["args"]
    assert any("hexstrike_mcp.py" in arg for arg in args)
    assert "http://127.0.0.1:8888" in args
    # Note must mention manual-start (safety contract)
    notes = entry.get("notes", "")
    assert "manual" in notes.lower(), "inventory entry must document manual-start requirement"


def test_hexstrike_logs_outside_krab_tree():
    """StandardOutPath/ErrorPath must be /tmp (not in Krab logs/) — server is external."""
    with PLIST_PATH.open("rb") as f:
        plist = plistlib.load(f)
    out_path = plist["StandardOutPath"]
    err_path = plist["StandardErrorPath"]
    assert out_path.startswith("/tmp/"), f"out log not in /tmp: {out_path}"
    assert err_path.startswith("/tmp/"), f"err log not in /tmp: {err_path}"
