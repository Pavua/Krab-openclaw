# -*- coding: utf-8 -*-
"""Тесты единого MCP-реестра и LM Studio JSON-генератора."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

try:
    from scripts.sync_lmstudio_mcp import _merge_existing_servers
except (ImportError, ModuleNotFoundError, FileNotFoundError):
    pytest.skip("scripts.sync_lmstudio_mcp not available", allow_module_level=True)

try:
    from src.core.mcp_registry import build_lmstudio_mcp_json, resolve_managed_server_launch
except (ImportError, ModuleNotFoundError, FileNotFoundError):
    pytest.skip("src.core.mcp_registry not available", allow_module_level=True)


def test_build_lmstudio_mcp_json_skips_missing_optional_and_high_risk() -> None:
    with patch.dict(os.environ, {}, clear=True):
        with patch("src.core.mcp_registry.load_project_env", return_value={}):
            payload, summary = build_lmstudio_mcp_json(
                include_optional_missing=False,
                include_high_risk=False,
            )

    servers = payload["mcpServers"]
    assert "filesystem" in servers
    assert "lmstudio" in servers
    assert "openclaw-browser" in servers
    assert "chrome-profile" in servers
    assert "context7" not in servers
    assert "github" not in servers
    assert "firecrawl" not in servers
    assert "shell" not in servers
    assert "filesystem-home" not in servers
    assert "context7" in summary["skipped_missing"]
    assert "shell" in summary["skipped_risk"]


def test_resolve_managed_server_launch_merges_project_env_and_fixed_env() -> None:
    fake_env = {
        "CONTEXT7_API_KEY": "ctx-demo",
        "LM_STUDIO_URL": "http://192.168.0.171:1234",
        "LM_STUDIO_API_KEY": "lm-demo-token",
        "BRAVE_API_KEY": "brave-legacy",
        "GITHUB_PERSONAL_ACCESS_TOKEN": "gh-legacy",
    }
    with patch.dict(os.environ, {}, clear=True):
        with patch("src.core.mcp_registry.load_project_env", return_value=fake_env):
            context7 = resolve_managed_server_launch("context7")
            lmstudio = resolve_managed_server_launch("lmstudio")
            brave = resolve_managed_server_launch("brave-search")
            github = resolve_managed_server_launch("github")

    assert context7["missing_env"] == []
    assert context7["env"]["CONTEXT7_API_KEY"] == "ctx-demo"
    assert brave["env"]["BRAVE_SEARCH_API_KEY"] == "brave-legacy"
    assert github["env"]["GITHUB_TOKEN"] == "gh-legacy"
    assert lmstudio["missing_env"] == []
    assert lmstudio["env"]["OPENAI_API_KEY"] == "lm-demo-token"
    assert lmstudio["env"]["OPENAI_BASE_URL"] == "http://192.168.0.171:1234/v1"


def test_merge_existing_servers_drops_managed_names_excluded_by_mode() -> None:
    existing = {
        "mcpServers": {
            "shell": {"command": "npx"},
            "xcodebuild": {"command": "npx"},
        }
    }

    merged, preserved = _merge_existing_servers(
        existing,
        {"filesystem": {"command": "python"}},
        ["filesystem", "shell"],
    )

    assert "filesystem" in merged["mcpServers"]
    assert "shell" not in merged["mcpServers"]
    assert "xcodebuild" in merged["mcpServers"]
    assert preserved == ["xcodebuild"]
