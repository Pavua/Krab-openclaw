# -*- coding: utf-8 -*-
"""
Wave 52-E tests: !mcp command — read-only MCP inventory + registered overview.

Coverage:
1) `!mcp` — list registered MCPs + status indicators
2) `!mcp info <name>` — detail card
3) `!mcp info <unknown>` — graceful "not found"
4) `!mcp inventory` — full registry grouped by status
5) Owner-only enforcement (non-owner rejected)
6) Subprocess failure → fallback to inventory.toml only
7) Required env-var detection (missing → ⚠️)
8) Status classifier (active/deferred/deprecated)
"""

from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.access_control import AccessLevel, AccessProfile
from src.handlers.commands.observability_commands import (
    _check_env_var_present,
    _classify_mcp_status,
    _format_mcp_info,
    _format_mcp_inventory_full,
    _format_mcp_summary,
    _get_registered_mcps,
    _load_mcp_inventory,
    handle_mcp,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message(text: str, owner: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=-100123),
        from_user=SimpleNamespace(id=42 if owner else 999, username="u"),
        reply=AsyncMock(),
    )


def _make_bot(owner: bool = True) -> MagicMock:
    bot = MagicMock()
    level = AccessLevel.OWNER if owner else AccessLevel.PARTIAL
    bot._get_access_profile = MagicMock(
        return_value=AccessProfile(level=level, source="test", matched_subject="42")
    )
    return bot


_SAMPLE_INVENTORY = """
[context7]
description = "Live API docs"
transport = "stdio"
command = "npx"
args = ["-y", "@upstash/context7-mcp"]
required_env = []

[github]
description = "GitHub Issues"
transport = "streamable-http"
url = "https://api.githubcopilot.com/mcp/"
required_env = ["GITHUB_PERSONAL_ACCESS_TOKEN"]

[hive-crypto]
description = "Hive crypto"
transport = "stdio"
command = "npx"
args = ["-y", "hive-intelligence"]
required_env = ["HIVE_API_KEY"]
status = "deferred"

[krab-tor]
description = "DEPRECATED Wave 50-B"
transport = "sse"
url = "http://127.0.0.1:8014/sse"
required_env = []
status = "deprecated"
notes = "DEPRECATED 2026-05-10: replaced by tor-full."

[tor-full]
description = "25 tor tools"
transport = "stdio"
command = "/usr/bin/python"
args = ["/path/server.py"]
required_env = []
status = "registered"
"""


