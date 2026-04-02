"""Проверки sync_telegram_mcp_configs.py для Codex/Claude MCP конфигов."""

from __future__ import annotations

import json

from scripts.sync_telegram_mcp_configs import sync_claude_config_text
from scripts.sync_telegram_mcp_configs import sync_codex_config_text


def test_sync_codex_config_text_replaces_existing_sections() -> None:
    source = """
model = 'gpt-5.4'

[mcp_servers.krab-telegram]
command = '/old/python'
args = ['/old/script.py']
startup_timeout_ms = 1000

[mcp_servers.krab-telegram-test]
command = '/old/python'
args = ['/old/script.py']
startup_timeout_ms = 1000

[mcp_servers.playwright]
command = 'npx'
""".lstrip()

    updated = sync_codex_config_text(source)

    assert "session-name', 'kraab'" in updated
    assert "session-name', 'p0lrd_cc'" in updated
    assert "command = '/Users/pablito/Antigravity_AGENTS/Краб/venv/bin/python'" in updated
    assert "[mcp_servers.playwright]" in updated
    assert "/old/python" not in updated


def test_sync_codex_config_text_appends_missing_sections() -> None:
    source = "model = 'gpt-5.4'\n"
    updated = sync_codex_config_text(source)

    assert "[mcp_servers.krab-telegram]" in updated
    assert "[mcp_servers.krab-telegram-test]" in updated


def test_sync_claude_config_text_updates_only_target_servers() -> None:
    source = json.dumps(
        {
            "mcpServers": {
                "krab-yung-nagato": {"command": "/old/python", "args": ["/old/script.py"]},
                "other-server": {"command": "npx", "args": ["-y", "foo"]},
            },
            "preferences": {"sidebarMode": "code"},
        },
        ensure_ascii=False,
    )

    updated = sync_claude_config_text(source)
    payload = json.loads(updated)

    assert payload["mcpServers"]["krab-yung-nagato"]["command"] == "/Users/pablito/Antigravity_AGENTS/Краб/venv/bin/python"
    assert payload["mcpServers"]["krab-yung-nagato"]["args"][2] == "kraab"
    assert payload["mcpServers"]["krab-p0lrd"]["args"][2] == "p0lrd_cc"
    assert payload["mcpServers"]["other-server"] == {"command": "npx", "args": ["-y", "foo"]}
    assert payload["preferences"]["sidebarMode"] == "code"