@pytest.fixture
def inv_path(tmp_path: pathlib.Path) -> pathlib.Path:
    p = tmp_path / "mcp_inventory.toml"
    p.write_text(_SAMPLE_INVENTORY, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 1. !mcp — list active with status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_list_shows_registered_with_status(
    inv_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`!mcp` shows active MCPs with key markers."""
    monkeypatch.setattr(
        "src.handlers.commands.observability_commands._MCP_INVENTORY_PATH", inv_path
    )
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "ghp_xxx")

    registered = {
        "context7": {"command": "npx", "args": ["-y"]},
        "github": {"url": "https://api.githubcopilot.com/mcp/", "transport": "streamable-http"},
        "tor-full": {"command": "/usr/bin/python"},
    }
    msg = _make_message("!mcp")
    bot = _make_bot(owner=True)

    with patch(
        "src.handlers.commands.observability_commands._get_registered_mcps",
        AsyncMock(return_value=(registered, None)),
    ):
        await handle_mcp(bot, msg)

    msg.reply.assert_called_once()
    out = msg.reply.call_args.args[0]
    assert "MCP Inventory" in out
    assert "3 registered" in out
    assert "✅ `context7`" in out
    assert "✅ `github`" in out
    assert "(key: ✓)" in out  # github token is set
    assert "Active" in out


# ---------------------------------------------------------------------------
# 2. !mcp info — detail card
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_info_existing_mcp_returns_details(
    inv_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "src.handlers.commands.observability_commands._MCP_INVENTORY_PATH", inv_path
    )
    monkeypatch.delenv("HIVE_API_KEY", raising=False)
    msg = _make_message("!mcp info hive-crypto")
    bot = _make_bot(owner=True)

    with patch(
        "src.handlers.commands.observability_commands._get_registered_mcps",
        AsyncMock(return_value=({}, None)),
    ):
        await handle_mcp(bot, msg)

    out = msg.reply.call_args.args[0]
    assert "MCP `hive-crypto`" in out
    assert "deferred" in out.lower()
    assert "HIVE_API_KEY" in out
    assert "❌" in out  # missing env marker
    assert "Hive crypto" in out


# ---------------------------------------------------------------------------
# 3. !mcp info <unknown>
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_info_unknown_mcp_returns_error(
    inv_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "src.handlers.commands.observability_commands._MCP_INVENTORY_PATH", inv_path
    )
    msg = _make_message("!mcp info nonexistent-xyz")
    bot = _make_bot(owner=True)

    with patch(
        "src.handlers.commands.observability_commands._get_registered_mcps",
        AsyncMock(return_value=({}, None)),
    ):
        await handle_mcp(bot, msg)

    out = msg.reply.call_args.args[0]
    assert "не найден" in out
    assert "nonexistent-xyz" in out


# ---------------------------------------------------------------------------
# 4. !mcp inventory — grouped by status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_inventory_groups_by_status(
    inv_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "src.handlers.commands.observability_commands._MCP_INVENTORY_PATH", inv_path
    )
    monkeypatch.delenv("HIVE_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
    registered = {"context7": {"command": "npx"}, "tor-full": {"command": "/p"}}
    msg = _make_message("!mcp inventory")
    bot = _make_bot(owner=True)

    with patch(
        "src.handlers.commands.observability_commands._get_registered_mcps",
        AsyncMock(return_value=(registered, None)),
    ):
        await handle_mcp(bot, msg)

    out = msg.reply.call_args.args[0]
    assert "Full MCP Registry" in out
    assert "Active" in out
    assert "Deferred" in out
    assert "Deprecated" in out
    assert "`context7`" in out
    assert "`tor-full`" in out
    assert "`hive-crypto`" in out  # deferred
    assert "`krab-tor`" in out  # deprecated
    assert "`github`" in out  # deferred (no token)


# ---------------------------------------------------------------------------
# 5. Owner-only enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_only_rejects_others(
    inv_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.exceptions import UserInputError

    monkeypatch.setattr(
        "src.handlers.commands.observability_commands._MCP_INVENTORY_PATH", inv_path
    )
    msg = _make_message("!mcp", owner=False)
    bot = _make_bot(owner=False)

    with pytest.raises(UserInputError) as exc_info:
        await handle_mcp(bot, msg)
    assert "владельцу" in exc_info.value.user_message
    msg.reply.assert_not_called()


# ---------------------------------------------------------------------------
# 6. Subprocess failure → fallback to inventory.toml
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_subprocess_failure_gracefully(
    inv_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "src.handlers.commands.observability_commands._MCP_INVENTORY_PATH", inv_path
    )
    msg = _make_message("!mcp")
    bot = _make_bot(owner=True)

    with patch(
        "src.handlers.commands.observability_commands._get_registered_mcps",
        AsyncMock(return_value=({}, "openclaw_cli_not_found")),
    ):
        await handle_mcp(bot, msg)

    out = msg.reply.call_args.args[0]
    assert "openclaw cli" in out
    assert "openclaw_cli_not_found" in out
    # Even with subprocess fail — inventory parsed and shown
    assert "MCP Inventory" in out
    assert "0 registered" in out


# ---------------------------------------------------------------------------
# 7. Required env check — missing token shows ⚠️
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_required_env_check(inv_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.handlers.commands.observability_commands._MCP_INVENTORY_PATH", inv_path
    )
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
    # Pretend github is registered but env missing → still flagged
    registered = {"github": {"url": "https://x", "transport": "streamable-http"}}
    msg = _make_message("!mcp")
    bot = _make_bot(owner=True)

    with patch(
        "src.handlers.commands.observability_commands._get_registered_mcps",
        AsyncMock(return_value=(registered, None)),
    ):
        await handle_mcp(bot, msg)

    out = msg.reply.call_args.args[0]
    assert "github" in out
    assert "key: ⚠️ missing" in out


# ---------------------------------------------------------------------------
# 8. Pure unit — _classify_mcp_status / helpers
# ---------------------------------------------------------------------------


def test_classify_mcp_status_variants() -> None:
    # active: registered + no deprecated flag
    assert _classify_mcp_status("a", {"description": "x"}, {"a"}) == "active"
    # deprecated wins
    assert _classify_mcp_status("a", {"status": "deprecated"}, {"a"}) == "deprecated"
    # explicit deferred
    assert _classify_mcp_status("b", {"status": "deferred"}, set()) == "deferred"
    # missing required env → deferred
    import os

    os.environ.pop("XYZ_NOT_SET", None)
    assert _classify_mcp_status("c", {"required_env": ["XYZ_NOT_SET"]}, set()) == "deferred"
    # neither registered nor deferred → inactive
    assert _classify_mcp_status("d", {}, set()) == "inactive"


def test_check_env_var_present() -> None:
    assert _check_env_var_present("X", env={"X": "value"}) is True
    assert _check_env_var_present("X", env={"X": ""}) is False
    assert _check_env_var_present("X", env={"X": "   "}) is False
    assert _check_env_var_present("Y", env={}) is False


def test_load_mcp_inventory_missing_file(tmp_path: pathlib.Path) -> None:
    assert _load_mcp_inventory(tmp_path / "nope.toml") == {}


def test_load_mcp_inventory_parses(inv_path: pathlib.Path) -> None:
    inv = _load_mcp_inventory(inv_path)
    assert "context7" in inv
    assert "github" in inv
    assert inv["github"]["transport"] == "streamable-http"
    assert inv["krab-tor"]["status"] == "deprecated"


@pytest.mark.asyncio
async def test_get_registered_mcps_subprocess_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FileNotFoundError → returns ({}, error_str)."""

    async def fake_create(*a, **kw):
        raise FileNotFoundError("openclaw")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create)
    result, err = await _get_registered_mcps(timeout=1.0)
    assert result == {}
    assert err == "openclaw_cli_not_found"


@pytest.mark.asyncio
async def test_get_registered_mcps_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid JSON output is parsed; trailing junk after JSON is ignored."""
    payload = {
        "context7": {"command": "npx", "args": ["-y"]},
        "github": {"url": "https://x", "transport": "streamable-http"},
    }
    out = json.dumps(payload).encode() + b"\n[openai-codex] Token refresh failed: 401\n"

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.communicate = AsyncMock(return_value=(out, b""))

    async def fake_create(*a, **kw):
        return fake_proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create)
    result, err = await _get_registered_mcps(timeout=1.0)
    assert err is None
    assert "context7" in result
    assert "github" in result


def test_format_mcp_summary_empty() -> None:
    out = _format_mcp_summary({}, {})
    assert "0 registered" in out
    assert "0 in registry" in out


def test_format_mcp_inventory_full_groups(inv_path: pathlib.Path) -> None:
    inv = _load_mcp_inventory(inv_path)
    registered = {"context7": {}, "tor-full": {}}
    out = _format_mcp_inventory_full(inv, registered)
    assert "Active" in out
    assert "Deprecated" in out
    assert "`krab-tor`" in out


def test_format_mcp_info_returns_none_for_unknown() -> None:
    assert _format_mcp_info("nope", {}, {}) is None
